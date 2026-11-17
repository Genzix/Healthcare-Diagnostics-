import hashlib
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import workflow as wf
from .models import Booking, BookingItem, BookingStatusLog, Payment, Notification
from .notifications import notify
from diagnostics.models import DiagnosticTest, HealthPackage
from partners.models import DiagnosticPartner, PartnerSlot, PartnerTestPricing, PartnerPackagePricing
from accounts.models import FamilyMember


# =====================================================================
# Errors, notifications, audit log
# =====================================================================

class WorkflowError(Exception):
    """A rejected lifecycle action. `http_status` lets views answer 400 / 403 / 409 precisely."""

    def __init__(self, message, code='invalid', http_status=400, allowed=()):
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.allowed = tuple(allowed)


def send_system_notification(user, notification_type, title, message, booking=None, push=False):
    """Backward-compatible wrapper around notifications.notify()."""
    return notify(user, notification_type, title, message, booking=booking, push=push)


def log_status(booking, from_status, to_status, actor=None, actor_role=wf.SYSTEM, notes='', metadata=None):
    return BookingStatusLog.objects.create(
        booking=booking,
        from_status=from_status or '',
        to_status=to_status,
        actor=actor if getattr(actor, 'is_authenticated', False) else None,
        actor_role=actor_role,
        notes=(notes or '').strip(),
        metadata=metadata or {},
    )


def _lock(booking):
    """Re-read the row under SELECT … FOR UPDATE so concurrent clicks serialise."""
    return Booking.objects.select_for_update().select_related('partner', 'user').get(pk=booking.pk)


def _sync(target, source):
    """Copy the locked row's state back onto the caller's instance."""
    for f in source._meta.concrete_fields:
        setattr(target, f.attname, getattr(source, f.attname))
    return target


# =====================================================================
# Report file validation
# =====================================================================

REPORT_MAX_BYTES = int(getattr(settings, 'REPORT_MAX_UPLOAD_MB', 10)) * 1024 * 1024


def validate_report_pdf(upload):
    """
    Accept only a real PDF: extension, declared type and the %PDF- magic bytes
    must agree. Returns {'size', 'sha256', 'name'}; raises WorkflowError.
    """
    if not upload:
        raise WorkflowError("Attach the laboratory-generated report as a PDF file.", code='report_file_required')
    name = getattr(upload, 'name', '') or 'report.pdf'
    size = getattr(upload, 'size', None) or 0
    if not name.lower().endswith('.pdf'):
        raise WorkflowError("Only PDF reports are accepted.", code='report_not_pdf')
    content_type = (getattr(upload, 'content_type', '') or '').lower()
    if content_type and content_type not in ('application/pdf', 'application/x-pdf', 'application/octet-stream'):
        raise WorkflowError("Only PDF reports are accepted.", code='report_not_pdf')
    if size <= 0:
        raise WorkflowError("The uploaded report is empty.", code='report_empty')
    if size > REPORT_MAX_BYTES:
        raise WorkflowError(f"Report is too large ({size / 1048576:.1f} MB). Maximum is {REPORT_MAX_BYTES // 1048576} MB.", code='report_too_large')

    digest = hashlib.sha256()
    head = b''
    upload.seek(0)
    for chunk in upload.chunks():
        if not head:
            head = chunk[:5]
        digest.update(chunk)
    upload.seek(0)
    if head != b'%PDF-':
        raise WorkflowError("This file is not a valid PDF document.", code='report_not_pdf')
    return {'size': size, 'sha256': digest.hexdigest(), 'name': name}


# =====================================================================
# Lab workflow engine
# =====================================================================

def _patient_message(step, booking):
    partner = booking.partner.centre_name if booking.partner_id else 'your diagnostic centre'
    msg = step.notify_message.format(booking_id=booking.booking_id, partner=partner)
    if booking.patient_type == 'FAMILY_MEMBER' and booking.patient_name:
        msg = f"{msg} (Patient: {booking.patient_name})"
    return msg


