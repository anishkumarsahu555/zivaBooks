from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from home.models import AuditLog, Bank, BookAccount, Party, StaffUser, Tenant, TenantMembership
from home.tests.utils import SeededTenantMixin


User = get_user_model()


class AuditReportTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.user_a = User.objects.create_user(username="audit-a", password="secret123")
        self.user_b = User.objects.create_user(username="audit-b", password="secret123")
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.user_a, role=TenantMembership.Role.OWNER, isDefault=True)
        TenantMembership.objects.create(tenant=self.tenant_b, user=self.user_b, role=TenantMembership.Role.OWNER, isDefault=True)
        view_perm = Permission.objects.get(codename="can_view_audit_logs", content_type__app_label="home")
        export_perm = Permission.objects.get(codename="can_export_audit_logs", content_type__app_label="home")
        self.user_a.user_permissions.add(view_perm, export_perm)
        self.user_b.user_permissions.add(view_perm, export_perm)

        self.party_a = Party.objects.create(tenant=self.tenant_a, name="Alpha Party", phone="9000000001")
        self.party_b = Party.objects.create(tenant=self.tenant_b, name="Beta Party", phone="9000000002")

    def test_audit_report_list_is_tenant_scoped(self):
        self.client.force_login(self.user_a)

        response = self.client.get(
            "/api/AuditLogListJson/",
            data={"draw": "1", "start": "0", "length": "20"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        rows = payload["data"]
        rendered = " ".join(" ".join(row[:7]) for row in rows)
        self.assertIn("Alpha Party", rendered)
        self.assertNotIn("Beta Party", rendered)

    def test_audit_report_csv_respects_search_and_tenant_scope(self):
        self.client.force_login(self.user_a)

        response = self.client.get(
            "/api/export_audit_log_csv/",
            data={"search": "Alpha Party"},
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Alpha Party", content)
        self.assertNotIn("Beta Party", content)

    def test_audit_detail_returns_serialized_payload(self):
        self.client.force_login(self.user_a)
        party_ct = ContentType.objects.get_for_model(Party)
        log = AuditLog.objects.filter(content_type=party_ct, object_id=self.party_a.pk).order_by("-id").first()

        response = self.client.get("/api/get_audit_log_detail_api/", data={"id": str(log.pk)})

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload["object_id"], self.party_a.pk)
        self.assertIn("Alpha Party", payload["object_repr"])


class AuditBusinessActionTests(SeededTenantMixin, TestCase):
    def test_change_password_creates_password_change_audit_log(self):
        self.client.force_login(self.owner)

        response = self.client.post("/api/change_password_api/", data={"password": "newsecret123"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "success")
        log = AuditLog.objects.filter(
            action=AuditLog.Action.PASSWORD_CHANGE,
            object_id=self.owner_staff.pk,
        ).order_by("-id").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor_id, self.owner.pk)

    def test_transfer_books_creates_transfer_audit_log(self):
        self.client.force_login(self.owner)

        cash_account = BookAccount.objects.create(
            tenant=self.tenant,
            type=BookAccount.Type.CASH,
            name="Main Cash",
            isDefault=True,
        )
        bank = Bank.objects.create(tenant=self.tenant, name="Axis", accountNumber="1234")
        bank_account = BookAccount.objects.create(
            tenant=self.tenant,
            type=BookAccount.Type.BANK,
            name="Axis Account",
            bank=bank,
        )

        response = self.client.post(
            "/api/transfer_books_api/",
            data={
                "fromAccountId": str(cash_account.pk),
                "toAccountId": str(bank_account.pk),
                "amount": "1500",
                "note": "Audit transfer test",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "success")
        log = AuditLog.objects.filter(action=AuditLog.Action.TRANSFER).order_by("-id").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor_id, self.owner.pk)
        self.assertEqual(log.changes["amount"], 1500.0)
