from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from home.models import Tenant, TenantMembership

User = get_user_model()


class ProfileRouteTests(TestCase):
    def test_myprofile_works_without_staff_profile(self):
        user = User.objects.create_superuser(
            username='profile.owner',
            email='profile.owner@example.com',
            password='secret123',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('homeApp:my_profile_collection'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My Profile')
        self.assertContains(response, 'Project Owner')

    def test_my_profile_works_without_staff_profile(self):
        user = User.objects.create_superuser(
            username='profile.admin',
            email='profile.admin@example.com',
            password='secret123',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('homeApp:my_profile'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My Profile')
        self.assertContains(response, 'Project Owner')

    def test_tenant_owner_profile_shows_tenant_owner_label(self):
        user = User.objects.create_user(
            username='tenant.owner',
            email='tenant.owner@example.com',
            password='secret123',
        )
        tenant = Tenant.objects.create(name='Route Tenant', slug='route-tenant', isActive=True)
        TenantMembership.objects.create(
            tenant=tenant,
            user=user,
            role=TenantMembership.Role.OWNER,
            isDefault=True,
        )
        self.client.force_login(user)

        response = self.client.get(reverse('homeApp:my_profile'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Tenant Owner')
