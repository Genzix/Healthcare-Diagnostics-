import uuid
from decimal import Decimal
from django.utils import timezone
from django.db import transaction

from .models import Booking, BookingItem, Payment, Notification
from diagnostics.models import DiagnosticTest, HealthPackage
from partners.models import DiagnosticPartner, PartnerSlot, PartnerTestPricing, PartnerPackagePricing
from accounts.models import FamilyMember


def send_system_notification(user, notification_type, title, message, booking=None):
    """
    Creates a notification log entry for the user.
    """
    return Notification.objects.create(
        user=user,
        booking=booking,
        notification_type=notification_type,
        title=title,
        message=message
    )


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


@transaction.atomic
def process_advance_payment(booking: Booking, payment_mode='ONLINE', gateway_transaction_id=None):
    """
    Processes the 30% advance payment.
    Rule: After payment, status becomes 'AWAITING_PARTNER_CONFIRMATION'.
    Do NOT label confirmed yet until partner explicitly accepts.
    """
    if booking.booking_status != 'AWAITING_ADVANCE_PAYMENT':
        return None, f"Booking cannot accept advance payment in current status: {booking.booking_status}."

    advance_due = booking.advance_amount_due

    # Generate Payment Record
    payment = Payment.objects.create(
        payment_id=f"PAY-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}",
        booking=booking,
        payment_type='ADVANCE_30',
        amount=advance_due,
        payment_mode=payment_mode,
        status='SUCCESS',
        gateway_transaction_id=gateway_transaction_id or f"TXN-{uuid.uuid4().hex[:8].upper()}",
    )

    # Increment slot booked count
    if booking.slot:
        booking.slot.booked_count += 1
        booking.slot.save(update_fields=['booked_count'])

    # Update Booking status
    booking.advance_amount_paid = advance_due
    booking.booking_status = 'AWAITING_PARTNER_CONFIRMATION'
    booking.payment_status = 'ADVANCE_PAID'
    booking.save(update_fields=['advance_amount_paid', 'booking_status', 'payment_status', 'updated_at'])

    # Send Notification to User
    send_system_notification(
        user=booking.user,
        booking=booking,
        notification_type='PAYMENT_RESULT',
        title='Advance Payment (30%) Received',
        message=(
            f"30% advance payment of ₹{advance_due} received for booking {booking.booking_id}. "
            "Your booking request is now awaiting partner confirmation."
        )
    )

    # Notify Partner user if exists
    if booking.partner.user:
        send_system_notification(
            user=booking.partner.user,
            booking=booking,
            notification_type='BOOKING_REMINDER',
            title='New Incoming Booking Request',
            message=f"New booking request {booking.booking_id} from {booking.patient_name} awaiting your confirmation."
        )

    return payment, None


@transaction.atomic
def partner_accept_booking(booking: Booking, partner_user):
    """
    Diagnostic partner accepts booking request -> status becomes CONFIRMED.
    """
    if booking.booking_status != 'AWAITING_PARTNER_CONFIRMATION':
        return None, "Only bookings awaiting partner confirmation can be accepted."

    booking.booking_status = 'CONFIRMED'
    booking.confirmed_at = timezone.now()
    booking.save(update_fields=['booking_status', 'confirmed_at', 'updated_at'])

    # Notify User
    send_system_notification(
        user=booking.user,
        booking=booking,
        notification_type='PARTNER_CONFIRMATION',
        title='Booking Confirmed by Partner Centre',
        message=(
            f"Your booking {booking.booking_id} has been confirmed by {booking.partner.centre_name} "
            f"for {booking.booking_date} at {booking.slot_start_time.strftime('%H:%M')}."
        )
    )

    return booking, None


