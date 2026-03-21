from datetime import datetime

from django.test import TestCase
from django.urls import reverse

from home.models import PartyLedgerEntry
from home.tests.utils import SeededTenantMixin


class PartyLedgerPdfTests(SeededTenantMixin, TestCase):
    def test_party_ledger_pdf_download_returns_pdf(self):
        PartyLedgerEntry.objects.create(
            tenant=self.tenant,
            party=self.party,
            txDateTime=datetime(2026, 3, 21, 10, 30),
            particulars="Sales Invoice TS/1001",
            debit=2500,
            credit=0,
            createdBy=self.owner_staff,
            source=PartyLedgerEntry.Source.SALES,
            reference="TS/1001",
        )

        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("homeApp:generate_party_ledger_pdf", args=[self.party.pk]),
            {"startDate": "20/03/2026", "endDate": "21/03/2026"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("party-ledger-", response["Content-Disposition"])
