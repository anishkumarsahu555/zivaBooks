from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
import base64

from home.models import Tenant
from home.tests.utils import SeededTenantMixin, tiny_png

User = get_user_model()


class BusinessProfileTests(SeededTenantMixin, TestCase):

    def test_settings_page_requires_auth(self):
        url = reverse('homeApp:business_profile_settings')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('homeApp:loginPage'))

    def test_settings_page_requires_admin_like_perms(self):
        url = reverse('homeApp:business_profile_settings')
        # Login as customer (not admin-like)
        self.client.force_login(self.customer)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('homeApp:homepage'))

    def test_settings_page_renders_successfully_for_owner(self):
        url = reverse('homeApp:business_profile_settings')
        self.client.force_login(self.owner)
        
        # We need to set tenant in the session context, or the middleware does it.
        # Let's ensure request context has correct host/tenant.
        response = self.client.get(url, HTTP_HOST='alpha-traders.localhost')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'home/settings/businessProfile.html')

    def test_update_business_profile_api_success(self):
        url = reverse('homeApp:update_business_profile_api')
        self.client.force_login(self.owner)

        # 1x1 transparent PNG base64
        base64_img = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="

        post_data = {
            'legalName': 'New Legal Name Pvt Ltd',
            'gstNumber': '27GSTIN1234A1Z5',
            'phone': '+91 99999 88888',
            'billingAddress': '123 New Business Street, Mumbai',
            'logo_data': base64_img
        }

        response = self.client.post(url, post_data, HTTP_HOST='alpha-traders.localhost')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'message': 'success'})

        # Verify tenant database changes
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.legalName, 'New Legal Name Pvt Ltd')
        self.assertEqual(self.tenant.gstNumber, '27GSTIN1234A1Z5')
        self.assertEqual(self.tenant.phone, '+91 99999 88888')
        self.assertEqual(self.tenant.billingAddress, '123 New Business Street, Mumbai')
        self.assertTrue(self.tenant.logo)
