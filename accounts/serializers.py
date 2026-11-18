import re
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import PhoneOTP, FamilyMember, phone_validator
from .services import normalize_phone_number

User = get_user_model()


class SendOTPRequestSerializer(serializers.Serializer):
    """
    Serializer for initiating registration OTP request.
    """
    phone_number = serializers.CharField(
        max_length=17,
        help_text="Phone number with optional '+' and country code (e.g., +919876543210 or 9876543210)."
    )
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_null=True, allow_blank=True)
    role = serializers.ChoiceField(
        choices=User.ROLE_CHOICES,
        default='PATIENT',
        required=False
    )

    def validate_phone_number(self, value):
        cleaned = normalize_phone_number(value)
        if not re.match(r'^\+?[0-9]{7,15}$', cleaned):
            raise serializers.ValidationError(
                "Invalid phone number format. Provide 7-15 digits with optional '+' country code."
            )
        
        # Check if already registered and verified
        if User.objects.filter(phone_number=cleaned, is_phone_verified=True).exists():
            raise serializers.ValidationError(
                "An account with this phone number already exists. Please log in."
            )
        return cleaned


class VerifyOTPRequestSerializer(serializers.Serializer):
    """
    Serializer for verifying OTP and creating the user account.
    """
    phone_number = serializers.CharField(max_length=17)
    otp = serializers.CharField(min_length=4, max_length=6, trim_whitespace=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_null=True, allow_blank=True)
    password = serializers.CharField(max_length=128, required=False, write_only=True)
    role = serializers.ChoiceField(
        choices=User.ROLE_CHOICES,
        default='PATIENT',
        required=False
    )

    def validate_phone_number(self, value):
        cleaned = normalize_phone_number(value)
        if not re.match(r'^\+?[0-9]{7,15}$', cleaned):
            raise serializers.ValidationError("Invalid phone number format.")
        return cleaned

    def validate_otp(self, value):
        code = str(value).strip()
        if not code.isdigit():
            raise serializers.ValidationError("OTP code must consist of digits only.")
        return code


class ResendOTPRequestSerializer(serializers.Serializer):
    """
    Serializer for resending registration OTP.
    """
    phone_number = serializers.CharField(max_length=17)

    def validate_phone_number(self, value):
        cleaned = normalize_phone_number(value)
        if not re.match(r'^\+?[0-9]{7,15}$', cleaned):
            raise serializers.ValidationError("Invalid phone number format.")
        return cleaned


