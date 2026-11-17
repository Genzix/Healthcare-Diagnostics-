from rest_framework import serializers
from .models import Booking, BookingItem, Payment, Notification


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
    report_download_url = serializers.SerializerMethodField()
    has_report = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = [
            'id',
            'booking_id',
            'user',
            'partner',
            'partner_name',
            'partner_address',
            'partner_phone',
            'patient_type',
            'family_member',
            'patient_name',
            'patient_age',
            'patient_gender',
            'patient_phone',
            'service_mode',
            'collection_address',
            'collection_city',
            'collection_pincode',
            'collection_landmark',
            'centre_name_snapshot',
            'centre_address_snapshot',
            'booking_date',
            'slot_start_time',
            'slot_end_time',
            'subtotal_amount',
            'home_collection_fee',
            'discount_amount',
            'total_amount',
            'advance_amount_due',
            'advance_amount_paid',
            'balance_amount_due',
            'balance_amount_paid',
            'booking_status',
            'payment_status',
            'partner_rejection_reason',
            'cancelled_by',
            'cancellation_reason',
            'cancellation_time',
            'report_file',
            'report_uploaded_at',
            'report_notes',
            'report_name',
            'report_download_url',
            'has_report',
            'items',
            'payments',
            'created_at',
            'updated_at',
            'confirmed_at',
            'completed_at',
        ]

    def get_report_download_url(self, obj):
        if obj.booking_status in ['REPORT_READY', 'TEST_COMPLETED'] or getattr(obj, 'report_file', None):
            return f"/api/bookings/{obj.booking_id}/download-report/"
        return None

    def get_has_report(self, obj):
        return bool(getattr(obj, 'report_file', None) or obj.booking_status in ['REPORT_READY', 'TEST_COMPLETED'])


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
            'payment_status',
            'items_count',
            'created_at',
        ]


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
    new_status = serializers.CharField(required=False)
    status = serializers.CharField(required=False)
    report_file = serializers.FileField(required=False, allow_null=True)
    report_notes = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    report_name = serializers.CharField(required=False, allow_blank=True)

    def to_internal_value(self, data):
        data_copy = data.copy() if hasattr(data, 'copy') else dict(data)
        if not data_copy.get('new_status') and data_copy.get('status'):
            data_copy['new_status'] = data_copy.get('status')
        if not data_copy.get('report_notes') and data_copy.get('notes'):
            data_copy['report_notes'] = data_copy.get('notes')
        return super().to_internal_value(data_copy)

    def validate(self, attrs):
        if not attrs.get('new_status'):
            raise serializers.ValidationError({"new_status": "Service status is required (e.g. 'new_status' or 'status')."})
        return attrs

    def validate_new_status(self, value):
        if not value:
            raise serializers.ValidationError("Status is required.")
        status_str = str(value).strip().upper().replace(' ', '_').replace('-', '_')
        status_mapping = {
            'SAMPLE_COLLECTED': 'SAMPLE_COLLECTED',
            'SAMPLE_COLLECTION': 'SAMPLE_COLLECTED',
            'SAMPLE_DONE': 'SAMPLE_COLLECTED',
            'VISIT_DONE': 'SAMPLE_COLLECTED',

            'LAB_ANALYSIS': 'LAB_ANALYSIS',
            'IN_ANALYSIS': 'LAB_ANALYSIS',
            'ANALYSIS': 'LAB_ANALYSIS',
            'PROCESSING': 'LAB_ANALYSIS',
            'LAB_PROCESSING': 'LAB_ANALYSIS',
            'UNDER_TESTING': 'LAB_ANALYSIS',
            'UNDER_ANALYSIS': 'LAB_ANALYSIS',
            'SAMPLE_RECEIVED_AT_LAB': 'LAB_ANALYSIS',

            'REPORT_READY': 'REPORT_READY',
            'REPORT_GENERATED': 'REPORT_READY',
            'REPORT_UPLOADED': 'REPORT_READY',
            'READY': 'REPORT_READY',

            'TEST_COMPLETED': 'TEST_COMPLETED',
            'COMPLETED': 'TEST_COMPLETED',
            'DONE': 'TEST_COMPLETED',
        }
        normalized = status_mapping.get(status_str, status_str)
        valid_statuses = ['SAMPLE_COLLECTED', 'LAB_ANALYSIS', 'REPORT_READY', 'TEST_COMPLETED']
        if normalized not in valid_statuses:
            raise serializers.ValidationError(
                f"'{value}' is not a valid service status. Choose from: {', '.join(valid_statuses)}"
            )
        return normalized


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
            'is_read',
            'created_at',
        ]

