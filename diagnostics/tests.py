from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from .models import Category, DiagnosticTest, HealthPackage


class DiagnosticsCatalogTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(
            name="Pathology",
            slug="pathology",
            description="Blood and urine laboratory testing"
        )

    def test_category_create_and_list(self):
        # Create category via API
        res = self.client.post('/api/categories/', {
            'name': 'Radiology',
            'description': 'X-ray, MRI, CT scans'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data['success'])
        self.assertEqual(res.data['category']['name'], 'Radiology')

        # List categories
        list_res = self.client.get('/api/categories/')
        self.assertEqual(list_res.status_code, status.HTTP_200_OK)
        self.assertEqual(list_res.data['count'], 2)

    def test_diagnostic_test_crud_flow(self):
        # 1. Create a diagnostic test
        test_data = {
            'name': 'Complete Blood Count (CBC)',
            'code': 'CBC-001',
            'category': self.category.id,
            'description': 'Evaluates overall health and detects a wide range of disorders.',
            'sample_type': 'BLOOD',
            'fasting_required': False,
            'fasting_duration_hours': 0,
            'turnaround_time_hours': 12,
            'preparation_instructions': 'No special preparation needed.',
            'base_price': '299.00',
            'is_available': True
        }
        create_res = self.client.post('/api/tests/', test_data, format='json')
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(create_res.data['success'])
        test_id = create_res.data['test']['id']

        # 2. Get test detail
        get_res = self.client.get(f'/api/tests/{test_id}/')
        self.assertEqual(get_res.status_code, status.HTTP_200_OK)
        self.assertEqual(get_res.data['test']['code'], 'CBC-001')
        self.assertEqual(get_res.data['test']['base_price'], '299.00')

        # 3. Update test (PATCH)
        patch_res = self.client.patch(f'/api/tests/{test_id}/', {'base_price': '350.00'}, format='json')
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_res.data['test']['base_price'], '350.00')

        # 4. Filter tests
        list_res = self.client.get('/api/tests/?search=CBC')
        self.assertEqual(list_res.status_code, status.HTTP_200_OK)
        self.assertEqual(list_res.data['count'], 1)

        # 5. Delete test
        del_res = self.client.delete(f'/api/tests/{test_id}/')
        self.assertEqual(del_res.status_code, status.HTTP_200_OK)
        self.assertFalse(DiagnosticTest.objects.filter(id=test_id).exists())

    def test_health_package_create_and_detail(self):
        # Create 2 tests
        t1 = DiagnosticTest.objects.create(
            name='Test 1', code='T1', category=self.category,
            description='Test 1', sample_type='BLOOD', base_price='100.00'
        )
        t2 = DiagnosticTest.objects.create(
            name='Test 2', code='T2', category=self.category,
            description='Test 2', sample_type='BLOOD', base_price='200.00'
        )

        # Create package
        pkg_data = {
            'name': 'Wellness Duo Package',
            'code': 'WELL-DUO',
            'description': 'Includes test 1 and test 2',
            'price': '250.00',
            'market_price': '300.00',
            'test_ids': [t1.id, t2.id]
        }
        res = self.client.post('/api/packages/', pkg_data, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data['success'])
        pkg_id = res.data['package']['id']

        # Get package detail
        detail_res = self.client.get(f'/api/packages/{pkg_id}/')
        self.assertEqual(detail_res.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_res.data['package']['tests_count'], 2)