@transaction.atomic
def transition_booking(
    booking: Booking,
    target: str,
    actor=None,
    actor_role: str = wf.PARTNER,
    notes: str = '',
    expected_status: str = None,
    report_file=None,
    report_name: str = '',
    report_notes: str = None,
    verified_by_name: str = '',
    verified_by_registration: str = '',
):
    """
    Move a booking one step along the lab pipeline. Raises WorkflowError.

    - Row-locked: two staff clicking at once cannot double-advance.
    - `expected_status` (optional) gives optimistic concurrency: if the booking
      moved since the client loaded it, answer 409 instead of acting on stale UI.
    - Each step's requirements (PDF on upload, verifier on verification, notes on
      send-back, verified file before release) are enforced here, not in the UI.
    """
    target = wf.normalize(target)
    locked = _lock(booking)
    current = locked.booking_status

    if expected_status and wf.normalize(expected_status) != current:
        raise WorkflowError(
            f"This booking is now '{wf.LABELS.get(current, current)}'. Refresh to see the latest status.",
            code='stale_status', http_status=409,
        )
    if target == wf.TEST_COMPLETED:
        raise WorkflowError(
            "'Test Completed' has been replaced by the report pipeline: upload, verify and release the report instead.",
            code='legacy_status', allowed=[s['status'] for s in wf.next_steps(current, actor_role)],
        )

    result = wf.check(current, target, actor_role)
    if not result.ok:
        raise WorkflowError(result.error, code=result.code,
                            http_status=403 if result.code == 'forbidden_actor' else 400, allowed=result.allowed)
    step = result.step
    now = timezone.now()
    notes = (notes or '').strip()
    metadata = {}
    fields = {'booking_status', 'status_changed_at', 'updated_at'}

    if step.requires_notes and len(notes) < 5:
        raise WorkflowError("Explain what needs correcting (at least 5 characters).", code='notes_required')

    # ── step-specific rules & side effects ──
    if target == wf.SAMPLE_COLLECTED:
        locked.sample_collected_at = now
        fields.add('sample_collected_at')

    elif target == wf.SAMPLE_RECEIVED:
        locked.sample_received_at = now
        fields.add('sample_received_at')

    elif target == wf.REPORT_GENERATED and current == wf.REPORT_UPLOADED:
        # Sent back by the verifier: the uploaded file is no longer trusted.
        locked.report_verified_by_name = ''
        locked.report_verified_by_registration = ''
        locked.report_verified_at = None
        fields.update({'report_verified_by_name', 'report_verified_by_registration', 'report_verified_at'})
        metadata['sent_back'] = True

    elif target == wf.REPORT_UPLOADED:
        meta = validate_report_pdf(report_file)
        if locked.report_file:
            metadata['replaced_sha256'] = locked.report_file_sha256
        locked.report_file = report_file
        locked.report_name = (report_name or meta['name'])[:255]
        locked.report_file_size = meta['size']
        locked.report_file_sha256 = meta['sha256']
        locked.report_uploaded_at = now
        # A new file invalidates any earlier verification.
        locked.report_verified_by_name = ''
        locked.report_verified_by_registration = ''
        locked.report_verified_at = None
        fields.update({'report_file', 'report_name', 'report_file_size', 'report_file_sha256', 'report_uploaded_at',
                       'report_verified_by_name', 'report_verified_by_registration', 'report_verified_at'})
        metadata.update(sha256=meta['sha256'], size=meta['size'], reupload=current == wf.REPORT_UPLOADED)

    elif target == wf.REPORT_VERIFIED:
        name = (verified_by_name or '').strip()
        if len(name) < 3:
            raise WorkflowError("Enter the name of the authorised person verifying this report.", code='verifier_required')
        if not locked.report_file:
            raise WorkflowError("Upload the report PDF before verifying it.", code='report_file_required')
        locked.report_verified_by_name = name[:150]
        locked.report_verified_by_registration = (verified_by_registration or '').strip()[:60]
        locked.report_verified_at = now
        fields.update({'report_verified_by_name', 'report_verified_by_registration', 'report_verified_at'})
        metadata.update(verified_by=name, registration=locked.report_verified_by_registration,
                        sha256=locked.report_file_sha256)

    elif target == wf.REPORT_READY:
        if not locked.report_file or not locked.report_verified_at:
            raise WorkflowError("Only an uploaded and verified report can be released.", code='report_not_verified')
        locked.report_released_at = now
        locked.patient_notified_at = now
        locked.completed_at = locked.completed_at or now
        fields.update({'report_released_at', 'patient_notified_at', 'completed_at'})

    if report_notes is not None and report_notes.strip():
        locked.report_notes = report_notes.strip()
        fields.add('report_notes')

    locked.booking_status = target
    locked.status_changed_at = now
    locked.save(update_fields=sorted(fields))
    log_status(locked, current, target, actor=actor, actor_role=actor_role, notes=notes, metadata=metadata)

    if step.notify_type:
        notify(locked.user, step.notify_type, step.notify_title, _patient_message(step, locked), booking=locked, push=step.push)

    return _sync(booking, locked)