class UserSerializer(serializers.ModelSerializer):
    """
    Serializer for displaying user account information and basic profile.
    Supports all elements of the 1MinDoc Profile UI:
    - Avatar initial & Demographics
    - Personal Information (Mobile, DOB formatted, Member since)
    - Saved Address
    - Loyalty points calculation & reward banner
    """
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    name = serializers.CharField(source='get_full_name', read_only=True)
    mobile = serializers.CharField(source='phone_number', read_only=True)
    registered = serializers.DateTimeField(source='date_joined', read_only=True)
    age = serializers.IntegerField(source='get_age', read_only=True)

    avatar_initial = serializers.SerializerMethodField()
    gender_display = serializers.SerializerMethodField()
    date_of_birth_display = serializers.SerializerMethodField()
    member_since = serializers.SerializerMethodField()
    saved_address = serializers.SerializerMethodField()
    loyalty_points = serializers.SerializerMethodField()
    loyalty_info = serializers.SerializerMethodField()

    def get_avatar_initial(self, obj):
        name = obj.get_full_name() or obj.first_name or obj.email or obj.phone_number or 'H'
        return (name[0] if name else 'H').upper()

    def get_gender_display(self, obj):
        return obj.gender.capitalize() if obj.gender else 'Not Specified'

    def get_date_of_birth_display(self, obj):
        if not obj.date_of_birth:
            return None
        dob = obj.date_of_birth
        if isinstance(dob, str):
            from datetime import date
            dob = date.fromisoformat(dob)
        return f"{dob.day} {dob.strftime('%b')} {dob.year}"

    def get_member_since(self, obj):
        if not obj.date_joined:
            return None
        dj = obj.date_joined
        return f"{dj.day} {dj.strftime('%b')} {dj.year}"

    def get_saved_address(self, obj):
        parts = [p.strip() for p in [obj.address, obj.city, obj.pincode] if p and p.strip()]
        return ", ".join(parts) if parts else "No saved address"

    def get_loyalty_points(self, obj):
        try:
            from bookings.models import Booking
            completed = Booking.objects.filter(user=obj, booking_status='COMPLETED')
            total_spent = sum([float(b.total_amount) for b in completed])
            return int(total_spent // 10)
        except Exception:
            return 0

    def get_loyalty_info(self, obj):
        pts = self.get_loyalty_points(obj)
        return {
            "points": pts,
            "display": f"{pts} pts",
            "rate": "Earn 1 point per ₹10 spent",
            "banner": "Book tests to start earning reward points!" if pts == 0 else f"You have {pts} reward points available for instant booking discounts!"
        }

    class Meta:
        model = User
        fields = [
            'id',
            'phone_number',
            'mobile',
            'first_name',
            'last_name',
            'full_name',
            'name',
            'avatar_initial',
            'email',
            'date_of_birth',
            'date_of_birth_display',
            'age',
            'gender',
            'gender_display',
            'city',
            'address',
            'pincode',
            'saved_address',
            'role',
            'is_staff',
            'is_superuser',
            'is_phone_verified',
            'date_joined',
            'registered',
            'member_since',
            'loyalty_points',
            'loyalty_info',
        ]
        read_only_fields = [
            'id', 'phone_number', 'role', 'is_staff', 'is_superuser',
            'is_phone_verified', 'date_joined', 'registered', 'member_since',
            'loyalty_points', 'loyalty_info'
        ]


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for editing user profile (Full name, DOB, gender, email, city, address).
    """
    class Meta:
        model = User
        fields = [
            'first_name',
            'last_name',
            'date_of_birth',
            'gender',
            'email',
            'city',
            'address',
            'pincode',
        ]


class FamilyMemberSerializer(serializers.ModelSerializer):
    """
    Serializer for adding, editing, and viewing family member profiles.

    Phone rules (see accounts/family_phone.py):
    - Required for adults; optional for children / members under 18.
    - Any new or changed number must carry a ``phone_verification_token``
      obtained from POST /api/family-members/phone/verify-otp/.
    - Accepts ``phone_number`` (preferred) or legacy ``contact_number`` on input.
    """
    age = serializers.IntegerField(source='get_age', read_only=True)
    mobile = serializers.CharField(source='contact_number', read_only=True)
    phone_number = serializers.CharField(
        source='contact_number', required=False, allow_blank=True, allow_null=True, max_length=20,
    )
    contact_number = serializers.CharField(read_only=True)
    phone_verification_token = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    phone_required = serializers.SerializerMethodField()
    primary_account = serializers.CharField(source='user.phone_number', read_only=True)
    account_status = serializers.SerializerMethodField()
    added = serializers.DateTimeField(source='created_at', read_only=True)

    def get_account_status(self, obj):
        return "Active" if getattr(obj.user, 'is_active', True) else "Inactive"

    def get_phone_required(self, obj):
        from .family_phone import phone_required_for
        return phone_required_for(obj.relationship, obj.date_of_birth)

    def to_internal_value(self, data):
        # `phone_number` wins over the legacy `contact_number` alias when both are sent.
        if 'phone_number' not in data and 'contact_number' in data:
            data = data.copy()
            data['phone_number'] = data.get('contact_number')
        return super().to_internal_value(data)

    def validate(self, attrs):
        from .family_phone import (
            FamilyPhoneError, canonical_phone, check_verification_token,
            phone_required_for, same_phone, validate_phone_for_owner,
        )
        request = self.context.get('request')
        owner = getattr(request, 'user', None)
        if owner is None or not owner.is_authenticated:
            raise serializers.ValidationError('Authentication required.')

        inst = self.instance
        token = attrs.pop('phone_verification_token', None)
        relationship = attrs.get('relationship', getattr(inst, 'relationship', None))
        dob = attrs.get('date_of_birth', getattr(inst, 'date_of_birth', None))
        required = phone_required_for(relationship, dob)

        phone_sent = 'contact_number' in attrs
        current = getattr(inst, 'contact_number', '') or ''
        raw = attrs.get('contact_number') if phone_sent else current
        raw = (raw or '').strip()

        if not raw:
            if required:
                raise serializers.ValidationError({'phone_number': [
                    'Mobile number is required. Only children and members under 18 can be added without one.'
                ]}, code='phone_required')
            attrs.update(contact_number='', is_phone_verified=False, phone_verified_at=None)
            return attrs

        try:
            phone = validate_phone_for_owner(owner, raw, getattr(inst, 'pk', None))
        except FamilyPhoneError as e:
            raise serializers.ValidationError({'phone_number': [e.message]}, code=e.code)

        unchanged = inst is not None and same_phone(current, phone)
        already_verified = unchanged and inst.is_phone_verified
        # A legacy, unverified number on a phone-optional member may be kept as is.
        keep_unverified = unchanged and not inst.is_phone_verified and not required and not token

        if already_verified or keep_unverified:
            attrs['contact_number'] = canonical_phone(current)
            return attrs

        try:
            self._otp_to_consume = check_verification_token(owner, phone, token)
        except FamilyPhoneError as e:
            raise serializers.ValidationError({'phone_number': [e.message]}, code=e.code)

        attrs.update(contact_number=phone, is_phone_verified=True, phone_verified_at=timezone.now())
        return attrs

    def save(self, **kwargs):
        from .family_phone import FamilyPhoneError, consume_verification_token
        with transaction.atomic():
            otp_id = getattr(self, '_otp_to_consume', None)
            if otp_id:
                try:
                    consume_verification_token(otp_id)
                except FamilyPhoneError as e:
                    raise serializers.ValidationError({'phone_number': [e.message]}, code=e.code)
            return super().save(**kwargs)

    class Meta:
        model = FamilyMember
        fields = [
            'id',
            'name',
            'relationship',
            'date_of_birth',
            'age',
            'gender',
            'phone_number',
            'contact_number',
            'mobile',
            'is_phone_verified',
            'phone_verified_at',
            'phone_required',
            'phone_verification_token',
            'primary_account',
            'account_status',
            'email',
            'created_at',
            'added',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'is_phone_verified', 'phone_verified_at']


class FamilyPhoneSendOTPSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
    member_id = serializers.IntegerField(required=False, allow_null=True)
    name = serializers.CharField(max_length=150, required=False, allow_blank=True)


class FamilyPhoneVerifyOTPSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
    otp = serializers.CharField(min_length=4, max_length=6, trim_whitespace=True)


class SendOTPLoginSerializer(serializers.Serializer):
    """
    Serializer for requesting a login OTP.
    """
    phone_number = serializers.CharField(max_length=17)

    def validate_phone_number(self, value):
        cleaned = normalize_phone_number(value)
        if not re.match(r'^\+?[0-9]{7,15}$', cleaned):
            raise serializers.ValidationError("Invalid phone number format.")
        return cleaned


class VerifyOTPLoginSerializer(serializers.Serializer):
    """
    Serializer for verifying login OTP.
    """
    phone_number = serializers.CharField(max_length=17)
    otp = serializers.CharField(min_length=4, max_length=6, trim_whitespace=True)

    def validate_phone_number(self, value):
        cleaned = normalize_phone_number(value)
        if not re.match(r'^\+?[0-9]{7,15}$', cleaned):
            raise serializers.ValidationError("Invalid phone number format.")
        return cleaned

    def validate_otp(self, value):
        code = str(value).strip()
        if not code.isdigit():
            raise serializers.ValidationError("OTP code must consist of digits only.")
        return code


class PasswordLoginSerializer(serializers.Serializer):
    """
    Serializer for authenticating with phone number and password.
    """
    phone_number = serializers.CharField(max_length=17)
    password = serializers.CharField(max_length=128, write_only=True)

    def validate_phone_number(self, value):
        cleaned = normalize_phone_number(value)
        if not re.match(r'^\+?[0-9]{7,15}$', cleaned):
            raise serializers.ValidationError("Invalid phone number format.")
        return cleaned


# ============================================================================
# Health Card Serializers (Modules: Profile, Metrics, Vitals, Activity, Sleep, Trends, Share)
# ============================================================================

from .models import HealthCardProfile, HealthMetricHistory, HealthCardShare


class HealthProfileBasicSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthCardProfile
        fields = ['blood_group', 'emergency_contact_name', 'emergency_contact_phone']


class BodyMetricsInputSerializer(serializers.Serializer):
    height_cm = serializers.FloatField(required=False, allow_null=True)
    weight_kg = serializers.FloatField(required=False, allow_null=True)


class VitalsInputSerializer(serializers.Serializer):
    bp_systolic = serializers.IntegerField(required=False, allow_null=True)
    bp_diastolic = serializers.IntegerField(required=False, allow_null=True)
    heart_rate = serializers.IntegerField(required=False, allow_null=True)
    spo2 = serializers.IntegerField(required=False, allow_null=True)
    temperature_f = serializers.FloatField(required=False, allow_null=True)


class ActivityInputSerializer(serializers.Serializer):
    daily_steps = serializers.IntegerField(required=False, allow_null=True)
    daily_water_ml = serializers.IntegerField(required=False, allow_null=True)


class SleepInputSerializer(serializers.Serializer):
    sleep_hours = serializers.FloatField(required=True)
    sleep_quality = serializers.ChoiceField(choices=['DEEP', 'GOOD', 'FAIR', 'RESTLESS'], default='GOOD')


class ShareCreateSerializer(serializers.Serializer):
    duration_hours = serializers.IntegerField(default=24, min_value=1, max_value=720)





