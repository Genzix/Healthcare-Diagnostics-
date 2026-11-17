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
    """
    Partner time slot.

    Validation rules (enforced on create and update):
      * end_time must be after start_time
      * max_capacity >= 1 and never below the seats already booked
      * date cannot be in the past (one day of tolerance for client time zones)
      * a slot that already has bookings cannot be moved (date / time / mode)
      * an active slot cannot overlap another active slot of the same partner
        for a conflicting service mode ('ALL' conflicts with every mode)

    The owning partner is never taken from the payload: views pass it through
    ``context['partner']`` (or it comes from the instance on update).
    """
    is_available = serializers.BooleanField(read_only=True)
    available_seats = serializers.SerializerMethodField()

    MAX_CAPACITY_LIMIT = 100

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
        read_only_fields = ['id', 'partner', 'booked_count', 'is_available', 'available_seats']

    def get_available_seats(self, obj):
        return max(0, obj.max_capacity - obj.booked_count)

    def _resolved(self, attrs, field, default=None):
        if field in attrs:
            return attrs[field]
        if self.instance is not None:
            return getattr(self.instance, field)
        return default

    def validate(self, attrs):
        from datetime import timedelta
        from django.utils import timezone

        inst = self.instance
        date = self._resolved(attrs, 'date')
        start = self._resolved(attrs, 'start_time')
        end = self._resolved(attrs, 'end_time')
        mode = self._resolved(attrs, 'service_mode', 'ALL')
        capacity = self._resolved(attrs, 'max_capacity', 5)
        is_active = self._resolved(attrs, 'is_active', True)

        if start is not None and end is not None and end <= start:
            raise serializers.ValidationError({'end_time': 'End time must be after start time.'})

        if capacity is not None:
            if capacity < 1:
                raise serializers.ValidationError({'max_capacity': 'Capacity must be at least 1.'})
            if capacity > self.MAX_CAPACITY_LIMIT:
                raise serializers.ValidationError({'max_capacity': f'Capacity cannot exceed {self.MAX_CAPACITY_LIMIT}.'})
            if inst is not None and capacity < inst.booked_count:
                raise serializers.ValidationError({
                    'max_capacity': f'Capacity cannot be lower than the {inst.booked_count} seat(s) already booked.'
                })

        schedule_changed = inst is None or any(
            f in attrs and attrs[f] != getattr(inst, f)
            for f in ('date', 'start_time', 'end_time', 'service_mode')
        )

        if inst is not None and inst.booked_count > 0 and schedule_changed:
            raise serializers.ValidationError(
                'This slot already has bookings, so its date, time or mode cannot be changed. '
                'Deactivate it and create a new slot instead.'
            )

        if schedule_changed and date is not None and date < timezone.localdate() - timedelta(days=1):
            raise serializers.ValidationError({'date': 'Slots cannot be scheduled in the past.'})

        reactivated = inst is not None and not inst.is_active and is_active
        if is_active and (schedule_changed or reactivated):
            partner = self.context.get('partner') or (inst.partner if inst else None)
            if partner is not None and date and start and end:
                clash = PartnerSlot.objects.filter(
                    partner=partner,
                    date=date,
                    is_active=True,
                    start_time__lt=end,
                    end_time__gt=start,
                )
                if inst is not None:
                    clash = clash.exclude(pk=inst.pk)
                if mode != 'ALL':
                    clash = clash.filter(service_mode__in=[mode, 'ALL'])
                hit = clash.order_by('start_time').first()
                if hit:
                    raise serializers.ValidationError(
                        f"Overlaps an existing {hit.get_service_mode_display().lower()} slot "
                        f"({hit.start_time.strftime('%H:%M')}-{hit.end_time.strftime('%H:%M')})."
                    )
        return attrs


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
            'user',
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
        extra_kwargs = {
            'user': {'required': False, 'allow_null': True},
        }


class DiagnosticPartnerDetailSerializer(serializers.ModelSerializer):
    service_areas = PartnerServiceAreaSerializer(many=True, read_only=True)
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_phone = serializers.CharField(source='user.phone_number', read_only=True)

    class Meta:
        model = DiagnosticPartner
        fields = [
            'id',
            'user',
            'user_email',
            'user_phone',
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