@transaction.atomic
def upload_report(booking, actor, report_file, actor_role=wf.PARTNER, report_name='', report_notes='', notes='', expected_status=None):
    """
    Upload (or replace) the lab PDF. Convenience: from LAB_ANALYSIS the
    REPORT_GENERATED step is recorded first, so the audit trail stays complete.
    """
    validate_report_pdf(report_file)  # fail fast, before any status moves
    locked = _lock(booking)
    if expected_status and wf.normalize(expected_status) != locked.booking_status:
        raise WorkflowError("This booking changed since you opened it. Refresh and try again.", code='stale_status', http_status=409)
    if locked.booking_status == wf.LAB_ANALYSIS:
        transition_booking(booking, wf.REPORT_GENERATED, actor=actor, actor_role=actor_role, notes='Recorded automatically on report upload.')
    return transition_booking(
        booking, wf.REPORT_UPLOADED, actor=actor, actor_role=actor_role, notes=notes,
        report_file=report_file, report_name=report_name, report_notes=report_notes,
    )


@transaction.atomic
def record_report_download(booking, user):
    """Patient opened the report: count it; the first open moves the booking to REPORT_DOWNLOADED."""
    locked = _lock(booking)
    now = timezone.now()
    locked.report_download_count = (locked.report_download_count or 0) + 1
    locked.report_last_downloaded_at = now
    first = locked.report_first_downloaded_at is None
    if first:
        locked.report_first_downloaded_at = now
    locked.save(update_fields=['report_download_count', 'report_last_downloaded_at', 'report_first_downloaded_at', 'updated_at'])
    _sync(booking, locked)
    if locked.booking_status in (wf.REPORT_READY, wf.TEST_COMPLETED):
        transition_booking(booking, wf.REPORT_DOWNLOADED, actor=user, actor_role=wf.PATIENT, notes='Patient downloaded the report.')
    return booking


def report_access(booking, user, partner=None):
    """
    Who may fetch the report file:
      - staff/admin: always (if a file exists)
      - the booking's partner: once uploaded (needed to verify it)
      - the patient account: only after release (and, if configured, after full payment)
    Returns (allowed: bool, audience: str, reason: str).
    """
    if not booking.report_file:
        return False, '', "The report has not been uploaded yet."
    if getattr(user, 'is_staff', False) or getattr(user, 'role', '') == 'ADMIN':
        return True, wf.ADMIN, ''
    if partner is not None and booking.partner_id == partner.pk:
        return True, wf.PARTNER, ''
    if booking.user_id == getattr(user, 'pk', None):
        if booking.booking_status not in wf.REPORT_RELEASED:
            return False, wf.PATIENT, "Your report is still being processed. You'll be notified when it is ready."
        if getattr(settings, 'REPORT_REQUIRES_FULL_PAYMENT', False) and booking.payment_status != 'FULLY_PAID':
            return False, wf.PATIENT, "Please pay the remaining balance to download your report."
        return True, wf.PATIENT, ''
    return False, '', "Booking not found."


def partner_update_service_status(
    booking: Booking,
    partner_user,
    new_status: str,
    report_file=None,
    report_notes: str = "",
    report_name: str = "",
    notes: str = "",
    verified_by_name: str = "",
    verified_by_registration: str = "",
    expected_status: str = None,
):
    """Backward-compatible (booking, error) wrapper over transition_booking()."""
    try:
        target = wf.normalize(new_status)
        if target == wf.REPORT_UPLOADED:
            return upload_report(booking, partner_user, report_file, report_name=report_name,
                                 report_notes=report_notes, notes=notes, expected_status=expected_status), None
        return transition_booking(
            booking, target, actor=partner_user, actor_role=wf.PARTNER, notes=notes,
            expected_status=expected_status, report_file=report_file, report_name=report_name,
            report_notes=report_notes, verified_by_name=verified_by_name,
            verified_by_registration=verified_by_registration,
        ), None
    except WorkflowError as exc:
        return None, exc.message



# =====================================================================
# Pricing & booking creation
# =====================================================================

