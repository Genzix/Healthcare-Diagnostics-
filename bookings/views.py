from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser, AllowAny
from django.utils import timezone
from django.db.models import Sum, Count, Q
from decimal import Decimal

from .models import Booking, BookingItem, Payment, Notification
from .serializers import (
    PriceEstimateRequestSerializer,
    BookingCreateSerializer,
    BookingDetailSerializer,
    BookingListSerializer,
    PaymentSerializer,
    AdvancePaymentRequestSerializer,
    BalancePaymentRequestSerializer,
    PartnerRejectBookingSerializer,
    PartnerStatusUpdateSerializer,
    PartnerOfflineBalancePaymentSerializer,
    BookingCancelSerializer,
    NotificationSerializer,
)
from .services import (
    calculate_booking_pricing,
    create_booking_order,
    process_advance_payment,
    partner_accept_booking,
    partner_reject_booking,
    partner_update_service_status,
    process_balance_payment,
    send_system_notification,
)
from partners.models import DiagnosticPartner


# =====================================================================
# Patient / User Booking Views
# =====================================================================

def get_user_booking(user, pk):
    if str(pk).isdigit():
        return Booking.objects.filter(user=user, pk=pk).first()
    return Booking.objects.filter(user=user, booking_id=pk).first()


class PriceEstimateView(APIView):
    """
    API endpoint: Pre-booking price summary & 30% advance / 70% balance calculation.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = PriceEstimateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        partner_id = serializer.validated_data['partner_id']
        try:
            partner = DiagnosticPartner.objects.get(pk=partner_id, status='ACTIVE')
        except DiagnosticPartner.DoesNotExist:
            return Response({"success": False, "message": "Diagnostic partner centre not found or not active."}, status=status.HTTP_404_NOT_FOUND)

        pricing, error = calculate_booking_pricing(
            partner=partner,
            test_ids=serializer.validated_data.get('test_ids', []),
            package_ids=serializer.validated_data.get('package_ids', []),
            service_mode=serializer.validated_data['service_mode'],
            pincode=serializer.validated_data.get('pincode')
        )
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"success": True, "estimate": pricing}, status=status.HTTP_200_OK)


class BookingListCreateView(APIView):
    """
    API endpoint: List user's bookings & create a new booking order.
    Query filters:
      - status: specific booking status
      - view: 'upcoming', 'completed', 'cancelled'
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        queryset = Booking.objects.filter(user=request.user).select_related('partner', 'slot')

        view_mode = request.query_params.get('view')
        if view_mode == 'upcoming':
            queryset = queryset.filter(
                booking_status__in=['AWAITING_ADVANCE_PAYMENT', 'AWAITING_PARTNER_CONFIRMATION', 'CONFIRMED', 'SAMPLE_COLLECTED']
            )
        elif view_mode == 'completed':
            queryset = queryset.filter(booking_status='TEST_COMPLETED')
        elif view_mode == 'cancelled':
            queryset = queryset.filter(booking_status__in=['CANCELLED', 'REJECTED_BY_PARTNER'])

        booking_status = request.query_params.get('status')
        if booking_status:
            queryset = queryset.filter(booking_status=booking_status)

        serializer = BookingListSerializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "count": queryset.count(),
                "bookings": serializer.data,
            },
            status=status.HTTP_200_OK
        )

    def post(self, request, *args, **kwargs):
        serializer = BookingCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        booking, error = create_booking_order(user=request.user, data=serializer.validated_data)
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "success": True,
                "message": "Booking order created. Please pay 30% advance to submit your booking request.",
                "booking": BookingDetailSerializer(booking).data,
            },
            status=status.HTTP_201_CREATED
        )


