from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from partners.models import DiagnosticPartner, PartnerSlot

User = get_user_model()


class PartnerSlotManagementTests(TestCase):
    """Partner-side slot CRUD: validation, overlap detection, range listing and bulk create."""

    URL = '/api/partner/slots/'

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(phone_number='+919800000001', first_name='Lab', is_phone_verified=True)
        self.partner = DiagnosticPartner.objects.create(
            user=self.user, centre_name='Slot Lab', license_number='LIC-SLOT-1',
            contact_person='Ops', contact_phone='+919800000002', contact_email='slot@lab.com',
            address_line='Street 1', city='Hyderabad', state='Telangana', pincode='500081', status='ACTIVE',
        )
        self.client.force_authenticate(self.user)
        self.day = (timezone.localdate() + timedelta(days=2)).isoformat()

    def body(self, start='09:00', end='10:00', mode='CENTRE_VISIT', cap=3, day=None):
        return {'date': day or self.day, 'start_time': start, 'end_time': end, 'service_mode': mode, 'max_capacity': cap}

    def test_create_single_without_partner_in_payload(self):
        res = self.client.post(self.URL, self.body(), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data['slot']['partner'], self.partner.id)
        self.assertEqual(res.data['slot']['available_seats'], 3)

    def test_rejects_end_before_start_and_past_dates(self):
        res = self.client.post(self.URL, self.body(start='10:00', end='09:00'), format='json')
        self.assertEqual(res.status_code, 400)
        past = (timezone.localdate() - timedelta(days=5)).isoformat()
        res = self.client.post(self.URL, self.body(day=past), format='json')
        self.assertEqual(res.status_code, 400)

    def test_overlap_rules(self):
        self.client.post(self.URL, self.body('09:00', '10:00', 'CENTRE_VISIT'), format='json')
        # Same mode overlapping -> rejected
        self.assertEqual(self.client.post(self.URL, self.body('09:30', '10:30', 'CENTRE_VISIT'), format='json').status_code, 400)
        # Touching edges is fine
        self.assertEqual(self.client.post(self.URL, self.body('10:00', '11:00', 'CENTRE_VISIT'), format='json').status_code, 201)
        # Different mode at the same time is fine
        self.assertEqual(self.client.post(self.URL, self.body('09:00', '10:00', 'HOME_COLLECTION'), format='json').status_code, 201)
        # ALL conflicts with everything
        self.assertEqual(self.client.post(self.URL, self.body('09:15', '09:45', 'ALL'), format='json').status_code, 400)

    def test_range_listing_and_summary(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.client.post(self.URL, self.body(day=tomorrow), format='json')
        self.client.post(self.URL, self.body(), format='json')
        res = self.client.get(self.URL, {'start_date': tomorrow, 'end_date': self.day})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['slots']), 2)
        self.assertEqual(res.data['summary']['total_capacity'], 6)
        # Legacy single-day query still works
        self.assertEqual(len(self.client.get(self.URL, {'date': self.day}).data['slots']), 1)

    def test_bulk_skip_reports_conflicts_including_in_batch(self):
        self.client.post(self.URL, self.body('09:00', '10:00'), format='json')
        payload = {'slots': [
            self.body('09:00', '10:00'),   # clashes with existing
            self.body('10:00', '11:00'),
            self.body('10:30', '11:30'),   # clashes with the previous item in this batch
            self.body('11:30', '12:30'),
        ]}
        res = self.client.post(self.URL, payload, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['created_count'], 2)
        self.assertEqual([s['index'] for s in res.data['skipped']], [0, 2])

    def test_bulk_fail_mode_is_atomic(self):
        payload = {'on_conflict': 'fail', 'slots': [self.body('09:00', '10:00'), self.body('09:30', '10:30')]}
        res = self.client.post(self.URL, payload, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(PartnerSlot.objects.count(), 0)

    def test_patch_and_delete_guards(self):
        slot_id = self.client.post(self.URL, self.body(cap=3), format='json').data['slot']['id']
        PartnerSlot.objects.filter(pk=slot_id).update(booked_count=2)
        url = f'{self.URL}{slot_id}/'
        self.assertEqual(self.client.patch(url, {'max_capacity': 1}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'start_time': '08:00'}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'max_capacity': 5}, format='json').status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 409)
        self.assertEqual(self.client.patch(url, {'is_active': False}, format='json').status_code, 200)

        free_id = self.client.post(self.URL, self.body('12:00', '13:00'), format='json').data['slot']['id']
        self.assertEqual(self.client.delete(f'{self.URL}{free_id}/').status_code, 200)

    def test_cannot_touch_another_partners_slot(self):
        other_user = User.objects.create_user(phone_number='+919800000009', is_phone_verified=True)
        other = DiagnosticPartner.objects.create(
            user=other_user, centre_name='Other', license_number='LIC-O', contact_person='x',
            contact_phone='+919800000010', contact_email='o@lab.com', address_line='a', city='c', state='s', pincode='500001',
        )
        slot = PartnerSlot.objects.create(partner=other, date=self.day, start_time=time(9), end_time=time(10))
        self.assertEqual(self.client.delete(f'{self.URL}{slot.id}/').status_code, 404)
