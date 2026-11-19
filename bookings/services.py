import hashlib
import uuid
from decimal import Decimal, ROUND_DOWN

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
    if step.target == wf.REPORT_READY and not is_report_payment_cleared(booking):
        # Don't tell an unpaid patient to "download" something they can't open yet.
        msg = (f"Your verified report for booking {booking.booking_id} from {partner} is ready. "
               f"Pay the outstanding ₹{booking.amount_outstanding:.2f} in the app to unlock and download it.")
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


# Patient-facing report states (also sent to clients as `report_access.state`).
REPORT_STATE_PENDING = 'PENDING'                    # lab hasn't released it yet
REPORT_STATE_PAYMENT_REQUIRED = 'PAYMENT_REQUIRED'  # released, but money is still owed
REPORT_STATE_AVAILABLE = 'AVAILABLE'                # released and fully paid


def report_payment_required():
    """Reports and results are released to patients only once the booking is fully paid (on by default)."""
    return getattr(settings, 'REPORT_REQUIRES_FULL_PAYMENT', True)


def is_report_payment_cleared(booking):
    """Nothing left to pay. Uses the ledger (amount_outstanding), not just the status flag."""
    if not report_payment_required():
        return True
    return booking.payment_status == 'FULLY_PAID' or booking.amount_outstanding <= Decimal('0.00')


def patient_report_state(booking):
    """
    The patient's view of their report — the one rule every surface uses
    (detail/list payloads, the download endpoint, notifications):
      {state, locked, amount_due, message}
    `locked` means: the report is released but withheld until the balance is paid.
    """
    if not booking.report_file or booking.booking_status not in wf.REPORT_RELEASED:
        return {'state': REPORT_STATE_PENDING, 'locked': False, 'amount_due': None,
                'message': "Your report is still being processed. You'll be notified when it is ready."}
    if not is_report_payment_cleared(booking):
        due = booking.amount_outstanding
        return {'state': REPORT_STATE_PAYMENT_REQUIRED, 'locked': True, 'amount_due': f"{due:.2f}",
                'message': f"Your report is ready. Pay the outstanding ₹{due:.2f} to view and download it."}
    return {'state': REPORT_STATE_AVAILABLE, 'locked': False, 'amount_due': '0.00', 'message': ''}


def is_report_visible_to_patient(booking):
    return patient_report_state(booking)['state'] == REPORT_STATE_AVAILABLE


def report_access(booking, user, partner=None):
    """
    Who may fetch the report file:
      - staff/admin: always (if a file exists)
      - the booking's partner: once uploaded (needed to verify it)
      - the patient account: only after release AND full payment
    Returns (allowed: bool, audience: str, reason: str).
    """
    if not booking.report_file:
        return False, '', "The report has not been uploaded yet."
    if getattr(user, 'is_staff', False) or getattr(user, 'role', '') == 'ADMIN':
        return True, wf.ADMIN, ''
    if partner is not None and booking.partner_id == partner.pk:
        return True, wf.PARTNER, ''
    if booking.user_id == getattr(user, 'pk', None):
        state = patient_report_state(booking)
        return state['state'] == REPORT_STATE_AVAILABLE, wf.PATIENT, state['message']
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

CENT = Decimal('0.01')
ADVANCE_RATE = Decimal('0.30')


def max_patients_per_order():
    """How many people one household order may cover (one visit, one slot)."""
    return max(1, int(getattr(settings, 'BOOKING_MAX_PATIENTS_PER_ORDER', 6)))


def split_amount(amount, parts):
    """
    Split money into `parts` shares that sum to `amount` exactly. The first share
    absorbs the paise remainder, so ₹150 / 4 → [37.50, 37.50, 37.50, 37.50] and
    ₹100 / 3 → [33.34, 33.33, 33.33].
    """
    amount = Decimal(str(amount)).quantize(CENT)
    if parts <= 1:
        return [amount]
    base = (amount / parts).quantize(CENT, rounding=ROUND_DOWN)
    return [amount - base * (parts - 1)] + [base] * (parts - 1)


def _advance_split(total):
    """30% advance upfront, exactly the remainder as balance."""
    advance = round(total * ADVANCE_RATE, 2)
    return advance, total - advance