class BookingDetailView(APIView):
    """
    API endpoint: Retrieve detailed booking breakdown, payment history, and items.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk, *args, **kwargs):
        booking = get_user_booking(request.user, pk)
        if not booking:
            return Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            {
                "success": True,
                "booking": BookingDetailSerializer(booking).data,
            },
            status=status.HTTP_200_OK
        )


class PayAdvanceView(APIView):
    """
    API endpoint: Collect 30% advance payment for booking.
    Rule: After payment, status becomes 'AWAITING_PARTNER_CONFIRMATION'.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        booking = get_user_booking(request.user, pk)
        if not booking:
            return Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = AdvancePaymentRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        payment, error = process_advance_payment(
            booking=booking,
            payment_mode=serializer.validated_data.get('payment_mode', 'ONLINE'),
            gateway_transaction_id=serializer.validated_data.get('gateway_transaction_id')
        )
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "success": True,
                "message": "Advance payment of 30% successful. Booking sent to partner centre awaiting confirmation.",
                "payment": PaymentSerializer(payment).data,
                "booking": BookingDetailSerializer(booking).data,
            },
            status=status.HTTP_200_OK
        )


class PayBalanceView(APIView):
    """
    API endpoint: Collect 70% balance payment online after test completion.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        booking = get_user_booking(request.user, pk)
        if not booking:
            return Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = BalancePaymentRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        payment, error = process_balance_payment(
            booking=booking,
            payment_mode=serializer.validated_data.get('payment_mode', 'ONLINE'),
            gateway_transaction_id=serializer.validated_data.get('gateway_transaction_id'),
            recorded_by_partner=False
        )
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "success": True,
                "message": "70% balance paid successfully. Booking fully settled.",
                "payment": PaymentSerializer(payment).data,
                "booking": BookingDetailSerializer(booking).data,
            },
            status=status.HTTP_200_OK
        )


class BookingCancelView(APIView):
    """
    API endpoint: Cancel booking with a reason.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        booking = get_user_booking(request.user, pk)
        if not booking:
            return Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

        if booking.booking_status in ['SAMPLE_COLLECTED', 'TEST_COMPLETED', 'CANCELLED', 'REJECTED_BY_PARTNER']:
            return Response(
                {"success": False, "message": f"Booking in status '{booking.booking_status}' cannot be cancelled."},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = BookingCancelSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        booking.booking_status = 'CANCELLED'
        booking.cancelled_by = 'USER'
        booking.cancellation_reason = serializer.validated_data['cancellation_reason']
        booking.cancellation_time = timezone.now()

        # If advance was paid, mark refund
        if booking.advance_amount_paid > Decimal('0.00'):
            booking.payment_status = 'REFUNDED'
            Payment.objects.create(
                payment_id=f"REF-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}",
                booking=booking,
                payment_type='REFUND',
                amount=booking.advance_amount_paid,
                payment_mode='ONLINE',
                status='SUCCESS',
                gateway_transaction_id=f"REFUND-{uuid.uuid4().hex[:8].upper()}"
            )

        # Release slot capacity
        if booking.slot and booking.slot.booked_count > 0:
            booking.slot.booked_count -= 1
            booking.slot.save(update_fields=['booked_count'])

        booking.save()

        send_system_notification(
            user=request.user,
            booking=booking,
            notification_type='PAYMENT_RESULT',
            title='Booking Cancelled',
            message=f"Booking {booking.booking_id} has been cancelled. Refund will be credited within 3-5 working days."
        )

        return Response(
            {
                "success": True,
                "message": "Booking cancelled successfully.",
                "booking": BookingDetailSerializer(booking).data,
            },
            status=status.HTTP_200_OK
        )


class NotificationListView(APIView):
    """
    API endpoint: View user's notifications (OTP, payments, partner confirmation, reminders).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        notifications = Notification.objects.filter(user=request.user)
        serializer = NotificationSerializer(notifications, many=True)
        return Response(
            {
                "success": True,
                "unread_count": notifications.filter(is_read=False).count(),
                "notifications": serializer.data,
            },
            status=status.HTTP_200_OK
        )


class NotificationMarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        try:
            notif = Notification.objects.get(pk=pk, user=request.user)
            notif.is_read = True
            notif.save(update_fields=['is_read'])
            return Response({"success": True, "message": "Notification marked as read."}, status=status.HTTP_200_OK)
        except Notification.DoesNotExist:
            return Response({"success": False, "message": "Notification not found."}, status=status.HTTP_404_NOT_FOUND)


# =====================================================================
# Diagnostic Partner Dashboard Views
# =====================================================================

def get_authenticated_partner(user):
    if hasattr(user, 'partner_profile'):
        return user.partner_profile
    return None


def get_partner_booking(partner, pk):
    if str(pk).isdigit():
        return Booking.objects.filter(partner=partner, pk=pk).first()
    return Booking.objects.filter(partner=partner, booking_id=pk).first()


class PartnerBookingsListView(APIView):
    """
    Partner Dashboard: View incoming and assigned booking requests with patient and service details.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        partner = get_authenticated_partner(request.user)
        if not partner:
            return Response({"success": False, "message": "Diagnostic partner profile required."}, status=status.HTTP_403_FORBIDDEN)

        queryset = Booking.objects.filter(partner=partner).prefetch_related('items', 'payments')

        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(booking_status=status_filter)

        serializer = BookingDetailSerializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "count": queryset.count(),
                "bookings": serializer.data,
            },
            status=status.HTTP_200_OK
        )


