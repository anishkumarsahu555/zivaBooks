from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase

from home.models import Tenant, TenantMembership

from .models import Validity
from home.models import Party


class TenantActivationAllowlistTests(TestCase):
    def test_expired_tenant_redirects_non_allowlisted_paths(self):
        tenant = Tenant.objects.create(name="Acme", slug="acme")
        user_model = get_user_model()
        user = user_model.objects.create_user(username="u1", password="pass12345")
        TenantMembership.objects.create(user=user, tenant=tenant, role=TenantMembership.Role.STAFF, isDefault=True)

        Validity.objects.create(
            tenant=tenant,
            activationDate=date.today() - timedelta(days=10),
            expiryDate=date.today() - timedelta(days=1),
            activationType="trial",
        )

        self.client.force_login(user)
        response = self.client.get("/customer_home/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/activate/")

    def test_expired_tenant_allows_home_and_shows_banner_message(self):
        tenant = Tenant.objects.create(name="Acme", slug="acme")
        user_model = get_user_model()
        user = user_model.objects.create_user(username="u1", password="pass12345")
        TenantMembership.objects.create(user=user, tenant=tenant, role=TenantMembership.Role.STAFF, isDefault=True)

        Validity.objects.create(
            tenant=tenant,
            activationDate=date.today() - timedelta(days=10),
            expiryDate=date.today() - timedelta(days=1),
            activationType="trial",
        )

        self.client.force_login(user)
        response = self.client.get("/home/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activation expired. Please contact ZivaInfotech for activation.")

    def test_postlogin_returns_activate_when_expired(self):
        tenant = Tenant.objects.create(name="Acme", slug="acme")
        user_model = get_user_model()
        user = user_model.objects.create_user(username="u2", password="pass12345")
        TenantMembership.objects.create(user=user, tenant=tenant, role=TenantMembership.Role.STAFF, isDefault=True)

        Validity.objects.create(
            tenant=tenant,
            activationDate=date.today() - timedelta(days=10),
            expiryDate=date.today() - timedelta(days=1),
            activationType="trial",
        )

        response = self.client.post("/postLogin/", {"userName": "u2", "password": "pass12345"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("message"), "success")
        self.assertEqual(payload.get("data"), "/activate/")

    def test_party_user_sees_customer_navigation_on_activate(self):
        tenant = Tenant.objects.create(name="Acme", slug="acme")
        user_model = get_user_model()
        user = user_model.objects.create_user(username="cust1", password="pass12345")
        TenantMembership.objects.create(user=user, tenant=tenant, role=TenantMembership.Role.PARTY, isDefault=True)
        Party.objects.create(tenant=tenant, name="Customer One", userID_id=user.pk)

        Validity.objects.create(
            tenant=tenant,
            activationDate=date.today() - timedelta(days=10),
            expiryDate=date.today() - timedelta(days=1),
            activationType="trial",
        )

        self.client.force_login(user)
        response = self.client.get("/activate/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Customer Portal")
        self.assertContains(response, "Activation expired")