def calculate_booking_pricing(partner: DiagnosticPartner, test_ids: list, package_ids: list, service_mode: str, pincode: str = None):
    """
    Calculates subtotal, home collection fee, total, 30% advance, and 70% balance.
    Takes into account partner-specific price overrides if configured.
    """
    items_breakdown = []
    subtotal = Decimal('0.00')

    # 1. Calculate Tests Pricing
    if test_ids:
        tests = DiagnosticTest.objects.filter(id__in=test_ids, is_available=True)
        # Check partner custom pricing overrides
        partner_test_prices = {
            pt.test_id: pt.price
            for pt in PartnerTestPricing.objects.filter(partner=partner, test__in=tests, is_available=True)
        }
        for test in tests:
            price = partner_test_prices.get(test.id, test.base_price)
            subtotal += price
            items_breakdown.append({
                'item_type': 'TEST',
                'id': test.id,
                'name': test.name,
                'code': test.code,
                'price': float(price),
            })

    # 2. Calculate Packages Pricing
    if package_ids:
        packages = HealthPackage.objects.filter(id__in=package_ids, is_available=True)
        partner_package_prices = {
            pp.package_id: pp.price
            for pp in PartnerPackagePricing.objects.filter(partner=partner, package__in=packages, is_available=True)
        }
        for pkg in packages:
            price = partner_package_prices.get(pkg.id, pkg.price)
            subtotal += price
            items_breakdown.append({
                'item_type': 'PACKAGE',
                'id': pkg.id,
                'name': pkg.name,
                'code': pkg.code,
                'price': float(price),
            })

    # 3. Home Collection Fee & Serviceability Check
    home_collection_fee = Decimal('0.00')
    is_serviceable = True

    if service_mode == 'HOME_COLLECTION':
        if not partner.offers_home_collection:
            return None, "This diagnostic centre does not offer home sample collection."
        if pincode:
            is_serviceable = partner.is_serviceable(pincode)
            if not is_serviceable:
                return None, f"Home collection is not available for pincode {pincode} by this partner."
        home_collection_fee = partner.home_collection_fee

    # 4. Total and 30% / 70% Split Rule
    total_amount = subtotal + home_collection_fee
    if total_amount <= Decimal('0.00'):
        return None, "Please select at least one valid diagnostic test or health package."

    # 30% Advance upfront, exactly 70% remaining balance
    advance_amount = round(total_amount * Decimal('0.30'), 2)
    balance_amount = total_amount - advance_amount

    return {
        'subtotal_amount': float(subtotal),
        'home_collection_fee': float(home_collection_fee),
        'total_amount': float(total_amount),
        'advance_amount_due_30_percent': float(advance_amount),
        'balance_amount_due_70_percent': float(balance_amount),
        'items': items_breakdown,
    }, None


