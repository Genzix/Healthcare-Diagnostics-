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
    # Health Card Views
    HealthCardDetailView,
    HealthCardProfileUpdateView,
    HealthCardBodyMetricsView,
    HealthCardVitalsView,
    HealthCardActivityView,
    HealthCardSleepView,
    HealthCardTrendsView,
    HealthCardShareCreateView,
    HealthCardPublicSharedView,
    HealthCardShareRevokeView,
    # Support & FAQs Views
    SupportCenterOverviewView,
    FAQListView,
    PrivacyPolicyView,
    TermsConditionsView,
    SupportContactInquiryView,
    # Admin Panel Views
    AdminUserListView,
    AdminFamilyMemberListView,
    AdminLoginView,
    AdminMeProfileView,
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

    # Health Card Modules (1MinDoc Health Card)
    path('api/health-card/', HealthCardDetailView.as_view(), name='api_health_card_detail'),
    path('api/health-card/profile/', HealthCardProfileUpdateView.as_view(), name='api_health_card_profile_update'),
    path('api/health-card/body-metrics/', HealthCardBodyMetricsView.as_view(), name='api_health_card_body_metrics'),
    path('api/health-card/vitals/', HealthCardVitalsView.as_view(), name='api_health_card_vitals'),
    path('api/health-card/activity/', HealthCardActivityView.as_view(), name='api_health_card_activity'),
    path('api/health-card/sleep/', HealthCardSleepView.as_view(), name='api_health_card_sleep'),
    path('api/health-card/trends/', HealthCardTrendsView.as_view(), name='api_health_card_trends'),
    path('api/health-card/share/', HealthCardShareCreateView.as_view(), name='api_health_card_share_create'),
    path('api/health-card/share/<str:share_token>/', HealthCardPublicSharedView.as_view(), name='api_health_card_public_shared'),
    path('api/health-card/share/<str:share_token>/revoke/', HealthCardShareRevokeView.as_view(), name='api_health_card_share_revoke'),

    # Help, Support & FAQs Endpoints (1MinDoc Support Center)
    path('api/support/', SupportCenterOverviewView.as_view(), name='api_support_overview'),
    path('api/support/faqs/', FAQListView.as_view(), name='api_support_faqs'),
    path('api/support/privacy-policy/', PrivacyPolicyView.as_view(), name='api_support_privacy_policy'),
    path('api/support/terms-conditions/', TermsConditionsView.as_view(), name='api_support_terms_conditions'),
    path('api/support/contact/', SupportContactInquiryView.as_view(), name='api_support_contact'),

    # Admin Panel Endpoints
    path('api/admin/login/', AdminLoginView.as_view(), name='api_admin_login'),
    path('api/admin/me/', AdminMeProfileView.as_view(), name='api_admin_me'),
    path('api/admin/profile/', AdminMeProfileView.as_view(), name='api_admin_profile'),
    path('api/admin/users/', AdminUserListView.as_view(), name='api_admin_users_list'),
    path('api/admin/family-members/', AdminFamilyMemberListView.as_view(), name='api_admin_family_members_list'),

    # Short aliases for convenience
    path('api/send-otp/', RegisterSendOTPView.as_view(), name='api_send_otp_alias'),
    path('api/verify-otp/', RegisterVerifyOTPView.as_view(), name='api_verify_otp_alias'),
    path('api/resend-otp/', RegisterResendOTPView.as_view(), name='api_resend_otp_alias'),
]

