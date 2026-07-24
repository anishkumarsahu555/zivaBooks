import json
from django.test import TestCase, Client
from django.urls import reverse
from home.models import LicenseInquiry


class ContactInquiryTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse('homeApp:contact_inquiry_api')

    def test_successful_form_submission(self):
        payload = {
            'full_name': 'Sarah Connor',
            'business_name': 'Cyberdyne Systems',
            'email': 'sarah@cyberdyne.com',
            'phone': '+15551234567',
            'license_plan': 'enterprise',
            'user_seats': '15',
            'message': 'Interested in enterprise cloud deployment for 15 locations.'
        }
        response = self.client.post(self.url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertIn('inquiry_id', data)

        inquiry = LicenseInquiry.objects.get(id=data['inquiry_id'])
        self.assertEqual(inquiry.full_name, 'Sarah Connor')
        self.assertEqual(inquiry.business_name, 'Cyberdyne Systems')
        self.assertEqual(inquiry.email, 'sarah@cyberdyne.com')
        self.assertEqual(inquiry.license_plan, 'enterprise')
        self.assertEqual(inquiry.user_seats, 15)
        self.assertEqual(inquiry.status, 'new')

    def test_form_data_encoded_submission(self):
        response = self.client.post(self.url, {
            'full_name': 'John Doe',
            'email': 'john@example.com',
            'phone': '9876543210',
            'license_plan': 'growth',
            'user_seats': '5'
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertTrue(LicenseInquiry.objects.filter(email='john@example.com').exists())

    def test_missing_required_fields_validation(self):
        # Missing name
        res1 = self.client.post(self.url, {'email': 'test@example.com', 'phone': '12345678'})
        self.assertEqual(res1.status_code, 400)
        self.assertEqual(res1.json()['status'], 'error')

        # Invalid email
        res2 = self.client.post(self.url, {'full_name': 'Alex', 'email': 'not-an-email', 'phone': '12345678'})
        self.assertEqual(res2.status_code, 400)

        # Invalid phone
        res3 = self.client.post(self.url, {'full_name': 'Alex', 'email': 'alex@example.com', 'phone': '123'})
        self.assertEqual(res3.status_code, 400)
