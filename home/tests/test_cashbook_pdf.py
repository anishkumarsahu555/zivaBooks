from datetime import datetime

from django.test import TestCase
from django.urls import reverse

from home.books import ensure_default_cash_account
from home.models import BookTransaction
from home.tests.utils import SeededTenantMixin


class CashbookPdfTests(SeededTenantMixin, TestCase):
    def test_cashbook_pdf_download_returns_pdf(self):
        account = ensure_default_cash_account(self.tenant)
        BookTransaction.objects.create(
            tenant=self.tenant,
            account=account,
            direction=BookTransaction.Direction.IN,
            amount=1500,
            txDateTime=datetime(2026, 3, 21, 11, 0),
            narration="Manual Receipt",
            party=self.party,
            createdBy=self.owner_staff,
            source=BookTransaction.Source.MANUAL,
        )

        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("homeApp:generate_cashbook_pdf"),
            {"account": str(account.pk), "startDate": "20/03/2026", "endDate": "21/03/2026"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("cashbook-", response["Content-Disposition"])
