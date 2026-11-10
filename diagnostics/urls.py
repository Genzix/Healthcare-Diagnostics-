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
]

