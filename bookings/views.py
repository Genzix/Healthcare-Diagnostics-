import logging
import os
import re

from django.db.models import Sum, Count, Q, Prefetch
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated, IsAdminUser, AllowAny
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from decimal import Decimal

from . import workflow as wf
from .models import Booking, BookingItem, BookingStatusLog, Payment, Notification
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
    GroupBookingCreateSerializer,
    group_summary,
)
from .services import (
    WorkflowError,
    calculate_booking_pricing,
    create_booking_order,
    process_advance_payment,
    partner_accept_booking,
    partner_reject_booking,
    transition_booking,
    upload_report,
    record_report_download,
    report_access,
    patient_report_state,
    cancel_booking,
    choose_pay_at_collection,
    pay_at_collection_eligibility,
    process_balance_payment,
    send_system_notification,
    public_pricing,
    create_group_booking_order,
    pay_group_advance,
    choose_group_pay_at_collection,
    cancel_group,
)
from partners.models import DiagnosticPartner

logger = logging.getLogger(__name__)


# =====================================================================
# Shared helpers
# =====================================================================

def booking_queryset():
    return (
        Booking.objects
        .select_related('partner', 'slot', 'user')
        .prefetch_related('items', 'payments', Prefetch('status_logs', queryset=BookingStatusLog.objects.order_by('created_at', 'id')))
    )


def filter_by_status(queryset, value):
    """
    ?status= accepts a canonical status, a comma list, or a group name:
      ?status=LAB_ANALYSIS   ?status=SAMPLE_RECEIVED,LAB_ANALYSIS   ?status=in_lab
    Unknown values match nothing (rather than silently returning everything).
    """
    if not value or value.strip().lower() in ('all', 'all statuses'):
        return queryset
    wanted = set()
    for part in value.split(','):
        part = part.strip()
        if not part:
            continue
        group = wf.STATUS_GROUPS.get(part.lower())
        wanted |= set(group) if group else {wf.normalize(part)}
    return queryset.filter(booking_status__in=wanted)


def workflow_error_response(exc: WorkflowError):
    return Response(
        {"success": False, "message": exc.message, "code": exc.code, "allowed_next_statuses": list(exc.allowed)},
        status=exc.http_status,
    )


def booking_payload(booking, audience, user=None, **extra):
    fresh = booking_queryset().get(pk=booking.pk)
    return {"success": True, "booking": BookingDetailSerializer(fresh, context={'audience': audience, 'user': user}).data, **extra}


def get_user_booking(user, pk):
    qs = booking_queryset().filter(user=user)
    return qs.filter(pk=pk).first() if str(pk).isdigit() else qs.filter(booking_id=pk).first()


# =====================================================================
# Patient / User Booking Views
# =====================================================================

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
            pincode=serializer.validated_data.get('pincode'),
            patient_count=serializer.validated_data.get('patient_count', 1),
        )
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)
        pricing = public_pricing(pricing)

        # Pay-at-collection is an optional add-on to the estimate: if the eligibility
        # check fails, still return the price and fail closed (advance-only) rather
        # than blocking checkout. The booking endpoint re-checks eligibility anyway.
        try:
            ok, reason = pay_at_collection_eligibility(partner, pricing['total_amount'], request.user)
        except Exception:
            logger.exception("pay_at_collection_eligibility failed (partner=%s, user=%s)", partner.pk, getattr(request.user, 'pk', None))
            ok, reason = False, "Pay at collection is unavailable right now. Please pay the advance online."
        pricing['pay_at_collection'] = {'available': ok, 'reason': reason}
        return Response({"success": True, "estimate": pricing}, status=status.HTTP_200_OK)



