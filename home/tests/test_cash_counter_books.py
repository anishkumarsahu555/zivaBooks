from datetime import datetime

from django.test import TestCase

from home.books import sync_book_transactions
from home.models import Bank, BookAccount, BookTransaction, CashCounter
from home.tests.utils import SeededTenantMixin


class CashCounterBooksMappingTests(SeededTenantMixin, TestCase):
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
        sync_book_transactions(entry)
        return entry

    def _active_transactions_for(self, entry):
        return BookTransaction.objects.filter(
            tenant=self.tenant,
            isDeleted=False,
            source=BookTransaction.Source.CASH_COUNTER,
            source_object_id=entry.pk,
        ).select_related("account")

    def test_cash_posts_to_cashbook_in(self):
        entry = self._create_entry(mode="Cash", amount=500)

        tx = self._active_transactions_for(entry).get()
        self.assertEqual(tx.account.type, BookAccount.Type.CASH)
        self.assertEqual(tx.direction, BookTransaction.Direction.IN)
        self.assertEqual(tx.amount, 500)

    def test_credit_does_not_post_to_books(self):
        entry = self._create_entry(mode="Credit", amount=700)

        self.assertFalse(self._active_transactions_for(entry).exists())

    def test_card_posts_to_bankbook_in(self):
        entry = self._create_entry(mode="Card", amount=900, bankID=self.bank)

        tx = self._active_transactions_for(entry).get()
        self.assertEqual(tx.account.type, BookAccount.Type.BANK)
        self.assertEqual(tx.direction, BookTransaction.Direction.IN)
        self.assertEqual(tx.amount, 900)

    def test_mix_splits_between_cashbook_and_bankbook(self):
        entry = self._create_entry(
            mode="Mix",
            amount=0,
            mixCashAmount=400,
            mixCardAmount=600,
            bankID=self.bank,
        )

        transactions = list(self._active_transactions_for(entry).order_by("component"))
        self.assertEqual(len(transactions), 2)

        bank_tx = next(tx for tx in transactions if tx.component == "bank")
        cash_tx = next(tx for tx in transactions if tx.component == "cash")

        self.assertEqual(cash_tx.account.type, BookAccount.Type.CASH)
        self.assertEqual(cash_tx.direction, BookTransaction.Direction.IN)
        self.assertEqual(cash_tx.amount, 400)

        self.assertEqual(bank_tx.account.type, BookAccount.Type.BANK)
        self.assertEqual(bank_tx.direction, BookTransaction.Direction.IN)
        self.assertEqual(bank_tx.amount, 600)

    def test_return_posts_to_cashbook_out(self):
        entry = self._create_entry(mode="Return", amount=250)

        tx = self._active_transactions_for(entry).get()
        self.assertEqual(tx.account.type, BookAccount.Type.CASH)
        self.assertEqual(tx.direction, BookTransaction.Direction.OUT)
        self.assertEqual(tx.amount, 250)

    def test_expense_posts_to_cashbook_out(self):
        entry = self._create_entry(mode="Expense", amount=350, expenseType="Travel")

        tx = self._active_transactions_for(entry).get()
        self.assertEqual(tx.account.type, BookAccount.Type.CASH)
        self.assertEqual(tx.direction, BookTransaction.Direction.OUT)
        self.assertEqual(tx.amount, 350)
