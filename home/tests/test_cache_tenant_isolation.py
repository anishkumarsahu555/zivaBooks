from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from home.models import Party, Tenant, TenantMembership
from home.tests.utils import SeededTenantMixin


class PartyCacheTenantIsolationTests(SeededTenantMixin, TestCase):
    def setUp(self):
        cache.clear()

    def test_party_list_cache_is_isolated_per_tenant(self):
        other_tenant = Tenant.objects.exclude(pk=self.tenant.pk).order_by("id").first()
        self.assertIsNotNone(other_tenant)

        other_owner_membership = (
            TenantMembership.objects.select_related("user")
            .filter(tenant=other_tenant, role=TenantMembership.Role.OWNER)
            .first()
        )
        self.assertIsNotNone(other_owner_membership)

        alpha_party = Party.objects.create(tenant=self.tenant, name="Alpha Cache Only Party")
        beta_party = Party.objects.create(tenant=other_tenant, name="Beta Cache Only Party")

        self.client.force_login(self.owner)
        alpha_response = self.client.get(reverse("homeApp:list_party_api"))
        self.assertEqual(alpha_response.status_code, 200)
        alpha_names = {row["Name"] for row in alpha_response.json()["data"]}
        self.assertIn(alpha_party.name, alpha_names)

        self.client.force_login(other_owner_membership.user)
        beta_response = self.client.get(reverse("homeApp:list_party_api"))
        self.assertEqual(beta_response.status_code, 200)
        beta_names = {row["Name"] for row in beta_response.json()["data"]}
        self.assertIn(beta_party.name, beta_names)
        self.assertNotIn(alpha_party.name, beta_names)