class BookingListCreateView(APIView):
    """
    GET  /api/bookings/?view=upcoming|completed|cancelled&status=<status|group|a,b>
    POST /api/bookings/
    """
    permission_classes = [IsAuthenticated]

    VIEW_GROUPS = {'upcoming': 'active', 'active': 'active', 'completed': 'completed', 'cancelled': 'cancelled', 'in_lab': 'in_lab'}

    def get(self, request, *args, **kwargs):
        queryset = booking_queryset().filter(user=request.user)
        view_mode = request.query_params.get('view')
        if view_mode in self.VIEW_GROUPS:
            queryset = filter_by_status(queryset, self.VIEW_GROUPS[view_mode])
        queryset = filter_by_status(queryset, request.query_params.get('status'))

        # Full (patient-audience) payload: the tracking UI needs history + report state per row.
        # ?compact=1 keeps the slim list shape for lightweight clients.
        if request.query_params.get('compact') in ('1', 'true'):
            serializer = BookingListSerializer(queryset, many=True)
        else:
            serializer = BookingDetailSerializer(queryset, many=True, context={'audience': wf.PATIENT})
        return Response({"success": True, "count": len(serializer.data), "bookings": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        serializer = BookingCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        booking, error = create_booking_order(user=request.user, data=serializer.validated_data)
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            booking_payload(booking, wf.PATIENT, user=request.user, message="Booking order created. Pay the 30% advance online, or choose pay at collection."),
            status=status.HTTP_201_CREATED,
        )


class BookingDetailView(APIView):
    """GET /api/bookings/<id>/ — breakdown, payments, items and the full status timeline."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk, *args, **kwargs):
        booking = get_user_booking(request.user, pk)
        if not booking:
            return Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"success": True, "booking": BookingDetailSerializer(booking, context={'audience': wf.PATIENT, 'user': request.user}).data})


class PayAtCollectionView(APIView):
    """
    POST /api/bookings/<id>/pay-at-collection/
    Submit the booking without an online advance; the full amount is paid in cash / UPI
    to the collector or at the centre (or online later). Eligibility is enforced server-side.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        booking = get_user_booking(request.user, pk)
        if not booking:
            return Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        booking, error = choose_pay_at_collection(booking, request.user)
        if error:
            return Response({"success": False, "message": error, "code": "pay_at_collection_unavailable"}, status=status.HTTP_400_BAD_REQUEST)
        return Response(booking_payload(booking, wf.PATIENT, user=request.user,
                                        message="Booking request sent. Pay at collection or online after the lab confirms."))


class PayAdvanceView(APIView):
    """POST /api/bookings/<id>/pay-advance/ — 30% advance → AWAITING_PARTNER_CONFIRMATION."""
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
            gateway_transaction_id=serializer.validated_data.get('gateway_transaction_id') or request.data.get('transaction_id'),
        )
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(booking_payload(
            booking, wf.PATIENT,
            message="Advance payment of 30% successful. Booking sent to partner centre awaiting confirmation.",
            payment=PaymentSerializer(payment).data,
        ))


class PayBalanceView(APIView):
    """POST /api/bookings/<id>/pay-balance/ — 70% balance, payable once the sample is collected."""
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
            gateway_transaction_id=serializer.validated_data.get('gateway_transaction_id') or request.data.get('transaction_id'),
            recorded_by_partner=False,
        )
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(booking_payload(
            booking, wf.PATIENT, message="Payment successful. Booking fully paid.",
            payment=PaymentSerializer(payment).data,
        ))


