from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from home.models import Tenant, TenantMembership
from home.permission_bundles import tenant_permission_bundle_definitions


User = get_user_model()


class TenantAdminPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username='project.owner',
            email='owner@example.com',
            password='secret123',
        )
        cls.tenant_admin = User.objects.create_user(
            username='tenant.admin',
            email='tenant.admin@example.com',
            password='secret123',
        )
        cls.other_admin = User.objects.create_user(
            username='tenant.admin.2',
            email='tenant.admin.2@example.com',
            password='secret123',
        )
        cls.party_perm = Permission.objects.get(content_type__app_label='home', codename='view_party')
        cls.collection_perm = Permission.objects.get(content_type__app_label='home', codename='add_collection')
        cls.audit_perm = Permission.objects.get(content_type__app_label='home', codename='can_view_audit_logs')
        cls.invoice_series_perm = Permission.objects.get(content_type__app_label='home', codename='view_invoiceseries')

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_superuser_can_create_tenant_with_tenant_admin_permissions(self):
        response = self.client.post(
            reverse('admin:home_tenant_add'),
            data={
                'name': 'Northwind',
                'slug': 'northwind',
                'isActive': 'on',
                'tenant_admin_user': str(self.tenant_admin.pk),
                'admin_permissions': [str(self.party_perm.pk), str(self.collection_perm.pk)],
                '_save': 'Save',
            },
        )

        self.assertEqual(response.status_code, 302)

        tenant = Tenant.objects.get(slug='northwind')
        membership = TenantMembership.objects.get(tenant=tenant, user=self.tenant_admin)
        self.assertEqual(membership.role, TenantMembership.Role.ADMIN)

        group = Group.objects.get(name='northwind:Tenant Admin')
        self.assertCountEqual(
            list(group.permissions.values_list('id', flat=True)),
            [self.party_perm.pk, self.collection_perm.pk],
        )
        self.assertTrue(self.tenant_admin.groups.filter(id=group.id).exists())

    def test_superuser_can_update_tenant_admin_and_permissions(self):
        tenant = Tenant.objects.create(name='Acme', slug='acme', isActive=True)
        TenantMembership.objects.create(
            tenant=tenant,
            user=self.tenant_admin,
            role=TenantMembership.Role.ADMIN,
            isDefault=True,
        )
        group = Group.objects.create(name='acme:Tenant Admin')
        group.permissions.set([self.party_perm])
        self.tenant_admin.groups.add(group)

        response = self.client.post(
            reverse('admin:home_tenant_change', args=[tenant.pk]),
            data={
                'name': 'Acme',
                'slug': 'acme',
                'isActive': 'on',
                'tenant_admin_user': str(self.other_admin.pk),
                'admin_permissions': [str(self.audit_perm.pk)],
                '_save': 'Save',
            },
        )

        self.assertEqual(response.status_code, 302)

        membership = TenantMembership.objects.get(tenant=tenant, user=self.other_admin)
        self.assertEqual(membership.role, TenantMembership.Role.ADMIN)

        group.refresh_from_db()
        self.assertCountEqual(
            list(group.permissions.values_list('id', flat=True)),
            [self.audit_perm.pk],
        )
        self.assertFalse(self.tenant_admin.groups.filter(id=group.id).exists())
        self.assertTrue(self.other_admin.groups.filter(id=group.id).exists())

    def test_audit_permissions_are_available_in_tenant_admin_selector(self):
        response = self.client.get(reverse('admin:home_tenant_add'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Can view audit logs')
        self.assertContains(response, 'Tenant admin permission presets')
        self.assertContains(response, 'Enable Invoice Series')

    def test_related_model_permissions_exist_for_orders_and_parties(self):
        response = self.client.get(reverse('admin:home_tenant_add'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Can view order attachment')
        self.assertContains(response, 'Can view party ledger entry')

    def test_invoice_series_bundle_definition_exists(self):
        bundle = next(
            (item for item in tenant_permission_bundle_definitions() if item['key'] == 'tenant_invoice_series'),
            None,
        )
        self.assertIsNotNone(bundle)
        self.assertEqual(bundle['label'], 'Enable Invoice Series')
        self.assertCountEqual(
            bundle['permissions'],
            [
                'home.view_invoiceseries',
                'home.add_invoiceseries',
                'home.change_invoiceseries',
                'home.delete_invoiceseries',
            ],
        )

    def test_superuser_can_create_tenant_using_bundle_selection(self):
        response = self.client.post(
            reverse('admin:home_tenant_add'),
            data={
                'name': 'Bundle Tenant',
                'slug': 'bundle-tenant',
                'isActive': 'on',
                'tenant_admin_user': str(self.tenant_admin.pk),
                'preset_bundles': ['tenant_invoice_series'],
                '_save': 'Save',
            },
        )

        self.assertEqual(response.status_code, 302)
        tenant = Tenant.objects.get(slug='bundle-tenant')
        group = Group.objects.get(name='bundle-tenant:Tenant Admin')
        self.assertTrue(group.permissions.filter(pk=self.invoice_series_perm.pk).exists())
