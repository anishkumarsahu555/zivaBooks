from django.test import TestCase

from home.models import Bank
from home.tests.utils import SeededTenantMixin


class BankApiCrudTests(SeededTenantMixin, TestCase):
    def test_bank_crud(self):
        self.client.force_login(self.owner)

        create = self.client.post(
            "/api/add_bank_api/",
            data={"bankName": "API Bank", "accountNumber": "API-001", "description": "Created via API test"},
        )
        self.assertEqual(create.status_code, 200)
        self.assertEqual(create.json().get("message"), "success")

        bank = Bank.objects.filter(tenant=self.tenant, name="API Bank", isDeleted=False).order_by("-id").first()
        self.assertIsNotNone(bank)

        list_resp = self.client.get("/api/BankListJson/", data={"draw": "1", "start": "0", "length": "5"})
        self.assertEqual(list_resp.status_code, 200)

        detail = self.client.get("/api/get_bank_detail/", data={"id": str(bank.pk)})
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["ID"], bank.pk)

        edit = self.client.post(
            "/api/edit_bank_api/",
            data={"EditId": str(bank.pk), "bankName": "API Bank (Updated)", "accountNumber": "API-002", "description": "Updated"},
        )
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.json().get("message"), "success")

        delete = self.client.post("/api/delete_bank/", data={"userID": str(bank.pk)})
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.json().get("message"), "success")

        bank.refresh_from_db()
        self.assertTrue(bank.isDeleted)

