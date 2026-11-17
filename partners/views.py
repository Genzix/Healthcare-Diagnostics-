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

    def post(self, request, *args, **kwargs):
        view = AdminPartnerListCreateView()
        return view.post(request, *args, **kwargs)


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


class PartnerLoginView(APIView):
    """
    POST /api/partner/login/
    Dedicated Partner Login endpoint using email or phone number + password.
    Authenticates staff and lab managers of Diagnostic Partner centres.
    Allows partner centres created by Admin to log in without prior manual registration.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        from accounts.services import normalize_phone_number, get_tokens_for_user
        from accounts.models import User
        from accounts.serializers import UserSerializer

        email = str(request.data.get('email') or '').strip()
        phone_number = str(request.data.get('phone_number') or '').strip()
        password = str(request.data.get('password') or '')

        if not password:
            return Response({
                "success": False,
                "message": "Password is required for partner login."
            }, status=status.HTTP_400_BAD_REQUEST)

        user = None
        clean_phone = ""
        if email:
            user = User.objects.filter(email__iexact=email).first()
        elif phone_number:
            clean_phone = normalize_phone_number(phone_number)
            user = User.objects.filter(phone_number=clean_phone).first()
            if not user and not clean_phone.startswith('+'):
                user = User.objects.filter(phone_number=f"+91{clean_phone}").first()
            if not user and clean_phone.startswith('+91'):
                user = User.objects.filter(phone_number=clean_phone[3:]).first()
        else:
            return Response({
                "success": False,
                "message": "Partner email or phone number is required."
            }, status=status.HTTP_400_BAD_REQUEST)

        # If User account does not exist yet, check if an Admin added a DiagnosticPartner centre with this phone/email
        partner_centre = None
        if not user:
            phone_variants = [clean_phone, phone_number]
            if clean_phone and not clean_phone.startswith('+'):
                phone_variants.append(f"+91{clean_phone}")
            if clean_phone and clean_phone.startswith('+91'):
                phone_variants.append(clean_phone[3:])

            partner_filter = Q(contact_phone__in=phone_variants)
            if email:
                partner_filter = partner_filter | Q(contact_email__iexact=email)

            partner_centre = DiagnosticPartner.objects.filter(partner_filter).first()
            if partner_centre:
                if partner_centre.user:
                    user = partner_centre.user
                else:
                    # Auto-provision user account for the admin-onboarded partner
                    target_phone = clean_phone if clean_phone.startswith('+') else f"+91{clean_phone}" if clean_phone else None
                    user = User.objects.create_user(
                        phone_number=target_phone,
                        email=partner_centre.contact_email or email,
                        password=password,
                        first_name=partner_centre.contact_person or partner_centre.centre_name,
                        role='LAB_STAFF',
                        is_phone_verified=True,
                    )
                    partner_centre.user = user
                    partner_centre.save(update_fields=['user'])

        if not user or not user.check_password(password):
            return Response({
                "success": False,
                "message": "Invalid credentials. Please verify your email/phone and password."
            }, status=status.HTTP_401_UNAUTHORIZED)

        if not user.is_active:
            return Response({
                "success": False,
                "message": "This partner account has been deactivated."
            }, status=status.HTTP_403_FORBIDDEN)

        # Retrieve partner centre profile
        partner = get_partner_for_user(user) or partner_centre
        if not partner and user.role != 'LAB_STAFF' and not user.is_staff:
            return Response({
                "success": False,
                "message": "No diagnostic partner centre profile associated with this account."
            }, status=status.HTTP_403_FORBIDDEN)

        if partner and partner.status == 'PENDING':
            return Response({
                "success": False,
                "message": "Your diagnostic partner centre is pending admin approval. You will receive access once verified."
            }, status=status.HTTP_403_FORBIDDEN)

        if partner and partner.status == 'SUSPENDED':
            return Response({
                "success": False,
                "message": "Your diagnostic partner centre is suspended. Please contact platform support."
            }, status=status.HTTP_403_FORBIDDEN)


class PartnerRegistrationView(APIView):
    """
    POST /api/partner/register/
    Public Inbound Application endpoint:
    Allows diagnostic labs/scan centres to apply to become a partner on the platform.
    Creates a partner profile in 'PENDING' status awaiting admin verification & approval.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        from accounts.models import User
        from accounts.services import normalize_phone_number

        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        password = str(data.pop('password', '') or '').strip()
        service_pincodes = data.pop('service_pincodes', None)

        if not password:
            return Response({
                "success": False,
                "message": "Password is required to create your partner account."
            }, status=status.HTTP_400_BAD_REQUEST)

        # Force status to PENDING for public registrations
        data['status'] = 'PENDING'

        serializer = DiagnosticPartnerSerializer(data=data)
        if not serializer.is_valid():
            return Response({
                "success": False,
                "message": "Validation failed.",
                "errors": serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        contact_phone = serializer.validated_data.get('contact_phone', '')
        clean_phone = normalize_phone_number(contact_phone)
        if User.objects.filter(phone_number=clean_phone).exists():
            return Response({
                "success": False,
                "message": f"An account with phone number {contact_phone} already exists."
            }, status=status.HTTP_400_BAD_REQUEST)

        contact_email = serializer.validated_data.get('contact_email', '')
        if contact_email and User.objects.filter(email__iexact=contact_email).exists():
            return Response({
                "success": False,
                "message": f"An account with email {contact_email} already exists."
            }, status=status.HTTP_400_BAD_REQUEST)

        # Create user account for the partner
        target_phone = clean_phone if clean_phone.startswith('+') else f"+91{clean_phone}" if clean_phone else None
        user = User.objects.create_user(
            phone_number=target_phone,
            email=contact_email or '',
            password=password,
            first_name=serializer.validated_data.get('contact_person') or serializer.validated_data.get('centre_name'),
            role='LAB_STAFF',
            is_phone_verified=True,
        )

        partner = serializer.save(user=user)

        # If service pincodes provided, create them
        if service_pincodes and isinstance(service_pincodes, list):
            for pin in service_pincodes:
                p_code = str(pin).strip()
                if p_code:
                    PartnerServiceArea.objects.get_or_create(
                        partner=partner,
                        pincode=p_code,
                        defaults={'city': partner.city, 'is_active': True}
                    )

        detail_serializer = DiagnosticPartnerDetailSerializer(partner)
        return Response({
            "success": True,
            "message": f"Diagnostic partner application for '{partner.centre_name}' submitted successfully. Your account is pending admin verification.",
            "partner": detail_serializer.data,
            "status": "PENDING"
        }, status=status.HTTP_201_CREATED)

        jwt_tokens = get_tokens_for_user(user)
        partner_data = None
        if partner:
            partner_data = {
                "id": partner.id,
                "centre_name": partner.centre_name,
                "license_number": partner.license_number,
                "city": partner.city,
                "status": partner.status,
                "offers_home_collection": partner.offers_home_collection,
            }

        return Response({
            "success": True,
            "message": f"Welcome back, {partner.centre_name if partner else user.get_full_name() or user.phone_number}.",
            "partner": partner_data,
            "user": UserSerializer(user).data,
            "tokens": jwt_tokens
        }, status=status.HTTP_200_OK)


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


# =====================================================================
# Admin Diagnostic Partner Management Endpoints
# =====================================================================

class AdminPartnerListCreateView(APIView):
    """
    GET  /api/admin/partners/  - List all diagnostic partners (active, pending, suspended)
    POST /api/admin/partners/  - Create/onboard a new diagnostic partner centre
    """
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        queryset = DiagnosticPartner.objects.all().order_by('-created_at')

        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status__iexact=status_filter)

        city = request.query_params.get('city')
        if city:
            queryset = queryset.filter(city__iexact=city)

        search = request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(centre_name__icontains=search) |
                Q(license_number__icontains=search) |
                Q(contact_person__icontains=search) |
                Q(city__icontains=search)
            )

        serializer = DiagnosticPartnerDetailSerializer(queryset, many=True)
        return Response({
            "success": True,
            "count": queryset.count(),
            "partners": serializer.data,
            "results": serializer.data,
        }, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        service_pincodes = data.pop('service_pincodes', None)
        user_id = data.get('user_id') or data.get('user')
        password = data.pop('password', None)

        serializer = DiagnosticPartnerSerializer(data=data)
        if not serializer.is_valid():
            return Response({
                "success": False,
                "message": "Failed to onboard diagnostic partner.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        partner = serializer.save()

        # Link existing or auto-create User account for partner dashboard login
        from django.contrib.auth import get_user_model
        from accounts.services import normalize_phone_number
        User = get_user_model()

        clean_phone = normalize_phone_number(partner.contact_phone)
        partner_user = None

        if user_id:
            partner_user = User.objects.filter(pk=user_id).first()
        elif clean_phone:
            partner_user = User.objects.filter(phone_number=clean_phone).first()
            if not partner_user and not clean_phone.startswith('+'):
                partner_user = User.objects.filter(phone_number=f"+91{clean_phone}").first()
        elif partner.contact_email:
            partner_user = User.objects.filter(email__iexact=partner.contact_email).first()

        # If user account doesn't exist, auto-create it with role LAB_STAFF
        if not partner_user and (clean_phone or partner.contact_email):
            target_phone = clean_phone if clean_phone.startswith('+') else f"+91{clean_phone}" if clean_phone else None
            partner_user = User.objects.create_user(
                phone_number=target_phone,
                email=partner.contact_email or '',
                password=password or 'Partner@123',
                first_name=partner.contact_person or partner.centre_name,
                role='LAB_STAFF',
                is_phone_verified=True,
            )
        elif partner_user and password:
            partner_user.set_password(password)
            partner_user.role = 'LAB_STAFF'
            partner_user.save()

        if partner_user and not partner.user:
            partner.user = partner_user
            partner.save(update_fields=['user'])

        # If initial service pincodes provided, create them
        if service_pincodes and isinstance(service_pincodes, list):
            for pin in service_pincodes:
                p_code = str(pin).strip()
                if p_code:
                    PartnerServiceArea.objects.get_or_create(
                        partner=partner,
                        pincode=p_code,
                        defaults={'city': partner.city, 'is_active': True}
                    )

        detail_serializer = DiagnosticPartnerDetailSerializer(partner)
        return Response({
            "success": True,
            "message": f"Diagnostic partner '{partner.centre_name}' onboarded successfully.",
            "partner": detail_serializer.data,
            "results": detail_serializer.data,
        }, status=status.HTTP_201_CREATED)


class AdminPartnerDetailView(APIView):
    """
    GET    /api/admin/partners/<id>/ - Retrieve full partner details
    PATCH  /api/admin/partners/<id>/ - Update partner details
    PUT    /api/admin/partners/<id>/ - Update partner details
    DELETE /api/admin/partners/<id>/ - Remove partner centre
    """
    permission_classes = [AllowAny]

    def get_object(self, pk):
        try:
            return DiagnosticPartner.objects.prefetch_related('service_areas').get(pk=pk)
        except DiagnosticPartner.DoesNotExist:
            return None

    def get(self, request, pk, *args, **kwargs):
        partner = self.get_object(pk)
        if not partner:
            return Response({"success": False, "message": "Diagnostic partner not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"success": True, "partner": DiagnosticPartnerDetailSerializer(partner).data}, status=status.HTTP_200_OK)

    def patch(self, request, pk, *args, **kwargs):
        partner = self.get_object(pk)
        if not partner:
            return Response({"success": False, "message": "Diagnostic partner not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = DiagnosticPartnerSerializer(partner, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response({"success": False, "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        updated = serializer.save()
        return Response({
            "success": True,
            "message": f"Partner '{updated.centre_name}' updated successfully.",
            "partner": DiagnosticPartnerDetailSerializer(updated).data,
        }, status=status.HTTP_200_OK)

    def put(self, request, pk, *args, **kwargs):
        return self.patch(request, pk, *args, **kwargs)

    def delete(self, request, pk, *args, **kwargs):
        partner = self.get_object(pk)
        if not partner:
            return Response({"success": False, "message": "Diagnostic partner not found."}, status=status.HTTP_404_NOT_FOUND)
        partner_name = partner.centre_name
        partner.delete()
        return Response({
            "success": True,
            "message": f"Partner '{partner_name}' deleted successfully."
        }, status=status.HTTP_200_OK)