def calculate_booking_pricing(partner: DiagnosticPartner, test_ids: list, package_ids: list, service_mode: str,
                              pincode: str = None, patient_count: int = 1):
    """
    Calculates subtotal, home collection fee, total, 30% advance, and 70% balance.
    Takes into account partner-specific price overrides if configured.

    `patient_count` > 1 prices a household order: every patient takes the same
    tests, but the technician makes ONE visit, so the home collection fee is
    charged once and shared across the patients' bookings (`per_patient`). The
    group advance is the sum of each booking's own 30%, so what the estimate
    shows is exactly what the per-booking ledgers will charge.
    """
    patient_count = int(patient_count or 1)
    limit = max_patients_per_order()
    if patient_count < 1 or patient_count > limit:
        return None, f"One booking can cover between 1 and {limit} patients."

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

    if service_mode == 'HOME_COLLECTION':
        if not partner.offers_home_collection:
            return None, "This diagnostic centre does not offer home sample collection."
        if pincode and not partner.is_serviceable(pincode):
            return None, f"Home collection is not available for pincode {pincode} by this partner."
        home_collection_fee = partner.home_collection_fee

    # 4. Per-patient ledgers (one visit fee, shared) and the 30% / 70% split
    if subtotal + home_collection_fee <= Decimal('0.00'):
        return None, "Please select at least one valid diagnostic test or health package."

    per_patient = []
    for fee_share in split_amount(home_collection_fee, patient_count):
        total = subtotal + fee_share
        advance, balance = _advance_split(total)
        per_patient.append({
            'subtotal_amount': subtotal,
            'home_collection_fee': fee_share,
            'total_amount': total,
            'advance_amount_due': advance,
            'balance_amount_due': balance,
        })

    total_amount = sum((p['total_amount'] for p in per_patient), Decimal('0.00'))
    advance_amount = sum((p['advance_amount_due'] for p in per_patient), Decimal('0.00'))
    balance_amount = sum((p['balance_amount_due'] for p in per_patient), Decimal('0.00'))

    return {
        'patient_count': patient_count,
        'per_patient_subtotal': float(subtotal),
        'subtotal_amount': float(subtotal * patient_count),
        'home_collection_fee': float(home_collection_fee),
        'total_amount': float(total_amount),
        'advance_amount_due_30_percent': float(advance_amount),
        'balance_amount_due_70_percent': float(balance_amount),
        'items': items_breakdown,
        'per_patient': [{k: float(v) for k, v in p.items()} for p in per_patient],
        '_ledgers': per_patient,  # Decimal rows for booking creation; stripped from API responses
    }, None


def public_pricing(pricing):
    """Pricing dict safe to serialise (drops the internal Decimal ledgers)."""
    return {k: v for k, v in pricing.items() if not k.startswith('_')}


# ── Booking creation building blocks (shared by single and household orders) ──

def _resolve_patient(user, patient_type, family_member_id=None):
    """Frozen patient snapshot for a booking. Raises WorkflowError."""
    if patient_type == 'FAMILY_MEMBER':
        member = FamilyMember.objects.filter(pk=family_member_id, user=user).first() if family_member_id else None
        if not member:
            raise WorkflowError("Selected family member profile not found.", code='patient_not_found')
        return {
            'patient_type': 'FAMILY_MEMBER',
            'family_member': member,
            'patient_name': member.name,
            'patient_age': member.get_age(),
            'patient_gender': member.gender,
            'patient_phone': member.contact_number or user.phone_number,
        }
    return {
        'patient_type': 'SELF',
        'family_member': None,
        'patient_name': user.get_full_name() or "Patient",
        'patient_age': user.get_age(),
        'patient_gender': user.gender or "Unspecified",
        'patient_phone': user.phone_number,
    }