@transaction.atomic
def create_booking_order(user, data: dict):
    """
    Creates a new booking request in AWAITING_ADVANCE_PAYMENT status.
    """
    partner_id = data.get('partner_id')
    try:
        partner = DiagnosticPartner.objects.get(pk=partner_id, status='ACTIVE')
    except DiagnosticPartner.DoesNotExist:
        return None, "Active diagnostic partner centre not found."

    service_mode = data.get('service_mode')
    if service_mode not in ['HOME_COLLECTION', 'CENTRE_VISIT']:
        return None, "Invalid service mode. Choose 'HOME_COLLECTION' or 'CENTRE_VISIT'."

    # Patient Details Snapshot
    patient_type = data.get('patient_type', 'SELF')
    family_member = None

    if patient_type == 'FAMILY_MEMBER':
        family_member_id = data.get('family_member_id')
        try:
            family_member = FamilyMember.objects.get(pk=family_member_id, user=user)
            patient_name = family_member.name
            patient_age = family_member.get_age()
            patient_gender = family_member.gender
            patient_phone = family_member.contact_number or user.phone_number
        except FamilyMember.DoesNotExist:
            return None, "Selected family member profile not found."
    else:
        patient_name = user.get_full_name() or "Patient"
        patient_age = user.get_age()
        patient_gender = user.gender or "Unspecified"
        patient_phone = user.phone_number

    # Home Collection or Centre Visit address
    collection_address = ""
    collection_city = ""
    collection_pincode = ""
    collection_landmark = ""
    centre_name_snapshot = ""
    centre_address_snapshot = ""

    if service_mode == 'HOME_COLLECTION':
        collection_address = data.get('collection_address', '').strip()
        collection_city = data.get('collection_city', '').strip()
        collection_pincode = str(data.get('collection_pincode', '')).strip()
        collection_landmark = data.get('collection_landmark', '').strip()

        if not collection_address or not collection_pincode:
            return None, "Collection address and pincode are required for home collection."

        if not partner.is_serviceable(collection_pincode):
            return None, f"Partner centre does not service pincode {collection_pincode}."
    else:
        centre_name_snapshot = partner.centre_name
        centre_address_snapshot = f"{partner.address_line}, {partner.city}, {partner.state} - {partner.pincode}"

    # Slot validation
    slot_id = data.get('slot_id')
    try:
        slot = PartnerSlot.objects.select_for_update().get(pk=slot_id, partner=partner, is_active=True)
        if not slot.is_available:
            return None, "Selected time slot is already fully booked. Please select another slot."
    except PartnerSlot.DoesNotExist:
        return None, "Valid time slot not found for this partner."

    # Pricing calculation
    test_ids = data.get('test_ids', [])
    package_ids = data.get('package_ids', [])
    pricing_data, error = calculate_booking_pricing(
        partner=partner,
        test_ids=test_ids,
        package_ids=package_ids,
        service_mode=service_mode,
        pincode=collection_pincode
    )
    if error:
        return None, error

    total_amount = Decimal(str(pricing_data['total_amount']))
    advance_amount_due = Decimal(str(pricing_data['advance_amount_due_30_percent']))
    balance_amount_due = Decimal(str(pricing_data['balance_amount_due_70_percent']))
    subtotal = Decimal(str(pricing_data['subtotal_amount']))
    home_collection_fee = Decimal(str(pricing_data['home_collection_fee']))

    # Create Booking
    booking = Booking.objects.create(
        user=user,
        partner=partner,
        patient_type=patient_type,
        family_member=family_member,
        patient_name=patient_name,
        patient_age=patient_age,
        patient_gender=patient_gender,
        patient_phone=patient_phone,
        service_mode=service_mode,
        collection_address=collection_address,
        collection_city=collection_city,
        collection_pincode=collection_pincode,
        collection_landmark=collection_landmark,
        centre_name_snapshot=centre_name_snapshot,
        centre_address_snapshot=centre_address_snapshot,
        booking_date=slot.date,
        slot=slot,
        slot_start_time=slot.start_time,
        slot_end_time=slot.end_time,
        subtotal_amount=subtotal,
        home_collection_fee=home_collection_fee,
        total_amount=total_amount,
        advance_amount_due=advance_amount_due,
        balance_amount_due=balance_amount_due,
        booking_status='AWAITING_ADVANCE_PAYMENT',
        payment_status='PENDING',
    )

    # Create Booking Items
    for item in pricing_data['items']:
        test_obj = DiagnosticTest.objects.filter(id=item['id']).first() if item['item_type'] == 'TEST' else None
        pkg_obj = HealthPackage.objects.filter(id=item['id']).first() if item['item_type'] == 'PACKAGE' else None
        BookingItem.objects.create(
            booking=booking,
            item_type=item['item_type'],
            test=test_obj,
            package=pkg_obj,
            item_name=item['name'],
            price=Decimal(str(item['price']))
        )

    return booking, None



# =====================================================================
# Payments, confirmation, cancellation
# =====================================================================

def _ref(prefix):
    return f"{prefix}-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


def _release_slot(booking):
    if booking.slot_id:
        slot = PartnerSlot.objects.select_for_update().get(pk=booking.slot_id)
        if slot.booked_count > 0:
            slot.booked_count -= 1
            slot.save(update_fields=['booked_count'])


@transaction.atomic
def process_advance_payment(booking: Booking, payment_mode='ONLINE', gateway_transaction_id=None):
    """
    30% advance → AWAITING_PARTNER_CONFIRMATION. The slot seat is claimed here
    under a row lock, so two patients cannot pay into the last seat.
    """
    locked = _lock(booking)
    if locked.booking_status != wf.AWAITING_ADVANCE_PAYMENT:
        return None, f"Booking cannot accept advance payment in current status: {locked.booking_status}."

    if locked.slot_id:
        slot = PartnerSlot.objects.select_for_update().get(pk=locked.slot_id)
        if not slot.is_available:
            return None, "This time slot filled up before your payment completed. Please choose another slot."
        slot.booked_count += 1
        slot.save(update_fields=['booked_count'])

    advance_due = locked.advance_amount_due
    payment = Payment.objects.create(
        payment_id=_ref('PAY'),
        booking=locked,
        payment_type='ADVANCE_30',
        amount=advance_due,
        payment_mode=payment_mode,
        status='SUCCESS',
        gateway_transaction_id=gateway_transaction_id or f"TXN-{uuid.uuid4().hex[:8].upper()}",
    )

    locked.advance_amount_paid = advance_due
    locked.booking_status = wf.AWAITING_PARTNER_CONFIRMATION
    locked.payment_status = 'ADVANCE_PAID'
    locked.status_changed_at = timezone.now()
    locked.save(update_fields=['advance_amount_paid', 'booking_status', 'payment_status', 'status_changed_at', 'updated_at'])
    log_status(locked, wf.AWAITING_ADVANCE_PAYMENT, wf.AWAITING_PARTNER_CONFIRMATION, actor=locked.user,
               actor_role=wf.PATIENT, notes='Advance payment received.', metadata={'payment_id': payment.payment_id})
    _sync(booking, locked)

    notify(locked.user, 'PAYMENT_RESULT', 'Advance Payment (30%) Received',
           f"30% advance payment of ₹{advance_due} received for booking {locked.booking_id}. "
           "Your booking request is now awaiting partner confirmation.", booking=locked)
    if locked.partner.user_id:
        notify(locked.partner.user, 'BOOKING_REMINDER', 'New Incoming Booking Request',
               f"New booking request {locked.booking_id} from {locked.patient_name} awaiting your confirmation.",
               booking=locked, push=True)
    return payment, None


