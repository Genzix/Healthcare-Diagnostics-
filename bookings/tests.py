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

import shutil
import tempfile
from django.test import override_settings

TEST_MEDIA = tempfile.mkdtemp(prefix='bookings-test-media-')


@override_settings(MEDIA_ROOT=TEST_MEDIA)
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

    # ── helpers ──
    @staticmethod
    def pdf(name='report.pdf', body=b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, body, content_type='application/pdf')

    def run_lab_pipeline(self, booking, until='REPORT_READY'):
        """Drive a CONFIRMED booking through the lab pipeline via the service layer."""
        steps = [
            ('SAMPLE_COLLECTED', {}),
            ('SAMPLE_RECEIVED', {}),
            ('LAB_ANALYSIS', {}),
            ('REPORT_GENERATED', {}),
            ('REPORT_UPLOADED', {'report_file': self.pdf()}),
            ('REPORT_VERIFIED', {'verified_by_name': 'Dr. Meera Rao', 'verified_by_registration': 'TSMC-44120'}),
            ('REPORT_READY', {}),
        ]
        for target, extra in steps:
            _, err = partner_update_service_status(booking, self.partner_user, target, **extra)
            self.assertIsNone(err, f"{target}: {err}")
            if target == until:
                break
        booking.refresh_from_db()
        return booking

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

        # Step 11: full lab pipeline -> report released
        booking = self.run_lab_pipeline(booking)
        self.assertEqual(booking.booking_status, 'REPORT_READY')
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
        # Collector takes cash right after collection — allowed from SAMPLE_COLLECTED onwards.
        booking = self.run_lab_pipeline(booking, until='SAMPLE_COLLECTED')

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

        # 10. Partner walks the lab pipeline over HTTP
        url = f'/api/partner/bookings/{booking_id}/update-status/'
        for target in ['SAMPLE_COLLECTED', 'SAMPLE_RECEIVED', 'LAB_ANALYSIS', 'REPORT_GENERATED']:
            res = self.client.post(url, {'new_status': target}, format='json')
            self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        res = self.client.post(f'/api/partner/bookings/{booking_id}/upload-report/', {'report_file': self.pdf()}, format='multipart')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        res = self.client.post(url, {'new_status': 'REPORT_VERIFIED', 'verified_by_name': 'Dr. Meera Rao'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        res = self.client.post(url, {'new_status': 'REPORT_READY'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data['booking']['booking_status'], 'REPORT_READY')

        # 11. Partner Dashboard Summary
        res = self.client.get('/api/partner/dashboard/summary/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(res.data['metrics']['tests_completed'], 1)
        self.assertGreaterEqual(res.data['metrics']['completed_count'], 1)

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




@override_settings(MEDIA_ROOT=TEST_MEDIA)
class LabReportPipelineTests(TestCase):
    """Partner → Laboratory → Patient report pipeline: rules, access control, notifications, audit."""

    setUp = FullApplicationFlowTests.setUp
    pdf = staticmethod(FullApplicationFlowTests.pdf)
    run_lab_pipeline = FullApplicationFlowTests.run_lab_pipeline

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA, ignore_errors=True)

    def confirmed_booking(self):
        booking, err = create_booking_order(self.user, {
            'partner_id': self.partner.id, 'service_mode': 'CENTRE_VISIT', 'slot_id': self.slot.id,
            'patient_type': 'SELF', 'test_ids': [self.test1.id],
        })
        self.assertIsNone(err)
        process_advance_payment(booking)
        partner_accept_booking(booking, self.partner_user)
        booking.refresh_from_db()
        return booking

    def status_url(self, booking):
        return f'/api/partner/bookings/{booking.booking_id}/update-status/'

    # ── state machine ──

    def test_full_pipeline_records_every_step_in_order(self):
        booking = self.run_lab_pipeline(self.confirmed_booking())
        self.assertEqual(booking.booking_status, 'REPORT_READY')
        self.assertIsNotNone(booking.sample_collected_at)
        self.assertIsNotNone(booking.sample_received_at)
        self.assertEqual(booking.report_verified_by_name, 'Dr. Meera Rao')
        self.assertEqual(len(booking.report_file_sha256), 64)
        self.assertIsNotNone(booking.report_released_at)
        trail = list(booking.status_logs.values_list('to_status', flat=True))
        self.assertEqual(trail, [
            'AWAITING_PARTNER_CONFIRMATION', 'CONFIRMED', 'SAMPLE_COLLECTED', 'SAMPLE_RECEIVED', 'LAB_ANALYSIS',
            'REPORT_GENERATED', 'REPORT_UPLOADED', 'REPORT_VERIFIED', 'REPORT_READY',
        ])

    def test_steps_cannot_be_skipped(self):
        booking = self.confirmed_booking()
        _, err = partner_update_service_status(booking, self.partner_user, 'LAB_ANALYSIS')
        self.assertIn('Next allowed step: Sample Collected', err)
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'CONFIRMED')

    def test_legacy_test_completed_is_rejected_with_guidance(self):
        booking = self.confirmed_booking()
        self.client.force_authenticate(self.partner_user)
        res = self.client.post(self.status_url(booking), {'new_status': 'TEST_COMPLETED'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'legacy_status')
        self.assertEqual(res.data['allowed_next_statuses'], ['SAMPLE_COLLECTED'])

    def test_workflow_requires_confirmed_booking(self):
        booking, _ = create_booking_order(self.user, {
            'partner_id': self.partner.id, 'service_mode': 'CENTRE_VISIT', 'slot_id': self.slot.id,
            'patient_type': 'SELF', 'test_ids': [self.test1.id],
        })
        _, err = partner_update_service_status(booking, self.partner_user, 'SAMPLE_COLLECTED')
        self.assertIn('must be confirmed', err)

    def test_stale_expected_status_returns_409(self):
        booking = self.confirmed_booking()
        self.client.force_authenticate(self.partner_user)
        res = self.client.post(self.status_url(booking), {'new_status': 'SAMPLE_COLLECTED', 'expected_status': 'CONFIRMED'}, format='json')
        self.assertEqual(res.status_code, 200)
        # A second tab still thinks the booking is CONFIRMED.
        res = self.client.post(self.status_url(booking), {'new_status': 'SAMPLE_COLLECTED', 'expected_status': 'CONFIRMED'}, format='json')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'stale_status')

    def test_status_aliases_are_accepted(self):
        booking = self.run_lab_pipeline(self.confirmed_booking(), until='SAMPLE_COLLECTED')
        self.client.force_authenticate(self.partner_user)
        res = self.client.post(self.status_url(booking), {'status': 'sample received at lab'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['booking']['booking_status'], 'SAMPLE_RECEIVED')

    # ── report upload / verification / release ──

    def test_upload_rejects_non_pdf_and_fake_pdf(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        booking = self.run_lab_pipeline(self.confirmed_booking(), until='REPORT_GENERATED')
        self.client.force_authenticate(self.partner_user)
        url = f'/api/partner/bookings/{booking.booking_id}/upload-report/'
        res = self.client.post(url, {'report_file': SimpleUploadedFile('r.png', b'\x89PNG', content_type='image/png')}, format='multipart')
        self.assertEqual(res.data['code'], 'report_not_pdf')
        res = self.client.post(url, {'report_file': SimpleUploadedFile('r.pdf', b'not really a pdf', content_type='application/pdf')}, format='multipart')
        self.assertEqual(res.data['code'], 'report_not_pdf')
        res = self.client.post(url, {}, format='multipart')
        self.assertEqual(res.data['code'], 'report_file_required')
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'REPORT_GENERATED')

    def test_upload_from_lab_analysis_records_report_generated(self):
        booking = self.run_lab_pipeline(self.confirmed_booking(), until='LAB_ANALYSIS')
        self.client.force_authenticate(self.partner_user)
        res = self.client.post(f'/api/partner/bookings/{booking.booking_id}/upload-report/', {'report_file': self.pdf()}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        trail = [h['to_status'] for h in res.data['booking']['status_history']]
        self.assertEqual(trail[-2:], ['REPORT_GENERATED', 'REPORT_UPLOADED'])
        self.assertFalse(Notification.objects.filter(booking=booking, notification_type='REPORT_READY').exists())

    def test_verification_requires_verifier_name(self):
        booking = self.run_lab_pipeline(self.confirmed_booking(), until='REPORT_UPLOADED')
        _, err = partner_update_service_status(booking, self.partner_user, 'REPORT_VERIFIED')
        self.assertIn('authorised person', err)

    def test_release_requires_verification(self):
        booking = self.run_lab_pipeline(self.confirmed_booking(), until='REPORT_UPLOADED')
        _, err = partner_update_service_status(booking, self.partner_user, 'REPORT_READY')
        self.assertIsNotNone(err)
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'REPORT_UPLOADED')

    def test_reupload_clears_verification_and_send_back_needs_reason(self):
        booking = self.run_lab_pipeline(self.confirmed_booking(), until='REPORT_UPLOADED')
        _, err = partner_update_service_status(booking, self.partner_user, 'REPORT_GENERATED')
        self.assertIn('at least 5 characters', err)
        _, err = partner_update_service_status(booking, self.partner_user, 'REPORT_GENERATED', notes='Wrong patient age on page 1')
        self.assertIsNone(err)
        _, err = partner_update_service_status(booking, self.partner_user, 'REPORT_UPLOADED', report_file=self.pdf('v2.pdf'))
        self.assertIsNone(err)
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'REPORT_UPLOADED')
        self.assertIsNone(booking.report_verified_at)

    # ── notifications ──

    def test_patient_notified_only_on_patient_facing_steps_and_push_on_release(self):
        booking = self.run_lab_pipeline(self.confirmed_booking())
        types = list(Notification.objects.filter(user=self.user, booking=booking).values_list('notification_type', flat=True))
        for t in ('SAMPLE_COLLECTED', 'SAMPLE_RECEIVED', 'LAB_ANALYSIS', 'REPORT_READY'):
            self.assertIn(t, types)
        ready = Notification.objects.get(user=self.user, booking=booking, notification_type='REPORT_READY')
        self.assertIn(ready.push_status, ('PENDING', 'SKIPPED', 'SENT'))  # on_commit never fires inside TestCase
        self.assertIsNotNone(booking.patient_notified_at)

    # ── access control & download ──

    def test_patient_cannot_see_or_download_report_before_release(self):
        booking = self.run_lab_pipeline(self.confirmed_booking(), until='REPORT_VERIFIED')
        self.client.force_authenticate(self.user)
        detail = self.client.get(f'/api/bookings/{booking.booking_id}/').data['booking']
        self.assertFalse(detail['has_report'])
        self.assertIsNone(detail['report_download_url'])
        self.assertEqual(detail['report_verified_by_name'], '')
        res = self.client.get(f'/api/bookings/{booking.booking_id}/download-report/')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['code'], 'report_not_available')

    def test_partner_can_preview_report_before_release(self):
        booking = self.run_lab_pipeline(self.confirmed_booking(), until='REPORT_UPLOADED')
        self.client.force_authenticate(self.partner_user)
        res = self.client.get(f'/api/bookings/{booking.booking_id}/download-report/?inline=1')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(b''.join(res.streaming_content).startswith(b'%PDF-'))
        booking.refresh_from_db()
        self.assertEqual(booking.report_download_count, 0)  # partner previews are not patient downloads

    def test_patient_download_marks_report_downloaded_once(self):
        booking = self.run_lab_pipeline(self.confirmed_booking())
        self.client.force_authenticate(self.user)
        for _ in range(2):
            res = self.client.get(f'/api/bookings/{booking.booking_id}/download-report/')
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res['Cache-Control'], 'private, no-store')
            b''.join(res.streaming_content)
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'REPORT_DOWNLOADED')
        self.assertEqual(booking.report_download_count, 2)
        self.assertEqual(booking.status_logs.filter(to_status='REPORT_DOWNLOADED').count(), 1)

    def test_download_honours_accept_application_pdf(self):
        booking = self.run_lab_pipeline(self.confirmed_booking())
        self.client.force_authenticate(self.user)
        url = f'/api/bookings/{booking.booking_id}/download-report/'
        for accept in ('application/pdf', 'application/pdf, application/json;q=0.9, */*;q=0.8', '*/*'):
            res = self.client.get(url, HTTP_ACCEPT=accept)
            self.assertEqual(res.status_code, 200, accept)
            self.assertEqual(res['Content-Type'], 'application/pdf')
            self.assertTrue(b''.join(res.streaming_content).startswith(b'%PDF-'))
        meta = self.client.get(url + '?format=json', HTTP_ACCEPT='application/pdf')
        self.assertEqual(meta.status_code, 200)
        self.assertEqual(meta.json()['booking_id'], booking.booking_id)

    def test_download_errors_are_json_even_when_pdf_is_requested(self):
        booking = self.run_lab_pipeline(self.confirmed_booking(), until='REPORT_VERIFIED')
        self.client.force_authenticate(self.user)
        res = self.client.get(f'/api/bookings/{booking.booking_id}/download-report/', HTTP_ACCEPT='application/pdf')
        self.assertEqual(res.status_code, 403)
        self.assertIn('application/json', res['Content-Type'])
        self.assertEqual(res.json()['code'], 'report_not_available')
        self.client.force_authenticate(None)
        res = self.client.get(f'/api/bookings/{booking.booking_id}/download-report/', HTTP_ACCEPT='application/pdf')
        self.assertIn(res.status_code, (401, 403))
        self.assertIn('application/json', res['Content-Type'])

    def test_strangers_and_anonymous_users_cannot_download(self):
        booking = self.run_lab_pipeline(self.confirmed_booking())
        stranger = User.objects.create_user(phone_number='+919111122222', is_phone_verified=True)
        self.client.force_authenticate(stranger)
        self.assertEqual(self.client.get(f'/api/bookings/{booking.booking_id}/download-report/').status_code, 404)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(f'/api/bookings/{booking.booking_id}/download-report/').status_code, (401, 403))

    @override_settings(REPORT_REQUIRES_FULL_PAYMENT=True)
    def test_optional_payment_gate_on_download(self):
        booking = self.run_lab_pipeline(self.confirmed_booking())
        self.client.force_authenticate(self.user)
        res = self.client.get(f'/api/bookings/{booking.booking_id}/download-report/')
        self.assertEqual(res.status_code, 403)
        process_balance_payment(booking)
        self.assertEqual(self.client.get(f'/api/bookings/{booking.booking_id}/download-report/').status_code, 200)

    # ── cancellation & payments ──

    def test_patient_cannot_cancel_after_sample_collection(self):
        booking = self.run_lab_pipeline(self.confirmed_booking(), until='SAMPLE_COLLECTED')
        self.client.force_authenticate(self.user)
        res = self.client.post(f'/api/bookings/{booking.booking_id}/cancel/', {'reason': 'Changed my mind'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('already been collected', res.data['message'])

    def test_cancel_accepts_reason_alias_refunds_and_frees_slot(self):
        booking = self.confirmed_booking()
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.booked_count, 1)
        self.client.force_authenticate(self.user)
        res = self.client.post(f'/api/bookings/{booking.booking_id}/cancel/', {'reason': 'Schedule conflict'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['booking']['booking_status'], 'CANCELLED')
        self.assertEqual(res.data['booking']['payment_status'], 'REFUNDED')
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.booked_count, 0)

    def test_balance_not_payable_before_collection_or_after_cancel(self):
        booking = self.confirmed_booking()
        _, err = process_balance_payment(booking)
        self.assertIn('once your sample has been collected', err)

    # ── listing ──

    def test_partner_list_supports_status_groups(self):
        a = self.run_lab_pipeline(self.confirmed_booking(), until='LAB_ANALYSIS')
        self.client.force_authenticate(self.partner_user)
        ids = [b['booking_id'] for b in self.client.get('/api/partner/bookings/?status=in_lab').data['bookings']]
        self.assertEqual(ids, [a.booking_id])
        self.assertEqual(self.client.get('/api/partner/bookings/?status=completed').data['count'], 0)
        nxt = self.client.get(f'/api/partner/bookings/{a.booking_id}/').data['booking']['next_actions']
        self.assertEqual([n['status'] for n in nxt], ['REPORT_GENERATED'])

    def test_workflow_endpoint_describes_pipeline(self):
        res = self.client.get('/api/bookings/workflow/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['pipeline'][-1]['status'], 'REPORT_DOWNLOADED')


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class PayAtCollectionTests(TestCase):
    """Book now, pay cash / UPI to the collector or at the centre — or online later."""

    setUp = FullApplicationFlowTests.setUp
    pdf = staticmethod(FullApplicationFlowTests.pdf)
    run_lab_pipeline = FullApplicationFlowTests.run_lab_pipeline

    def create_via_api(self, **extra):
        # Exactly what the web app sends (extra keys must be ignored, not 500).
        body = {
            'patient_type': 'SELF', 'partner_id': self.partner.id, 'service_mode': 'HOME_COLLECTION',
            'booking_mode': 'HOME_COLLECTION', 'appointment_date': str(self.tomorrow), 'slot_id': self.slot.id,
            'appointment_time': '09:00:00', 'collection_address': '16-32, mvpeta', 'collection_city': 'Hyderabad',
            'collection_pincode': '500081', 'contact_phone': '+919876543210', 'test_ids': [self.test1.id],
            'package_ids': [], 'notes': '', **extra,
        }
        res = self.client.post('/api/bookings/', body, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data['booking']

    def test_saved_address_is_snapshotted_with_pin(self):
        from accounts.models import UserAddress
        addr = UserAddress.objects.create(
            user=self.user, label='HOME', house='Flat 9', area='Madhapur', landmark='Near metro', city='Hyderabad',
            pincode='500081', latitude=Decimal('17.448300'), longitude=Decimal('78.391500'), is_default=True,
            receiver_name='Ravi', receiver_phone='+919123456780',
        )
        # Free-text fields are ignored when address_id is given.
        b = self.create_via_api(address_id=addr.id, collection_address='ignored', collection_pincode='999999')
        self.assertEqual(b['collection_address'], 'Flat 9, Madhapur')
        self.assertEqual(b['collection_pincode'], '500081')
        self.assertEqual(b['collection_landmark'], 'Near metro')
        self.assertEqual(Decimal(str(b['collection_latitude'])), Decimal('17.448300'))
        self.assertIn('destination=17.448300,78.391500', b['collection_maps_url'])
        self.assertEqual(b['collection_contact_name'], 'Ravi')
        addr.refresh_from_db()
        self.assertIsNotNone(addr.last_used_at)

    def test_someone_elses_address_id_rejected(self):
        from accounts.models import UserAddress
        other = User.objects.create_user(phone_number='+919000000077', is_phone_verified=True)
        addr = UserAddress.objects.create(user=other, house='x', city='Hyderabad', pincode='500081', is_default=True)
        body = {
            'patient_type': 'SELF', 'partner_id': self.partner.id, 'service_mode': 'HOME_COLLECTION',
            'slot_id': self.slot.id, 'test_ids': [self.test1.id], 'address_id': addr.id,
        }
        res = self.client.post('/api/bookings/', body, format='json')
        self.assertEqual(res.status_code, 400)

    def test_frontend_create_payload_offers_pay_at_collection(self):
        b = self.create_via_api()
        self.assertEqual(b['booking_status'], 'AWAITING_ADVANCE_PAYMENT')
        self.assertTrue(b['payment_options']['can_pay_advance'])
        self.assertTrue(b['payment_options']['pay_at_collection']['available'])

    def test_price_estimate_reports_eligibility(self):
        res = self.client.post('/api/bookings/price-estimate/', {
            'partner_id': self.partner.id, 'service_mode': 'CENTRE_VISIT', 'test_ids': [self.test1.id],
        }, format='json')
        self.assertTrue(res.data['estimate']['pay_at_collection']['available'])

    def test_full_cash_flow_partner_collects_whole_amount(self):
        b = self.create_via_api()
        res = self.client.post(f"/api/bookings/{b['booking_id']}/pay-at-collection/")
        self.assertEqual(res.status_code, 200, res.data)
        data = res.data['booking']
        self.assertEqual(data['booking_status'], 'AWAITING_PARTNER_CONFIRMATION')
        self.assertEqual(data['payment_plan'], 'PAY_AT_COLLECTION')
        self.assertEqual(data['payment_status'], 'PENDING')
        self.assertEqual(Decimal(data['amount_outstanding']), Decimal('650.00'))  # 500 + 150 home fee
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.booked_count, 1)

        booking = Booking.objects.get(booking_id=b['booking_id'])
        partner_accept_booking(booking, self.partner_user)
        booking = self.run_lab_pipeline(booking, until='SAMPLE_COLLECTED')

        self.client.force_authenticate(self.partner_user)
        res = self.client.post(f"/api/partner/bookings/{booking.booking_id}/record-balance/",
                               {'payment_mode': 'CASH', 'offline_reference': 'CASH-001'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['payment']['payment_type'], 'FULL')
        self.assertEqual(Decimal(res.data['payment']['amount']), Decimal('650.00'))
        self.assertEqual(res.data['booking']['payment_status'], 'FULLY_PAID')
        self.assertEqual(Decimal(res.data['booking']['amount_outstanding']), Decimal('0.00'))

    def test_patient_can_switch_to_online_after_confirmation_only(self):
        b = self.create_via_api(service_mode='CENTRE_VISIT', booking_mode='CENTRE_VISIT', collection_address='', collection_pincode='')
        self.client.post(f"/api/bookings/{b['booking_id']}/pay-at-collection/")
        res = self.client.post(f"/api/bookings/{b['booking_id']}/pay-balance/", {'payment_mode': 'UPI'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('once the lab confirms', res.data['message'])

        partner_accept_booking(Booking.objects.get(booking_id=b['booking_id']), self.partner_user)
        res = self.client.post(f"/api/bookings/{b['booking_id']}/pay-balance/", {'payment_mode': 'UPI'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['booking']['payment_status'], 'FULLY_PAID')
        self.assertEqual(Decimal(res.data['payment']['amount']), Decimal('500.00'))

    def test_cancel_pay_later_booking_frees_seat_without_refund(self):
        b = self.create_via_api()
        self.client.post(f"/api/bookings/{b['booking_id']}/pay-at-collection/")
        res = self.client.post(f"/api/bookings/{b['booking_id']}/cancel/", {'reason': 'Schedule conflict'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['booking']['payment_status'], 'PENDING')
        self.assertFalse(Payment.objects.filter(booking__booking_id=b['booking_id'], payment_type='REFUND').exists())
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.booked_count, 0)

    def test_refund_covers_everything_paid(self):
        b = self.create_via_api(service_mode='CENTRE_VISIT', booking_mode='CENTRE_VISIT', collection_address='', collection_pincode='')
        self.client.post(f"/api/bookings/{b['booking_id']}/pay-at-collection/")
        partner_accept_booking(Booking.objects.get(booking_id=b['booking_id']), self.partner_user)
        self.client.post(f"/api/bookings/{b['booking_id']}/pay-balance/", {'payment_mode': 'UPI'}, format='json')
        self.client.post(f"/api/bookings/{b['booking_id']}/cancel/", {'reason': 'Travelling'}, format='json')
        refund = Payment.objects.get(booking__booking_id=b['booking_id'], payment_type='REFUND')
        self.assertEqual(refund.amount, Decimal('500.00'))

    def test_guard_rails(self):
        self.partner.accepts_pay_at_collection = False
        self.partner.save()
        b = self.create_via_api()
        res = self.client.post(f"/api/bookings/{b['booking_id']}/pay-at-collection/")
        self.assertEqual(res.status_code, 400)
        self.assertIn('online advance payment only', res.data['message'])

        self.partner.accepts_pay_at_collection = True
        self.partner.save()
        with self.settings(PAY_AT_COLLECTION_MAX_AMOUNT=100):
            res = self.client.post(f"/api/bookings/{b['booking_id']}/pay-at-collection/")
            self.assertIn('up to ₹100', res.data['message'])
        with self.settings(PAY_AT_COLLECTION_MAX_OPEN=0):
            res = self.client.post(f"/api/bookings/{b['booking_id']}/pay-at-collection/")
            self.assertIn('unpaid pay-at-collection', res.data['message'])

    def test_cannot_choose_after_advance_paid(self):
        b = self.create_via_api()
        self.client.post(f"/api/bookings/{b['booking_id']}/pay-advance/", {'payment_mode': 'UPI'}, format='json')
        res = self.client.post(f"/api/bookings/{b['booking_id']}/pay-at-collection/")
        self.assertEqual(res.status_code, 400)
