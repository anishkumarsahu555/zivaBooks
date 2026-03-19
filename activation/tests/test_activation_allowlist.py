from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase

from activation.models import Validity
from home.models import Party, Tenant, TenantMembership


class TenantActivationAllowlistTests(TestCase):
    def test_expired_tenant_blocks_api_requests_with_json(self):
        tenant = Tenant.objects.create(name="Acme", slug="acme")
        user_model = get_user_model()
        user = user_model.objects.create_user(username="api-user", password="pass12345")
        TenantMembership.objects.create(user=user, tenant=tenant, role=TenantMembership.Role.STAFF, isDefault=True)

        Validity.objects.create(
            tenant=tenant,
            activationDate=date.today() - timedelta(days=10),
            expiryDate=date.today() - timedelta(days=1),
            activationType="trial",
        )

        self.client.force_login(user)
        response = self.client.get(
            "/api/get_staff_dashboard_report_api/",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload.get("message"), "license_expired")
        self.assertEqual(payload.get("redirect"), "/activate/")

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

    def test_expired_tenant_allows_static_and_admin_paths(self):
        tenant = Tenant.objects.create(name="Acme", slug="acme")
        user_model = get_user_model()
        user = user_model.objects.create_user(username="u3", password="pass12345")
        TenantMembership.objects.create(user=user, tenant=tenant, role=TenantMembership.Role.STAFF, isDefault=True)

        Validity.objects.create(
            tenant=tenant,
            activationDate=date.today() - timedelta(days=10),
            expiryDate=date.today() - timedelta(days=1),
            activationType="trial",
        )

        self.client.force_login(user)

        static_response = self.client.get("/static/sw/serviceworker.js")
        self.assertNotEqual(static_response.status_code, 302)
        if static_response.status_code == 302:
            self.assertNotEqual(static_response["Location"], "/activate/")

        admin_response = self.client.get("/admin/")
        if admin_response.status_code == 302:
            self.assertNotEqual(admin_response["Location"], "/activate/")

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

    def test_tenant_specific_validity_overrides_global(self):
        global_validity_tenant = None
        tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        user_model = get_user_model()

        user_a = user_model.objects.create_user(username="ua", password="pass12345")
        user_b = user_model.objects.create_user(username="ub", password="pass12345")
        TenantMembership.objects.create(user=user_a, tenant=tenant_a, role=TenantMembership.Role.STAFF, isDefault=True)
        TenantMembership.objects.create(user=user_b, tenant=tenant_b, role=TenantMembership.Role.STAFF, isDefault=True)

        Validity.objects.create(
            tenant=global_validity_tenant,
            activationDate=date.today() - timedelta(days=1),
            expiryDate=date.today() + timedelta(days=10),
            activationType="global",
        )
        Validity.objects.create(
            tenant=tenant_a,
            activationDate=date.today() - timedelta(days=10),
            expiryDate=date.today() - timedelta(days=1),
            activationType="expired-a",
        )

        self.client.force_login(user_a)
        response = self.client.get("/customer_home/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/activate/")

        self.client.force_login(user_b)
        response = self.client.get("/home/")
        self.assertEqual(response.status_code, 200)

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