def _resolve_location(user, partner, service_mode, data):
    """Collection address (home) or centre snapshot (visit). Raises WorkflowError."""
    loc = {
        'collection_address': '', 'collection_city': '', 'collection_pincode': '', 'collection_landmark': '',
        'collection_latitude': None, 'collection_longitude': None, 'collection_address_ref': None,
        'collection_contact_name': '', 'collection_contact_phone': '',
        'centre_name_snapshot': '', 'centre_address_snapshot': '',
    }
    if service_mode != 'HOME_COLLECTION':
        loc['centre_name_snapshot'] = partner.centre_name
        loc['centre_address_snapshot'] = f"{partner.address_line}, {partner.city}, {partner.state} - {partner.pincode}"
        return loc

    address_id = data.get('address_id')
    if address_id:
        from accounts.models import UserAddress
        saved = UserAddress.objects.filter(pk=address_id, user=user).first()
        if not saved:
            raise WorkflowError("Selected address not found. Please choose another address.", code='address_not_found')
        loc.update(
            collection_address=', '.join(p for p in [saved.house, saved.area] if p),
            collection_city=saved.city,
            collection_pincode=saved.pincode,
            collection_landmark=saved.landmark,
            collection_latitude=saved.latitude,
            collection_longitude=saved.longitude,
            collection_address_ref=saved,
            collection_contact_name=saved.receiver_name,
            collection_contact_phone=saved.receiver_phone,
        )
    else:
        loc.update(
            collection_address=(data.get('collection_address') or '').strip(),
            collection_city=(data.get('collection_city') or '').strip(),
            collection_pincode=str(data.get('collection_pincode') or '').strip(),
            collection_landmark=(data.get('collection_landmark') or '').strip(),
        )

    if not loc['collection_address'] or not loc['collection_pincode']:
        raise WorkflowError("Collection address and pincode are required for home collection.", code='address_required')
    if not partner.is_serviceable(loc['collection_pincode']):
        raise WorkflowError(f"Partner centre does not service pincode {loc['collection_pincode']}.", code='not_serviceable')
    return loc


def _resolve_slot(partner, slot_id, seats=1):
    """Row-lock the slot and check it can seat `seats` patients. Seats are claimed at payment time."""
    slot = PartnerSlot.objects.select_for_update().filter(pk=slot_id, partner=partner, is_active=True).first() if slot_id else None
    if not slot:
        raise WorkflowError("Valid time slot not found for this partner.", code='slot_not_found')
    free = slot.max_capacity - slot.booked_count
    if free < 1:
        raise WorkflowError("Selected time slot is already fully booked. Please select another slot.", code='slot_full', http_status=409)
    if free < seats:
        raise WorkflowError(
            f"Only {free} seat{'s' if free != 1 else ''} left in this slot — not enough for {seats} patients. Please pick another slot.",
            code='slot_full', http_status=409,
        )
    return slot


def _resolve_partner(data):
    partner = DiagnosticPartner.objects.filter(pk=data.get('partner_id'), status='ACTIVE').first()
    if not partner:
        raise WorkflowError("Active diagnostic partner centre not found.", code='partner_not_found')
    service_mode = data.get('service_mode')
    if service_mode not in ('HOME_COLLECTION', 'CENTRE_VISIT'):
        raise WorkflowError("Invalid service mode. Choose 'HOME_COLLECTION' or 'CENTRE_VISIT'.", code='invalid_service_mode')
    return partner, service_mode


def _item_templates(pricing_items):
    """Resolve catalogue rows once per order (not once per patient per item)."""
    test_ids = [i['id'] for i in pricing_items if i['item_type'] == 'TEST']
    pkg_ids = [i['id'] for i in pricing_items if i['item_type'] == 'PACKAGE']
    tests = DiagnosticTest.objects.in_bulk(test_ids)
    pkgs = HealthPackage.objects.in_bulk(pkg_ids)
    return [
        {
            'item_type': i['item_type'],
            'test': tests.get(i['id']) if i['item_type'] == 'TEST' else None,
            'package': pkgs.get(i['id']) if i['item_type'] == 'PACKAGE' else None,
            'item_name': i['name'],
            'price': Decimal(str(i['price'])),
        }
        for i in pricing_items
    ]


