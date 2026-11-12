from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from .models import PhoneOTP
from .services import (
    normalize_phone_number,
    create_and_send_registration_otp,
    verify_registration_otp,
    create_user_account,
)

User = get_user_model()


class AccountsRegistrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.test_phone = "+919876543210"

    def test_normalize_phone_number(self):
        self.assertEqual(normalize_phone_number(" +91 98765-43210 "), "+919876543210")
        self.assertEqual(normalize_phone_number("(987) 654-3210"), "9876543210")

    def test_create_and_send_registration_otp(self):
        otp_record, err = create_and_send_registration_otp(self.test_phone)
        self.assertIsNone(err)
        self.assertIsNotNone(otp_record)
        self.assertEqual(otp_record.phone_number, self.test_phone)
        self.assertEqual(len(otp_record.otp_code), 6)
        self.assertFalse(otp_record.is_verified)

    def test_verify_registration_otp_success(self):
        otp_record, _ = create_and_send_registration_otp(self.test_phone)
        verified_record, err = verify_registration_otp(self.test_phone, otp_record.otp_code)
        self.assertIsNone(err)
        self.assertTrue(verified_record.is_verified)

    def test_verify_registration_otp_invalid_code(self):
        otp_record, _ = create_and_send_registration_otp(self.test_phone)
        verified_record, err = verify_registration_otp(self.test_phone, "000000")
        self.assertIsNone(verified_record)
        self.assertIn("Invalid OTP code", err)

        # Check attempt count incremented
        otp_record.refresh_from_db()
        self.assertEqual(otp_record.attempts, 1)

    def test_verify_registration_otp_expired(self):
        otp_record, _ = create_and_send_registration_otp(self.test_phone)
        otp_record.expires_at = timezone.now() - timedelta(minutes=1)
        otp_record.save()

        verified_record, err = verify_registration_otp(self.test_phone, otp_record.otp_code)
        self.assertIsNone(verified_record)
        self.assertIn("expired", err.lower())

    def test_create_user_account(self):
        user_data = {
            'first_name': 'Alice',
            'last_name': 'Smith',
            'email': 'alice@example.com',
            'role': 'PATIENT'
        }
        user, _ = create_user_account(self.test_phone, user_data=user_data)
        self.assertEqual(user.phone_number, self.test_phone)
        self.assertEqual(user.first_name, 'Alice')
        self.assertTrue(user.is_phone_verified)
        self.assertEqual(user.role, 'PATIENT')

    def test_api_send_otp_endpoint(self):
        url = '/api/register/send-otp/'
        payload = {
            'phone_number': '+919999988888',
            'first_name': 'Bob',
            'role': 'PATIENT'
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data.get('success'))

    def test_api_verify_otp_endpoint(self):
        # 1. Send OTP
        send_url = '/api/register/send-otp/'
        send_res = self.client.post(send_url, {'phone_number': '+919111122222'}, format='json')
        self.assertEqual(send_res.status_code, status.HTTP_200_OK)

        otp_code = PhoneOTP.objects.filter(phone_number='+919111122222').latest('created_at').otp_code

        # 2. Verify OTP
        verify_url = '/api/register/verify-otp/'
        verify_res = self.client.post(
            verify_url,
            {
                'phone_number': '+919111122222',
                'otp': otp_code,
                'first_name': 'Charlie',
                'last_name': 'Brown'
            },
            format='json'
        )
        self.assertEqual(verify_res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(verify_res.data.get('success'))
        self.assertEqual(verify_res.data['user']['phone_number'], '+919111122222')
        self.assertEqual(verify_res.data['user']['first_name'], 'Charlie')

    def test_login_flow(self):
        # 1. Create verified user
        User.objects.create_user(
            phone_number='+919222233333',
            password='Password123!',
            is_phone_verified=True
        )

        # 2. Request Login OTP
        login_send_res = self.client.post(
            '/api/login/send-otp/',
            {'phone_number': '+919222233333'},
            format='json'
        )
        self.assertEqual(login_send_res.status_code, status.HTTP_200_OK)

        login_otp = PhoneOTP.objects.filter(
            phone_number='+919222233333',
            purpose='LOGIN'
        ).latest('created_at').otp_code

        # 3. Verify Login OTP
        login_verify_res = self.client.post(
            '/api/login/verify-otp/',
            {
                'phone_number': '+919222233333',
                'otp': login_otp
            },
            format='json'
        )
        self.assertEqual(login_verify_res.status_code, status.HTTP_200_OK)
        self.assertTrue(login_verify_res.data.get('success'))
        self.assertIn('tokens', login_verify_res.data)
        self.assertIn('access', login_verify_res.data['tokens'])
        self.assertIn('refresh', login_verify_res.data['tokens'])

        access_token = login_verify_res.data['tokens']['access']
        refresh_token = login_verify_res.data['tokens']['refresh']

        # 4. Access protected profile using JWT Bearer token
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        profile_res = self.client.get('/api/profile/')
        self.assertEqual(profile_res.status_code, status.HTTP_200_OK)
        self.assertEqual(profile_res.data['user']['phone_number'], '+919222233333')

        # 5. Refresh JWT Access Token
        self.client.credentials()  # clear credentials
        refresh_res = self.client.post('/api/token/refresh/', {'refresh': refresh_token}, format='json')
        self.assertEqual(refresh_res.status_code, status.HTTP_200_OK)
        self.assertIn('access', refresh_res.data)

        # 6. Verify JWT Token
        verify_res = self.client.post('/api/token/verify/', {'token': access_token}, format='json')
        self.assertEqual(verify_res.status_code, status.HTTP_200_OK)


class HealthCardTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from accounts.models import ensure_health_card_tables
        ensure_health_card_tables()

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone_number='+919876500001',
            first_name='Habhi',
            last_name='Kumar',
            gender='MALE',
            date_of_birth='2004-05-15',
            is_phone_verified=True
        )
        self.client.force_authenticate(user=self.user)

    def test_health_card_full_flow(self):
        # 1. GET initial Health Card
        res = self.client.get('/api/health-card/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['health_card']['header']['patient_name'], 'Habhi Kumar')
        self.assertEqual(res.data['health_card']['header']['avatar_initial'], 'H')
        self.assertEqual(res.data['health_card']['header']['gender'], 'Male')

        # 2. PATCH Profile (blood group & emergency contact)
        patch_res = self.client.patch('/api/health-card/profile/', {
            'blood_group': 'O+',
            'emergency_contact_name': 'Rajesh Kumar',
            'emergency_contact_phone': '+919876500002'
        }, format='json')
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_res.data['basic_profile']['blood_group'], 'O+')

        # 3. POST Body Metrics (height 175cm, weight 68kg)
        metric_res = self.client.post('/api/health-card/body-metrics/', {
            'height_cm': 175.0,
            'weight_kg': 68.0
        }, format='json')
        self.assertEqual(metric_res.status_code, status.HTTP_200_OK)
        self.assertEqual(metric_res.data['metrics']['bmi'], 22.2)
        self.assertEqual(metric_res.data['metrics']['bmi_category'], 'Normal')
        self.assertIsNotNone(metric_res.data['metrics']['bmr'])

        # 4. POST Vitals (BP, HR, SpO2, Temp)
        vitals_res = self.client.post('/api/health-card/vitals/', {
            'bp_systolic': 120,
            'bp_diastolic': 80,
            'heart_rate': 72,
            'spo2': 98,
            'temperature_f': 98.4
        }, format='json')
        self.assertEqual(vitals_res.status_code, status.HTTP_200_OK)
        self.assertEqual(vitals_res.data['vitals']['bp_display'], '120/80 mmHg')
        self.assertEqual(vitals_res.data['vitals']['heart_rate'], 72)

        # 5. POST Daily Activity (steps, water)
        act_res = self.client.post('/api/health-card/activity/', {
            'daily_steps': 8500,
            'daily_water_ml': 2500
        }, format='json')
        self.assertEqual(act_res.status_code, status.HTTP_200_OK)
        self.assertEqual(act_res.data['daily_activity']['daily_steps'], 8500)

        # 6. POST Sleep Tracking
        sleep_res = self.client.post('/api/health-card/sleep/', {
            'sleep_hours': 7.5,
            'sleep_quality': 'DEEP'
        }, format='json')
        self.assertEqual(sleep_res.status_code, status.HTTP_200_OK)
        self.assertEqual(sleep_res.data['sleep']['sleep_hours'], 7.5)

        # 7. GET Health Trends
        trend_res = self.client.get('/api/health-card/trends/?days=7')
        self.assertEqual(trend_res.status_code, status.HTTP_200_OK)
        self.assertIn('chart_series', trend_res.data)

        # 8. POST Share Link (24-hour time-limited link)
        share_res = self.client.post('/api/health-card/share/', {
            'duration_hours': 24
        }, format='json')
        self.assertEqual(share_res.status_code, status.HTTP_201_CREATED)
        share_token = share_res.data['share_token']

        # 9. GET Public Shared Link (Unauthenticated view)
        public_client = APIClient()
        public_res = public_client.get(f'/api/health-card/share/{share_token}/')
        self.assertEqual(public_res.status_code, status.HTTP_200_OK)
        self.assertEqual(public_res.data['health_card']['header']['patient_name'], 'Habhi Kumar')
        self.assertEqual(public_res.data['health_card']['summary_pills']['blood'], 'O+')

        # 10. DELETE Revoke Share Link
        revoke_res = self.client.delete(f'/api/health-card/share/{share_token}/revoke/')
        self.assertEqual(revoke_res.status_code, status.HTTP_200_OK)

        # 11. Verify revoked link returns 410 Gone
        revoked_public_res = public_client.get(f'/api/health-card/share/{share_token}/')
        self.assertEqual(revoked_public_res.status_code, status.HTTP_410_GONE)


class SupportAndFAQTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_support_center_overview(self):
        res = self.client.get('/api/support/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['success'])
        self.assertEqual(res.data['app_info']['name'], '1MinDoc')
        self.assertEqual(res.data['app_info']['version'], '1.0.0')
        self.assertEqual(len(res.data['quick_links']), 4)
        self.assertEqual(len(res.data['featured_faqs']), 5)

    def test_faqs_list_and_search(self):
        res = self.client.get('/api/support/faqs/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(res.data['count'], 5)

        # Search for "book"
        search_res = self.client.get('/api/support/faqs/?search=book')
        self.assertEqual(search_res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(search_res.data['count'], 1)

    def test_privacy_and_terms(self):
        priv_res = self.client.get('/api/support/privacy-policy/')
        self.assertEqual(priv_res.status_code, status.HTTP_200_OK)
        self.assertIn('content', priv_res.data)

        terms_res = self.client.get('/api/support/terms-conditions/')
        self.assertEqual(terms_res.status_code, status.HTTP_200_OK)
        self.assertIn('content', terms_res.data)

    def test_support_contact_inquiry(self):
        res = self.client.post('/api/support/contact/', {
            'name': 'Habhi',
            'phone': '+919876543210',
            'subject': 'Sample Collection Query',
            'message': 'Can I reschedule my phlebotomist visit to 10AM?'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data['success'])


class AdminPanelUsersAndFamilyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone_number='+919876543210',
            first_name='Habhi',
            last_name='Kumar',
            gender='MALE',
            is_phone_verified=True
        )
        from accounts.models import FamilyMember
        self.family = FamilyMember.objects.create(
            user=self.user,
            name='Anita Kumar',
            relationship='SPOUSE',
            gender='FEMALE',
            contact_number='+919876543211'
        )

    def test_admin_users_list_and_search(self):
        res = self.client.get('/api/admin/users/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(res.data['count'], 1)
        self.assertEqual(res.data['users'][0]['phone_number'], '+919876543210')
        self.assertEqual(res.data['users'][0]['name'], 'Habhi Kumar')

        # Test search filter
        search_res = self.client.get('/api/admin/users/?search=Habhi')
        self.assertEqual(search_res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(search_res.data['count'], 1)

    def test_admin_family_members_list_and_search(self):
        res = self.client.get('/api/admin/family-members/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(res.data['count'], 1)
        self.assertEqual(res.data['family_members'][0]['name'], 'Anita Kumar')
        self.assertEqual(res.data['family_members'][0]['primary_account'], '+919876543210')
        self.assertEqual(res.data['family_members'][0]['account_status'], 'Active')

        # Test search filter
        search_res = self.client.get('/api/admin/family-members/?search=Anita')
        self.assertEqual(search_res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(search_res.data['count'], 1)

    def test_admin_separate_login_and_me_check(self):
        # 1. Attempt admin login as a normal patient via email -> 403 Forbidden
        self.user.email = 'patient@example.com'
        self.user.set_password('PatientPass123')
        self.user.save()
        bad_login = self.client.post('/api/admin/login/', {
            'email': 'patient@example.com',
            'password': 'PatientPass123'
        }, format='json')
        self.assertEqual(bad_login.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("Access denied", bad_login.data['message'])

        # 2. Missing email validation -> 400 Bad Request
        no_email = self.client.post('/api/admin/login/', {
            'password': 'SomePassword'
        }, format='json')
        self.assertEqual(no_email.status_code, status.HTTP_400_BAD_REQUEST)

        # 3. Create actual Administrator user with email
        admin = User.objects.create_superuser(
            phone_number='+919000000001',
            password='AdminPassSecure123'
        )
        admin.email = 'admin@1mindoc.com'
        admin.save()

        # 4. Successful Admin Login via EMAIL
        login_res = self.client.post('/api/admin/login/', {
            'email': 'admin@1mindoc.com',
            'password': 'AdminPassSecure123'
        }, format='json')
        self.assertEqual(login_res.status_code, status.HTTP_200_OK)
        self.assertTrue(login_res.data['is_admin'])
        token = login_res.data['tokens']['access']

        # 5. Dedicated Admin Me Profile Check
        admin_client = APIClient()
        admin_client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        me_res = admin_client.get('/api/admin/me/')
        self.assertEqual(me_res.status_code, status.HTTP_200_OK)
        self.assertTrue(me_res.data['is_admin'])
        self.assertEqual(me_res.data['admin']['email'], 'admin@1mindoc.com')
        self.assertEqual(me_res.data['admin']['role'], 'ADMIN')
        self.assertTrue(me_res.data['admin']['is_staff'])

        # 6. Normal patient calls /api/admin/me/ -> 403 Forbidden
        patient_client = APIClient()
        patient_client.force_authenticate(user=self.user)
        patient_me = patient_client.get('/api/admin/me/')
        self.assertEqual(patient_me.status_code, status.HTTP_403_FORBIDDEN)



