from __future__ import annotations

from datetime import date

from django.test import Client, TestCase
from django.test.utils import override_settings

from home.models import Bank, Order, Party, PartyGroup
from home.otp import get_collection_otp_payload
from home.tests.utils import SeededTenantMixin


@override_settings(
    CACHES={
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    }
)
class FunctionalEndToEndFlowsTests(SeededTenantMixin, TestCase):
    def _login_via_postlogin(self, client: Client, username: str, password: str) -> str:
        response = client.post("/postLogin/", data={"userName": username, "password": password})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("message"), "success")
        return payload.get("data") or ""

    def test_login_routing_owner_staff_customer(self):
        owner_client = Client()
        dest = self._login_via_postlogin(owner_client, "alpha.owner", "alpha123")
        self.assertEqual(dest, "/home/")

        staff_client = Client()
        dest = self._login_via_postlogin(staff_client, "alphatraders.collector", "collect123")
        self.assertEqual(dest, "/home/")

        customer_client = Client()
        dest = self._login_via_postlogin(customer_client, "alphatraders.customer", "customer123")
        self.assertEqual(dest, "/customer_home/")

    def test_collection_otp_end_to_end_with_seeded_users(self):
        staff_client = Client()
        self._login_via_postlogin(staff_client, "alphatraders.collector", "collect123")

        # Ensure collection form page loads (sets up expected UI flow).
        resp = staff_client.get("/add_collection/")
        self.assertEqual(resp.status_code, 200)

        otp_resp = staff_client.post(
            "/api/request_collection_otp_api/",
            data={
                "party": str(self.party.pk),
                "paymentMode": "Cash",
                "amountPaid": "100",
                "collectDate": date.today().strftime("%d/%m/%Y"),
            },
        )
        self.assertEqual(otp_resp.status_code, 200)
        self.assertEqual(otp_resp.json().get("message"), "success")

        otp_payload = get_collection_otp_payload(self.party.tenant_id, self.party.pk)
        self.assertIsNotNone(otp_payload)
        otp = str(otp_payload.get("otp") or "")
        self.assertEqual(len(otp), 6)

        customer_client = Client()
        self._login_via_postlogin(customer_client, "alphatraders.customer", "customer123")
        customer_client.get("/customer_home/")

        customer_otp = customer_client.get("/api/customer_active_collection_otp/")
        self.assertEqual(customer_otp.status_code, 200)
        cust_payload = customer_otp.json()
        self.assertEqual(cust_payload.get("message"), "success")
        self.assertTrue(cust_payload.get("active"))
        self.assertEqual(cust_payload.get("otp"), otp)

        verify = staff_client.post("/api/verify_collection_otp_api/", data={"party": str(self.party.pk), "otp": otp})
        self.assertEqual(verify.status_code, 200)
        self.assertEqual(verify.json().get("message"), "success")

        add_resp = staff_client.post(
            "/api/add_collection_by_staff_api/",
            data={
                "party": str(self.party.pk),
                "paymentMode": "Cash",
                "amountPaid": "100",
                "bank": "",
                "detail": "",
                "remark": "",
                "lat": "0.0",
                "lng": "0.0",
                "chequeDate": "",
                "collectDate": date.today().strftime("%d/%m/%Y"),
                "TransferredParty": "",
                "otpRequired": "1",
                "otpCode": otp,
            },
        )
        self.assertEqual(add_resp.status_code, 200)
        self.assertEqual(add_resp.json().get("message"), "success")

    def test_owner_creates_bank_party_and_order_flow(self):
        owner_client = Client()
        self._login_via_postlogin(owner_client, "alpha.owner", "alpha123")

        bank_resp = owner_client.post(
            "/api/add_bank_api/",
            data={"bankName": "E2E Bank", "accountNumber": "E2E-001", "description": "Created via E2E flow"},
        )
        self.assertEqual(bank_resp.status_code, 200)
        self.assertEqual(bank_resp.json().get("message"), "success")
        bank = Bank.objects.filter(tenant=self.tenant, name="E2E Bank", isDeleted=False).order_by("-id").first()
        self.assertIsNotNone(bank)

        pg_resp = owner_client.post(
            "/api/add_party_group_api/",
            data={"groupName": "E2E PartyGroup", "description": "Created via E2E flow"},
        )
        self.assertEqual(pg_resp.status_code, 200)
        self.assertEqual(pg_resp.json().get("message"), "success")
        party_group = PartyGroup.objects.filter(tenant=self.tenant, name="E2E PartyGroup", isDeleted=False).order_by("-id").first()
        self.assertIsNotNone(party_group)

        party_resp = owner_client.post(
            "/api/add_party_api/",
            data={
                "partyName": "E2E Party",
                "phoneNumber": "9000000101",
                "openingBalance": "500.00",
                "partyGroup": str(party_group.pk),
                "staffID": str(self.owner_staff.pk),
            },
        )
        self.assertEqual(party_resp.status_code, 200)
        self.assertEqual(party_resp.json().get("message"), "success")
        party = Party.objects.filter(tenant=self.tenant, name="E2E Party", isDeleted=False).order_by("-id").first()
        self.assertIsNotNone(party)

        create_order = owner_client.post(
            "/api/add_order_api/",
            data={
                "buildDate": date.today().strftime("%d/%m/%Y"),
                "party": str(party.pk),
                "orderText": "E2E order text",
                "remark": "E2E remark",
                "lat": "0.0",
                "lng": "0.0",
                "loc": "N/A",
            },
        )
        self.assertEqual(create_order.status_code, 200)
        order_payload = create_order.json()
        self.assertEqual(order_payload.get("message"), "success")
        order_id = order_payload.get("order_id")
        self.assertTrue(order_id)

        approve = owner_client.post("/api/approve_order/", data={"userID": str(order_id)})
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.json().get("message"), "success")

        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.status, Order.Status.APPROVED)

        delete = owner_client.post("/api/delete_order/", data={"userID": str(order_id)})
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.json().get("message"), "success")

