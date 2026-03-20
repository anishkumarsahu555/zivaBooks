from django.test import TestCase

from home.models import Bank, BookAccount
from home.tests.utils import SeededTenantMixin


class BankApiCrudTests(SeededTenantMixin, TestCase):
    def test_bank_crud(self):
        self.client.force_login(self.owner)

        create = self.client.post(
            "/api/add_bank_api/",
            data={
                "bankName": "API Bank",
                "accountNumber": "API-001",
                "description": "Created via API test",
                "openingBalance": "150.25",
            },
        )
        self.assertEqual(create.status_code, 200)
        self.assertEqual(create.json().get("message"), "success")

        bank = Bank.objects.filter(tenant=self.tenant, name="API Bank", isDeleted=False).order_by("-id").first()
        self.assertIsNotNone(bank)
        bank_account = BookAccount.objects.filter(tenant=self.tenant, bank=bank, isDeleted=False).first()
        self.assertIsNotNone(bank_account)
        self.assertEqual(bank_account.openingBalance, 150.25)

        list_resp = self.client.get("/api/BankListJson/", data={"draw": "1", "start": "0", "length": "5"})
        self.assertEqual(list_resp.status_code, 200)

        detail = self.client.get("/api/get_bank_detail/", data={"id": str(bank.pk)})
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["ID"], bank.pk)
        self.assertEqual(detail.json()["data"]["OpeningBalance"], "150.25")

        edit = self.client.post(
            "/api/edit_bank_api/",
            data={
                "EditId": str(bank.pk),
                "bankName": "API Bank (Updated)",
                "accountNumber": "API-002",
                "description": "Updated",
                "openingBalance": "225.50",
            },
        )
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.json().get("message"), "success")
        bank_account.refresh_from_db()
        self.assertEqual(bank_account.openingBalance, 225.50)

        delete = self.client.post("/api/delete_bank/", data={"userID": str(bank.pk)})
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.json().get("message"), "success")

        bank.refresh_from_db()
        self.assertTrue(bank.isDeleted)