@transaction.atomic
def partner_reject_booking(booking: Booking, partner_user, reason: str):
    """
    Diagnostic partner rejects request -> status becomes REJECTED_BY_PARTNER,
    slot is freed, advance refund process is recorded.
    """
    if booking.booking_status != 'AWAITING_PARTNER_CONFIRMATION':
        return None, "Only bookings awaiting partner confirmation can be rejected."

    booking.booking_status = 'REJECTED_BY_PARTNER'
    booking.partner_rejection_reason = reason
    booking.cancelled_by = 'PARTNER'
    booking.cancellation_time = timezone.now()
    booking.payment_status = 'REFUNDED'
    booking.save(update_fields=[
        'booking_status', 'partner_rejection_reason', 'cancelled_by',
        'cancellation_time', 'payment_status', 'updated_at'
    ])

    # Free Slot capacity
    if booking.slot and booking.slot.booked_count > 0:
        booking.slot.booked_count -= 1
        booking.slot.save(update_fields=['booked_count'])

    # Create Refund Record
    Payment.objects.create(
        payment_id=f"REF-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}",
        booking=booking,
        payment_type='REFUND',
        amount=booking.advance_amount_paid,
        payment_mode='ONLINE',
        status='SUCCESS',
        gateway_transaction_id=f"REFUND-TXN-{uuid.uuid4().hex[:8].upper()}",
    )

    # Notify User
    send_system_notification(
        user=booking.user,
        booking=booking,
        notification_type='PARTNER_REJECTION',
        title='Booking Rejected by Partner Centre',
        message=(
            f"Your booking {booking.booking_id} could not be confirmed by {booking.partner.centre_name}. "
            f"Reason: {reason}. Full refund of advance payment ₹{booking.advance_amount_paid} has been processed."
        )
    )

    return booking, None


@transaction.atomic
def partner_update_service_status(booking: Booking, partner_user, new_status: str):
    """
    Updates status: Confirmed -> SAMPLE_COLLECTED -> TEST_COMPLETED.
    When TEST_COMPLETED is reached, prompts balance payment.
    """
    allowed_transitions = {
        'CONFIRMED': ['SAMPLE_COLLECTED'],
        'SAMPLE_COLLECTED': ['TEST_COMPLETED'],
    }

    current = booking.booking_status
    if new_status not in allowed_transitions.get(current, []):
        return None, f"Cannot transition status from {current} to {new_status}."

    booking.booking_status = new_status
    if new_status == 'TEST_COMPLETED':
        booking.completed_at = timezone.now()

    booking.save(update_fields=['booking_status', 'completed_at', 'updated_at'])

    if new_status == 'SAMPLE_COLLECTED':
        send_system_notification(
            user=booking.user,
            booking=booking,
            notification_type='BOOKING_REMINDER',
            title='Sample Collection / Visit Done',
            message=f"Sample collection for booking {booking.booking_id} has been completed. Processing tests."
        )
    elif new_status == 'TEST_COMPLETED':
        send_system_notification(
            user=booking.user,
            booking=booking,
            notification_type='TEST_COMPLETED',
            title='Test Completed - Balance Payment Due',
            message=(
                f"Diagnostics service completed for booking {booking.booking_id}. "
                f"Please pay the remaining 70% balance of ₹{booking.balance_amount_due} to view/download reports."
            )
        )

    return booking, None


@transaction.atomic
def process_balance_payment(booking: Booking, payment_mode='ONLINE', gateway_transaction_id=None, offline_reference=None, recorded_by_partner=False):
    """
    Collects 70% balance payment either via online payment gateway or approved partner offline collection.
    """
    if booking.payment_status == 'FULLY_PAID':
        return None, "Balance has already been fully paid for this booking."

    balance_due = booking.balance_amount_due - booking.balance_amount_paid
    if balance_due <= Decimal('0.00'):
        return None, "No outstanding balance for this booking."

    payment = Payment.objects.create(
        payment_id=f"PAY-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}",
        booking=booking,
        payment_type='BALANCE_70',
        amount=balance_due,
        payment_mode=payment_mode,
        status='SUCCESS',
        gateway_transaction_id=gateway_transaction_id or f"TXN-{uuid.uuid4().hex[:8].upper()}",
        offline_payment_reference=offline_reference or "",
        recorded_by_partner=recorded_by_partner,
    )

    booking.balance_amount_paid += balance_due
    booking.payment_status = 'FULLY_PAID'
    booking.save(update_fields=['balance_amount_paid', 'payment_status', 'updated_at'])

    send_system_notification(
        user=booking.user,
        booking=booking,
        notification_type='PAYMENT_RESULT',
        title='Balance Payment (70%) Received - Booking Fully Paid',
        message=f"Final 70% payment of ₹{balance_due} received. Booking {booking.booking_id} is now fully settled."
    )

    return payment, None

