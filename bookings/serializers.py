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
            'items',
            'payments',
            'created_at',
            'updated_at',
            'confirmed_at',
            'completed_at',
        ]


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
    payment_mode = serializers.ChoiceField(choices=['ONLINE', 'UPI', 'CARD'], default='ONLINE')
    gateway_transaction_id = serializers.CharField(required=False, allow_blank=True)


class BalancePaymentRequestSerializer(serializers.Serializer):
    payment_mode = serializers.ChoiceField(choices=['ONLINE', 'UPI', 'CARD'], default='ONLINE')
    gateway_transaction_id = serializers.CharField(required=False, allow_blank=True)


class PartnerAcceptBookingSerializer(serializers.Serializer):
    pass


class PartnerRejectBookingSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(required=True, min_length=5)


class PartnerStatusUpdateSerializer(serializers.Serializer):
    new_status = serializers.ChoiceField(choices=['SAMPLE_COLLECTED', 'TEST_COMPLETED'])


class PartnerOfflineBalancePaymentSerializer(serializers.Serializer):
    payment_mode = serializers.ChoiceField(choices=['CASH', 'UPI', 'CARD'], default='CASH')
    offline_reference = serializers.CharField(required=True, help_text="Cash receipt number or UPI txn reference")


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

