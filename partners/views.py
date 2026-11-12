from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.utils import timezone
from django.db.models import Q

from .models import (
    DiagnosticPartner,
    PartnerServiceArea,
    PartnerSlot,
    PartnerTestPricing,
    PartnerPackagePricing,
)
from .serializers import (
    DiagnosticPartnerSerializer,
    DiagnosticPartnerDetailSerializer,
    PartnerServiceAreaSerializer,
    PartnerSlotSerializer,
    PartnerTestPricingSerializer,
    PartnerPackagePricingSerializer,
    PartnerProfileUpdateSerializer,
    CheckServiceabilitySerializer,
)


# =====================================================================
# Public Endpoints (Patients & Users)
# =====================================================================

class PartnerListView(APIView):
    """
    API endpoint: Browse all active diagnostic partner centres.
    Query parameters:
      - city: filter by city
      - pincode: filter centres in pincode
      - home_collection: 'true'/'false'
      - search: search centre name or address
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        queryset = DiagnosticPartner.objects.filter(status='ACTIVE')

        city = request.query_params.get('city')
        if city:
            queryset = queryset.filter(city__iexact=city)

        pincode = request.query_params.get('pincode')
        if pincode:
            queryset = queryset.filter(
                Q(pincode=pincode) | Q(service_areas__pincode=pincode, service_areas__is_active=True)
            ).distinct()

        home_collection = request.query_params.get('home_collection')
        if home_collection is not None:
            queryset = queryset.filter(offers_home_collection=home_collection.lower() in ['true', '1'])

        search = request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(centre_name__icontains=search) |
                Q(address_line__icontains=search) |
                Q(city__icontains=search)
            )

        serializer = DiagnosticPartnerSerializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "count": queryset.count(),
                "partners": serializer.data,
            },
            status=status.HTTP_200_OK
        )


class PartnerDetailView(APIView):
    """
    API endpoint: View full diagnostic centre details, address, and home collection areas.
    """
    permission_classes = [AllowAny]

    def get(self, request, pk, *args, **kwargs):
        try:
            partner = DiagnosticPartner.objects.prefetch_related('service_areas').get(pk=pk, status='ACTIVE')
        except DiagnosticPartner.DoesNotExist:
            return Response(
                {"success": False, "message": "Diagnostic partner centre not found or not active."},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = DiagnosticPartnerDetailSerializer(partner)
        return Response(
            {
                "success": True,
                "partner": serializer.data,
            },
            status=status.HTTP_200_OK
        )


class PartnerSlotListView(APIView):
    """
    API endpoint: View available time slots for a partner centre.
    Query parameters:
      - date: YYYY-MM-DD (defaults to today)
      - service_mode: 'HOME_COLLECTION' or 'CENTRE_VISIT'
    """
    permission_classes = [AllowAny]

    def get(self, request, pk, *args, **kwargs):
        partner_id = pk
        date_str = request.query_params.get('date')
        if date_str:
            target_date = date_str
        else:
            target_date = timezone.now().date()

        queryset = PartnerSlot.objects.filter(
            partner_id=partner_id,
            date=target_date,
            is_active=True
        )

        service_mode = request.query_params.get('service_mode')
        if service_mode:
            queryset = queryset.filter(Q(service_mode=service_mode) | Q(service_mode='ALL'))

        # Only slots that have available capacity
        available_slots = [slot for slot in queryset if slot.is_available]
        serializer = PartnerSlotSerializer(available_slots, many=True)

        return Response(
            {
                "success": True,
                "partner_id": partner_id,
                "date": str(target_date),
                "count": len(available_slots),
                "slots": serializer.data,
            },
            status=status.HTTP_200_OK
        )


class CheckServiceabilityView(APIView):
    """
    API endpoint: Check if a partner centre services a given pincode for home sample collection.
    Supports both POST and GET with query parameters ?pincode=...&partner_id=...
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        pincode = request.query_params.get('pincode', '').strip()
        partner_id = request.query_params.get('partner_id')

        if not pincode:
            return Response(
                {"success": False, "message": "Pincode parameter is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if partner_id:
            try:
                partner = DiagnosticPartner.objects.get(pk=partner_id, status='ACTIVE')
            except DiagnosticPartner.DoesNotExist:
                return Response(
                    {"success": False, "message": "Diagnostic partner centre not found."},
                    status=status.HTTP_404_NOT_FOUND
                )
            is_serviceable = partner.is_serviceable(pincode)
            return Response({
                "success": True,
                "partner_id": partner.id,
                "centre_name": partner.centre_name,
                "pincode": pincode,
                "is_serviceable": is_serviceable,
                "home_collection_fee": float(partner.home_collection_fee),
                "message": "Home collection is available in this area." if is_serviceable else "Home collection is currently not available for this pincode.",
            }, status=status.HTTP_200_OK)

        # If no specific partner_id is provided, search all active partners
        serviceable_partners = DiagnosticPartner.objects.filter(
            status='ACTIVE',
            offers_home_collection=True,
            service_areas__pincode=pincode,
            service_areas__is_active=True
        ).distinct()

        is_serviceable = serviceable_partners.exists()
        return Response({
            "success": True,
            "pincode": pincode,
            "is_serviceable": is_serviceable,
            "serviceable_partners_count": serviceable_partners.count(),
            "partners": [
                {
                    "id": p.id,
                    "centre_name": p.centre_name,
                    "city": p.city,
                    "home_collection_fee": float(p.home_collection_fee)
                } for p in serviceable_partners
            ],
            "message": "Home collection is available in this area." if is_serviceable else "Home collection is currently not available for this pincode."
        }, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        serializer = CheckServiceabilitySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        partner_id = serializer.validated_data['partner_id']
        pincode = serializer.validated_data['pincode']

        try:
            partner = DiagnosticPartner.objects.get(pk=partner_id, status='ACTIVE')
        except DiagnosticPartner.DoesNotExist:
            return Response(
                {"success": False, "message": "Diagnostic partner centre not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        is_serviceable = partner.is_serviceable(pincode)
        return Response(
            {
                "success": True,
                "partner_id": partner.id,
                "centre_name": partner.centre_name,
                "pincode": pincode,
                "is_serviceable": is_serviceable,
                "home_collection_fee": float(partner.home_collection_fee),
                "message": "Home collection is available in this area." if is_serviceable else "Home collection is currently not available for this pincode.",
            },
            status=status.HTTP_200_OK
        )


# =====================================================================
# Diagnostic Partner Dashboard Endpoints
# =====================================================================

def get_partner_for_user(user):
    if hasattr(user, 'partner_profile'):
        return user.partner_profile
    return None


class PartnerProfileManageView(APIView):
    """
    Partner Dashboard: View & manage partner centre profile.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        partner = get_partner_for_user(request.user)
        if not partner:
            return Response(
                {"success": False, "message": "No diagnostic partner profile associated with this account."},
                status=status.HTTP_403_FORBIDDEN
            )
        serializer = DiagnosticPartnerDetailSerializer(partner)
        return Response({"success": True, "partner": serializer.data}, status=status.HTTP_200_OK)

    def patch(self, request, *args, **kwargs):
        partner = get_partner_for_user(request.user)
        if not partner:
            return Response(
                {"success": False, "message": "No diagnostic partner profile associated with this account."},
                status=status.HTTP_403_FORBIDDEN
            )
        serializer = PartnerProfileUpdateSerializer(partner, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(
            {"success": True, "message": "Centre profile updated successfully.", "partner": serializer.data},
            status=status.HTTP_200_OK
        )


class PartnerServiceAreaListCreateView(APIView):
    """
    Partner Dashboard: Manage home collection service areas.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        partner = get_partner_for_user(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)
        areas = partner.service_areas.all()
        serializer = PartnerServiceAreaSerializer(areas, many=True)
        return Response({"success": True, "service_areas": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        partner = get_partner_for_user(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)
        serializer = PartnerServiceAreaSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        area = serializer.save(partner=partner)
        return Response(
            {"success": True, "message": f"Service area for pincode {area.pincode} added.", "service_area": serializer.data},
            status=status.HTTP_201_CREATED
        )


class PartnerServiceAreaDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk, *args, **kwargs):
        partner = get_partner_for_user(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)
        try:
            area = partner.service_areas.get(pk=pk)
            area.delete()
            return Response({"success": True, "message": "Service area removed."}, status=status.HTTP_200_OK)
        except PartnerServiceArea.DoesNotExist:
            return Response({"success": False, "message": "Service area not found."}, status=status.HTTP_404_NOT_FOUND)


class PartnerSlotListCreateView(APIView):
    """
    Partner Dashboard: Configure available dates and time slots.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        partner = get_partner_for_user(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)
        date = request.query_params.get('date', timezone.now().date())
        slots = partner.slots.filter(date=date).order_by('start_time')
        serializer = PartnerSlotSerializer(slots, many=True)
        return Response({"success": True, "slots": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        partner = get_partner_for_user(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)
        serializer = PartnerSlotSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        slot = serializer.save(partner=partner)
        return Response({"success": True, "message": "Slot created successfully.", "slot": PartnerSlotSerializer(slot).data}, status=status.HTTP_201_CREATED)


class PartnerTestPricingManageView(APIView):
    """
    Partner Dashboard: Manage custom test prices and availability.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        partner = get_partner_for_user(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)
        pricings = partner.test_pricings.select_related('test').all()
        serializer = PartnerTestPricingSerializer(pricings, many=True)
        return Response({"success": True, "test_pricings": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        partner = get_partner_for_user(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)
        test_id = request.data.get('test_id')
        price = request.data.get('price')
        is_available = request.data.get('is_available', True)

        pricing, created = PartnerTestPricing.objects.update_or_create(
            partner=partner,
            test_id=test_id,
            defaults={'price': price, 'is_available': is_available}
        )
        return Response(
            {"success": True, "message": "Test pricing updated.", "pricing": PartnerTestPricingSerializer(pricing).data},
            status=status.HTTP_200_OK
        )


class PartnerPackagePricingManageView(APIView):
    """
    Partner Dashboard: Manage custom package prices and availability.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        partner = get_partner_for_user(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)
        pricings = partner.package_pricings.select_related('package').all()
        serializer = PartnerPackagePricingSerializer(pricings, many=True)
        return Response({"success": True, "package_pricings": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        partner = get_partner_for_user(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)
        package_id = request.data.get('package_id')
        price = request.data.get('price')
        is_available = request.data.get('is_available', True)

        pricing, created = PartnerPackagePricing.objects.update_or_create(
            partner=partner,
            package_id=package_id,
            defaults={'price': price, 'is_available': is_available}
        )
        return Response(
            {"success": True, "message": "Package pricing updated.", "pricing": PartnerPackagePricingSerializer(pricing).data},
            status=status.HTTP_200_OK
        )


class PartnerDashboardSummaryView(APIView):
    """
    Partner Dashboard: View high-level metrics (incoming, confirmed, completed bookings, etc.).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        partner = get_partner_for_user(request.user)
        if not partner:
            return Response({"success": False, "message": "Partner profile required."}, status=status.HTTP_403_FORBIDDEN)

        bookings = partner.bookings.all()
        incoming = bookings.filter(booking_status='AWAITING_PARTNER_CONFIRMATION').count()
        confirmed = bookings.filter(booking_status='CONFIRMED').count()
        in_progress = bookings.filter(booking_status='SAMPLE_COLLECTED').count()
        completed = bookings.filter(booking_status='TEST_COMPLETED').count()

        return Response(
            {
                "success": True,
                "partner": {
                    "id": partner.id,
                    "centre_name": partner.centre_name,
                    "status": partner.status,
                },
                "metrics": {
                    "incoming_requests_awaiting_confirmation": incoming,
                    "confirmed_upcoming_bookings": confirmed,
                    "samples_collected_in_progress": in_progress,
                    "tests_completed": completed,
                    "total_bookings": bookings.count(),
                }
            },
            status=status.HTTP_200_OK
        )