class BookingCancelView(APIView):
    """POST /api/bookings/<id>/cancel/ — body: cancellation_reason (alias: reason). Only before sample collection."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        booking = get_user_booking(request.user, pk)
        if not booking:
            return Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

        reason = request.data.get('cancellation_reason') or request.data.get('reason') or ''
        serializer = BookingCancelSerializer(data={'cancellation_reason': reason})
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        booking, error = cancel_booking(booking, request.user, serializer.validated_data['cancellation_reason'])
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)
        return Response(booking_payload(booking, wf.PATIENT, message="Booking cancelled successfully."))


# =====================================================================
# Household orders: several patients, one address, one slot, one visit
# =====================================================================

def group_payload(user, group_ref, **extra):
    rows = list(booking_queryset().filter(user=user, group_ref=group_ref).exclude(group_ref='').order_by('pk'))
    if not rows:
        return None
    ctx = {'audience': wf.PATIENT, 'user': user}
    return {
        "success": True,
        "group": group_summary(rows, user),
        "bookings": BookingDetailSerializer(rows, many=True, context=ctx).data,
        **extra,
    }


def _not_found():
    return Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)


class GroupBookingCreateView(APIView):
    """
    POST /api/bookings/group/
    Same body as POST /api/bookings/ plus `patients: [{patient_type, family_member_id}]`.
    Creates one booking per patient (all-or-nothing) linked by `group_ref`.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = GroupBookingCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        try:
            bookings, group_ref = create_group_booking_order(request.user, serializer.validated_data)
        except WorkflowError as exc:
            return workflow_error_response(exc)
        return Response(
            group_payload(request.user, group_ref, message=f"Booking created for {len(bookings)} patients. "
                                                          "Pay the 30% advance online, or choose pay at collection."),
            status=status.HTTP_201_CREATED,
        )


class GroupBookingDetailView(APIView):
    """GET /api/bookings/group/<group_ref>/ — order summary plus every patient's booking."""
    permission_classes = [IsAuthenticated]

    def get(self, request, group_ref, *args, **kwargs):
        payload = group_payload(request.user, group_ref)
        return Response(payload) if payload else _not_found()


class GroupPayAdvanceView(APIView):
    """POST /api/bookings/group/<group_ref>/pay-advance/ — one transaction, every patient's 30%."""
    permission_classes = [IsAuthenticated]

    def post(self, request, group_ref, *args, **kwargs):
        serializer = AdvancePaymentRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        try:
            _, payments = pay_group_advance(
                request.user, group_ref,
                payment_mode=serializer.validated_data.get('payment_mode', 'ONLINE'),
                gateway_transaction_id=serializer.validated_data.get('gateway_transaction_id') or request.data.get('transaction_id'),
            )
        except WorkflowError as exc:
            return workflow_error_response(exc)
        return Response(group_payload(
            request.user, group_ref,
            message="Advance payment successful. Booking sent to the partner centre for confirmation.",
            payments=PaymentSerializer(payments, many=True).data,
        ))


class GroupPayAtCollectionView(APIView):
    """POST /api/bookings/group/<group_ref>/pay-at-collection/ — submit the order, pay everything at collection."""
    permission_classes = [IsAuthenticated]

    def post(self, request, group_ref, *args, **kwargs):
        try:
            choose_group_pay_at_collection(request.user, group_ref)
        except WorkflowError as exc:
            return workflow_error_response(exc)
        return Response(group_payload(request.user, group_ref,
                                      message="Booking request sent. Pay at collection or online after the lab confirms."))


class GroupCancelView(APIView):
    """POST /api/bookings/group/<group_ref>/cancel/ — cancel every patient still before sample collection."""
    permission_classes = [IsAuthenticated]

    def post(self, request, group_ref, *args, **kwargs):
        reason = request.data.get('cancellation_reason') or request.data.get('reason') or ''
        serializer = BookingCancelSerializer(data={'cancellation_reason': reason})
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        try:
            cancel_group(request.user, group_ref, serializer.validated_data['cancellation_reason'])
        except WorkflowError as exc:
            return workflow_error_response(exc)
        return Response(group_payload(request.user, group_ref, message="Booking cancelled successfully."))


class NotificationListView(APIView):
    """GET /api/notifications/?unread=1"""
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        notifications = Notification.objects.filter(user=request.user).select_related('booking')
        unread_count = notifications.filter(is_read=False).count()
        if request.query_params.get('unread') in ('1', 'true'):
            notifications = notifications.filter(is_read=False)
        return Response({
            "success": True,
            "unread_count": unread_count,
            "notifications": NotificationSerializer(notifications[:200], many=True).data,
        })


class NotificationMarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        updated = Notification.objects.filter(pk=pk, user=request.user).update(is_read=True)
        if not updated:
            return Response({"success": False, "message": "Notification not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"success": True, "message": "Notification marked as read."})


# =====================================================================
# Diagnostic Partner Dashboard Views
# =====================================================================

def get_authenticated_partner(user):
    if not user or not user.is_authenticated:
        return None
    try:
        if hasattr(user, 'partner_profile') and user.partner_profile:
            return user.partner_profile
    except Exception:
        pass
    return DiagnosticPartner.objects.filter(user=user).first()


def get_partner_booking(partner, pk):
    qs = booking_queryset().filter(partner=partner)
    return qs.filter(pk=pk).first() if str(pk).isdigit() else qs.filter(booking_id=pk).first()


class PartnerBookingView(APIView):
    """Base: resolves the partner and the booking, 403/404 otherwise."""
    permission_classes = [IsAuthenticated]

    def resolve(self, request, pk):
        partner = get_authenticated_partner(request.user)
        if not partner:
            return None, None, Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)
        booking = get_partner_booking(partner, pk)
        if not booking:
            return partner, None, Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return partner, booking, None


class PartnerBookingsListView(APIView):
    """GET /api/partner/bookings/?status=<status|group|a,b>&search=…"""
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        partner = get_authenticated_partner(request.user)
        if not partner:
            return Response({"success": False, "message": "Diagnostic partner profile required."}, status=status.HTTP_403_FORBIDDEN)

        queryset = filter_by_status(booking_queryset().filter(partner=partner), request.query_params.get('status'))
        search = (request.query_params.get('search') or '').strip()
        if search:
            queryset = queryset.filter(Q(booking_id__icontains=search) | Q(patient_name__icontains=search) | Q(patient_phone__icontains=search))

        data = BookingDetailSerializer(queryset, many=True, context={'audience': wf.PARTNER}).data
        return Response({"success": True, "count": len(data), "bookings": data})


class PartnerBookingDetailView(PartnerBookingView):
    """GET /api/partner/bookings/<id>/"""

    def get(self, request, pk, *args, **kwargs):
        _, booking, err = self.resolve(request, pk)
        if err:
            return err
        return Response({"success": True, "booking": BookingDetailSerializer(booking, context={'audience': wf.PARTNER}).data})


class PartnerAcceptBookingView(PartnerBookingView):
    def post(self, request, pk, *args, **kwargs):
        _, booking, err = self.resolve(request, pk)
        if err:
            return err
        booking, error = partner_accept_booking(booking=booking, partner_user=request.user)
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)
        return Response(booking_payload(booking, wf.PARTNER, message=f"Booking {booking.booking_id} confirmed successfully."))


class PartnerRejectBookingView(PartnerBookingView):
    def post(self, request, pk, *args, **kwargs):
        _, booking, err = self.resolve(request, pk)
        if err:
            return err
        serializer = PartnerRejectBookingSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        booking, error = partner_reject_booking(booking=booking, partner_user=request.user, reason=serializer.validated_data['rejection_reason'])
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)
        return Response(booking_payload(booking, wf.PARTNER, message=f"Booking {booking.booking_id} rejected. Refund initiated."))


