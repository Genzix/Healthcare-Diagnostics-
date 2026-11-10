import re
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
    """
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    age = serializers.IntegerField(source='get_age', read_only=True)

    class Meta:
        model = User
        fields = [
            'id',
            'phone_number',
            'first_name',
            'last_name',
            'full_name',
            'email',
            'date_of_birth',
            'age',
            'gender',
            'city',
            'address',
            'pincode',
            'role',
            'is_phone_verified',
            'date_joined',
        ]
        read_only_fields = ['id', 'phone_number', 'role', 'is_phone_verified', 'date_joined']


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
    """
    age = serializers.IntegerField(source='get_age', read_only=True)

    class Meta:
        model = FamilyMember
        fields = [
            'id',
            'name',
            'relationship',
            'date_of_birth',
            'age',
            'gender',
            'contact_number',
            'email',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


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