def _create_booking_row(user, partner, service_mode, patient, location, slot, ledger, item_templates, group_ref=''):
    booking = Booking.objects.create(
        user=user,
        partner=partner,
        group_ref=group_ref,
        service_mode=service_mode,
        **patient,
        **location,
        booking_date=slot.date,
        slot=slot,
        slot_start_time=slot.start_time,
        slot_end_time=slot.end_time,
        subtotal_amount=ledger['subtotal_amount'],
        home_collection_fee=ledger['home_collection_fee'],
        total_amount=ledger['total_amount'],
        advance_amount_due=ledger['advance_amount_due'],
        balance_amount_due=ledger['balance_amount_due'],
        booking_status=wf.AWAITING_ADVANCE_PAYMENT,
        payment_status='PENDING',
    )
    BookingItem.objects.bulk_create([BookingItem(booking=booking, **tpl) for tpl in item_templates])
    return booking


def _price_or_raise(partner, data, service_mode, pincode, patient_count):
    pricing, error = calculate_booking_pricing(
        partner=partner,
        test_ids=data.get('test_ids', []),
        package_ids=data.get('package_ids', []),
        service_mode=service_mode,
        pincode=pincode,
        patient_count=patient_count,
    )
    if error:
        raise WorkflowError(error, code='pricing_error')
    return pricing


@transaction.atomic
def create_booking_order(user, data: dict):
    """
    Creates a new booking request in AWAITING_ADVANCE_PAYMENT status.
    """
    try:
        partner, service_mode = _resolve_partner(data)
        patient = _resolve_patient(user, data.get('patient_type', 'SELF'), data.get('family_member_id'))
        location = _resolve_location(user, partner, service_mode, data)
        slot = _resolve_slot(partner, data.get('slot_id'))
        pricing = _price_or_raise(partner, data, service_mode, location['collection_pincode'], 1)
    except WorkflowError as exc:
        return None, exc.message

    booking = _create_booking_row(
        user, partner, service_mode, patient, location, slot,
        pricing['_ledgers'][0], _item_templates(pricing['items']),
    )
    if location['collection_address_ref']:
        from accounts.addresses import touch_address
        touch_address(location['collection_address_ref'])
    return booking, None


def _patient_key(entry):
    ptype = (entry.get('patient_type') or 'SELF').upper()
    return ('FAMILY_MEMBER', int(entry.get('family_member_id') or 0)) if ptype == 'FAMILY_MEMBER' else ('SELF', 0)


def _new_group_ref():
    from .models import generate_group_ref
    for _ in range(5):
        ref = generate_group_ref()
        if not Booking.objects.filter(group_ref=ref).exists():
            return ref
    raise WorkflowError("Could not allocate an order reference. Please try again.", code='group_ref_collision', http_status=503)


@transaction.atomic
def create_group_booking_order(user, data: dict):
    """
    One household order: several patients, same tests, one address, one slot, one
    visit. Each patient gets their own booking (own sample label, report, lab
    pipeline and refund), linked by `group_ref`. All-or-nothing: if any patient
    fails validation, no booking is created.

    Returns (bookings, group_ref). Raises WorkflowError.
    """
    entries = data.get('patients') or []
    limit = max_patients_per_order()
    if not entries:
        raise WorkflowError("Select at least one patient.", code='patients_required')
    if len(entries) > limit:
        raise WorkflowError(f"One booking can cover at most {limit} patients.", code='too_many_patients')
    keys = [_patient_key(e) for e in entries]
    if len(set(keys)) != len(keys):
        raise WorkflowError("Each patient can be added only once.", code='duplicate_patient')

    partner, service_mode = _resolve_partner(data)
    patients = [_resolve_patient(user, e.get('patient_type', 'SELF'), e.get('family_member_id')) for e in entries]
    location = _resolve_location(user, partner, service_mode, data)
    slot = _resolve_slot(partner, data.get('slot_id'), seats=len(patients))

    # Same person cannot hold two live seats in the same slot (an unpaid draft holds no seat).
    clashes = Booking.objects.filter(
        user=user, slot=slot, booking_status__in=wf.ACTIVE - {wf.AWAITING_ADVANCE_PAYMENT},
    ).values_list('patient_type', 'family_member_id', 'patient_name')
    taken = {('FAMILY_MEMBER', fm) if pt == 'FAMILY_MEMBER' else ('SELF', 0): name for pt, fm, name in clashes}
    already = [taken[k] for k in keys if k in taken]
    if already:
        raise WorkflowError(
            f"{', '.join(already)} already {'has' if len(already) == 1 else 'have'} a booking in this slot.",
            code='patient_already_booked', http_status=409,
        )

    pricing = _price_or_raise(partner, data, service_mode, location['collection_pincode'], len(patients))
    templates = _item_templates(pricing['items'])
    group_ref = _new_group_ref()
    bookings = [
        _create_booking_row(user, partner, service_mode, patient, location, slot, ledger, templates, group_ref=group_ref)
        for patient, ledger in zip(patients, pricing['_ledgers'])
    ]
    if location['collection_address_ref']:
        from accounts.addresses import touch_address
        touch_address(location['collection_address_ref'])
    return bookings, group_ref



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


