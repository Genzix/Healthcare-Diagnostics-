from decimal import Decimal
from datetime import time, timedelta
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import FamilyMember
from diagnostics.models import Category, DiagnosticTest, HealthPackage
from partners.models import DiagnosticPartner, PartnerSlot, PartnerServiceArea
from bookings.models import Booking, Payment, Notification
from bookings.services import (
    calculate_booking_pricing,
    create_booking_order,
    process_advance_payment,
    partner_accept_booking,
    partner_reject_booking,
    partner_update_service_status,
    process_balance_payment,
)

User = get_user_model()


class FullApplicationFlowTests(TestCase):
    """
    Test suite verifying the 12-step Application Flow and 30%/70% payment rules.
    """
    def setUp(self):
        self.client = APIClient()

        # 1. User & Family
        self.user = User.objects.create_user(
            phone_number='+919876543210',
            first_name='Habhi',
            last_name='Ram',
            city='Hyderabad',
            date_of_birth='1995-05-15',
            gender='MALE',
            is_phone_verified=True
        )
        self.client.force_authenticate(user=self.user)

        self.family_member = FamilyMember.objects.create(
            user=self.user,
            name='John Ram',
            relationship='PARENT',
            date_of_birth='1965-08-20',
            gender='MALE',
            contact_number='+919876543211'
        )

        # 2. Diagnostics Catalogue
        self.category = Category.objects.create(name='Pathology', slug='pathology')
        self.test1 = DiagnosticTest.objects.create(
            name='Complete Blood Count (CBC)',
            code='CBC01',
            category=self.category,
            description='Measures red and white blood cells.',
            sample_type='BLOOD',
            base_price=Decimal('500.00'),
            is_available=True
        )
        self.test2 = DiagnosticTest.objects.create(
            name='Lipid Profile',
            code='LIPID01',
            category=self.category,
            description='Measures cholesterol levels.',
            sample_type='BLOOD',
            fasting_required=True,
            base_price=Decimal('800.00'),
            is_available=True
        )

        self.package = HealthPackage.objects.create(
            name='Full Body Health Checkup',
            code='PKG-FB-01',
            description='Comprehensive package',
            price=Decimal('2000.00'),
            market_price=Decimal('3000.00'),
            is_available=True
        )
        self.package.tests.add(self.test1, self.test2)

        # 3. Diagnostic Partner Centre
        self.partner_user = User.objects.create_user(
            phone_number='+919888877777',
            first_name='PartnerAdmin',
            role='LAB_STAFF',
            is_phone_verified=True
        )
        self.partner = DiagnosticPartner.objects.create(
            user=self.partner_user,
            centre_name='Apex Diagnostic Centre',
            license_number='LIC-APEX-001',
            contact_person='Dr. Sharma',
            contact_phone='+919888877777',
            contact_email='apex@diagnostics.com',
            address_line='Plot 42, Hitec City',
            city='Hyderabad',
            state='Telangana',
            pincode='500081',
            offers_home_collection=True,
            home_collection_fee=Decimal('150.00'),
            status='ACTIVE'
        )

        # Serviceable Area
        PartnerServiceArea.objects.create(
            partner=self.partner,
            city='Hyderabad',
            pincode='500081'
        )

        # Partner Slot
        self.tomorrow = timezone.now().date() + timedelta(days=1)
        self.slot = PartnerSlot.objects.create(
            partner=self.partner,
            date=self.tomorrow,
            start_time=time(9, 0),
            end_time=time(10, 0),
            service_mode='ALL',
            max_capacity=5,
            booked_count=0,
            is_active=True
        )

    def test_step_8_price_estimate_30_70_split(self):
        """
        Step 8 Review: Verifies exact calculation of Subtotal, Home Fee, 30% Advance, 70% Balance.
        Total = 500 (CBC) + 800 (Lipid) + 150 (Home fee) = 1450.00
        30% Advance = 435.00
        70% Balance = 1015.00
        """
        pricing, err = calculate_booking_pricing(
            partner=self.partner,
            test_ids=[self.test1.id, self.test2.id],
            package_ids=[],
            service_mode='HOME_COLLECTION',
            pincode='500081'
        )
        self.assertIsNone(err)
        self.assertEqual(pricing['subtotal_amount'], 1300.00)
        self.assertEqual(pricing['home_collection_fee'], 150.00)
        self.assertEqual(pricing['total_amount'], 1450.00)
        self.assertEqual(pricing['advance_amount_due_30_percent'], 435.00)
        self.assertEqual(pricing['balance_amount_due_70_percent'], 1015.00)

    def test_step_5_6_7_booking_creation_for_family_member(self):
        """
        Steps 5-7: Select family member, home collection mode, slot -> creates booking.
        """
        booking_payload = {
            'partner_id': self.partner.id,
            'service_mode': 'HOME_COLLECTION',
            'slot_id': self.slot.id,
            'patient_type': 'FAMILY_MEMBER',
            'family_member_id': self.family_member.id,
            'test_ids': [self.test1.id],
            'package_ids': [],
            'collection_address': 'Flat 302, Green Towers',
            'collection_city': 'Hyderabad',
            'collection_pincode': '500081'
        }

        booking, err = create_booking_order(self.user, booking_payload)
        self.assertIsNone(err)
        self.assertEqual(booking.patient_name, 'John Ram')
        self.assertEqual(booking.patient_gender, 'MALE')
        self.assertEqual(booking.booking_status, 'AWAITING_ADVANCE_PAYMENT')
        self.assertEqual(booking.payment_status, 'PENDING')
        self.assertEqual(booking.total_amount, Decimal('650.00'))  # 500 + 150 home fee
        self.assertEqual(booking.advance_amount_due, Decimal('195.00'))  # 30%
        self.assertEqual(booking.balance_amount_due, Decimal('455.00'))  # 70%

    def test_step_9_pay_advance_30_percent(self):
        """
        Step 9: Advance payment collected; status becomes AWAITING_PARTNER_CONFIRMATION (not confirmed yet).
        """
        booking_payload = {
            'partner_id': self.partner.id,
            'service_mode': 'CENTRE_VISIT',
            'slot_id': self.slot.id,
            'patient_type': 'SELF',
            'test_ids': [self.test1.id],
            'package_ids': [],
        }
        booking, _ = create_booking_order(self.user, booking_payload)

        payment, err = process_advance_payment(booking=booking, payment_mode='ONLINE')
        self.assertIsNone(err)
        self.assertEqual(payment.payment_type, 'ADVANCE_30')
        self.assertEqual(payment.amount, Decimal('150.00'))  # 30% of 500

        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'AWAITING_PARTNER_CONFIRMATION')
        self.assertEqual(booking.payment_status, 'ADVANCE_PAID')
        self.assertEqual(booking.advance_amount_paid, Decimal('150.00'))

    def test_step_10_partner_acceptance_confirms_booking(self):
        """
        Step 10: Partner accepts booking -> CONFIRMED.
        """
        booking_payload = {
            'partner_id': self.partner.id,
            'service_mode': 'CENTRE_VISIT',
            'slot_id': self.slot.id,
            'patient_type': 'SELF',
            'test_ids': [self.test1.id],
        }
        booking, _ = create_booking_order(self.user, booking_payload)
        process_advance_payment(booking)

        booking, err = partner_accept_booking(booking, self.partner_user)
        self.assertIsNone(err)
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'CONFIRMED')
        self.assertIsNotNone(booking.confirmed_at)

    def test_step_10_partner_rejection_triggers_refund(self):
        """
        Step 10 (Alternative): Partner rejects -> REJECTED_BY_PARTNER and refund is recorded.
        """
        booking_payload = {
            'partner_id': self.partner.id,
            'service_mode': 'CENTRE_VISIT',
            'slot_id': self.slot.id,
            'patient_type': 'SELF',
            'test_ids': [self.test1.id],
        }
        booking, _ = create_booking_order(self.user, booking_payload)
        process_advance_payment(booking)

        booking, err = partner_reject_booking(booking, self.partner_user, reason="Lab equipment under calibration")
        self.assertIsNone(err)
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'REJECTED_BY_PARTNER')
        self.assertEqual(booking.payment_status, 'REFUNDED')

        refund_payment = Payment.objects.filter(booking=booking, payment_type='REFUND').first()
        self.assertIsNotNone(refund_payment)
        self.assertEqual(refund_payment.amount, Decimal('150.00'))

    def test_step_11_and_12_service_completion_and_70_percent_balance_payment(self):
        """
        Step 11 & 12: Partner marks test completed -> User pays remaining 70% balance online.
        """
        booking_payload = {
            'partner_id': self.partner.id,
            'service_mode': 'CENTRE_VISIT',
            'slot_id': self.slot.id,
            'patient_type': 'SELF',
            'test_ids': [self.test1.id],
        }
        booking, _ = create_booking_order(self.user, booking_payload)
        process_advance_payment(booking)
        partner_accept_booking(booking, self.partner_user)

        # Step 11: Sample collected -> Test completed
        partner_update_service_status(booking, self.partner_user, 'SAMPLE_COLLECTED')
        partner_update_service_status(booking, self.partner_user, 'TEST_COMPLETED')

        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'TEST_COMPLETED')
        self.assertIsNotNone(booking.completed_at)

        # Step 12: Pay 70% balance (350.00)
        payment, err = process_balance_payment(booking=booking, payment_mode='ONLINE')
        self.assertIsNone(err)
        self.assertEqual(payment.payment_type, 'BALANCE_70')
        self.assertEqual(payment.amount, Decimal('350.00'))

        booking.refresh_from_db()
        self.assertEqual(booking.payment_status, 'FULLY_PAID')

    def test_step_12_partner_records_approved_offline_balance(self):
        """
        Step 12 (Alternative): Partner collects remaining 70% offline via Cash/UPI.
        """
        booking_payload = {
            'partner_id': self.partner.id,
            'service_mode': 'CENTRE_VISIT',
            'slot_id': self.slot.id,
            'patient_type': 'SELF',
            'test_ids': [self.test1.id],
        }
        booking, _ = create_booking_order(self.user, booking_payload)
        process_advance_payment(booking)
        partner_accept_booking(booking, self.partner_user)
        partner_update_service_status(booking, self.partner_user, 'SAMPLE_COLLECTED')
        partner_update_service_status(booking, self.partner_user, 'TEST_COMPLETED')

        payment, err = process_balance_payment(
            booking=booking,
            payment_mode='CASH',
            offline_reference='CASH-REC-0099',
            recorded_by_partner=True
        )
        self.assertIsNone(err)
        self.assertTrue(payment.recorded_by_partner)
        self.assertEqual(payment.offline_payment_reference, 'CASH-REC-0099')

        booking.refresh_from_db()
        self.assertEqual(booking.payment_status, 'FULLY_PAID')

    def test_end_to_end_api_endpoints_http(self):
        """
        Exercises the entire application flow via HTTP requests:
        Root -> Catalogue -> Partners -> Profile -> Family -> Estimate -> Book -> Pay30 -> Accept -> TestCompleted -> Pay70.
        """
        # 1. Root API Directory
        res = self.client.get('/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'online')

        # 2. Browse Catalogue
        res = self.client.get('/api/categories/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(res.data['categories']), 1)

        res = self.client.get('/api/tests/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(res.data['tests']), 2)

        res = self.client.get(f'/api/tests/{self.test1.id}/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['test']['code'], 'CBC01')

        res = self.client.get('/api/packages/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(res.data['packages']), 1)

        res = self.client.get(f'/api/packages/{self.package.id}/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 3. Partners & Availability
        res = self.client.get('/api/partners/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        res = self.client.get(f'/api/partners/{self.partner.id}/slots/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        res = self.client.post('/api/partners/check-serviceability/', {
            'partner_id': self.partner.id,
            'pincode': '500081'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['is_serviceable'])

        # 4. Profile Management
        res = self.client.get('/api/profile/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['user']['phone_number'], self.user.phone_number)

        res = self.client.patch('/api/profile/', {'city': 'Bengaluru'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['user']['city'], 'Bengaluru')

        # 5. Family Profiles
        res = self.client.get('/api/family-members/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data['family_members']), 1)

        res = self.client.post('/api/family-members/', {
            'name': 'Sarah Ram',
            'relationship': 'CHILD',
            'date_of_birth': '2015-03-10',
            'gender': 'FEMALE'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        # 6. Price Estimate (Review)
        res = self.client.post('/api/bookings/price-estimate/', {
            'partner_id': self.partner.id,
            'service_mode': 'HOME_COLLECTION',
            'test_ids': [self.test1.id, self.test2.id],
            'package_ids': [],
            'pincode': '500081'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['estimate']['total_amount'], 1450.00)
        self.assertEqual(res.data['estimate']['advance_amount_due_30_percent'], 435.00)
        self.assertEqual(res.data['estimate']['balance_amount_due_70_percent'], 1015.00)

        # 7. Create Booking Order
        res = self.client.post('/api/bookings/', {
            'partner_id': self.partner.id,
            'service_mode': 'HOME_COLLECTION',
            'slot_id': self.slot.id,
            'patient_type': 'SELF',
            'test_ids': [self.test1.id],
            'collection_address': 'Flat 101, Hillview',
            'collection_city': 'Hyderabad',
            'collection_pincode': '500081'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        booking_id = res.data['booking']['id']
        booking_str_id = res.data['booking']['booking_id']

        # 8. Pay 30% Advance
        res = self.client.post(f'/api/bookings/{booking_id}/pay-advance/', {
            'payment_mode': 'ONLINE',
            'gateway_transaction_id': 'TXN_TEST_1234'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['booking']['booking_status'], 'AWAITING_PARTNER_CONFIRMATION')
        self.assertEqual(res.data['booking']['payment_status'], 'ADVANCE_PAID')

        # 9. Partner Accepts Booking
        self.client.force_authenticate(user=self.partner_user)
        res = self.client.post(f'/api/partner/bookings/{booking_id}/accept/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['booking']['booking_status'], 'CONFIRMED')

        # 10. Partner Updates to Sample Collected and Test Completed
        res = self.client.post(f'/api/partner/bookings/{booking_id}/update-status/', {'new_status': 'SAMPLE_COLLECTED'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        res = self.client.post(f'/api/partner/bookings/{booking_id}/update-status/', {'new_status': 'TEST_COMPLETED'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['booking']['booking_status'], 'TEST_COMPLETED')

        # 11. Partner Dashboard Summary
        res = self.client.get('/api/partner/dashboard/summary/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(res.data['metrics']['tests_completed'], 1)

        # 12. User Pays Remaining 70% Balance Online
        self.client.force_authenticate(user=self.user)
        res = self.client.post(f'/api/bookings/{booking_str_id}/pay-balance/', {
            'payment_mode': 'ONLINE',
            'gateway_transaction_id': 'TXN_BAL_5678'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['booking']['payment_status'], 'FULLY_PAID')

        # 13. Notifications
        res = self.client.get('/api/notifications/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(res.data['notifications']), 1)

        # 14. Admin Dashboard Metrics
        admin_user = User.objects.create_superuser(
            phone_number='+919000000000',
            password='AdminPassword123'
        )
        self.client.force_authenticate(user=admin_user)
        res = self.client.get('/api/admin/dashboard/metrics/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(res.data['metrics']['registered_users'], 1)
        self.assertGreaterEqual(res.data['metrics']['bookings']['total_count'], 1)


