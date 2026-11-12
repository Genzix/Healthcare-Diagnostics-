from django.urls import path
from .views import (
    CategoryListView,
    DiagnosticTestListView,
    DiagnosticTestDetailView,
    HealthPackageListView,
    HealthPackageDetailView,
)

app_name = 'diagnostics'

urlpatterns = [
    # Categories
    path('api/categories/', CategoryListView.as_view(), name='api_categories_list'),

    # Individual Tests
    path('api/tests/', DiagnosticTestListView.as_view(), name='api_tests_list'),
    path('api/tests/<int:pk>/', DiagnosticTestDetailView.as_view(), name='api_test_detail'),

    # Health Packages
    path('api/packages/', HealthPackageListView.as_view(), name='api_packages_list'),
    path('api/packages/<int:pk>/', HealthPackageDetailView.as_view(), name='api_package_detail'),

    # Admin Management Endpoints
    path('api/admin/tests/', DiagnosticTestListView.as_view(), name='api_admin_tests_list_create'),
    path('api/admin/tests/<int:pk>/', DiagnosticTestDetailView.as_view(), name='api_admin_test_detail'),
    path('api/admin/packages/', HealthPackageListView.as_view(), name='api_admin_packages_list_create'),
    path('api/admin/packages/<int:pk>/', HealthPackageDetailView.as_view(), name='api_admin_package_detail'),
]

