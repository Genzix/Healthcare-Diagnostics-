from django.urls import path
# pyrefly: ignore[missing-import]
from rest_framework_simplejwt.views import (
    TokenRefreshView,
    TokenVerifyView,
)
from .views import (
    RegisterSendOTPView,
    RegisterVerifyOTPView,
    RegisterResendOTPView,
    UnifiedRegisterView,
    LoginSendOTPView,
    LoginVerifyOTPView,
    PasswordLoginView,
    LogoutView,
    UserProfileView,
    FamilyMemberListCreateView,
    FamilyMemberDetailView,
)

app_name = 'accounts'

urlpatterns = [
    # Registration Endpoints
    path('api/register/', UnifiedRegisterView.as_view(), name='api_register_unified'),
    path('api/register/send-otp/', RegisterSendOTPView.as_view(), name='api_register_send_otp'),
    path('api/register/verify-otp/', RegisterVerifyOTPView.as_view(), name='api_register_verify_otp'),
    path('api/register/resend-otp/', RegisterResendOTPView.as_view(), name='api_register_resend_otp'),

    # Login Endpoints
    path('api/login/send-otp/', LoginSendOTPView.as_view(), name='api_login_send_otp'),
    path('api/login/verify-otp/', LoginVerifyOTPView.as_view(), name='api_login_verify_otp'),
    path('api/login/password/', PasswordLoginView.as_view(), name='api_login_password'),

    # JWT Token Management
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/token/verify/', TokenVerifyView.as_view(), name='token_verify'),

    # Profile & Session Management
    path('api/profile/', UserProfileView.as_view(), name='api_user_profile'),
    path('api/logout/', LogoutView.as_view(), name='api_logout'),

    # Family Member Profiles
    path('api/family-members/', FamilyMemberListCreateView.as_view(), name='api_family_members_list_create'),
    path('api/family-members/<int:pk>/', FamilyMemberDetailView.as_view(), name='api_family_member_detail'),

    # Short aliases for convenience
    path('api/send-otp/', RegisterSendOTPView.as_view(), name='api_send_otp_alias'),
    path('api/verify-otp/', RegisterVerifyOTPView.as_view(), name='api_verify_otp_alias'),
    path('api/resend-otp/', RegisterResendOTPView.as_view(), name='api_resend_otp_alias'),
]

