from django.urls import path
from .views import (
    PriceEstimateView,
    BookingListCreateView,
    BookingDetailView,
    PayAdvanceView,
    PayBalanceView,
    BookingCancelView,
    NotificationListView,
    NotificationMarkReadView,
    PartnerBookingsListView,
    PartnerAcceptBookingView,
    PartnerRejectBookingView,
    PartnerUpdateStatusView,
    PartnerUploadReportView,
    PartnerRecordBalancePaymentView,
    BookingReportDownloadView,
    AdminDashboardMetricsView,
    AdminAllBookingsListView,
    AdminPartnerApprovalView,
    BookingWorkflowView,
    PayAtCollectionView,
    PartnerBookingDetailView,
)

app_name = 'bookings'

urlpatterns = [
    # Patient Booking Flow
    path('api/bookings/price-estimate/', PriceEstimateView.as_view(), name='api_booking_price_estimate'),
    path('api/bookings/workflow/', BookingWorkflowView.as_view(), name='api_booking_workflow'),
    path('api/bookings/', BookingListCreateView.as_view(), name='api_bookings_list_create'),
    path('api/bookings/<int:pk>/', BookingDetailView.as_view(), name='api_booking_detail_int'),
    path('api/bookings/<str:pk>/', BookingDetailView.as_view(), name='api_booking_detail_str'),
    path('api/bookings/<int:pk>/pay-advance/', PayAdvanceView.as_view(), name='api_booking_pay_advance_int'),
    path('api/bookings/<str:pk>/pay-advance/', PayAdvanceView.as_view(), name='api_booking_pay_advance_str'),
    path('api/bookings/<int:pk>/pay-at-collection/', PayAtCollectionView.as_view(), name='api_booking_pay_at_collection_int'),
    path('api/bookings/<str:pk>/pay-at-collection/', PayAtCollectionView.as_view(), name='api_booking_pay_at_collection_str'),
    path('api/bookings/<int:pk>/pay-balance/', PayBalanceView.as_view(), name='api_booking_pay_balance_int'),
    path('api/bookings/<str:pk>/pay-balance/', PayBalanceView.as_view(), name='api_booking_pay_balance_str'),
    path('api/bookings/<int:pk>/cancel/', BookingCancelView.as_view(), name='api_booking_cancel_int'),
    path('api/bookings/<str:pk>/cancel/', BookingCancelView.as_view(), name='api_booking_cancel_str'),

    # Patient Report Viewing & 1MinDoc Branded PDF Download
    path('api/bookings/<int:pk>/report/', BookingReportDownloadView.as_view(), name='api_booking_report_int'),
    path('api/bookings/<str:pk>/report/', BookingReportDownloadView.as_view(), name='api_booking_report_str'),
    path('api/bookings/<int:pk>/download-report/', BookingReportDownloadView.as_view(), name='api_booking_download_report_int'),
    path('api/bookings/<str:pk>/download-report/', BookingReportDownloadView.as_view(), name='api_booking_download_report_str'),

    # Notifications
    path('api/notifications/', NotificationListView.as_view(), name='api_notifications_list'),
    path('api/notifications/<int:pk>/mark-read/', NotificationMarkReadView.as_view(), name='api_notification_mark_read'),

    # Diagnostic Partner Dashboard
    path('api/partner/bookings/', PartnerBookingsListView.as_view(), name='api_partner_bookings_list'),
    path('api/partner/bookings/<int:pk>/', PartnerBookingDetailView.as_view(), name='api_partner_booking_detail_int'),
    path('api/partner/bookings/<str:pk>/', PartnerBookingDetailView.as_view(), name='api_partner_booking_detail_str'),
    path('api/partner/bookings/<int:pk>/accept/', PartnerAcceptBookingView.as_view(), name='api_partner_booking_accept_int'),
    path('api/partner/bookings/<str:pk>/accept/', PartnerAcceptBookingView.as_view(), name='api_partner_booking_accept_str'),
    path('api/partner/bookings/<int:pk>/reject/', PartnerRejectBookingView.as_view(), name='api_partner_booking_reject_int'),
    path('api/partner/bookings/<str:pk>/reject/', PartnerRejectBookingView.as_view(), name='api_partner_booking_reject_str'),
    path('api/partner/bookings/<int:pk>/update-status/', PartnerUpdateStatusView.as_view(), name='api_partner_booking_update_status_int'),
    path('api/partner/bookings/<str:pk>/update-status/', PartnerUpdateStatusView.as_view(), name='api_partner_booking_update_status_str'),
    path('api/partner/bookings/<int:pk>/upload-report/', PartnerUploadReportView.as_view(), name='api_partner_booking_upload_report_int'),
    path('api/partner/bookings/<str:pk>/upload-report/', PartnerUploadReportView.as_view(), name='api_partner_booking_upload_report_str'),
    path('api/partner/bookings/<int:pk>/record-balance/', PartnerRecordBalancePaymentView.as_view(), name='api_partner_booking_record_balance_int'),
    path('api/partner/bookings/<str:pk>/record-balance/', PartnerRecordBalancePaymentView.as_view(), name='api_partner_booking_record_balance_str'),

    # Admin Panel
    path('api/admin/dashboard/metrics/', AdminDashboardMetricsView.as_view(), name='api_admin_dashboard_metrics'),
    path('api/admin/bookings/', AdminAllBookingsListView.as_view(), name='api_admin_bookings_list'),
    path('api/admin/partners/<int:pk>/approve/', AdminPartnerApprovalView.as_view(), name='api_admin_partner_approve'),
]

