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

