from rest_framework import serializers
from .models import (
    DiagnosticPartner,
    PartnerServiceArea,
    PartnerSlot,
    PartnerTestPricing,
    PartnerPackagePricing,
)


class PartnerServiceAreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerServiceArea
        fields = ['id', 'city', 'pincode', 'is_active', 'created_at']


class PartnerSlotSerializer(serializers.ModelSerializer):
    is_available = serializers.BooleanField(read_only=True)
    available_seats = serializers.SerializerMethodField()

    class Meta:
        model = PartnerSlot
        fields = [
            'id',
            'partner',
            'date',
            'start_time',
            'end_time',
            'service_mode',
            'max_capacity',
            'booked_count',
            'available_seats',
            'is_available',
            'is_active',
        ]
        read_only_fields = ['id', 'booked_count', 'is_available', 'available_seats']

    def get_available_seats(self, obj):
        return max(0, obj.max_capacity - obj.booked_count)


class PartnerTestPricingSerializer(serializers.ModelSerializer):
    test_name = serializers.CharField(source='test.name', read_only=True)
    test_code = serializers.CharField(source='test.code', read_only=True)

    class Meta:
        model = PartnerTestPricing
        fields = ['id', 'partner', 'test', 'test_name', 'test_code', 'price', 'is_available']


class PartnerPackagePricingSerializer(serializers.ModelSerializer):
    package_name = serializers.CharField(source='package.name', read_only=True)
    package_code = serializers.CharField(source='package.code', read_only=True)

    class Meta:
        model = PartnerPackagePricing
        fields = ['id', 'partner', 'package', 'package_name', 'package_code', 'price', 'is_available']


class DiagnosticPartnerSerializer(serializers.ModelSerializer):
    service_areas_count = serializers.IntegerField(source='service_areas.count', read_only=True)

    class Meta:
        model = DiagnosticPartner
        fields = [
            'id',
            'centre_name',
            'license_number',
            'contact_person',
            'contact_phone',
            'contact_email',
            'address_line',
            'city',
            'state',
            'pincode',
            'latitude',
            'longitude',
            'offers_home_collection',
            'home_collection_fee',
            'status',
            'service_areas_count',
            'created_at',
        ]


class DiagnosticPartnerDetailSerializer(serializers.ModelSerializer):
    service_areas = PartnerServiceAreaSerializer(many=True, read_only=True)

    class Meta:
        model = DiagnosticPartner
        fields = [
            'id',
            'centre_name',
            'license_number',
            'contact_person',
            'contact_phone',
            'contact_email',
            'address_line',
            'city',
            'state',
            'pincode',
            'latitude',
            'longitude',
            'offers_home_collection',
            'home_collection_fee',
            'status',
            'service_areas',
            'created_at',
        ]


class PartnerProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticPartner
        fields = [
            'centre_name',
            'contact_person',
            'contact_phone',
            'contact_email',
            'address_line',
            'city',
            'state',
            'pincode',
            'offers_home_collection',
            'home_collection_fee',
        ]


class CheckServiceabilitySerializer(serializers.Serializer):
    partner_id = serializers.IntegerField(required=True)
    pincode = serializers.CharField(max_length=10, required=True)