class PartnerAcceptBookingView(APIView):
    """
    Partner Dashboard: Accept booking request -> Confirmed.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        partner = get_authenticated_partner(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)

        booking = get_partner_booking(partner, pk)
        if not booking:
            return Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

        booking, error = partner_accept_booking(booking=booking, partner_user=request.user)
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {"success": True, "message": f"Booking {booking.booking_id} confirmed successfully.", "booking": BookingDetailSerializer(booking).data},
            status=status.HTTP_200_OK
        )


class PartnerRejectBookingView(APIView):
    """
    Partner Dashboard: Reject booking request with reason -> Triggers refund & notification.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        partner = get_authenticated_partner(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)

        booking = get_partner_booking(partner, pk)
        if not booking:
            return Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = PartnerRejectBookingSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        reason = serializer.validated_data['rejection_reason']
        booking, error = partner_reject_booking(booking=booking, partner_user=request.user, reason=reason)
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {"success": True, "message": f"Booking {booking.booking_id} rejected. Refund initiated.", "booking": BookingDetailSerializer(booking).data},
            status=status.HTTP_200_OK
        )


class PartnerUpdateStatusView(APIView):
    """
    Partner Dashboard: Update service status: Confirmed -> SAMPLE_COLLECTED -> TEST_COMPLETED.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        partner = get_authenticated_partner(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)

        booking = get_partner_booking(partner, pk)
        if not booking:
            return Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = PartnerStatusUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        new_status = serializer.validated_data['new_status']
        booking, error = partner_update_service_status(booking=booking, partner_user=request.user, new_status=new_status)
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {"success": True, "message": f"Booking status updated to {new_status}.", "booking": BookingDetailSerializer(booking).data},
            status=status.HTTP_200_OK
        )


class PartnerRecordBalancePaymentView(APIView):
    """
    Partner Dashboard: Record approved offline 70% balance collection (Cash, UPI, Card).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        partner = get_authenticated_partner(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)

        booking = get_partner_booking(partner, pk)
        if not booking:
            return Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = PartnerOfflineBalancePaymentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        payment, error = process_balance_payment(
            booking=booking,
            payment_mode=serializer.validated_data['payment_mode'],
            offline_reference=serializer.validated_data['offline_reference'],
            recorded_by_partner=True
        )
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "success": True,
                "message": "Offline balance payment recorded successfully.",
                "payment": PaymentSerializer(payment).data,
                "booking": BookingDetailSerializer(booking).data,
            },
            status=status.HTTP_200_OK
        )


# =====================================================================
# Admin Panel Views
# =====================================================================

