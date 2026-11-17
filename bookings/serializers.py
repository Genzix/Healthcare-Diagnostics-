from rest_framework import serializers

from . import workflow as wf
from .models import Booking, BookingItem, BookingStatusLog, Payment, Notification


def _audience(serializer):
    """PATIENT (default, least privilege) | PARTNER | ADMIN — set by the view via context."""
    return (serializer.context or {}).get('audience', wf.PATIENT)


class BookingStatusLogSerializer(serializers.ModelSerializer):
    from_label = serializers.SerializerMethodField()
    to_label = serializers.SerializerMethodField()
    status = serializers.CharField(source='to_status', read_only=True)

    class Meta:
        model = BookingStatusLog
        fields = ['id', 'status', 'from_status', 'to_status', 'from_label', 'to_label', 'actor_role', 'notes', 'created_at']

    def get_from_label(self, obj):
        return wf.LABELS.get(obj.from_status, obj.from_status)

    def get_to_label(self, obj):
        return wf.LABELS.get(obj.to_status, obj.to_status)


class BookingItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingItem
        fields = ['id', 'item_type', 'item_name', 'price', 'test', 'package']


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            'id',
            'payment_id',
            'payment_type',
            'amount',
            'payment_mode',
            'status',
            'gateway_transaction_id',
            'receipt_number',
            'offline_payment_reference',
            'recorded_by_partner',
            'created_at',
        ]


class BookingDetailSerializer(serializers.ModelSerializer):
    items = BookingItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    partner_name = serializers.CharField(source='partner.centre_name', read_only=True)
    partner_address = serializers.CharField(source='partner.address_line', read_only=True)
    partner_phone = serializers.CharField(source='partner.contact_phone', read_only=True)
    status_label = serializers.SerializerMethodField()
    lab_progress = serializers.SerializerMethodField()
    status_history = serializers.SerializerMethodField()
    next_actions = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    report_download_url = serializers.SerializerMethodField()
    has_report = serializers.SerializerMethodField()
    report_notes = serializers.SerializerMethodField()
    report_verified_by_name = serializers.SerializerMethodField()

    # Never expose the raw /media/ path: reports are fetched through the
    # authenticated download endpoint, which enforces who may see them.
    REPORT_PRIVATE_FIELDS = ('report_file_size', 'report_file_sha256', 'report_verified_by_registration', 'report_uploaded_at', 'report_name')

    class Meta:
        model = Booking
        fields = [
            'id', 'booking_id', 'user', 'partner', 'partner_name', 'partner_address', 'partner_phone',
            'patient_type', 'family_member', 'patient_name', 'patient_age', 'patient_gender', 'patient_phone',
            'service_mode', 'collection_address', 'collection_city', 'collection_pincode', 'collection_landmark',
            'centre_name_snapshot', 'centre_address_snapshot',
            'booking_date', 'slot_start_time', 'slot_end_time',
            'subtotal_amount', 'home_collection_fee', 'discount_amount', 'total_amount',
            'advance_amount_due', 'advance_amount_paid', 'balance_amount_due', 'balance_amount_paid',
            'booking_status', 'status_label', 'payment_status', 'lab_progress', 'next_actions', 'can_cancel',
            'partner_rejection_reason', 'cancelled_by', 'cancellation_reason', 'cancellation_time',
            'has_report', 'report_download_url', 'report_name', 'report_notes', 'report_uploaded_at',
            'report_file_size', 'report_file_sha256',
            'report_verified_by_name', 'report_verified_by_registration', 'report_verified_at',
            'report_released_at', 'patient_notified_at',
            'report_first_downloaded_at', 'report_last_downloaded_at', 'report_download_count',
            'status_history', 'items', 'payments',
            'created_at', 'updated_at', 'confirmed_at', 'sample_collected_at', 'sample_received_at',
            'completed_at', 'status_changed_at',
        ]

    def to_representation(self, obj):
        data = super().to_representation(obj)
        if _audience(self) == wf.PATIENT and not obj.is_report_released:
            for f in self.REPORT_PRIVATE_FIELDS:
                data[f] = None
        return data

    def _visible_to_patient(self, obj):
        return _audience(self) != wf.PATIENT or obj.is_report_released

    def get_status_label(self, obj):
        return wf.LABELS.get(obj.booking_status, obj.booking_status)

    def get_lab_progress(self, obj):
        return wf.progress(obj.booking_status)

    def get_status_history(self, obj):
        logs = obj.status_logs.all()
        if _audience(self) == wf.PATIENT:
            # Internal QA notes (e.g. a verifier's send-back reason) stay inside the lab.
            return [
                {**row, 'notes': '' if row['to_status'] in (wf.REPORT_GENERATED, wf.REPORT_UPLOADED, wf.REPORT_VERIFIED) else row['notes']}
                for row in BookingStatusLogSerializer(logs, many=True).data
            ]
        return BookingStatusLogSerializer(logs, many=True).data

    def get_next_actions(self, obj):
        aud = _audience(self)
        return wf.next_steps(obj.booking_status, wf.PARTNER) if aud in (wf.PARTNER, wf.ADMIN) else []

    def get_can_cancel(self, obj):
        return obj.booking_status in wf.CANCELLABLE

    def get_has_report(self, obj):
        return bool(obj.report_file) and self._visible_to_patient(obj)

    def get_report_download_url(self, obj):
        if self.get_has_report(obj):
            return f"/api/bookings/{obj.booking_id}/download-report/"
        return None

    def get_report_notes(self, obj):
        return obj.report_notes if self._visible_to_patient(obj) else ''

    def get_report_verified_by_name(self, obj):
        return obj.report_verified_by_name if self._visible_to_patient(obj) else ''


