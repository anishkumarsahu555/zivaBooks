from django.test import TestCase
from django.urls import reverse

from home.models import CollectionCashCounter, PartyLedgerEntry
from home.tests.utils import SeededTenantMixin


class CashCounterCollectionCreationTests(SeededTenantMixin, TestCase):
    def test_add_counter_collection_is_auto_approved_and_posts_to_party_ledger(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("homeApp:add_collection_from_cash_counter_api"),
            data={
                "party": f"{self.party.name} @ {self.party.pk}",
                "TransferredParty": "",
                "paymentMode": "Cash",
                "amountPaid": "880",
                "bank": "",
                "detail": "counter pay",
                "remark": "walk-in paid",
                "lat": "0.0",
                "lng": "0.0",
                "chequeDate": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "success")

        entry = CollectionCashCounter.objects.filter(tenant=self.tenant).order_by("-id").first()
        self.assertIsNotNone(entry)
        self.assertTrue(entry.isApproved)
        self.assertEqual(entry.approvedBy, self.owner_staff)
        self.assertIsNotNone(entry.approvedOn)

        ledger = PartyLedgerEntry.objects.get(
            tenant=self.tenant,
            party=self.party,
            source=PartyLedgerEntry.Source.COUNTER_COLLECTION,
            source_object_id=entry.pk,
            isDeleted=False,
        )
        self.assertEqual(ledger.credit, 880)
        self.assertEqual(ledger.debit, 0.0)
