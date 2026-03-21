from datetime import datetime

from django.test import TestCase

from home.party_ledger import sync_party_ledger_entries_for_object
from home.models import Bank, CashCounter, PartyLedgerEntry
from home.tests.utils import SeededTenantMixin


class CashCounterPartyLedgerTests(SeededTenantMixin, TestCase):
    def setUp(self):
        self.bank = Bank.objects.filter(tenant=self.tenant, isDeleted=False).order_by("id").first()
        self.assertIsNotNone(self.bank)

    def _create_entry(self, **kwargs):
        entry = CashCounter.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            createdBy=self.owner_staff,
            entryDate=datetime(2026, 3, 21, 11, 0),
            **kwargs,
        )
        sync_party_ledger_entries_for_object(entry)
        return entry

    def _active_entries_for(self, source_obj):
        return PartyLedgerEntry.objects.filter(
            tenant=self.tenant,
            party=self.party,
            isDeleted=False,
            source=PartyLedgerEntry.Source.CASH_COUNTER,
            source_object_id=source_obj.pk,
        )

    def test_cash_does_not_post_to_party_ledger(self):
        entry = self._create_entry(mode="Cash", amount=500, invoiceNumber="CCS0001")

        self.assertFalse(self._active_entries_for(entry).exists())

    def test_credit_posts_credit_to_party_ledger(self):
        entry = self._create_entry(mode="Credit", amount=700, invoiceNumber="CCS0002")

        ledger = self._active_entries_for(entry).get()
        self.assertEqual(ledger.debit, 0.0)
        self.assertEqual(ledger.credit, 700)

    def test_card_does_not_post_to_party_ledger(self):
        entry = self._create_entry(mode="Card", amount=900, bankID=self.bank, invoiceNumber="CCS0003")

        self.assertFalse(self._active_entries_for(entry).exists())

    def test_mix_does_not_post_to_party_ledger(self):
        entry = self._create_entry(
            mode="Mix",
            amount=0,
            mixCashAmount=400,
            mixCardAmount=600,
            bankID=self.bank,
            invoiceNumber="CCS0004",
        )

        self.assertFalse(self._active_entries_for(entry).exists())

    def test_return_does_not_post_to_party_ledger(self):
        entry = self._create_entry(mode="Return", amount=250, invoiceNumber="CCS0005")

        self.assertFalse(self._active_entries_for(entry).exists())

    def test_advance_posts_debit_to_party_ledger(self):
        entry = self._create_entry(mode="Advance", amount=300)

        ledger = self._active_entries_for(entry).get()
        self.assertEqual(ledger.debit, 300)
        self.assertEqual(ledger.credit, 0.0)
