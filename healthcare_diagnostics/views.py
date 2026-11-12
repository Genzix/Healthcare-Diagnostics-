from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([AllowAny])
def api_root_view(request):
    """
    Project Root API Directory & Health Check.
    Provides clickable links to all service modules in the DRF browsable UI.
    """
    return Response({
        "status": "online",
        "service": "Healthcare Diagnostics API",
        "version": "1.0.0",
        "description": "REST API backend for diagnostic testing, home sample collection, partner centres, and 30%/70% advance payment flow.",
        "endpoints": {
            "authentication_and_profile": {
                "register_send_otp": request.build_absolute_uri('/api/register/send-otp/'),
                "register_verify_otp": request.build_absolute_uri('/api/register/verify-otp/'),
                "login_send_otp": request.build_absolute_uri('/api/login/send-otp/'),
                "login_verify_otp": request.build_absolute_uri('/api/login/verify-otp/'),
                "jwt_token_refresh": request.build_absolute_uri('/api/token/refresh/'),
                "jwt_token_verify": request.build_absolute_uri('/api/token/verify/'),
                "profile": request.build_absolute_uri('/api/profile/'),
                "family_members": request.build_absolute_uri('/api/family-members/'),
            },
            "health_card": {
                "health_card_detail": request.build_absolute_uri('/api/health-card/'),
                "edit_profile": request.build_absolute_uri('/api/health-card/profile/'),
                "body_metrics": request.build_absolute_uri('/api/health-card/body-metrics/'),
                "vitals": request.build_absolute_uri('/api/health-card/vitals/'),
                "daily_activity": request.build_absolute_uri('/api/health-card/activity/'),
                "sleep_tracking": request.build_absolute_uri('/api/health-card/sleep/'),
                "health_trends": request.build_absolute_uri('/api/health-card/trends/'),
                "share_link": request.build_absolute_uri('/api/health-card/share/'),
            },
            "catalogue": {
                "categories": request.build_absolute_uri('/api/categories/'),
                "tests": request.build_absolute_uri('/api/tests/'),
                "packages": request.build_absolute_uri('/api/packages/'),
            },
            "partners": {
                "partner_centres": request.build_absolute_uri('/api/partners/'),
                "check_serviceability": request.build_absolute_uri('/api/partners/check-serviceability/'),
            },
            "bookings": {
                "price_estimate": request.build_absolute_uri('/api/bookings/price-estimate/'),
                "bookings_list_create": request.build_absolute_uri('/api/bookings/'),
                "notifications": request.build_absolute_uri('/api/notifications/'),
            },
            "support_and_faqs": {
                "support_overview": request.build_absolute_uri('/api/support/'),
                "faqs": request.build_absolute_uri('/api/support/faqs/'),
                "privacy_policy": request.build_absolute_uri('/api/support/privacy-policy/'),
                "terms_and_conditions": request.build_absolute_uri('/api/support/terms-conditions/'),
                "contact_inquiry": request.build_absolute_uri('/api/support/contact/'),
            },
            "partner_dashboard": {
                "summary": request.build_absolute_uri('/api/partner/dashboard/summary/'),
                "centre_profile": request.build_absolute_uri('/api/partner/centre-profile/'),
                "bookings": request.build_absolute_uri('/api/partner/bookings/'),
            },
            "admin": {
                "login": request.build_absolute_uri('/api/admin/login/'),
                "me": request.build_absolute_uri('/api/admin/me/'),
                "dashboard_metrics": request.build_absolute_uri('/api/admin/dashboard/metrics/'),
                "users": request.build_absolute_uri('/api/admin/users/'),
                "family_members": request.build_absolute_uri('/api/admin/family-members/'),
                "bookings": request.build_absolute_uri('/api/admin/bookings/'),
                "django_admin": request.build_absolute_uri('/admin/'),
            }
        }
    })