class AdminDashboardMetricsView(APIView):
    """
    Admin Panel: Dashboard of users, bookings, booking value, advance collected, and balance outstanding.
    """
    permission_classes = [IsAdminUser]

    def get(self, request, *args, **kwargs):
        from accounts.models import User

        total_users = User.objects.count()
        total_partners = DiagnosticPartner.objects.count()
        active_partners = DiagnosticPartner.objects.filter(status='ACTIVE').count()

        bookings_qs = Booking.objects.all()
        total_bookings = bookings_qs.count()

        total_booking_value = bookings_qs.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        total_advance_collected = bookings_qs.aggregate(advance=Sum('advance_amount_paid'))['advance'] or Decimal('0.00')
        total_balance_collected = bookings_qs.aggregate(balance=Sum('balance_amount_paid'))['balance'] or Decimal('0.00')

        # Balance outstanding on active / completed bookings
        outstanding_balance = bookings_qs.exclude(
            booking_status__in=['CANCELLED', 'REJECTED_BY_PARTNER']
        ).aggregate(
            due=Sum('balance_amount_due'),
            paid=Sum('balance_amount_paid')
        )
        total_due = outstanding_balance['due'] or Decimal('0.00')
        total_paid = outstanding_balance['paid'] or Decimal('0.00')
        balance_outstanding = total_due - total_paid

        # Status breakdowns
        status_counts = dict(bookings_qs.values('booking_status').annotate(count=Count('id')).values_list('booking_status', 'count'))

        return Response(
            {
                "success": True,
                "metrics": {
                    "registered_users": total_users,
                    "diagnostic_partners": {
                        "total": total_partners,
                        "active": active_partners,
                    },
                    "bookings": {
                        "total_count": total_bookings,
                        "status_breakdown": status_counts,
                    },
                    "financials": {
                        "total_gross_booking_value": float(total_booking_value),
                        "advance_collected_30_percent": float(total_advance_collected),
                        "balance_collected_70_percent": float(total_balance_collected),
                        "total_revenue_collected": float(total_advance_collected + total_balance_collected),
                        "balance_outstanding_due": float(max(Decimal('0.00'), balance_outstanding)),
                    }
                }
            },
            status=status.HTTP_200_OK
        )


class AdminAllBookingsListView(APIView):
    """
    Admin Panel: Monitor all bookings, advance payments, outstanding balances, refunds.
    """
    permission_classes = [IsAdminUser]

    def get(self, request, *args, **kwargs):
        queryset = Booking.objects.select_related('user', 'partner').prefetch_related('items', 'payments')

        partner_id = request.query_params.get('partner_id')
        if partner_id:
            queryset = queryset.filter(partner_id=partner_id)

        booking_status = request.query_params.get('status')
        if booking_status:
            queryset = queryset.filter(booking_status=booking_status)

        payment_status = request.query_params.get('payment_status')
        if payment_status:
            queryset = queryset.filter(payment_status=payment_status)

        serializer = BookingDetailSerializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "count": queryset.count(),
                "bookings": serializer.data,
            },
            status=status.HTTP_200_OK
        )


class AdminPartnerApprovalView(APIView):
    """
    Admin Panel: Onboard, review, and activate a diagnostic partner.
    """
    permission_classes = [IsAdminUser]

    def post(self, request, pk, *args, **kwargs):
        try:
            partner = DiagnosticPartner.objects.get(pk=pk)
        except DiagnosticPartner.DoesNotExist:
            return Response({"success": False, "message": "Partner centre not found."}, status=status.HTTP_404_NOT_FOUND)

        action = request.data.get('action', 'ACTIVATE')  # ACTIVATE or SUSPEND
        if action == 'ACTIVATE':
            partner.status = 'ACTIVE'
        elif action == 'SUSPEND':
            partner.status = 'SUSPENDED'
        else:
            return Response({"success": False, "message": "Invalid action. Use 'ACTIVATE' or 'SUSPEND'."}, status=status.HTTP_400_BAD_REQUEST)

        partner.save(update_fields=['status', 'updated_at'])
        return Response(
            {
                "success": True,
                "message": f"Partner centre {partner.centre_name} status updated to {partner.status}.",
                "partner_status": partner.status,
            },
            status=status.HTTP_200_OK
        )