@transaction.atomic
def partner_accept_booking(booking: Booking, partner_user):
    locked = _lock(booking)
    if locked.booking_status != wf.AWAITING_PARTNER_CONFIRMATION:
        return None, "Only bookings awaiting partner confirmation can be accepted."

    now = timezone.now()
    locked.booking_status = wf.CONFIRMED
    locked.confirmed_at = now
    locked.status_changed_at = now
    locked.save(update_fields=['booking_status', 'confirmed_at', 'status_changed_at', 'updated_at'])
    log_status(locked, wf.AWAITING_PARTNER_CONFIRMATION, wf.CONFIRMED, actor=partner_user, actor_role=wf.PARTNER)
    _sync(booking, locked)

    notify(locked.user, 'PARTNER_CONFIRMATION', 'Booking Confirmed by Partner Centre',
           f"Your booking {locked.booking_id} has been confirmed by {locked.partner.centre_name} "
           f"for {locked.booking_date} at {locked.slot_start_time.strftime('%H:%M')}.", booking=locked, push=True)
    return booking, None


def _refund_advance(locked, note):
    if locked.advance_amount_paid > Decimal('0.00') and not locked.payments.filter(payment_type='REFUND').exists():
        Payment.objects.create(
            payment_id=_ref('REF'),
            booking=locked,
            payment_type='REFUND',
            amount=locked.advance_amount_paid,
            payment_mode='ONLINE',
            status='SUCCESS',
            gateway_transaction_id=f"REFUND-TXN-{uuid.uuid4().hex[:8].upper()}",
        )
        locked.payment_status = 'REFUNDED'
        return True
    return False


@transaction.atomic
def partner_reject_booking(booking: Booking, partner_user, reason: str):
    locked = _lock(booking)
    if locked.booking_status != wf.AWAITING_PARTNER_CONFIRMATION:
        return None, "Only bookings awaiting partner confirmation can be rejected."

    now = timezone.now()
    previous = locked.booking_status
    locked.booking_status = wf.REJECTED_BY_PARTNER
    locked.partner_rejection_reason = reason
    locked.cancelled_by = 'PARTNER'
    locked.cancellation_time = now
    locked.status_changed_at = now
    _refund_advance(locked, reason)
    locked.save(update_fields=['booking_status', 'partner_rejection_reason', 'cancelled_by',
                               'cancellation_time', 'payment_status', 'status_changed_at', 'updated_at'])
    _release_slot(locked)
    log_status(locked, previous, wf.REJECTED_BY_PARTNER, actor=partner_user, actor_role=wf.PARTNER, notes=reason)
    _sync(booking, locked)

    notify(locked.user, 'PARTNER_REJECTION', 'Booking Rejected by Partner Centre',
           f"Your booking {locked.booking_id} could not be confirmed by {locked.partner.centre_name}. "
           f"Reason: {reason}. Full refund of advance payment ₹{locked.advance_amount_paid} has been processed.",
           booking=locked, push=True)
    return booking, None