def _claim_seats(slot_id, seats, full_message):
    """
    Take `seats` places in the slot under a row lock — all or nothing, so a
    household order never ends up half-seated. Raises WorkflowError (409).
    """
    if not slot_id or seats <= 0:
        return
    slot = PartnerSlot.objects.select_for_update().get(pk=slot_id)
    if not slot.is_active or slot.max_capacity - slot.booked_count < seats:
        raise WorkflowError(full_message, code='slot_full', http_status=409)
    slot.booked_count += seats
    slot.save(update_fields=['booked_count'])


def _apply_advance(locked, payment_mode, gateway_transaction_id, metadata=None):
    """Record the 30% advance on an already-locked, seated booking (no notifications)."""
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
               actor_role=wf.PATIENT, notes='Advance payment received.',
               metadata={'payment_id': payment.payment_id, **(metadata or {})})
    return payment


def _apply_pay_later(locked, user, metadata=None):
    """Switch an already-locked, seated booking to pay-at-collection (no notifications)."""
    locked.payment_plan = PAY_LATER
    locked.booking_status = wf.AWAITING_PARTNER_CONFIRMATION
    locked.status_changed_at = timezone.now()
    locked.save(update_fields=['payment_plan', 'booking_status', 'status_changed_at', 'updated_at'])
    log_status(locked, wf.AWAITING_ADVANCE_PAYMENT, wf.AWAITING_PARTNER_CONFIRMATION, actor=user,
               actor_role=wf.PATIENT, notes='Pay at collection selected.',
               metadata={'payment_plan': PAY_LATER, **(metadata or {})})


@transaction.atomic
def process_advance_payment(booking: Booking, payment_mode='ONLINE', gateway_transaction_id=None):
    """
    30% advance → AWAITING_PARTNER_CONFIRMATION. The slot seat is claimed here
    under a row lock, so two patients cannot pay into the last seat.
    """
    locked = _lock(booking)
    if locked.booking_status != wf.AWAITING_ADVANCE_PAYMENT:
        return None, f"Booking cannot accept advance payment in current status: {locked.booking_status}."

    try:
        _claim_seats(locked.slot_id, 1, "This time slot filled up before your payment completed. Please choose another slot.")
    except WorkflowError as exc:
        return None, exc.message

    payment = _apply_advance(locked, payment_mode, gateway_transaction_id)
    advance_due = payment.amount
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
    """Refund everything actually paid (advance, plus any balance / full payment already collected)."""
    paid = locked.amount_paid
    if paid > Decimal('0.00') and not locked.payments.filter(payment_type='REFUND').exists():
        Payment.objects.create(
            payment_id=_ref('REF'),
            booking=locked,
            payment_type='REFUND',
            amount=paid,
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
           f"Reason: {reason}. "
           + (f"A full refund of ₹{locked.amount_paid} has been processed." if locked.amount_paid > 0 else "No payment was taken."),
           booking=locked, push=True)
    return booking, None


# ── Pay at collection ───────────────────────────────────────────────

PAY_LATER = 'PAY_AT_COLLECTION'


