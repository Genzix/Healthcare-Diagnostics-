from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated

from .models import (
    User,
    FamilyMember,
    HealthCardProfile,
    HealthMetricHistory,
    HealthCardShare,
    ensure_health_card_tables,
    ensure_support_tables,
)
from .serializers import (
    SendOTPRequestSerializer,
    VerifyOTPRequestSerializer,
    ResendOTPRequestSerializer,
    UserSerializer,
    SendOTPLoginSerializer,
    VerifyOTPLoginSerializer,
    PasswordLoginSerializer,
    UserProfileUpdateSerializer,
    FamilyMemberSerializer,
    HealthProfileBasicSerializer,
    BodyMetricsInputSerializer,
    VitalsInputSerializer,
    ActivityInputSerializer,
    SleepInputSerializer,
    ShareCreateSerializer,
)
from .services import (
    normalize_phone_number,
    create_and_send_registration_otp,
    verify_registration_otp,
    create_user_account,
    create_and_send_login_otp,
    verify_login_otp,
    authenticate_with_password,
    get_tokens_for_user,
)


class RegisterSendOTPView(APIView):
    """
    API endpoint: Step 1 of Registration.
    User submits phone number (and optional metadata) to receive an OTP.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = SendOTPRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "success": False,
                    "errors": serializer.errors,
                    "message": "Validation failed. Please verify your phone number.",
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        phone_number = serializer.validated_data['phone_number']
        metadata = {
            'first_name': serializer.validated_data.get('first_name', ''),
            'last_name': serializer.validated_data.get('last_name', ''),
            'email': serializer.validated_data.get('email', ''),
            'role': serializer.validated_data.get('role', 'PATIENT'),
        }

        otp_record, error_message = create_and_send_registration_otp(
            phone_number=phone_number,
            metadata=metadata
        )

        if error_message:
            return Response(
                {
                    "success": False,
                    "message": error_message,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        response_data = {
            "success": True,
            "message": f"OTP sent successfully to {phone_number}.",
            "phone_number": phone_number,
            "otp_expiry_minutes": getattr(settings, 'OTP_EXPIRY_MINUTES', 5),
        }

        # Include OTP in response during DEBUG mode for effortless testing
        if getattr(settings, 'DEBUG', False) and otp_record:
            response_data["debug_otp"] = otp_record.otp_code

        return Response(response_data, status=status.HTTP_200_OK)


class RegisterVerifyOTPView(APIView):
    """
    API endpoint: Step 2 of Registration.
    User submits phone number and OTP code.
    Upon verification, the user account is created.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = VerifyOTPRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "success": False,
                    "errors": serializer.errors,
                    "message": "Validation failed. Please verify phone number and OTP.",
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        phone_number = serializer.validated_data['phone_number']
        otp_code = serializer.validated_data['otp']

        # 1. Verify OTP
        otp_record, error_message = verify_registration_otp(phone_number, otp_code)
        if error_message:
            return Response(
                {
                    "success": False,
                    "message": error_message,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2. Collect user profile attributes (request overrides earlier metadata)
        stored_metadata = otp_record.metadata if otp_record else {}
        user_data = {
            'first_name': serializer.validated_data.get('first_name') or stored_metadata.get('first_name', ''),
            'last_name': serializer.validated_data.get('last_name') or stored_metadata.get('last_name', ''),
            'email': serializer.validated_data.get('email') or stored_metadata.get('email', ''),
            'role': serializer.validated_data.get('role') or stored_metadata.get('role', 'PATIENT'),
            'password': serializer.validated_data.get('password'),
        }

        # 3. Create account
        user, token_key = create_user_account(phone_number, user_data=user_data)
        jwt_tokens = get_tokens_for_user(user)

        user_data_response = UserSerializer(user).data
        response_data = {
            "success": True,
            "message": "Account created successfully.",
            "user": user_data_response,
        }
        if jwt_tokens:
            response_data["tokens"] = jwt_tokens
        elif token_key:
            response_data["token"] = token_key

        return Response(response_data, status=status.HTTP_201_CREATED)


class RegisterResendOTPView(APIView):
    """
    API endpoint: Resend registration OTP with cooldown protection.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = ResendOTPRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "success": False,
                    "errors": serializer.errors,
                    "message": "Validation failed.",
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        phone_number = serializer.validated_data['phone_number']
        otp_record, error_message = create_and_send_registration_otp(phone_number=phone_number)

        if error_message:
            return Response(
                {
                    "success": False,
                    "message": error_message,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        response_data = {
            "success": True,
            "message": f"A new OTP has been sent to {phone_number}.",
            "phone_number": phone_number,
            "otp_expiry_minutes": getattr(settings, 'OTP_EXPIRY_MINUTES', 5),
        }
        if getattr(settings, 'DEBUG', False) and otp_record:
            response_data["debug_otp"] = otp_record.otp_code

        return Response(response_data, status=status.HTTP_200_OK)


class UnifiedRegisterView(APIView):
    """
    Single unified registration endpoint.
    - If 'otp' is in request: processes verification and account creation.
    - If 'otp' is not in request: processes OTP dispatch.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        if 'otp' in request.data and request.data.get('otp'):
            return RegisterVerifyOTPView().post(request, *args, **kwargs)
        return RegisterSendOTPView().post(request, *args, **kwargs)


class LoginSendOTPView(APIView):
    """
    API endpoint: Requests a login OTP for an existing verified phone number.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = SendOTPLoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "success": False,
                    "errors": serializer.errors,
                    "message": "Validation failed.",
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        phone_number = serializer.validated_data['phone_number']
        otp_record, error_message = create_and_send_login_otp(phone_number=phone_number)

        if error_message:
            return Response(
                {
                    "success": False,
                    "message": error_message,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        response_data = {
            "success": True,
            "message": f"Login OTP sent successfully to {phone_number}.",
            "phone_number": phone_number,
            "otp_expiry_minutes": getattr(settings, 'OTP_EXPIRY_MINUTES', 5),
        }
        if getattr(settings, 'DEBUG', False) and otp_record:
            response_data["debug_otp"] = otp_record.otp_code

        return Response(response_data, status=status.HTTP_200_OK)


class LoginVerifyOTPView(APIView):
    """
    API endpoint: Verifies login OTP and returns auth token.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = VerifyOTPLoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "success": False,
                    "errors": serializer.errors,
                    "message": "Validation failed.",
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        phone_number = serializer.validated_data['phone_number']
        otp_code = serializer.validated_data['otp']

        user, token_key, error_message = verify_login_otp(phone_number, otp_code)
        if error_message:
            return Response(
                {
                    "success": False,
                    "message": error_message,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        jwt_tokens = get_tokens_for_user(user)
        response_data = {
            "success": True,
            "message": "Login successful.",
            "user": UserSerializer(user).data,
        }
        if jwt_tokens:
            response_data["tokens"] = jwt_tokens
        elif token_key:
            response_data["token"] = token_key

        return Response(response_data, status=status.HTTP_200_OK)


class PasswordLoginView(APIView):
    """
    API endpoint: Authenticates user with phone number and password.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = PasswordLoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "success": False,
                    "errors": serializer.errors,
                    "message": "Validation failed.",
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        phone_number = serializer.validated_data['phone_number']
        password = serializer.validated_data['password']

        user, token_key, error_message = authenticate_with_password(phone_number, password)
        if error_message:
            return Response(
                {
                    "success": False,
                    "message": error_message,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        jwt_tokens = get_tokens_for_user(user)
        response_data = {
            "success": True,
            "message": "Login successful.",
            "user": UserSerializer(user).data,
        }
        if jwt_tokens:
            response_data["tokens"] = jwt_tokens
        elif token_key:
            response_data["token"] = token_key

        return Response(response_data, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """
    API endpoint: Logs out authenticated user and invalidates token.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            # Delete auth token if exists
            if hasattr(request.user, 'auth_token') and request.user.auth_token:
                request.user.auth_token.delete()
        except Exception:
            pass

        return Response(
            {
                "success": True,
                "message": "Successfully logged out.",
            },
            status=status.HTTP_200_OK
        )


class UserProfileView(APIView):
    """
    API endpoint: Get or update authenticated user profile.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        serializer = UserSerializer(request.user)
        return Response(
            {
                "success": True,
                "user": serializer.data,
                "profile": serializer.data,
            },
            status=status.HTTP_200_OK
        )

    def put(self, request, *args, **kwargs):
        return self.patch(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        serializer = UserProfileUpdateSerializer(
            request.user,
            data=request.data,
            partial=True
        )
        if not serializer.is_valid():
            return Response(
                {
                    "success": False,
                    "errors": serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer.save()
        user_data = UserSerializer(request.user).data
        return Response(
            {
                "success": True,
                "message": "Profile updated successfully.",
                "user": user_data,
                "profile": user_data,
            },
            status=status.HTTP_200_OK
        )


class FamilyMemberListCreateView(APIView):
    """
    API endpoint: List and add family members for authenticated user.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        members = FamilyMember.objects.filter(user=request.user)
        serializer = FamilyMemberSerializer(members, many=True)
        return Response(
            {
                "success": True,
                "count": members.count(),
                "family_members": serializer.data,
            },
            status=status.HTTP_200_OK
        )

    def post(self, request, *args, **kwargs):
        serializer = FamilyMemberSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "success": False,
                    "errors": serializer.errors,
                    "message": "Failed to add family member.",
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        member = serializer.save(user=request.user)
        return Response(
            {
                "success": True,
                "message": f"Family member {member.name} added successfully.",
                "family_member": FamilyMemberSerializer(member).data,
            },
            status=status.HTTP_201_CREATED
        )


class FamilyMemberDetailView(APIView):
    """
    API endpoint: Retrieve, edit, or delete a specific family member profile.
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, user, pk):
        try:
            return FamilyMember.objects.get(pk=pk, user=user)
        except FamilyMember.DoesNotExist:
            return None

    def get(self, request, pk, *args, **kwargs):
        member = self.get_object(request.user, pk)
        if not member:
            return Response(
                {"success": False, "message": "Family member not found."},
                status=status.HTTP_404_NOT_FOUND
            )
        return Response(
            {"success": True, "family_member": FamilyMemberSerializer(member).data},
            status=status.HTTP_200_OK
        )

    def put(self, request, pk, *args, **kwargs):
        return self.patch(request, pk, *args, **kwargs)

    def patch(self, request, pk, *args, **kwargs):
        member = self.get_object(request.user, pk)
        if not member:
            return Response(
                {"success": False, "message": "Family member not found."},
                status=status.HTTP_404_NOT_FOUND
            )
        serializer = FamilyMemberSerializer(member, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        serializer.save()
        return Response(
            {
                "success": True,
                "message": "Family member updated successfully.",
                "family_member": serializer.data,
            },
            status=status.HTTP_200_OK
        )

    def delete(self, request, pk, *args, **kwargs):
        member = self.get_object(request.user, pk)
        if not member:
            return Response(
                {"success": False, "message": "Family member not found."},
                status=status.HTTP_404_NOT_FOUND
            )
        member_name = member.name
        member.delete()
        return Response(
            {
                "success": True,
                "message": f"Family member {member_name} removed successfully.",
            },
            status=status.HTTP_200_OK
        )


# ============================================================================
# Health Card Helpers & Views (1MinDoc Health Card)
# ============================================================================

def get_or_create_profile(user):
    ensure_health_card_tables()
    profile, _ = HealthCardProfile.objects.get_or_create(user=user)
    return profile


def serialize_health_card(profile, request=None):
    user = profile.user
    full_name = user.get_full_name() or user.phone_number
    initial = (full_name[0] if full_name else 'H').upper()
    age = user.get_age()
    gender = user.gender.capitalize() if user.gender else 'Not Specified'

    # BMI & BMR
    bmi_val = profile.bmi
    bmi_cat = profile.bmi_category
    bmr_val = profile.bmr

    # Active share link
    now = timezone.now()
    active_share = HealthCardShare.objects.filter(user=user, is_active=True, expires_at__gt=now).order_by('-created_at').first()
    share_info = None
    if active_share:
        share_url = request.build_absolute_uri(f"/api/health-card/share/{active_share.share_token}/") if request else f"/api/health-card/share/{active_share.share_token}/"
        share_info = {
            "share_token": active_share.share_token,
            "share_url": share_url,
            "expires_at": active_share.expires_at,
            "duration_hours": active_share.duration_hours,
        }

    return {
        "header": {
            "app_name": "1MinDoc",
            "title": "Health Card",
            "patient_name": full_name,
            "avatar_initial": initial,
            "age": age,
            "gender": gender,
            "demographics_display": f"{age} Yrs · {gender}" if age else gender,
            "disclaimer": "Informational · not a medical record"
        },
        "summary_pills": {
            "blood": profile.blood_group or "—",
            "height": f"{profile.height_cm} cm" if profile.height_cm else "—",
            "weight": f"{profile.weight_kg} kg" if profile.weight_kg else "—",
            "bmi": f"{bmi_val} ({bmi_cat})" if bmi_val else "—"
        },
        "basic_profile": {
            "name": full_name,
            "age": age,
            "gender": gender,
            "blood_group": profile.blood_group or "Not recorded",
            "emergency_contact_name": profile.emergency_contact_name or "Not recorded",
            "emergency_contact_phone": profile.emergency_contact_phone or "Not recorded",
        },
        "body_metrics": {
            "height": {
                "value": profile.height_cm,
                "unit": "cm",
                "label": f"{profile.height_cm} cm" if profile.height_cm else "Not recorded"
            },
            "weight": {
                "value": profile.weight_kg,
                "unit": "kg",
                "label": f"{profile.weight_kg} kg" if profile.weight_kg else "Not recorded"
            },
            "bmi": {
                "value": bmi_val,
                "category": bmi_cat,
                "label": f"{bmi_val} · {bmi_cat}" if bmi_val else "Needs height + weight"
            },
            "bmr": {
                "value": bmr_val,
                "unit": "kcal/day",
                "label": f"{bmr_val} kcal/day" if bmr_val else "Needs height, weight, age"
            }
        },
        "vitals": {
            "bp": {
                "systolic": profile.bp_systolic,
                "diastolic": profile.bp_diastolic,
                "display": profile.bp_display,
                "status": profile.bp_status
            },
            "hr": {
                "value": profile.heart_rate,
                "unit": "bpm",
                "display": f"{profile.heart_rate} bpm" if profile.heart_rate else "No data"
            },
            "spo2": {
                "value": profile.spo2,
                "unit": "%",
                "display": profile.spo2_display,
                "status": profile.spo2_status
            },
            "temp": {
                "value": profile.temperature_f,
                "unit": "°F",
                "display": profile.temperature_display
            }
        },
        "daily_activity": {
            "steps": {
                "current": profile.daily_steps,
                "goal": 10000,
                "percent": min(100, round((profile.daily_steps / 10000) * 100)) if profile.daily_steps else 0,
                "display": f"{profile.daily_steps:,} steps"
            },
            "water": {
                "current_ml": profile.daily_water_ml,
                "goal_ml": 3000,
                "percent": min(100, round((profile.daily_water_ml / 3000) * 100)) if profile.daily_water_ml else 0,
                "display": f"{profile.daily_water_ml} ml"
            }
        },
        "sleep_tracking": {
            "duration_hours": profile.sleep_hours,
            "quality": profile.sleep_quality,
            "display": f"{profile.sleep_hours} hrs ({profile.sleep_quality.capitalize()})" if profile.sleep_hours else "No data"
        },
        "share": share_info,
        "updated_at": profile.updated_at
    }


class HealthCardDetailView(APIView):
    """
    GET /api/health-card/
    Fetches the comprehensive Health Card for the authenticated patient.
    Covers Basic Profile, Body Metrics, Vitals, Activity, Sleep, and Share Status.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        profile = get_or_create_profile(request.user)
        card_data = serialize_health_card(profile, request)
        return Response({"success": True, "health_card": card_data}, status=status.HTTP_200_OK)


class HealthCardProfileUpdateView(APIView):
    """
    PATCH /api/health-card/profile/
    Updates Basic Health Profile details (blood group, emergency contact).
    Triggered by 'Edit details' on the Health Card UI.
    """
    permission_classes = [IsAuthenticated]

    def patch(self, request, *args, **kwargs):
        profile = get_or_create_profile(request.user)
        serializer = HealthProfileBasicSerializer(profile, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response({
            "success": True,
            "message": "Health profile details updated successfully.",
            "basic_profile": {
                "blood_group": profile.blood_group,
                "emergency_contact_name": profile.emergency_contact_name,
                "emergency_contact_phone": profile.emergency_contact_phone
            }
        }, status=status.HTTP_200_OK)


class HealthCardBodyMetricsView(APIView):
    """
    POST /api/health-card/body-metrics/
    Records Height & Weight, recalculates BMI & BMR, and saves to health history.
    Triggered by 'Add measurement' button.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = BodyMetricsInputSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        profile = get_or_create_profile(request.user)
        ht = serializer.validated_data.get('height_cm')
        wt = serializer.validated_data.get('weight_kg')

        if ht is not None:
            profile.height_cm = ht
            HealthMetricHistory.objects.create(
                user=request.user, category='BODY', metric_name='height_cm', metric_value=ht
            )
        if wt is not None:
            profile.weight_kg = wt
            HealthMetricHistory.objects.create(
                user=request.user, category='BODY', metric_name='weight_kg', metric_value=wt
            )

        profile.save()

        # Log BMI if both exist
        if profile.bmi:
            HealthMetricHistory.objects.create(
                user=request.user, category='BODY', metric_name='bmi', metric_value=profile.bmi
            )

        return Response({
            "success": True,
            "message": "Body metrics recorded successfully.",
            "metrics": {
                "height_cm": profile.height_cm,
                "weight_kg": profile.weight_kg,
                "bmi": profile.bmi,
                "bmi_category": profile.bmi_category,
                "bmr": profile.bmr
            }
        }, status=status.HTTP_200_OK)


class HealthCardVitalsView(APIView):
    """
    POST /api/health-card/vitals/
    Records Blood Pressure, Heart Rate, SpO2, Temperature and logs to history.
    Triggered by 'Add reading' in Vitals.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = VitalsInputSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        profile = get_or_create_profile(request.user)
        d = serializer.validated_data

        if d.get('bp_systolic') is not None and d.get('bp_diastolic') is not None:
            profile.bp_systolic = d['bp_systolic']
            profile.bp_diastolic = d['bp_diastolic']
            HealthMetricHistory.objects.create(
                user=request.user, category='VITALS', metric_name='bp',
                metric_value=d['bp_systolic'], secondary_value=d['bp_diastolic']
            )

        if d.get('heart_rate') is not None:
            profile.heart_rate = d['heart_rate']
            HealthMetricHistory.objects.create(
                user=request.user, category='VITALS', metric_name='heart_rate', metric_value=d['heart_rate']
            )

        if d.get('spo2') is not None:
            profile.spo2 = d['spo2']
            HealthMetricHistory.objects.create(
                user=request.user, category='VITALS', metric_name='spo2', metric_value=d['spo2']
            )

        if d.get('temperature_f') is not None:
            profile.temperature_f = d['temperature_f']
            HealthMetricHistory.objects.create(
                user=request.user, category='VITALS', metric_name='temperature_f', metric_value=d['temperature_f']
            )

        profile.save()
        return Response({
            "success": True,
            "message": "Vitals reading recorded successfully.",
            "vitals": {
                "bp_display": profile.bp_display,
                "bp_status": profile.bp_status,
                "heart_rate": profile.heart_rate,
                "spo2_display": profile.spo2_display,
                "temperature_display": profile.temperature_display
            }
        }, status=status.HTTP_200_OK)


class HealthCardActivityView(APIView):
    """
    POST /api/health-card/activity/
    Records Daily Activity: steps and water intake (ml).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = ActivityInputSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        profile = get_or_create_profile(request.user)
        steps = serializer.validated_data.get('daily_steps')
        water = serializer.validated_data.get('daily_water_ml')

        if steps is not None:
            profile.daily_steps = steps
            HealthMetricHistory.objects.create(
                user=request.user, category='ACTIVITY', metric_name='steps', metric_value=steps
            )
        if water is not None:
            profile.daily_water_ml = water
            HealthMetricHistory.objects.create(
                user=request.user, category='ACTIVITY', metric_name='water_ml', metric_value=water
            )

        profile.save()
        return Response({
            "success": True,
            "message": "Daily activity recorded successfully.",
            "daily_activity": {
                "daily_steps": profile.daily_steps,
                "daily_water_ml": profile.daily_water_ml
            }
        }, status=status.HTTP_200_OK)


class HealthCardSleepView(APIView):
    """
    POST /api/health-card/sleep/
    Records Sleep Tracking: duration in hours and quality.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = SleepInputSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        profile = get_or_create_profile(request.user)
        hours = serializer.validated_data['sleep_hours']
        quality = serializer.validated_data.get('sleep_quality', 'GOOD')

        profile.sleep_hours = hours
        profile.sleep_quality = quality
        profile.save()

        HealthMetricHistory.objects.create(
            user=request.user, category='SLEEP', metric_name='sleep_hours',
            metric_value=hours, notes=quality
        )

        return Response({
            "success": True,
            "message": "Sleep tracking recorded successfully.",
            "sleep": {
                "sleep_hours": profile.sleep_hours,
                "sleep_quality": profile.sleep_quality
            }
        }, status=status.HTTP_200_OK)


class HealthCardTrendsView(APIView):
    """
    GET /api/health-card/trends/?metric=weight&days=7
    Returns historical readings and chart data points.
    Supported metric values: 'weight', 'bmi', 'bp', 'heart_rate', 'spo2', 'steps', 'water', 'sleep', 'all'.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        ensure_health_card_tables()
        metric = request.query_params.get('metric', 'all').lower()
        days = int(request.query_params.get('days', 7))
        since_date = timezone.now() - timedelta(days=days)

        metric_map = {
            'weight': 'weight_kg',
            'bmi': 'bmi',
            'bp': 'bp',
            'heart_rate': 'heart_rate',
            'spo2': 'spo2',
            'steps': 'steps',
            'water': 'water_ml',
            'sleep': 'sleep_hours',
        }

        qs = HealthMetricHistory.objects.filter(user=request.user, recorded_at__gte=since_date)
        if metric in metric_map:
            qs = qs.filter(metric_name=metric_map[metric])

        records = qs.order_by('recorded_at')
        chart_series = {}

        for r in records:
            name = r.metric_name
            if name not in chart_series:
                chart_series[name] = {"dates": [], "values": [], "secondary_values": []}
            chart_series[name]["dates"].append(r.recorded_at.strftime("%Y-%m-%d %H:%M"))
            chart_series[name]["values"].append(r.metric_value)
            if r.secondary_value is not None:
                chart_series[name]["secondary_values"].append(r.secondary_value)

        trend_summary = {}
        for name, series in chart_series.items():
            vals = series["values"]
            trend_summary[name] = {
                "latest": vals[-1] if vals else None,
                "min": min(vals) if vals else None,
                "max": max(vals) if vals else None,
                "count": len(vals)
            }

        return Response({
            "success": True,
            "days_analyzed": days,
            "chart_series": chart_series,
            "summary": trend_summary
        }, status=status.HTTP_200_OK)


class HealthCardShareCreateView(APIView):
    """
    POST /api/health-card/share/
    Generates a secure, time-limited sharing link for doctors / family members.
    Request body: {"duration_hours": 24}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = ShareCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        duration = serializer.validated_data.get('duration_hours', 24)
        share = HealthCardShare.create_share_link(request.user, duration_hours=duration)
        share_url = request.build_absolute_uri(f"/api/health-card/share/{share.share_token}/")

        return Response({
            "success": True,
            "message": f"Time-limited Health Card sharing link created (valid for {duration} hours).",
            "share_token": share.share_token,
            "share_url": share_url,
            "expires_at": share.expires_at,
            "duration_hours": duration
        }, status=status.HTTP_201_CREATED)


class HealthCardPublicSharedView(APIView):
    """
    GET /api/health-card/share/<share_token>/
    Public Endpoint: Allows doctors / family to view patient Health Card via time-limited link.
    No login required. Validates link expiration.
    """
    permission_classes = [AllowAny]

    def get(self, request, share_token, *args, **kwargs):
        ensure_health_card_tables()
        share = HealthCardShare.objects.filter(share_token=share_token).first()
        if not share or not share.is_valid():
            return Response({
                "success": False,
                "message": "This health card sharing link has expired or has been revoked."
            }, status=status.HTTP_410_GONE)

        profile = get_or_create_profile(share.user)
        card_data = serialize_health_card(profile, request)
        card_data["shared_metadata"] = {
            "expires_at": share.expires_at,
            "view_mode": "READ_ONLY_DOCTOR_ACCESS"
        }

        return Response({
            "success": True,
            "health_card": card_data
        }, status=status.HTTP_200_OK)


class HealthCardShareRevokeView(APIView):
    """
    DELETE /api/health-card/share/<share_token>/
    Revokes an active sharing link early.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, share_token, *args, **kwargs):
        ensure_health_card_tables()
        share = HealthCardShare.objects.filter(share_token=share_token, user=request.user).first()
        if not share:
            return Response({"success": False, "message": "Share link not found."}, status=status.HTTP_404_NOT_FOUND)

        share.is_active = False
        share.save(update_fields=['is_active'])
        return Response({"success": True, "message": "Share link revoked successfully."}, status=status.HTTP_200_OK)


# ============================================================================
# Help, Support & FAQs Views (1MinDoc Support Center)
# ============================================================================

FAQS_DATA = [
    {
        "id": 1,
        "question": "How do I book a test?",
        "answer": (
            "1. Browse individual diagnostic tests or health packages from the Home or Tests tab.\n"
            "2. Select your preferred diagnostic partner centre or choose Home Sample Collection.\n"
            "3. Choose an available date and convenient time slot.\n"
            "4. Select the patient (yourself or a saved family member).\n"
            "5. Pay the 30% advance to confirm your booking. The remaining 70% is payable after test completion."
        ),
        "category": "Booking"
    },
    {
        "id": 2,
        "question": "How are reports delivered?",
        "answer": (
            "Once your sample is collected and processed by our NABL/CAP-accredited diagnostic partner lab, "
            "verified digital test reports are uploaded directly to your Bookings tab. You will also receive an "
            "SMS and WhatsApp notification with a direct download link. Verified PDF reports remain accessible in your account forever."
        ),
        "category": "Reports"
    },
    {
        "id": 3,
        "question": "Can I book for a family member?",
        "answer": (
            "Yes! You can manage health profiles for your entire family. Navigate to the Family tab in the sidebar "
            "to add family members with their name, date of birth, gender, and relationship. During booking, simply select "
            "the family member from the patient dropdown."
        ),
        "category": "Family"
    },
    {
        "id": 4,
        "question": "What is the cancellation policy?",
        "answer": (
            "You can cancel your appointment anytime before sample collection. If cancelled prior to partner acceptance or "
            "if the partner rejects your request due to slot unavailability, a 100% refund of your 30% advance payment is automatically "
            "initiated to your original payment method within 2-4 business days."
        ),
        "category": "Cancellations & Refunds"
    },
    {
        "id": 5,
        "question": "How do I earn loyalty points?",
        "answer": (
            "Every completed diagnostic test and health package booking automatically earns 1MinDoc Reward Points "
            "(5% of total order value). Accumulated points can be redeemed as instant discounts on future diagnostic "
            "bookings and health package orders."
        ),
        "category": "Rewards"
    },
    {
        "id": 6,
        "question": "Is fasting required for all blood tests?",
        "answer": (
            "No, fasting is only required for specific tests such as Fasting Blood Glucose (8-10 hours), "
            "Lipid Profile (10-12 hours), and certain full body checkup packages. Preparation guidelines are clearly "
            "specified under each test description before booking."
        ),
        "category": "Preparation"
    },
    {
        "id": 7,
        "question": "How does Home Sample Collection work?",
        "answer": (
            "A certified phlebotomist visits your entered address during your chosen time slot. They carry sterile, sealed "
            "collection kits, follow strict cold-chain sample preservation, and transport your samples to our accredited partner lab."
        ),
        "category": "Home Collection"
    }
]

SUPPORT_CONTACT_DATA = {
    "title": "Help & Support",
    "timing": "9AM – 9PM, Mon–Sat",
    "phone": "+91 1800 123 4567",
    "email": "support@1mindoc.com",
    "whatsapp": "+91 98765 43210",
    "address": "1MinDoc Healthcare Technologies Pvt Ltd, Hitech City, Hyderabad, 500081"
}

PRIVACY_POLICY_TEXT = (
    "1MinDoc Healthcare Platform Privacy Policy\n\n"
    "Last Updated: November 2026\n\n"
    "1. Information We Collect:\n"
    "We collect your phone number, basic profile (name, age, gender), family profiles, delivery address for home sample "
    "collection, and diagnostic test booking history.\n\n"
    "2. Medical Data & Health Card Security:\n"
    "Your health card metrics (blood group, vitals, body metrics) and test reports are encrypted at rest and in transit. "
    "We never sell or disclose your medical reports to third parties without your explicit consent.\n\n"
    "3. Lab Partner Sharing:\n"
    "When you book a test, necessary sample collection details (name, age, gender, contact number, collection address) "
    "are securely shared with the certified diagnostic partner lab fulfilling your request.\n\n"
    "4. Time-Limited Sharing:\n"
    "Health Card public sharing links are cryptographically signed and expire automatically after the duration you select."
)

TERMS_CONDITIONS_TEXT = (
    "1MinDoc Terms and Conditions of Service\n\n"
    "Last Updated: November 2026\n\n"
    "1. Diagnostic Services:\n"
    "1MinDoc connects patients with licensed, NABL/CAP-accredited diagnostic laboratories. Diagnostic partners are solely "
    "responsible for lab analysis, accuracy of reports, and clinical observations.\n\n"
    "2. Advance Payment & Balance:\n"
    "Bookings require a 30% advance payment to reserve slot capacity. The remaining 70% balance is payable upon completion "
    "of the service (online via card/UPI or approved offline cash/POS at the partner counter).\n\n"
    "3. Rescheduling & Cancellations:\n"
    "Free rescheduling is permitted up to 2 hours prior to the scheduled slot time. Full refunds apply on partner rejection."
)


class SupportCenterOverviewView(APIView):
    """
    GET /api/support/
    Returns the complete Help & Support Screen overview matching the 1MinDoc UI:
      - 4 Quick Action Links: Help & Support, FAQs, Privacy Policy, Terms & Conditions
      - Top 5 Featured FAQs with accordions
      - App Metadata: 1MinDoc v1.0.0
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return Response({
            "success": True,
            "app_info": {
                "name": "1MinDoc",
                "version": "1.0.0",
                "tagline": "Healthcare Diagnostics & Health Card"
            },
            "quick_links": [
                {
                    "id": "help_support",
                    "title": "Help & Support",
                    "subtitle": "9AM – 9PM, Mon–Sat",
                    "icon": "headphones",
                    "route": "/support/contact"
                },
                {
                    "id": "faqs",
                    "title": "FAQs",
                    "subtitle": "Common questions answered",
                    "icon": "help-circle",
                    "route": "/support/faqs"
                },
                {
                    "id": "privacy_policy",
                    "title": "Privacy Policy",
                    "subtitle": "How we use your data",
                    "icon": "shield",
                    "route": "/support/privacy-policy"
                },
                {
                    "id": "terms_conditions",
                    "title": "Terms & Conditions",
                    "subtitle": "Usage terms",
                    "icon": "file-text",
                    "route": "/support/terms"
                }
            ],
            "support_contact": SUPPORT_CONTACT_DATA,
            "featured_faqs": FAQS_DATA[:5]
        }, status=status.HTTP_200_OK)


class FAQListView(APIView):
    """
    GET /api/support/faqs/
    Returns all Frequently Asked Questions, with optional filtering by category or keyword search.
    Query params: ?search=book&category=Booking
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        search = request.query_params.get('search', '').lower().strip()
        cat = request.query_params.get('category', '').lower().strip()

        results = FAQS_DATA
        if cat:
            results = [f for f in results if f['category'].lower() == cat]
        if search:
            results = [f for f in results if search in f['question'].lower() or search in f['answer'].lower()]

        return Response({
            "success": True,
            "count": len(results),
            "faqs": results
        }, status=status.HTTP_200_OK)


class PrivacyPolicyView(APIView):
    """
    GET /api/support/privacy-policy/
    Returns full Privacy Policy text and summary.
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return Response({
            "success": True,
            "title": "Privacy Policy",
            "last_updated": "November 2026",
            "summary": "How 1MinDoc uses and protects your diagnostic and personal data.",
            "content": PRIVACY_POLICY_TEXT
        }, status=status.HTTP_200_OK)


class TermsConditionsView(APIView):
    """
    GET /api/support/terms-conditions/
    Returns Usage Terms & Conditions.
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return Response({
            "success": True,
            "title": "Terms & Conditions",
            "last_updated": "November 2026",
            "summary": "1MinDoc platform usage terms and payment conditions.",
            "content": TERMS_CONDITIONS_TEXT
        }, status=status.HTTP_200_OK)


class SupportContactInquiryView(APIView):
    """
    POST /api/support/contact/
    Allows patients to submit a support message or request a callback during support hours (9AM-9PM).
    Body: {"name": "...", "phone": "...", "email": "...", "subject": "...", "message": "..."}
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        ensure_support_tables()
        d = request.data
        name = d.get('name') or (request.user.get_full_name() if request.user.is_authenticated else '')
        phone = d.get('phone') or (request.user.phone_number if request.user.is_authenticated else '')
        email = d.get('email') or (request.user.email if request.user.is_authenticated else '')
        subject = d.get('subject', 'General Inquiry')
        message = d.get('message', '').strip()

        if not message:
            return Response({
                "success": False,
                "message": "Message field is required."
            }, status=status.HTTP_400_BAD_REQUEST)

        user_id = request.user.id if request.user.is_authenticated else None

        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO accounts_supportinquiry (user_id, name, phone, email, subject, message) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [user_id, name, phone, email, subject, message]
            )

        return Response({
            "success": True,
            "message": "Your message has been received. Our support team will get back to you shortly during support hours (9AM – 9PM, Mon–Sat).",
            "inquiry": {
                "name": name,
                "phone": phone,
                "subject": subject
            }
        }, status=status.HTTP_201_CREATED)


# ============================================================================
# Admin Management Views (1MinDoc Admin Panel: Users & Family Members)
# ============================================================================

class AdminUserListView(APIView):
    """
    GET /api/admin/users/
    Admin Panel: List all registered platform users with search by name, mobile, email.
    Matches Image 2: Name, Mobile, Email, Gender, Registered, Actions.
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        from django.db.models import Q
        queryset = User.objects.all().order_by('-date_joined')

        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(phone_number__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(email__icontains=search)
            )

        role = request.query_params.get('role')
        if role:
            queryset = queryset.filter(role=role.upper())

        serializer = UserSerializer(queryset, many=True)
        return Response({
            "success": True,
            "count": queryset.count(),
            "users": serializer.data,
            "results": serializer.data
        }, status=status.HTTP_200_OK)


class AdminFamilyMemberListView(APIView):
    """
    GET /api/admin/family-members/
    Admin Panel: List all registered family member profiles with search.
    Matches Image 3: Name, Mobile, Relationship, Primary Account, Account Status, Added.
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        from django.db.models import Q
        queryset = FamilyMember.objects.select_related('user').all().order_by('-created_at')

        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(contact_number__icontains=search) |
                Q(user__phone_number__icontains=search)
            )

        serializer = FamilyMemberSerializer(queryset, many=True)
        return Response({
            "success": True,
            "count": queryset.count(),
            "family_members": serializer.data,
            "family_profiles": serializer.data,
            "results": serializer.data
        }, status=status.HTTP_200_OK)


class AdminLoginView(APIView):
    """
    POST /api/admin/login/
    Dedicated Admin Login endpoint using Email and Password.
    Strictly enforces that user must have role='ADMIN', is_staff=True, or is_superuser=True.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        email = str(request.data.get('email') or '').strip()
        phone_number = str(request.data.get('phone_number') or '').strip()
        password = str(request.data.get('password') or '')

        if not password:
            return Response({
                "success": False,
                "message": "Password is required for admin login."
            }, status=status.HTTP_400_BAD_REQUEST)

        # Primary authentication by email
        user = None
        if email:
            user = User.objects.filter(email__iexact=email).first()
        elif phone_number:
            # Fallback for phone-based admin logins
            clean_phone = normalize_phone_number(phone_number)
            user = User.objects.filter(phone_number=clean_phone).first()
        else:
            return Response({
                "success": False,
                "message": "Admin email is required."
            }, status=status.HTTP_400_BAD_REQUEST)

        if not user or not user.check_password(password):
            return Response({
                "success": False,
                "message": "Invalid admin email or password."
            }, status=status.HTTP_401_UNAUTHORIZED)

        if not user.is_active:
            return Response({
                "success": False,
                "message": "This admin account is disabled."
            }, status=status.HTTP_403_FORBIDDEN)

        # STRICT CHECK: Must be staff, superuser, or role == ADMIN
        if not (user.is_staff or user.is_superuser or user.role == 'ADMIN'):
            return Response({
                "success": False,
                "message": "Access denied. Only system administrators can access the admin portal."
            }, status=status.HTTP_403_FORBIDDEN)

        jwt_tokens = get_tokens_for_user(user)
        user_data = UserSerializer(user).data

        return Response({
            "success": True,
            "message": f"Welcome back, Administrator {user.get_full_name() or user.email or user.phone_number}.",
            "is_admin": True,
            "user": user_data,
            "tokens": jwt_tokens
        }, status=status.HTTP_200_OK)


class AdminMeProfileView(APIView):
    """
    GET /api/admin/me/ or GET /api/admin/profile/
    Dedicated Admin verification endpoint.
    Confirms whether the current authenticated token belongs to an administrator.
    Returns 403 Forbidden if called by a regular patient.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        if not (user.is_staff or user.is_superuser or user.role == 'ADMIN'):
            return Response({
                "success": False,
                "is_admin": False,
                "message": "Access denied. Current user is not an administrator."
            }, status=status.HTTP_403_FORBIDDEN)

        return Response({
            "success": True,
            "is_admin": True,
            "admin": {
                "id": user.id,
                "phone_number": user.phone_number,
                "name": user.get_full_name() or user.phone_number,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "role": user.role,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
                "permissions": [
                    "MANAGE_USERS",
                    "MANAGE_FAMILY_MEMBERS",
                    "MANAGE_TESTS",
                    "MANAGE_PACKAGES",
                    "MANAGE_BOOKINGS",
                    "APPROVE_PARTNERS",
                    "VIEW_FINANCIALS"
                ]
            }
        }, status=status.HTTP_200_OK)



