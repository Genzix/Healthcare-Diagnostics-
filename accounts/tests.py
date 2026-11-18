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





class FamilyMemberPhoneVerificationTests(TestCase):
    """Adults need an OTP-verified phone; children / minors may skip it."""

    SEND = '/api/family-members/phone/send-otp/'
    VERIFY = '/api/family-members/phone/verify-otp/'
    LIST = '/api/family-members/'

    def setUp(self):
        from django.test import override_settings
        self._settings = override_settings(OTP_RESEND_WAIT_SECONDS=0)
        self._settings.enable()
        self.addCleanup(self._settings.disable)
        self.user = User.objects.create_user(phone_number='+919876543210', first_name='Asha', is_phone_verified=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _otp(self, phone):
        return PhoneOTP.objects.filter(purpose='FAMILY_MEMBER', phone_number=phone).latest('created_at')

    def _verified_token(self, phone='9123456780'):
        self.assertEqual(self.client.post(self.SEND, {'phone_number': phone}).status_code, 200)
        otp = self._otp('+91' + phone[-10:])
        res = self.client.post(self.VERIFY, {'phone_number': phone, 'otp': otp.otp_code})
        self.assertEqual(res.status_code, 200, res.data)
        return res.data['verification_token']

    def _adult(self, **extra):
        return {'name': 'Ravi', 'relationship': 'SPOUSE', 'gender': 'MALE', 'date_of_birth': '1990-01-01', **extra}

    def test_adult_requires_phone(self):
        res = self.client.post(self.LIST, self._adult(), format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('phone_number', res.data['errors'])

    def test_adult_with_unverified_phone_rejected(self):
        res = self.client.post(self.LIST, self._adult(phone_number='+919123456780'), format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Verify', str(res.data['errors']['phone_number']))

    def test_child_without_phone_allowed(self):
        res = self.client.post(self.LIST, {'name': 'Kid', 'relationship': 'CHILD', 'gender': 'FEMALE', 'date_of_birth': '2018-05-05'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertFalse(res.data['family_member']['phone_required'])

    def test_minor_sibling_without_phone_allowed(self):
        dob = (timezone.localdate() - timedelta(days=365 * 12)).isoformat()
        res = self.client.post(self.LIST, {'name': 'Bro', 'relationship': 'SIBLING', 'gender': 'MALE', 'date_of_birth': dob}, format='json')
        self.assertEqual(res.status_code, 201, res.data)

    def test_full_flow_creates_verified_member(self):
        token = self._verified_token()
        res = self.client.post(self.LIST, self._adult(phone_number='9123456780', phone_verification_token=token), format='json')
        self.assertEqual(res.status_code, 201, res.data)
        m = res.data['family_member']
        self.assertEqual(m['phone_number'], '+919123456780')
        self.assertTrue(m['is_phone_verified'])
        self.assertNotIn('phone_verification_token', m)

    def test_token_is_single_use(self):
        token = self._verified_token()
        self.client.post(self.LIST, self._adult(phone_number='9123456780', phone_verification_token=token), format='json')
        res = self.client.post(self.LIST, self._adult(name='Other', phone_number='9123456780', phone_verification_token=token), format='json')
        self.assertEqual(res.status_code, 400)

    def test_token_bound_to_phone(self):
        token = self._verified_token('9123456780')
        res = self.client.post(self.LIST, self._adult(phone_number='9000000001', phone_verification_token=token), format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('not the one verified', str(res.data['errors']['phone_number']))

    def test_token_bound_to_account(self):
        token = self._verified_token()
        other = User.objects.create_user(phone_number='+919000000099', is_phone_verified=True)
        c = APIClient(); c.force_authenticate(other)
        res = c.post(self.LIST, self._adult(phone_number='9123456780', phone_verification_token=token), format='json')
        self.assertEqual(res.status_code, 400)

    def test_own_number_rejected(self):
        res = self.client.post(self.SEND, {'phone_number': '9876543210'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'own_phone')

    def test_wrong_otp_counts_attempts_then_locks(self):
        self.client.post(self.SEND, {'phone_number': '9123456780'})
        for i in range(4):
            res = self.client.post(self.VERIFY, {'phone_number': '9123456780', 'otp': '000000' if self._otp('+919123456780').otp_code != '000000' else '111111'})
            self.assertEqual(res.data['code'], 'otp_mismatch')
        res = self.client.post(self.VERIFY, {'phone_number': '9123456780', 'otp': '000000' if self._otp('+919123456780').otp_code != '000000' else '111111'})
        self.assertEqual(res.data['code'], 'otp_locked')
        res = self.client.post(self.VERIFY, {'phone_number': '9123456780', 'otp': self._otp('+919123456780').otp_code})
        self.assertEqual(res.data['code'], 'otp_locked')

    def test_resend_cooldown(self):
        from django.test import override_settings
        with override_settings(OTP_RESEND_WAIT_SECONDS=60):
            self.client.post(self.SEND, {'phone_number': '9123456780'})
            res = self.client.post(self.SEND, {'phone_number': '9123456780'})
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data['code'], 'resend_cooldown')
        self.assertIn('Retry-After', res)

    def test_new_code_invalidates_previous(self):
        self.client.post(self.SEND, {'phone_number': '9123456780'})
        old = self._otp('+919123456780').otp_code
        self.client.post(self.SEND, {'phone_number': '9123456780'})
        new = self._otp('+919123456780').otp_code
        if old != new:
            PhoneOTP.objects.filter(otp_code=new).update(otp_code='999999' if old != '999999' else '888888')
            res = self.client.post(self.VERIFY, {'phone_number': '9123456780', 'otp': old})
            self.assertEqual(res.data['code'], 'otp_mismatch')

    def test_duplicate_number_in_family_rejected(self):
        token = self._verified_token()
        self.client.post(self.LIST, self._adult(phone_number='9123456780', phone_verification_token=token), format='json')
        res = self.client.post(self.SEND, {'phone_number': '+91 91234 56780'})
        self.assertEqual(res.data['code'], 'duplicate_phone')

    def test_edit_without_changing_verified_phone_needs_no_otp(self):
        token = self._verified_token()
        mid = self.client.post(self.LIST, self._adult(phone_number='9123456780', phone_verification_token=token), format='json').data['family_member']['id']
        res = self.client.put(f'{self.LIST}{mid}/', self._adult(name='Ravi Kumar', phone_number='+919123456780'), format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data['family_member']['is_phone_verified'])

    def test_edit_changing_phone_needs_new_otp(self):
        token = self._verified_token()
        mid = self.client.post(self.LIST, self._adult(phone_number='9123456780', phone_verification_token=token), format='json').data['family_member']['id']
        res = self.client.put(f'{self.LIST}{mid}/', self._adult(phone_number='9000000001'), format='json')
        self.assertEqual(res.status_code, 400)

    def test_changing_child_to_spouse_requires_phone(self):
        mid = self.client.post(self.LIST, {'name': 'Kid', 'relationship': 'CHILD', 'gender': 'FEMALE', 'date_of_birth': '2018-05-05'}, format='json').data['family_member']['id']
        res = self.client.patch(f'{self.LIST}{mid}/', {'relationship': 'SPOUSE', 'date_of_birth': '1990-01-01'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('phone_number', res.data['errors'])

    def test_send_requires_auth(self):
        res = APIClient().post(self.SEND, {'phone_number': '9123456780'})
        self.assertIn(res.status_code, (401, 403))


from unittest import mock  # noqa: E402


class SavedAddressTests(TestCase):
    URL = '/api/addresses/'

    def setUp(self):
        self.user = User.objects.create_user(phone_number='+919876543210', first_name='Asha', is_phone_verified=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _body(self, **kw):
        return {
            'label': 'HOME', 'house': 'Flat 402, Sai Residency', 'area': 'MVP Colony', 'landmark': 'Opp. park',
            'city': 'Visakhapatnam', 'state': 'Andhra Pradesh', 'pincode': '530017',
            'latitude': 17.7409123456789, 'longitude': 83.3360987654321, 'location_source': 'GPS', **kw,
        }

    def _add(self, **kw):
        return self.client.post(self.URL, self._body(**kw), format='json')

    def test_first_address_becomes_default_and_mirrors_profile(self):
        res = self._add()
        self.assertEqual(res.status_code, 201, res.data)
        a = res.data['address']
        self.assertTrue(a['is_default'])
        self.assertEqual(str(a['latitude']), '17.740912')  # rounded to 6 dp
        self.user.refresh_from_db()
        self.assertEqual(self.user.pincode, '530017')
        self.assertIn('Flat 402', self.user.address)

    def test_location_required(self):
        res = self._add(latitude=None, longitude=None)
        self.assertEqual(res.status_code, 400)
        self.assertIn('latitude', res.data['errors'])

    def test_outside_india_rejected(self):
        res = self._add(latitude=51.5, longitude=-0.12)
        self.assertEqual(res.status_code, 400)

    def test_invalid_pincode(self):
        self.assertEqual(self._add(pincode='012345').status_code, 400)

    def test_second_home_conflicts_then_replace(self):
        first = self._add().data['address']['id']
        res = self._add(house='New house')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 'label_taken')
        self.assertEqual(res.data['existing_id'], first)
        res = self.client.post(self.URL + '?replace_label=true', self._body(house='New house'), format='json')
        self.assertEqual(res.status_code, 201, res.data)
        from accounts.models import UserAddress
        old = UserAddress.objects.get(pk=first)
        self.assertEqual(old.label, 'OTHER')

    def test_other_requires_name_and_allows_many(self):
        self.assertEqual(self._add(label='OTHER').status_code, 400)
        self.assertEqual(self._add(label='OTHER', custom_label="Mom's").status_code, 201)
        self.assertEqual(self._add(label='OTHER', custom_label='Gym').status_code, 201)

    def test_set_default_is_exclusive(self):
        a = self._add().data['address']['id']
        b = self._add(label='WORK', city='Hyderabad', pincode='500081').data['address']['id']
        res = self.client.post(f'{self.URL}{b}/set-default/')
        self.assertEqual(res.status_code, 200)
        defaults = [x['id'] for x in res.data['addresses'] if x['is_default']]
        self.assertEqual(defaults, [b])
        self.assertEqual(res.data['addresses'][0]['id'], b)  # default listed first
        self.user.refresh_from_db()
        self.assertEqual(self.user.city, 'Hyderabad')
        self.assertNotEqual(a, b)

    def test_delete_default_promotes_another(self):
        a = self._add().data['address']['id']
        b = self._add(label='WORK').data['address']['id']
        res = self.client.delete(f'{self.URL}{a}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['new_default_id'], b)

    def test_cannot_touch_other_users_address(self):
        a = self._add().data['address']['id']
        other = User.objects.create_user(phone_number='+919000000001', is_phone_verified=True)
        c = APIClient(); c.force_authenticate(other)
        self.assertEqual(c.patch(f'{self.URL}{a}/', {'house': 'x'}, format='json').status_code, 404)
        self.assertEqual(c.delete(f'{self.URL}{a}/').status_code, 404)

    def test_receiver_phone_needs_name(self):
        res = self._add(receiver_phone='9123456780')
        self.assertEqual(res.status_code, 400)
        res = self._add(receiver_phone='9123456780', receiver_name='Ravi')
        self.assertEqual(res.data['address']['receiver_phone'], '+919123456780')

    def test_legacy_address_must_be_pinned_on_edit(self):
        from accounts.models import UserAddress
        legacy = UserAddress.objects.create(user=self.user, house='16-32', city='Anakapalli', pincode='531001', location_source='LEGACY', is_default=True)
        res = self.client.patch(f'{self.URL}{legacy.pk}/', {'house': '16-33'}, format='json')
        self.assertEqual(res.status_code, 400)
        res = self.client.patch(f'{self.URL}{legacy.pk}/', {'house': '16-33', 'latitude': 17.69, 'longitude': 83.0, 'location_source': 'MAP'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)

    @mock.patch('accounts.addresses.requests.get')
    def test_reverse_geocode_parses_and_caches(self, get):
        from django.core.cache import cache
        cache.clear()
        get.return_value.json.return_value = {
            'lat': '17.74', 'lon': '83.33', 'display_name': 'MVP Colony, Visakhapatnam',
            'address': {'road': 'Sector 6', 'suburb': 'MVP Colony', 'city': 'Visakhapatnam', 'state': 'Andhra Pradesh', 'postcode': '530017', 'country_code': 'in'},
        }
        get.return_value.raise_for_status.return_value = None
        res = self.client.get('/api/addresses/reverse-geocode/?lat=17.7409&lng=83.3361')
        self.assertEqual(res.status_code, 200, res.data)
        r = res.data['result']
        self.assertEqual(r['pincode'], '530017')
        self.assertEqual(r['city'], 'Visakhapatnam')
        self.assertEqual(r['latitude'], 17.7409)  # user's pin, not the geocoder's
        self.client.get('/api/addresses/reverse-geocode/?lat=17.7409&lng=83.3361')
        self.assertEqual(get.call_count, 1)

    @mock.patch('accounts.addresses.requests.get')
    def test_geocoder_down_returns_503(self, get):
        import requests as rq
        from django.core.cache import cache
        cache.clear()
        get.side_effect = rq.ConnectionError('down')
        res = self.client.get('/api/addresses/search/?q=mvp colony')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.data['code'], 'geocoder_unavailable')

    def test_short_search_returns_empty_without_calling_provider(self):
        with mock.patch('accounts.addresses.requests.get') as get:
            res = self.client.get('/api/addresses/search/?q=mv')
        self.assertEqual(res.data['results'], [])
        get.assert_not_called()

    def test_legacy_profile_address_migration(self):
        import importlib
        from django.apps import apps as global_apps
        from accounts.models import UserAddress
        mig = importlib.import_module('accounts.migrations.0007_migrate_profile_addresses')
        u = User.objects.create_user(phone_number='+919000000055', address='16-32, mvpeta, Anakapalli, 531001', city='Anakapalli', pincode='531001')
        User.objects.create_user(phone_number='+919000000056', address='somewhere', city='', pincode='')  # skipped
        mig.forwards(global_apps, None)
        mig.forwards(global_apps, None)  # idempotent
        rows = UserAddress.objects.filter(user=u)
        self.assertEqual(rows.count(), 1)
        a = rows.get()
        self.assertEqual((a.house, a.area, a.pincode, a.is_default, a.has_location), ('16-32', 'mvpeta', '531001', True, False))