def pay_at_collection_eligibility(partner, total_amount, user=None):
    """
    Can this booking skip the online advance and be paid to the collector / at the centre?
    Returns (available: bool, reason: str). Guard-rails, all configurable:
      PAY_AT_COLLECTION_ENABLED     (default True)
      PAY_AT_COLLECTION_MAX_AMOUNT  (default 5000)  — cap the no-show risk
      PAY_AT_COLLECTION_MAX_OPEN    (default 2)     — unpaid pay-later bookings per patient
      partner.accepts_pay_at_collection
    """
    if not getattr(settings, 'PAY_AT_COLLECTION_ENABLED', True):
        return False, "Pay at collection is currently unavailable."
    if partner is not None and not getattr(partner, 'accepts_pay_at_collection', True):
        return False, "This centre accepts online advance payment only."
    cap = Decimal(str(getattr(settings, 'PAY_AT_COLLECTION_MAX_AMOUNT', 5000)))
    if total_amount is not None and Decimal(str(total_amount)) > cap:
        return False, f"Pay at collection is available for bookings up to ₹{cap:,.0f}. Please pay the advance online."
    if user is not None and getattr(user, 'is_authenticated', False):
        max_open = int(getattr(settings, 'PAY_AT_COLLECTION_MAX_OPEN', 2))
        # Count orders, not patients: a family of four on one visit is one open order.
        rows = Booking.objects.filter(
            user=user, payment_plan=PAY_LATER, payment_status='PENDING',
            booking_status__in=wf.ACTIVE,
        ).values_list('group_ref', 'pk')
        open_unpaid = len({ref or f'#{pk}' for ref, pk in rows})
        if open_unpaid >= max_open:
            return False, f"You already have {open_unpaid} unpaid pay-at-collection bookings. Please pay online for this one."
    return True, ''


@transaction.atomic
def choose_pay_at_collection(booking: Booking, user):
    """
    Patient books now and pays the full amount at collection (cash / UPI to the
    collector, or at the centre). Skips the online advance; the slot seat is
    claimed under lock exactly as an advance payment would.
    """
    locked = _lock(booking)
    if locked.booking_status != wf.AWAITING_ADVANCE_PAYMENT:
        return None, f"Payment option can only be chosen before the booking is submitted (current: {wf.LABELS.get(locked.booking_status)})."
    ok, reason = pay_at_collection_eligibility(locked.partner, locked.total_amount, user)
    if not ok:
        return None, reason

    try:
        _claim_seats(locked.slot_id, 1, "This time slot just filled up. Please choose another slot.")
    except WorkflowError as exc:
        return None, exc.message

    _apply_pay_later(locked, user)
    _sync(booking, locked)

    where = 'to the sample collector' if locked.service_mode == 'HOME_COLLECTION' else 'at the centre'
    notify(locked.user, 'PAYMENT_RESULT', 'Booking request sent',
           f"Booking {locked.booking_id} is awaiting partner confirmation. Pay ₹{locked.total_amount} {where} "
           "(cash or UPI), or pay online any time after the lab confirms.", booking=locked)
    if locked.partner.user_id:
        notify(locked.partner.user, 'BOOKING_REMINDER', 'New booking · pay at collection',
               f"New booking {locked.booking_id} from {locked.patient_name}. Collect ₹{locked.total_amount} {where}.",
               booking=locked, push=True)
    return booking, None


@transaction.atomic
def cancel_booking(booking: Booking, user, reason: str, actor_role=wf.PATIENT, notify_user=True):
    """Patient cancellation — allowed only before the sample is collected.

    `notify_user=False` lets an order-level cancel send one summary instead of one message per patient.
    """
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

    if not notify_user:
        return booking, None
    notify(locked.user, 'BOOKING_CANCELLED', 'Booking Cancelled',
           f"Booking {locked.booking_id} has been cancelled."
           + (f" A refund of ₹{locked.amount_paid} will be credited within 3-5 working days." if refunded else ""),
           booking=locked)
    if previous != wf.AWAITING_ADVANCE_PAYMENT and locked.partner.user_id:
        notify(locked.partner.user, 'BOOKING_CANCELLED', 'Booking Cancelled by Patient',
               f"Booking {locked.booking_id} ({locked.patient_name}) was cancelled. Reason: {reason}", booking=locked)
    return booking, None


# ── Household (multi-patient) orders ────────────────────────────────