class PartnerUpdateStatusView(PartnerBookingView):
    """
    POST /api/partner/bookings/<id>/update-status/   (JSON, or multipart when uploading)

    Moves the booking one step: SAMPLE_COLLECTED → SAMPLE_RECEIVED → LAB_ANALYSIS →
    REPORT_GENERATED → REPORT_UPLOADED → REPORT_VERIFIED → REPORT_READY.
    Errors: 400 invalid step / missing requirement, 403 wrong actor, 409 stale `expected_status`.
    """
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request, pk, *args, **kwargs):
        _, booking, err = self.resolve(request, pk)
        if err:
            return err
        serializer = PartnerStatusUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        v = serializer.validated_data
        target = v['new_status']
        report_file = request.FILES.get('report_file') or v.get('report_file')

        try:
            if target == wf.REPORT_UPLOADED:
                upload_report(booking, request.user, report_file, report_name=v.get('report_name', ''),
                              report_notes=v.get('report_notes', ''), notes=v.get('notes', ''),
                              expected_status=v.get('expected_status'))
            else:
                transition_booking(
                    booking, target, actor=request.user, actor_role=wf.PARTNER,
                    notes=v.get('notes', ''), expected_status=v.get('expected_status'),
                    report_notes=v.get('report_notes'),
                    verified_by_name=v.get('verified_by_name', ''),
                    verified_by_registration=v.get('verified_by_registration', ''),
                )
        except WorkflowError as exc:
            return workflow_error_response(exc)

        return Response(booking_payload(
            booking, wf.PARTNER,
            message=f"Booking {booking.booking_id} moved to {wf.LABELS.get(booking.booking_status)}.",
        ))


class PartnerUploadReportView(PartnerBookingView):
    """
    POST /api/partner/bookings/<id>/upload-report/   multipart: report_file (PDF), report_notes, report_name
    Allowed from LAB_ANALYSIS (records REPORT_GENERATED first), REPORT_GENERATED, or
    REPORT_UPLOADED (replaces the file and clears any earlier verification).
    The patient is NOT notified here — only on release after verification.
    """
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk, *args, **kwargs):
        _, booking, err = self.resolve(request, pk)
        if err:
            return err
        try:
            upload_report(
                booking, request.user, request.FILES.get('report_file'),
                report_name=request.data.get('report_name', ''),
                report_notes=request.data.get('report_notes', ''),
                notes=request.data.get('notes', ''),
                expected_status=request.data.get('expected_status') or None,
            )
        except WorkflowError as exc:
            return workflow_error_response(exc)
        return Response(booking_payload(booking, wf.PARTNER, message=f"Report uploaded for {booking.booking_id}. It must be verified before release."))


_SAFE_NAME = re.compile(r'[^A-Za-z0-9._-]+')


