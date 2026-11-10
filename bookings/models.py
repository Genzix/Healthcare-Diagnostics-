import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone


def generate_booking_id():
    return f"BK-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


def generate_receipt_number():
    return f"RCP-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


class Booking(models.Model):
    PATIENT_TYPE_CHOICES = (
        ('SELF', 'Self (Account Holder)'),
        ('FAMILY_MEMBER', 'Family Member'),
    )

    SERVICE_MODE_CHOICES = (
        ('HOME_COLLECTION', 'Home Sample Collection'),
        ('CENTRE_VISIT', 'Diagnostic Centre Visit'),
    )

    BOOKING_STATUS_CHOICES = (
        ('AWAITING_ADVANCE_PAYMENT', 'Awaiting Advance Payment'),
        ('AWAITING_PARTNER_CONFIRMATION', 'Awaiting Partner Confirmation'),
        ('CONFIRMED', 'Confirmed'),
        ('SAMPLE_COLLECTED', 'Sample Collected / Visit Done'),
        ('TEST_COMPLETED', 'Test Completed'),
        ('CANCELLED', 'Cancelled'),
        ('REJECTED_BY_PARTNER', 'Rejected by Partner'),
    )

    PAYMENT_STATUS_CHOICES = (
        ('PENDING', 'Payment Pending'),
        ('ADVANCE_PAID', 'Advance 30% Paid'),
        ('FULLY_PAID', 'Fully Paid (100%)'),
        ('REFUNDED', 'Refunded'),
        ('FAILED', 'Payment Failed'),
    )

    # Identifiers
    booking_id = models.CharField(
        max_length=30,
        unique=True,
        default=generate_booking_id,
        db_index=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bookings'
    )
    partner = models.ForeignKey(
        'partners.DiagnosticPartner',
        on_delete=models.PROTECT,
        related_name='bookings'
    )

    # Patient Selection
    patient_type = models.CharField(
        max_length=20,
        choices=PATIENT_TYPE_CHOICES,
        default='SELF'
    )
    family_member = models.ForeignKey(
        'accounts.FamilyMember',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bookings'
    )

    # Patient Snapshot (Frozen at time of booking)
    patient_name = models.CharField(max_length=150)
    patient_age = models.PositiveIntegerField(null=True, blank=True)
    patient_gender = models.CharField(max_length=20)
    patient_phone = models.CharField(max_length=17)

    # Service Mode & Details
    service_mode = models.CharField(max_length=20, choices=SERVICE_MODE_CHOICES)

    # Home Collection Address (if HOME_COLLECTION)
    collection_address = models.TextField(blank=True)
    collection_city = models.CharField(max_length=100, blank=True)
    collection_pincode = models.CharField(max_length=10, blank=True)
    collection_landmark = models.CharField(max_length=150, blank=True)

    # Diagnostic Centre Visit Snapshot (if CENTRE_VISIT)
    centre_name_snapshot = models.CharField(max_length=200, blank=True)
    centre_address_snapshot = models.TextField(blank=True)

    # Slot & Schedule
    booking_date = models.DateField(db_index=True)
    slot = models.ForeignKey(
        'partners.PartnerSlot',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bookings'
    )
    slot_start_time = models.TimeField()
    slot_end_time = models.TimeField()

    # Pricing & 30% / 70% Breakdown
    subtotal_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    home_collection_fee = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)  # final displayed booking amount

    advance_amount_due = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Exactly 30% of total booking amount required upfront"
    )
    advance_amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    balance_amount_due = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Remaining 70% of total booking amount due after test completion"
    )
    balance_amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    # Status Fields
    booking_status = models.CharField(
        max_length=40,
        choices=BOOKING_STATUS_CHOICES,
        default='AWAITING_ADVANCE_PAYMENT',
        db_index=True
    )
    payment_status = models.CharField(
        max_length=30,
        choices=PAYMENT_STATUS_CHOICES,
        default='PENDING',
        db_index=True
    )

    # Rejection & Cancellation
    partner_rejection_reason = models.TextField(blank=True)
    cancelled_by = models.CharField(max_length=20, blank=True)  # USER, PARTNER, ADMIN
    cancellation_reason = models.TextField(blank=True)
    cancellation_time = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Booking'
        verbose_name_plural = 'Bookings'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.booking_id} | {self.patient_name} | {self.partner.centre_name} - {self.booking_status}"


class BookingItem(models.Model):
    ITEM_TYPE_CHOICES = (
        ('TEST', 'Individual Diagnostic Test'),
        ('PACKAGE', 'Health Package'),
    )

    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name='items'
    )
    item_type = models.CharField(max_length=20, choices=ITEM_TYPE_CHOICES)
    test = models.ForeignKey(
        'diagnostics.DiagnosticTest',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='booking_items'
    )
    package = models.ForeignKey(
        'diagnostics.HealthPackage',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='booking_items'
    )
    item_name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = 'Booking Item'
        verbose_name_plural = 'Booking Items'

    def __str__(self):
        return f"{self.booking.booking_id} - {self.item_name} (₹{self.price})"


class Payment(models.Model):
    PAYMENT_TYPE_CHOICES = (
        ('ADVANCE_30', '30% Advance Payment'),
        ('BALANCE_70', '70% Balance Payment'),
        ('REFUND', 'Refund Payment'),
    )

    PAYMENT_MODE_CHOICES = (
        ('ONLINE', 'Online Gateway'),
        ('CASH', 'Cash'),
        ('UPI', 'UPI'),
        ('CARD', 'Debit/Credit Card'),
    )

    STATUS_CHOICES = (
        ('INITIATED', 'Initiated'),
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
        ('REFUNDED', 'Refunded'),
    )

    payment_id = models.CharField(max_length=50, unique=True, db_index=True)
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name='payments'
    )
    payment_type = models.CharField(max_length=20, choices=PAYMENT_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_mode = models.CharField(max_length=20, choices=PAYMENT_MODE_CHOICES, default='ONLINE')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='INITIATED')

    gateway_transaction_id = models.CharField(max_length=100, blank=True)
    receipt_number = models.CharField(max_length=50, unique=True, default=generate_receipt_number)
    offline_payment_reference = models.CharField(max_length=100, blank=True)
    recorded_by_partner = models.BooleanField(
        default=False,
        help_text="Designates if this was recorded by the diagnostic partner as approved offline collection"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Payment'
        verbose_name_plural = 'Payments'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.payment_id} | {self.payment_type} | ₹{self.amount} - {self.status}"


class Notification(models.Model):
    NOTIFICATION_TYPE_CHOICES = (
        ('OTP', 'OTP Verification'),
        ('PAYMENT_RESULT', 'Payment Result'),
        ('PARTNER_CONFIRMATION', 'Partner Confirmation'),
        ('PARTNER_REJECTION', 'Partner Rejection'),
        ('BOOKING_REMINDER', 'Booking Reminder'),
        ('TEST_COMPLETED', 'Test Completed'),
        ('BALANCE_PAYMENT_REMINDER', 'Balance Payment Reminder'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications'
    )
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications'
    )
    notification_type = models.CharField(max_length=40, choices=NOTIFICATION_TYPE_CHOICES)
    title = models.CharField(max_length=200)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Notification'
        verbose_name_plural = 'Notifications'
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.notification_type}] {self.user.phone_number}: {self.title}"