class BookingListSerializer(serializers.ModelSerializer):
    partner_name = serializers.CharField(source='partner.centre_name', read_only=True)
    items_count = serializers.IntegerField(source='items.count', read_only=True)

    class Meta:
        model = Booking
        fields = [
            'id',
            'booking_id',
            'patient_name',
            'partner_name',
            'service_mode',
            'booking_date',
            'slot_start_time',
            'slot_end_time',
            'total_amount',
            'advance_amount_paid',
            'balance_amount_due',
            'booking_status',
            'status_label',
            'payment_status',
            'lab_progress',
            'has_report',
            'items_count',
            'created_at',
            'updated_at',
        ]

    status_label = serializers.SerializerMethodField()
    lab_progress = serializers.SerializerMethodField()
    has_report = serializers.SerializerMethodField()

    def get_status_label(self, obj):
        return wf.LABELS.get(obj.booking_status, obj.booking_status)

    def get_lab_progress(self, obj):
        return wf.progress(obj.booking_status)

    def get_has_report(self, obj):
        return bool(obj.report_file) and obj.is_report_released


class PriceEstimateRequestSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField(required=True)
    service_mode = serializers.ChoiceField(choices=['HOME_COLLECTION', 'CENTRE_VISIT'], required=True)
    test_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    package_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    pincode = serializers.CharField(max_length=10, required=False, allow_blank=True)


class BookingCreateSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField(required=True)
    service_mode = serializers.ChoiceField(choices=['HOME_COLLECTION', 'CENTRE_VISIT'], required=True)
    slot_id = serializers.IntegerField(required=True)

    patient_type = serializers.ChoiceField(choices=['SELF', 'FAMILY_MEMBER'], default='SELF')
    family_member_id = serializers.IntegerField(required=False, allow_null=True)

    test_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    package_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)

    # Required if HOME_COLLECTION
    collection_address = serializers.CharField(required=False, allow_blank=True)
    collection_city = serializers.CharField(required=False, allow_blank=True)
    collection_pincode = serializers.CharField(required=False, allow_blank=True)
    collection_landmark = serializers.CharField(required=False, allow_blank=True)


class AdvancePaymentRequestSerializer(serializers.Serializer):
    payment_mode = serializers.CharField(required=False, default='ONLINE')
    gateway_transaction_id = serializers.CharField(required=False, allow_blank=True)

    def validate_payment_mode(self, value):
        if not value:
            return 'ONLINE'
        mode = str(value).strip().upper()
        mode_mapping = {
            'CREDIT_CARD': 'CARD',
            'DEBIT_CARD': 'CARD',
            'NETBANKING': 'NET_BANKING',
        }
        mode = mode_mapping.get(mode, mode)
        allowed = ['ONLINE', 'UPI', 'CARD', 'WALLET', 'NET_BANKING', 'CASH']
        if mode not in allowed:
            raise serializers.ValidationError(f'"{value}" is not a valid choice. Choose from: {", ".join(allowed)}.')
        return mode


