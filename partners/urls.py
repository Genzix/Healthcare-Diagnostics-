from django.urls import path
from .views import (
    PartnerListView,
    PartnerDetailView,
    PartnerSlotListView,
    CheckServiceabilityView,
    PartnerProfileManageView,
    PartnerServiceAreaListCreateView,
    PartnerServiceAreaDeleteView,
    PartnerSlotListCreateView,
    PartnerTestPricingManageView,
    PartnerPackagePricingManageView,
    PartnerDashboardSummaryView,
    AdminPartnerListCreateView,
    AdminPartnerDetailView,
    PartnerLoginView,
    PartnerRegistrationView,
)

app_name = 'partners'

urlpatterns = [
    # Public Client Endpoints
    path('api/partners/', PartnerListView.as_view(), name='api_partners_list'),
    path('api/partners/<int:pk>/', PartnerDetailView.as_view(), name='api_partner_detail'),
    path('api/partners/<int:pk>/slots/', PartnerSlotListView.as_view(), name='api_partner_slots'),
    path('api/partners/check-serviceability/', CheckServiceabilityView.as_view(), name='api_check_serviceability'),

    # Admin Partner Management Endpoints
    path('api/admin/partners/', AdminPartnerListCreateView.as_view(), name='api_admin_partners_list_create'),
    path('api/admin/partners/<int:pk>/', AdminPartnerDetailView.as_view(), name='api_admin_partner_detail'),

    # Partner Auth & Dashboard Endpoints
    path('api/partner/register/', PartnerRegistrationView.as_view(), name='api_partner_register'),
    path('api/partner/register', PartnerRegistrationView.as_view(), name='api_partner_register_noslash'),
    path('api/partner/login/', PartnerLoginView.as_view(), name='api_partner_login'),
    path('api/partner/login', PartnerLoginView.as_view(), name='api_partner_login_noslash'),
    path('api/partner/dashboard/summary/', PartnerDashboardSummaryView.as_view(), name='api_partner_dashboard_summary'),
    path('api/partner/centre-profile/', PartnerProfileManageView.as_view(), name='api_partner_profile'),
    path('api/partner/service-areas/', PartnerServiceAreaListCreateView.as_view(), name='api_partner_service_areas'),
    path('api/partner/service-areas/<int:pk>/', PartnerServiceAreaDeleteView.as_view(), name='api_partner_service_area_delete'),
    path('api/partner/slots/', PartnerSlotListCreateView.as_view(), name='api_partner_slots_manage'),
    path('api/partner/tests/', PartnerTestPricingManageView.as_view(), name='api_partner_tests_manage'),
    path('api/partner/packages/', PartnerPackagePricingManageView.as_view(), name='api_partner_packages_manage'),
]

