from django.test import TestCase
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType

from home.models import AuditLog, Tenant
from home.tests.utils import SeededTenantMixin


class TenantSettingsEndToEndTests(SeededTenantMixin, TestCase):
    def setUp(self):
        self.client.force_login(self.owner)

    def test_owner_can_configure_shadow_and_feature_flags_end_to_end(self):
        status_response = self.client.get(reverse('homeApp:get_non_gst_shadow_status_api'))
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()['message'], 'success')
        self.assertEqual(status_response.json()['data']['tenantName'], self.tenant.name)

        configure_response = self.client.post(
            reverse('homeApp:configure_non_gst_shadow_code_api'),
            data={'password': 'alpha123', 'accessCode': '123456'},
        )
        self.assertEqual(configure_response.status_code, 200, msg=configure_response.content)
        self.assertEqual(configure_response.json()['message'], 'success')

        toggle_response = self.client.post(
            reverse('homeApp:toggle_non_gst_shadow_api'),
            data={'password': 'alpha123', 'accessCode': '123456', 'enabled': 'true'},
        )
        self.assertEqual(toggle_response.status_code, 200, msg=toggle_response.content)
        self.assertEqual(toggle_response.json()['message'], 'success')
        self.assertTrue(toggle_response.json()['enabled'])

        flags_response = self.client.post(
            reverse('homeApp:update_tenant_feature_flags_api'),
            data={
                'password': 'alpha123',
                'accessCode': '123456',
                'inventoryEnabled': 'false',
                'advancedInvoicingEnabled': 'true',
            },
        )
        self.assertEqual(flags_response.status_code, 200, msg=flags_response.content)
        self.assertEqual(flags_response.json()['message'], 'success')
        self.assertFalse(flags_response.json()['data']['inventoryEnabled'])
        self.assertTrue(flags_response.json()['data']['advancedInvoicingEnabled'])

        self.tenant.refresh_from_db()
        self.assertTrue(self.tenant.nonGstShadowEnabled)
        self.assertFalse(self.tenant.inventoryEnabled)
        self.assertTrue(self.tenant.advancedInvoicingEnabled)
        tenant_content_type = ContentType.objects.get_for_model(Tenant)
        self.assertGreaterEqual(
            AuditLog.objects.filter(
                content_type=tenant_content_type,
                object_id=self.tenant.pk,
                action=AuditLog.Action.UPDATE,
            ).count(),
            3,
        )

    def test_invalid_access_code_does_not_update_flags(self):
        self.client.post(
            reverse('homeApp:configure_non_gst_shadow_code_api'),
            data={'password': 'alpha123', 'accessCode': '123456'},
        )
        response = self.client.post(
            reverse('homeApp:update_tenant_feature_flags_api'),
            data={
                'password': 'alpha123',
                'accessCode': '000000',
                'inventoryEnabled': 'false',
                'advancedInvoicingEnabled': 'false',
            },
        )
        self.assertEqual(response.status_code, 200, msg=response.content)
        self.assertEqual(response.json()['message'], 'error')
        self.assertEqual(response.json()['error'], 'Invalid 6-digit passcode.')

        self.tenant.refresh_from_db()
        self.assertTrue(self.tenant.inventoryEnabled)
        self.assertTrue(self.tenant.advancedInvoicingEnabled)