class BalancePaymentRequestSerializer(serializers.Serializer):
    payment_mode = serializers.CharField(required=False, default='ONLINE')
    gateway_transaction_id = serializers.CharField(required=False, allow_blank=True)

    def validate_payment_mode(self, value):
        if not value:
            return 'ONLINE'
        mode = str(value).strip().upper()
        mode_mapping = {
            'CREDIT_CARD': 'CARD',
            'DEBIT_CARD': 'CARD',
            'NETBANKING': 'NET_BANKING',
        }
        mode = mode_mapping.get(mode, mode)
        allowed = ['ONLINE', 'UPI', 'CARD', 'WALLET', 'NET_BANKING', 'CASH']
        if mode not in allowed:
            raise serializers.ValidationError(f'"{value}" is not a valid choice. Choose from: {", ".join(allowed)}.')
        return mode


class PartnerAcceptBookingSerializer(serializers.Serializer):
    pass


class PartnerRejectBookingSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(required=True, min_length=5)


class PartnerStatusUpdateSerializer(serializers.Serializer):
    """
    POST /api/partner/bookings/<id>/update-status/
      new_status | status          target stage (aliases accepted, e.g. "received at lab")
      notes                        shown in the timeline (required when sending a report back)
      expected_status              optional optimistic-concurrency guard → 409 if stale
      report_file (multipart)      required for REPORT_UPLOADED
      report_notes, report_name    optional, with the upload
      verified_by_name             required for REPORT_VERIFIED
      verified_by_registration     optional registration number of the verifier
    """
    new_status = serializers.CharField(required=False)
    status = serializers.CharField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    expected_status = serializers.CharField(required=False, allow_blank=True)
    report_file = serializers.FileField(required=False, allow_null=True)
    report_notes = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    report_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    verified_by_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    verified_by_registration = serializers.CharField(required=False, allow_blank=True, max_length=60)

    def validate(self, attrs):
        raw = attrs.get('new_status') or attrs.get('status')
        if not raw:
            raise serializers.ValidationError({"new_status": "Target status is required (e.g. 'SAMPLE_RECEIVED')."})
        target = wf.normalize(raw)
        known = {wf.TEST_COMPLETED, *wf.STEPS.keys()}
        if target not in known:
            raise serializers.ValidationError({
                "new_status": f"'{raw}' is not a lab workflow status. Choose from: {', '.join(wf.PIPELINE[1:-1])}."
            })
        attrs['new_status'] = target
        if attrs.get('expected_status'):
            attrs['expected_status'] = wf.normalize(attrs['expected_status'])
        return attrs


class PartnerReportUploadSerializer(serializers.Serializer):
    report_file = serializers.FileField(required=False, allow_null=True)
    report_notes = serializers.CharField(required=False, allow_blank=True)
    report_name = serializers.CharField(required=False, allow_blank=True)


class PartnerOfflineBalancePaymentSerializer(serializers.Serializer):
    payment_mode = serializers.CharField(required=False, default='CASH')
    offline_reference = serializers.CharField(required=True, help_text="Cash receipt number or UPI txn reference")

    def validate_payment_mode(self, value):
        if not value:
            return 'CASH'
        mode = str(value).strip().upper()
        mode_mapping = {
            'CREDIT_CARD': 'CARD',
            'DEBIT_CARD': 'CARD',
            'NETBANKING': 'NET_BANKING',
        }
        mode = mode_mapping.get(mode, mode)
        allowed = ['CASH', 'UPI', 'CARD', 'WALLET', 'ONLINE', 'NET_BANKING']
        if mode not in allowed:
            raise serializers.ValidationError(f'"{value}" is not a valid choice. Choose from: {", ".join(allowed)}.')
        return mode


class BookingCancelSerializer(serializers.Serializer):
    cancellation_reason = serializers.CharField(required=True, min_length=5)


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            'id',
            'notification_type',
            'title',
            'message',
            'booking',
            'booking_ref',
            'is_read',
            'push_status',
            'created_at',
        ]

    booking_ref = serializers.CharField(source='booking.booking_id', read_only=True, default=None)

