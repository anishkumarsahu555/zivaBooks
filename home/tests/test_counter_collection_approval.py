from datetime import datetime

from django.test import TestCase
from django.urls import reverse

from home.models import CollectionCashCounter, PartyLedgerEntry
from home.tests.utils import SeededTenantMixin


class CounterCollectionApprovalTests(SeededTenantMixin, TestCase):
    def test_approving_counter_collection_creates_party_ledger_entry(self):
        entry = CollectionCashCounter.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            modeOfPayment="Cash",
            paidAmount=750,
            remark="counter payment",
            collectedBy=self.owner_staff,
            collectionDateTime=datetime(2026, 3, 21, 12, 0),
            isApproved=False,
        )

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("homeApp:approve_collection"),
            data={"userID": f"c{entry.pk}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "success")

        entry.refresh_from_db()
        self.assertTrue(entry.isApproved)

        ledger = PartyLedgerEntry.objects.get(
            tenant=self.tenant,
            party=self.party,
            source=PartyLedgerEntry.Source.COUNTER_COLLECTION,
            source_object_id=entry.pk,
            isDeleted=False,
        )
        self.assertEqual(ledger.credit, 750)
        self.assertEqual(ledger.debit, 0.0)