def _lock_group(user, group_ref):
    """Lock every booking of the user's order, in pk order (a stable lock order avoids deadlocks)."""
    rows = list(
        Booking.objects.select_for_update().select_related('partner', 'user')
        .filter(user=user, group_ref=group_ref).exclude(group_ref='').order_by('pk')
    )
    if not rows:
        raise WorkflowError("Booking not found.", code='not_found', http_status=404)
    return rows


def _awaiting_payment(rows):
    pending = [b for b in rows if b.booking_status == wf.AWAITING_ADVANCE_PAYMENT]
    if not pending:
        raise WorkflowError("This order has already been submitted — nothing is left to pay now.", code='nothing_due', http_status=409)
    return pending


def _claim_group_seats(pending):
    """All patients' seats in one go (an order never ends up half-seated)."""
    from collections import Counter
    for slot_id, seats in sorted(Counter(b.slot_id for b in pending if b.slot_id).items()):
        _claim_seats(
            slot_id, seats,
            f"This time slot no longer has {seats} free seat{'s' if seats != 1 else ''}. Please choose another slot.",
        )


def join_names(names):
    names = [n for n in names if n]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}" if names else ''


def _when(b):
    return f"{b.booking_date:%d %b} at {b.slot_start_time:%H:%M}"


@transaction.atomic
def pay_group_advance(user, group_ref, payment_mode='ONLINE', gateway_transaction_id=None):
    """
    One checkout for the whole household: every pending booking gets its own 30%
    advance ledger entry, all under ONE gateway transaction, and all seats are
    claimed atomically. Returns (bookings_paid, payments). Raises WorkflowError.
    """
    rows = _lock_group(user, group_ref)
    pending = _awaiting_payment(rows)
    _claim_group_seats(pending)

    txn = gateway_transaction_id or f"TXN-{uuid.uuid4().hex[:8].upper()}"
    payments = [_apply_advance(b, payment_mode, txn, {'group_ref': group_ref}) for b in pending]
    total = sum((p.amount for p in payments), Decimal('0.00'))
    lead, names = pending[0], join_names([b.patient_name for b in pending])

    notify(user, 'PAYMENT_RESULT', 'Advance Payment (30%) Received',
           f"30% advance of ₹{total} received for {len(pending)} patients ({names}) — order {group_ref}. "
           "Your booking request is now awaiting partner confirmation.", booking=lead)
    if lead.partner.user_id:
        notify(lead.partner.user, 'BOOKING_REMINDER', f'New household booking · {len(pending)} patients',
               f"Order {group_ref}: {names} at one address, {_when(lead)}. "
               f"Bookings {', '.join(b.booking_id for b in pending)} await your confirmation.",
               booking=lead, push=True)
    return pending, payments


@transaction.atomic
def choose_group_pay_at_collection(user, group_ref):
    """Submit the whole household order and pay everything at collection. Raises WorkflowError."""
    rows = _lock_group(user, group_ref)
    pending = _awaiting_payment(rows)
    lead = pending[0]
    total = sum((b.total_amount for b in pending), Decimal('0.00'))
    ok, reason = pay_at_collection_eligibility(lead.partner, total, user)
    if not ok:
        raise WorkflowError(reason, code='pay_at_collection_unavailable')
    _claim_group_seats(pending)
    for b in pending:
        _apply_pay_later(b, user, {'group_ref': group_ref})

    where = 'to the sample collector' if lead.service_mode == 'HOME_COLLECTION' else 'at the centre'
    names = join_names([b.patient_name for b in pending])
    notify(user, 'PAYMENT_RESULT', 'Booking request sent',
           f"Order {group_ref} for {names} is awaiting partner confirmation. Pay ₹{total} {where} "
           "(cash or UPI), or pay online any time after the lab confirms.", booking=lead)
    if lead.partner.user_id:
        notify(lead.partner.user, 'BOOKING_REMINDER', 'New household booking · pay at collection',
               f"Order {group_ref}: {names} at one address, {_when(lead)}. Collect ₹{total} {where}.",
               booking=lead, push=True)
    return pending


