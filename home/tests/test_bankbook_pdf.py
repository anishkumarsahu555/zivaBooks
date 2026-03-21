from datetime import datetime

from django.test import TestCase
from django.urls import reverse

from home.books import ensure_bank_account
from home.models import Bank, BookTransaction
from home.tests.utils import SeededTenantMixin


class BankbookPdfTests(SeededTenantMixin, TestCase):
    def test_bankbook_pdf_download_returns_pdf(self):
        bank = Bank.objects.filter(tenant=self.tenant, isDeleted=False).order_by('id').first()
        self.assertIsNotNone(bank)
        account = ensure_bank_account(self.tenant, bank)
        BookTransaction.objects.create(
            tenant=self.tenant,
            account=account,
            direction=BookTransaction.Direction.IN,
            amount=2100,
            txDateTime=datetime(2026, 3, 21, 11, 30),
            narration="Bank Deposit",
            party=self.party,
            createdBy=self.owner_staff,
            source=BookTransaction.Source.MANUAL,
        )

        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("homeApp:generate_bankbook_pdf"),
            {"bank": str(bank.pk), "startDate": "20/03/2026", "endDate": "21/03/2026"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("bankbook-", response["Content-Disposition"])
