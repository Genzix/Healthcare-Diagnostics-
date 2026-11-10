from django.conf import settings
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated

from .models import FamilyMember
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
)
from .services import (
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
        return Response(
            {
                "success": True,
                "message": "Profile updated successfully.",
                "user": UserSerializer(request.user).data,
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