@transaction.atomic
def cancel_group(user, group_ref, reason):
    """Cancel every still-cancellable booking of the order, with one summary notification each side."""
    rows = _lock_group(user, group_ref)
    targets = [b for b in rows if b.booking_status in wf.CANCELLABLE]
    if not targets:
        raise WorkflowError("Nothing in this order can be cancelled any more.", code='not_cancellable')
    was_submitted = [b for b in targets if b.booking_status != wf.AWAITING_ADVANCE_PAYMENT]
    for b in targets:
        _, error = cancel_booking(b, user, reason, notify_user=False)
        if error:
            raise WorkflowError(error, code='not_cancellable')
    refunded = sum((b.amount_paid for b in targets if b.payment_status == 'REFUNDED'), Decimal('0.00'))

    lead = targets[0]
    notify(user, 'BOOKING_CANCELLED', 'Booking Cancelled',
           f"Order {group_ref} ({join_names([b.patient_name for b in targets])}) has been cancelled."
           + (f" A refund of ₹{refunded} will be credited within 3-5 working days." if refunded else ""),
           booking=lead)
    if was_submitted and lead.partner.user_id:
        notify(lead.partner.user, 'BOOKING_CANCELLED', 'Household booking cancelled by patient',
               f"Order {group_ref}: {', '.join(b.booking_id for b in was_submitted)} cancelled. Reason: {reason}",
               booking=lead)
    return targets


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



def payable_statuses(booking):
    """Prepaid: settle once the sample is collected. Pay-at-collection: any time after the lab confirms."""
    if booking.payment_plan == PAY_LATER:
        return {wf.CONFIRMED} | wf.BALANCE_PAYABLE
    return wf.BALANCE_PAYABLE


@transaction.atomic
def process_balance_payment(booking: Booking, payment_mode='ONLINE', gateway_transaction_id=None, offline_reference=None, recorded_by_partner=False):
    """
    Settle everything still owed — online by the patient, or recorded offline
    (cash / UPI / card) by the partner.
      prepaid booking         → the 70% balance           (payment_type BALANCE_70)
      pay-at-collection       → the full booking amount   (payment_type FULL)
    """
    locked = _lock(booking)
    if locked.booking_status in wf.VOID:
        return None, "This booking is cancelled; no payment is due."
    if locked.booking_status not in payable_statuses(locked):
        if locked.payment_plan == PAY_LATER:
            return None, "Payment can be made once the lab confirms your booking."
        return None, "The balance can be paid once your sample has been collected."
    if locked.payment_status == 'FULLY_PAID':
        return None, "This booking is already fully paid."

    due = locked.amount_outstanding
    if due <= Decimal('0.00'):
        return None, "No outstanding amount for this booking."

    is_full = locked.advance_amount_paid <= Decimal('0.00')
    payment = Payment.objects.create(
        payment_id=_ref('PAY'),
        booking=locked,
        payment_type='FULL' if is_full else 'BALANCE_70',
        amount=due,
        payment_mode=payment_mode,
        status='SUCCESS',
        gateway_transaction_id=gateway_transaction_id or f"TXN-{uuid.uuid4().hex[:8].upper()}",
        offline_payment_reference=offline_reference or "",
        recorded_by_partner=recorded_by_partner,
    )

    # Keep the 30/70 ledger meaningful for reporting even when paid in one go.
    locked.advance_amount_paid = locked.advance_amount_due
    locked.balance_amount_paid = locked.total_amount - locked.advance_amount_due
    locked.payment_status = 'FULLY_PAID'
    locked.save(update_fields=['advance_amount_paid', 'balance_amount_paid', 'payment_status', 'updated_at'])
    _sync(booking, locked)

    how = 'collected by the lab' if recorded_by_partner else 'received'
    notify(locked.user, 'PAYMENT_RESULT', 'Payment received · booking fully paid',
           f"Payment of ₹{due} {how} for booking {locked.booking_id}. Nothing more is due.", booking=locked)
    if locked.report_file and locked.booking_status in wf.REPORT_RELEASED:
        # The report was waiting on this payment — tell the patient it's open now.
        notify(locked.user, 'REPORT_READY', 'Your report is unlocked',
               f"Thanks for your payment. Your report for booking {locked.booking_id} is now available to view and download.",
               booking=locked, push=True)
    return payment, None