@transaction.atomic
def cancel_booking(booking: Booking, user, reason: str, actor_role=wf.PATIENT):
    """Patient cancellation — allowed only before the sample is collected."""
    locked = _lock(booking)
    previous = locked.booking_status
    if previous not in wf.CANCELLABLE:
        if previous in wf.VOID:
            return None, "This booking is already cancelled."
        return None, "Your sample has already been collected, so this booking can no longer be cancelled. Please contact support."

    now = timezone.now()
    seat_claimed = previous != wf.AWAITING_ADVANCE_PAYMENT  # seat is only taken once the advance is paid
    locked.booking_status = wf.CANCELLED
    locked.cancelled_by = 'USER' if actor_role == wf.PATIENT else actor_role
    locked.cancellation_reason = reason
    locked.cancellation_time = now
    locked.status_changed_at = now
    refunded = _refund_advance(locked, reason)
    locked.save(update_fields=['booking_status', 'cancelled_by', 'cancellation_reason', 'cancellation_time',
                               'payment_status', 'status_changed_at', 'updated_at'])
    if seat_claimed:
        _release_slot(locked)
    log_status(locked, previous, wf.CANCELLED, actor=user, actor_role=actor_role, notes=reason)
    _sync(booking, locked)

    notify(locked.user, 'BOOKING_CANCELLED', 'Booking Cancelled',
           f"Booking {locked.booking_id} has been cancelled."
           + (f" A refund of ₹{locked.advance_amount_paid} will be credited within 3-5 working days." if refunded else ""),
           booking=locked)
    if previous != wf.AWAITING_ADVANCE_PAYMENT and locked.partner.user_id:
        notify(locked.partner.user, 'BOOKING_CANCELLED', 'Booking Cancelled by Patient',
               f"Booking {locked.booking_id} ({locked.patient_name}) was cancelled. Reason: {reason}", booking=locked)
    return booking, None


def generate_1mindoc_pdf_report(booking: Booking) -> bytes:
    """
    Generates a professional medical diagnostic report PDF with 1MinDoc branding.
    Includes booking metadata, patient details, partner centre info, tests table, and verification seal.
    """
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from django.utils import timezone

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )
    story = []
    styles = getSampleStyleSheet()

    primary_color = colors.HexColor('#0F766E')   # 1MinDoc Teal
    secondary_color = colors.HexColor('#0F172A') # Dark Slate
    accent_color = colors.HexColor('#F8FAFC')    # Slate 50
    text_muted = colors.HexColor('#64748B')

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=22,
        leading=26,
        textColor=primary_color,
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=13,
        textColor=text_muted,
    )
    header_style = ParagraphStyle(
        'DocHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=15,
        textColor=secondary_color,
    )
    cell_bold = ParagraphStyle(
        'CellBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=secondary_color,
    )
    cell_normal = ParagraphStyle(
        'CellNormal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=secondary_color,
    )

    # 1. Header with 1MinDoc Branding
    header_data = [
        [
            Paragraph("<b>1MinDoc</b> <font size=11 color='#0F766E'>Diagnostics</font>", title_style),
            Paragraph(f"<b>OFFICIAL DIAGNOSTIC REPORT</b><br/><font color='#64748B'>ID: {booking.booking_id}</font>", subtitle_style)
        ],
        [
            Paragraph("Rapid, Reliable Healthcare Diagnostics • www.1mindoc.genzix.space", subtitle_style),
            Paragraph(f"Date: {timezone.now().strftime('%d %b %Y, %I:%M %p')}", subtitle_style)
        ]
    ]
    t_header = Table(header_data, colWidths=[320, 200])
    t_header.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(t_header)
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=2, color=primary_color, spaceAfter=12))

    # 2. Patient & Partner Centre Information (2 Columns)
    partner = booking.partner
    patient_info = [
        [Paragraph("<b>PATIENT DETAILS</b>", header_style), Paragraph("<b>DIAGNOSTIC CENTRE</b>", header_style)],
        [Paragraph(f"<b>Name:</b> {booking.patient_name}", cell_normal), Paragraph(f"<b>Centre:</b> {partner.centre_name if partner else 'Authorized Partner'}", cell_normal)],
        [Paragraph(f"<b>Age / Gender:</b> {booking.patient_age or 'N/A'} yrs / {booking.patient_gender}", cell_normal), Paragraph(f"<b>License:</b> {partner.license_number if partner else 'Verified'}", cell_normal)],
        [Paragraph(f"<b>Contact:</b> {booking.patient_phone}", cell_normal), Paragraph(f"<b>Location:</b> {partner.city if partner else ''}, {partner.state if partner else ''}", cell_normal)],
        [Paragraph(f"<b>Service Mode:</b> {booking.get_service_mode_display()}", cell_normal), Paragraph(f"<b>Booking Date:</b> {booking.booking_date}", cell_normal)],
    ]
    t_info = Table(patient_info, colWidths=[260, 260])
    t_info.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), accent_color),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#E2E8F0')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#F1F5F9')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(t_info)
    story.append(Spacer(1, 14))

    # 3. Tests & Investigation Table
    story.append(Paragraph("<b>INVESTIGATIONS & TEST RESULTS</b>", header_style))
    story.append(Spacer(1, 6))

    items_data = [
        [
            Paragraph("<b>#</b>", cell_bold),
            Paragraph("<b>Investigation / Package</b>", cell_bold),
            Paragraph("<b>Type</b>", cell_bold),
            Paragraph("<b>Clinical Status</b>", cell_bold)
        ]
    ]

    booking_items = booking.items.all()
    if booking_items.exists():
        for idx, item in enumerate(booking_items, start=1):
            items_data.append([
                Paragraph(str(idx), cell_normal),
                Paragraph(f"<b>{item.item_name}</b>", cell_normal),
                Paragraph("Diagnostic Test" if item.item_type == 'TEST' else "Health Package", cell_normal),
                Paragraph("<font color='#0F766E'><b>Analyzed & Verified</b></font>", cell_normal)
            ])
    else:
        items_data.append([
            Paragraph("1", cell_normal),
            Paragraph("Comprehensive Diagnostic Panel", cell_normal),
            Paragraph("Diagnostic Test", cell_normal),
            Paragraph("<font color='#0F766E'><b>Analyzed & Verified</b></font>", cell_normal)
        ])

    t_items = Table(items_data, colWidths=[30, 270, 110, 110])
    t_items.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), primary_color),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#CBD5E1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, accent_color]),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(t_items)
    story.append(Spacer(1, 14))

    # 4. Clinical Notes / Observations (if provided by lab)
    notes_text = getattr(booking, 'report_notes', '') or "Laboratory parameters analyzed and verified under standard quality protocols. Results validated against biological reference intervals."
    story.append(Paragraph("<b>CLINICAL OBSERVATIONS & LAB NOTES:</b>", cell_bold))
    story.append(Spacer(1, 4))
    story.append(Paragraph(notes_text, cell_normal))
    story.append(Spacer(1, 18))

    # 5. Signatory & 1MinDoc Security Seal
    sign_data = [
        [
            Paragraph("<b>Verified by:</b><br/>Authorized Diagnostic Lab Technician<br/>" + (partner.centre_name if partner else "1MinDoc Network Partner"), cell_normal),
            Paragraph("<b>Quality Assurance:</b><br/><b>1MinDoc Digital Verification Seal</b><br/>Authenticated Electronic Medical Record", cell_normal)
        ]
    ]
    t_sign = Table(sign_data, colWidths=[260, 260])
    t_sign.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LINEABOVE', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
    ]))
    story.append(t_sign)
    story.append(Spacer(1, 15))

    # 6. Footer
    footer_text = Paragraph(
        "<font size=8 color='#64748B'>This report is digitally generated by 1MinDoc Healthcare Technologies (1mindoc.genzix.space). "
        "For medical interpretations or queries, please consult your physician or email support@1mindoc.com.</font>",
        subtitle_style
    )
    story.append(footer_text)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()