class PDFRenderer(BaseRenderer):
    """
    Lets DRF content negotiation accept `Accept: application/pdf`.

    It never renders anything itself: the file goes out as a FileResponse, which
    skips DRF rendering. Without it, DRF only knows JSON and answers 406
    before the view runs.
    """
    media_type = 'application/pdf'
    format = 'pdf'
    charset = None
    render_style = 'binary'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class BookingReportDownloadView(APIView):
    """
    GET /api/bookings/<id>/download-report/   (alias: /report/)

    Streams the laboratory-uploaded PDF to an authorised viewer only:
      patient → after release AND full payment (first download moves the booking to REPORT_DOWNLOADED);
                released-but-unpaid answers 402 {code: payment_required, amount_outstanding, pay_url}
      partner → its own bookings, once uploaded (to verify it)
      admin   → always
    ?format=json returns metadata instead; ?inline=1 opens in the browser instead of downloading.

    Accepts `Accept: application/pdf` (or anything else). The file goes out as
    a PDF; errors and metadata always go out as JSON.
    """
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer, PDFRenderer]

    def perform_content_negotiation(self, request, force=False):
        # Never 406: e.g. `?format=json` + `Accept: application/pdf` must still get its JSON
        # (and a 402/403 must reach the client as JSON, not as "Not Acceptable").
        return super().perform_content_negotiation(request, force=True)

    def finalize_response(self, request, response, *args, **kwargs):
        # Anything that is not the file (403/404/410, auth errors, ?format=json)
        # is a dict payload: always render it as JSON, even when the client asked for PDF.
        if isinstance(response, Response):
            request.accepted_renderer = JSONRenderer()
            request.accepted_media_type = JSONRenderer.media_type
        return super().finalize_response(request, response, *args, **kwargs)

    def get(self, request, pk, *args, **kwargs):
        qs = Booking.objects.select_related('partner')
        booking = qs.filter(pk=pk).first() if str(pk).isdigit() else qs.filter(booking_id=pk).first()
        partner = get_authenticated_partner(request.user)
        allowed, audience, reason = report_access(booking, request.user, partner) if booking else (False, '', 'Booking not found.')

        if not booking or (not allowed and not audience):
            # Same answer for "doesn't exist" and "not yours" — don't leak booking IDs.
            return Response({"success": False, "message": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        if not allowed:
            state = patient_report_state(booking) if audience == wf.PATIENT else None
            if state and state['locked']:
                # 402: the report exists and is released — only payment stands in the way.
                return Response({
                    "success": False, "message": state['message'], "code": "payment_required",
                    "booking_id": booking.booking_id, "booking_status": booking.booking_status,
                    "payment_status": booking.payment_status, "amount_outstanding": state['amount_due'],
                    "pay_url": f"/api/bookings/{booking.booking_id}/pay-balance/",
                }, status=status.HTTP_402_PAYMENT_REQUIRED)
            return Response({"success": False, "message": reason, "code": "report_not_available",
                             "booking_status": booking.booking_status}, status=status.HTTP_403_FORBIDDEN)

        if request.query_params.get('format') == 'json':
            return Response({
                "success": True,
                "booking_id": booking.booking_id,
                "booking_status": booking.booking_status,
                "report_name": booking.report_name,
                "report_file_size": booking.report_file_size,
                "report_sha256": booking.report_file_sha256,
                "report_uploaded_at": booking.report_uploaded_at,
                "report_verified_by_name": booking.report_verified_by_name,
                "report_verified_at": booking.report_verified_at,
                "report_released_at": booking.report_released_at,
                "report_notes": booking.report_notes,
                "download_url": request.build_absolute_uri(f"/api/bookings/{booking.booking_id}/download-report/"),
            })

        try:
            handle = booking.report_file.open('rb')
        except (FileNotFoundError, OSError):
            return Response({"success": False, "message": "The report file is missing from storage. Please contact support."},
                            status=status.HTTP_410_GONE)

        if audience == wf.PATIENT:
            record_report_download(booking, request.user)

        filename = _SAFE_NAME.sub('_', f"Report_{booking.booking_id}_{booking.patient_name}")[:120] + '.pdf'
        response = FileResponse(handle, content_type='application/pdf',
                                as_attachment=request.query_params.get('inline') not in ('1', 'true'), filename=filename)
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class PartnerRecordBalancePaymentView(PartnerBookingView):
    """POST /api/partner/bookings/<id>/record-balance/ — offline 70% collection (Cash, UPI, Card)."""

    def post(self, request, pk, *args, **kwargs):
        _, booking, err = self.resolve(request, pk)
        if err:
            return err
        serializer = PartnerOfflineBalancePaymentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        payment, error = process_balance_payment(
            booking=booking,
            payment_mode=serializer.validated_data['payment_mode'],
            offline_reference=serializer.validated_data['offline_reference'],
            recorded_by_partner=True,
        )
        if error:
            return Response({"success": False, "message": error}, status=status.HTTP_400_BAD_REQUEST)
        return Response(booking_payload(booking, wf.PARTNER, message="Offline balance payment recorded successfully.",
                                        payment=PaymentSerializer(payment).data))


class BookingWorkflowView(APIView):
    """GET /api/bookings/workflow/ — the pipeline definition, so clients never hard-code it."""
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        steps = [{
            'status': s,
            'label': wf.LABELS[s],
            'description': wf.STEPS[s].description if s in wf.STEPS else 'Partner accepted the booking.',
            'action_label': wf.STEPS[s].action_label if s in wf.STEPS else None,
            'actor': sorted(wf.STEPS[s].actors) if s in wf.STEPS else ['PARTNER'],
            'notifies_patient': bool(wf.STEPS[s].notify_type) if s in wf.STEPS else True,
            'push': wf.STEPS[s].push if s in wf.STEPS else False,
        } for s in wf.PIPELINE]
        return Response({
            "success": True,
            "pipeline": steps,
            "groups": {k: sorted(v) for k, v in wf.STATUS_GROUPS.items()},
            "labels": wf.LABELS,
        })


# =====================================================================
# Admin Panel Views
# =====================================================================

class AdminDashboardMetricsView(APIView):
    """
    Admin Panel: Dashboard of users, bookings, booking value, advance collected, and balance outstanding.
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        from accounts.models import User, FamilyMember
        from diagnostics.models import DiagnosticTest, HealthPackage

        total_users = User.objects.count()
        total_family = FamilyMember.objects.count()
        total_tests = DiagnosticTest.objects.count()
        total_packages = HealthPackage.objects.count()
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
        def count_of(group):
            return sum(status_counts.get(st, 0) for st in wf.STATUS_GROUPS[group])

        pending_count = count_of('pending') + status_counts.get('PENDING', 0)
        confirmed_count = status_counts.get(wf.CONFIRMED, 0)
        in_lab_count = count_of('in_lab')
        completed_count = count_of('completed') + status_counts.get('COMPLETED', 0)
        cancelled_count = count_of('cancelled')

        return Response(
            {
                "success": True,
                # Direct top-level fields matching the 8 Dashboard Cards in 1MinDoc Admin UI
                "registered_users": total_users,
                "family_members": total_family,
                "diagnostic_tests": total_tests,
                "total_bookings": total_bookings,
                "pending": pending_count,
                "confirmed": confirmed_count,
                "in_lab": in_lab_count,
                "completed": completed_count,
                "cancelled": cancelled_count,
                "packages": total_packages,
                # Full structured metrics object
                "metrics": {
                    "registered_users": total_users,
                    "family_members": total_family,
                    "diagnostic_tests": total_tests,
                    "packages": total_packages,
                    "diagnostic_partners": {
                        "total": total_partners,
                        "active": active_partners,
                    },
                    "bookings": {
                        "total_count": total_bookings,
                        "pending": pending_count,
                        "confirmed": confirmed_count,
                        "in_lab": in_lab_count,
                        "completed": completed_count,
                        "cancelled": cancelled_count,
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
    Supports query parameters:
      - search: search by booking ID, patient name, patient phone, or partner centre name
      - status: filter by status (All Statuses, PENDING, CONFIRMED, COMPLETED, CANCELLED, etc.)
      - partner_id: filter by specific diagnostic partner ID
      - payment_status: filter by payment status
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        queryset = booking_queryset().order_by('-created_at')

        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(booking_id__icontains=search) |
                Q(patient_name__icontains=search) |
                Q(patient_phone__icontains=search) |
                Q(user__phone_number__icontains=search) |
                Q(user__first_name__icontains=search) |
                Q(user__last_name__icontains=search) |
                Q(partner__centre_name__icontains=search)
            )

        partner_id = request.query_params.get('partner_id')
        if partner_id:
            queryset = queryset.filter(partner_id=partner_id)

        booking_status = (request.query_params.get('status') or '').strip()
        legacy = {'PENDING': 'pending', 'COMPLETED': 'completed', 'CANCELED': 'cancelled', 'CANCELLED': 'cancelled'}
        queryset = filter_by_status(queryset, legacy.get(booking_status.upper(), booking_status))

        payment_status = request.query_params.get('payment_status')
        if payment_status:
            queryset = queryset.filter(payment_status=payment_status)

        serializer = BookingDetailSerializer(queryset, many=True, context={'audience': wf.ADMIN})
        return Response(
            {
                "success": True,
                "count": len(serializer.data),
                "bookings": serializer.data,
                "results": serializer.data,
            },
            status=status.HTTP_200_OK
        )


class AdminPartnerApprovalView(APIView):
    """
    Admin Panel: Onboard, review, and activate a diagnostic partner.
    """
    permission_classes = [AllowAny]

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

