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
            "partner_dashboard": {
                "summary": request.build_absolute_uri('/api/partner/dashboard/summary/'),
                "centre_profile": request.build_absolute_uri('/api/partner/centre-profile/'),
                "bookings": request.build_absolute_uri('/api/partner/bookings/'),
            },
            "admin": {
                "dashboard_metrics": request.build_absolute_uri('/api/admin/dashboard/metrics/'),
                "bookings": request.build_absolute_uri('/api/admin/bookings/'),
                "django_admin": request.build_absolute_uri('/admin/'),
            }
        }
    })