@transaction.atomic
def process_balance_payment(booking: Booking, payment_mode='ONLINE', gateway_transaction_id=None, offline_reference=None, recorded_by_partner=False):
    """
    70% balance — online by the patient or recorded offline by the partner.
    Payable from sample collection onwards; never on a cancelled booking.
    """
    locked = _lock(booking)
    if locked.booking_status in wf.VOID:
        return None, "This booking is cancelled; no balance is due."
    if locked.booking_status not in wf.BALANCE_PAYABLE:
        return None, "The balance can be paid once your sample has been collected."
    if locked.payment_status == 'FULLY_PAID':
        return None, "Balance has already been fully paid for this booking."

    balance_due = locked.balance_amount_due - locked.balance_amount_paid
    if balance_due <= Decimal('0.00'):
        return None, "No outstanding balance for this booking."

    payment = Payment.objects.create(
        payment_id=_ref('PAY'),
        booking=locked,
        payment_type='BALANCE_70',
        amount=balance_due,
        payment_mode=payment_mode,
        status='SUCCESS',
        gateway_transaction_id=gateway_transaction_id or f"TXN-{uuid.uuid4().hex[:8].upper()}",
        offline_payment_reference=offline_reference or "",
        recorded_by_partner=recorded_by_partner,
    )

    locked.balance_amount_paid += balance_due
    locked.payment_status = 'FULLY_PAID'
    locked.save(update_fields=['balance_amount_paid', 'payment_status', 'updated_at'])
    _sync(booking, locked)

    notify(locked.user, 'PAYMENT_RESULT', 'Balance Payment (70%) Received - Booking Fully Paid',
           f"Final 70% payment of ₹{balance_due} received. Booking {locked.booking_id} is now fully settled.",
           booking=locked)
    return payment, None
