from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from home.models import Bank, BookTransaction, CashCounter, InvoiceSeries, Party, PartyLedgerEntry, Sales, StaffUser, Tenant, TenantMembership
from home.books import sync_book_transactions
from home.party_ledger import sync_party_ledger_entries_for_object


User = get_user_model()


class InvoiceSeriesTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Invoice Tenant", slug="invoice-tenant")
        self.user = User.objects.create_user(username="invoice-user", password="secret123")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=TenantMembership.Role.STAFF,
            isDefault=True,
        )
        self.staff = StaffUser.objects.create(
            tenant=self.tenant,
            user_ID=self.user,
            name="Invoice Staff",
            phone="9999999990",
            isActive="Active",
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="change_sales"),
            Permission.objects.get(codename="view_sales"),
            Permission.objects.get(codename="can_manage_sales"),
        )
        self.party = Party.objects.create(
            tenant=self.tenant,
            name="Invoice Party",
            phone="8888888888",
            openingBalance="0",
        )
        self.bank = Bank.objects.create(
            tenant=self.tenant,
            name="Invoice Bank",
            accountNumber="1234567890",
        )
        self.sales_series = InvoiceSeries.objects.create(
            tenant=self.tenant,
            module=InvoiceSeries.Module.SALES,
            seriesCode="BCS",
            startNumber=1,
            endNumber=9999,
            nextNumber=1,
            paddingLength=4,
            isActive=True,
            isDefault=True,
        )
        self.cash_series = InvoiceSeries.objects.create(
            tenant=self.tenant,
            module=InvoiceSeries.Module.CASH_COUNTER,
            seriesCode="CCS",
            startNumber=1,
            endNumber=9999,
            nextNumber=1,
            paddingLength=4,
            isActive=True,
            isDefault=True,
        )
        self.client.force_login(self.user)

    def _create_cash_counter(self, **data):
        response = self.client.post(
            reverse("homeApp:add_cash_counter_entry_api"),
            data=data,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "success")
        return self.tenant.cash_counters.order_by("-id").first()

    def test_sales_series_suggestion_skips_used_invoice_numbers(self):
        Sales.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            createdBy=self.staff,
            invoiceSeries=self.sales_series,
            invoiceSequenceNumber=1,
            invoiceNumber="BCS0001",
            amount=10,
            buildDate="2026-03-21",
        )

        response = self.client.get(
            reverse("homeApp:invoice_series_suggestions_api"),
            {"module": InvoiceSeries.Module.SALES},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload[0]["nextNumber"], "0002")
        self.assertEqual(payload[0]["invoiceNumber"], "BCS0002")
        self.assertEqual(payload[0]["invoiceNature"], "mixed")
        self.assertFalse(payload[0]["lockInvoiceNature"])

    def test_sales_series_suggestion_includes_previous_unused_numbers(self):
        self.sales_series.nextNumber = 6
        self.sales_series.save(update_fields=["nextNumber", "lastUpdatedOn"])
        for number in [1, 5]:
            Sales.objects.create(
                tenant=self.tenant,
                partyID=self.party,
                createdBy=self.staff,
                invoiceSeries=self.sales_series,
                invoiceSequenceNumber=number,
                invoiceNumber=f"BCS{str(number).zfill(4)}",
                amount=10,
                buildDate="2026-03-21",
            )

        response = self.client.get(
            reverse("homeApp:invoice_series_suggestions_api"),
            {"module": InvoiceSeries.Module.SALES},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"][0]
        sequence_numbers = [item["sequenceNumber"] for item in payload["upcomingNumbers"][:5]]
        self.assertEqual(sequence_numbers[:4], [2, 3, 4, 6])

    def test_add_sales_uses_reserved_invoice_number(self):
        Sales.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            createdBy=self.staff,
            invoiceSeries=self.sales_series,
            invoiceSequenceNumber=1,
            invoiceNumber="BCS0001",
            amount=10,
            buildDate="2026-03-20",
        )

        response = self.client.post(
            reverse("homeApp:add_sales_by_admin_api"),
            data={
                "invoiceSeriesId": str(self.sales_series.pk),
                "colDate": "21/03/2026",
                "party": f"{self.party.name} @ {self.party.pk}",
                "amount": "250",
                "remark": "new sale",
            },
        )

        self.assertEqual(response.status_code, 200, msg=getattr(response, "content", b""))
        sale = Sales.objects.exclude(invoiceSequenceNumber=1).get()
        self.assertEqual(sale.invoiceSequenceNumber, 2)
        self.assertEqual(sale.invoiceNumber, "BCS0002")
        self.assertFalse(BookTransaction.objects.filter(source_object_id=sale.pk, isDeleted=False).exists())

    def test_add_sales_failure_does_not_consume_invoice_number(self):
        with mock.patch("home.apiView.sync_book_transactions", side_effect=RuntimeError("boom")):
            response = self.client.post(
                reverse("homeApp:add_sales_by_admin_api"),
                data={
                    "invoiceSeriesId": str(self.sales_series.pk),
                    "colDate": "21/03/2026",
                    "party": f"{self.party.name} @ {self.party.pk}",
                    "amount": "250",
                    "remark": "should rollback",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "error")
        self.sales_series.refresh_from_db()
        self.assertEqual(self.sales_series.nextNumber, 1)
        self.assertFalse(Sales.objects.filter(invoiceSeries=self.sales_series, invoiceSequenceNumber=1).exists())

    def test_add_sales_can_use_selected_unused_invoice_number(self):
        Sales.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            createdBy=self.staff,
            invoiceSeries=self.sales_series,
            invoiceSequenceNumber=1,
            invoiceNumber="BCS0001",
            amount=10,
            buildDate="2026-03-20",
        )

        response = self.client.post(
            reverse("homeApp:add_sales_by_admin_api"),
            data={
                "invoiceSeriesId": str(self.sales_series.pk),
                "requestedInvoiceNumber": "5",
                "colDate": "21/03/2026",
                "party": f"{self.party.name} @ {self.party.pk}",
                "amount": "250",
                "remark": "selected chip",
            },
        )

        self.assertEqual(response.status_code, 200, msg=getattr(response, "content", b""))
        sale = Sales.objects.exclude(invoiceSequenceNumber=1).get()
        self.assertEqual(sale.invoiceSequenceNumber, 5)
        self.assertEqual(sale.invoiceNumber, "BCS0005")

    def test_add_sales_can_use_visible_previous_gap_invoice_number(self):
        self.sales_series.nextNumber = 6
        self.sales_series.save(update_fields=["nextNumber", "lastUpdatedOn"])
        for number in [1, 5]:
            Sales.objects.create(
                tenant=self.tenant,
                partyID=self.party,
                createdBy=self.staff,
                invoiceSeries=self.sales_series,
                invoiceSequenceNumber=number,
                invoiceNumber=f"BCS{str(number).zfill(4)}",
                amount=10,
                buildDate="2026-03-20",
            )

        response = self.client.post(
            reverse("homeApp:add_sales_by_admin_api"),
            data={
                "invoiceSeriesId": str(self.sales_series.pk),
                "requestedInvoiceNumber": "2",
                "colDate": "21/03/2026",
                "party": f"{self.party.name} @ {self.party.pk}",
                "amount": "250",
                "remark": "selected old gap",
            },
        )

        self.assertEqual(response.status_code, 200, msg=getattr(response, "content", b""))
        self.assertEqual(response.json()["message"], "success", msg=response.json())
        sale = Sales.objects.get(invoiceSequenceNumber=2)
        self.assertEqual(sale.invoiceNumber, "BCS0002")

    def test_add_online_sale_saves_bank_and_mode(self):
        response = self.client.post(
            reverse("homeApp:add_sales_by_admin_api"),
            data={
                "invoiceSeriesId": str(self.sales_series.pk),
                "colDate": "21/03/2026",
                "party": str(self.party.pk),
                "paymentMode": "Online",
                "bank": str(self.bank.pk),
                "amount": "250",
                "remark": "online sale",
            },
        )

        self.assertEqual(response.status_code, 200, msg=getattr(response, "content", b""))
        self.assertEqual(response.json()["message"], "success", msg=response.json())
        sale = Sales.objects.get(invoiceNumber="BCS0001")
        self.assertEqual(sale.modeOfPayment, Sales.Mode.ONLINE)
        self.assertEqual(sale.bankID_id, self.bank.pk)
        self.assertEqual(sale.amount, 250.0)
        self.assertEqual(sale.mixCashAmount, 0.0)
        self.assertEqual(sale.mixOnlineAmount, 0.0)
        self.assertFalse(BookTransaction.objects.filter(source_object_id=sale.pk, isDeleted=False).exists())

    def test_locked_b2b_series_forces_invoice_kind(self):
        self.sales_series.invoiceNature = InvoiceSeries.InvoiceNature.B2B
        self.sales_series.lockInvoiceNature = True
        self.sales_series.save(update_fields=["invoiceNature", "lockInvoiceNature", "lastUpdatedOn"])

        response = self.client.post(
            reverse("homeApp:add_sales_by_admin_api"),
            data={
                "invoiceSeriesId": str(self.sales_series.pk),
                "colDate": "21/03/2026",
                "party": str(self.party.pk),
                "amount": "250",
                "invoiceKind": "b2c",
                "buyerLegalName": "Locked Buyer",
                "buyerGstNumber": "22AAAAA0000A1Z5",
                "remark": "series locked",
            },
        )

        self.assertEqual(response.status_code, 200, msg=getattr(response, "content", b""))
        self.assertEqual(response.json()["message"], "error")
        self.assertIn("locked for B2B", response.json()["error"])

        response = self.client.post(
            reverse("homeApp:add_sales_by_admin_api"),
            data={
                "invoiceSeriesId": str(self.sales_series.pk),
                "colDate": "21/03/2026",
                "party": str(self.party.pk),
                "amount": "250",
                "invoiceKind": "b2b",
                "buyerLegalName": "Locked Buyer",
                "buyerGstNumber": "22AAAAA0000A1Z5",
                "remark": "series locked",
            },
        )

        self.assertEqual(response.status_code, 200, msg=getattr(response, "content", b""))
        self.assertEqual(response.json()["message"], "success", msg=response.json())
        sale = Sales.objects.get(invoiceNumber="BCS0001")
        self.assertEqual(sale.invoiceKind, Sales.InvoiceKind.B2B)

    def test_add_mix_sale_saves_split_amounts(self):
        response = self.client.post(
            reverse("homeApp:add_sales_by_admin_api"),
            data={
                "invoiceSeriesId": str(self.sales_series.pk),
                "colDate": "21/03/2026",
                "party": str(self.party.pk),
                "paymentMode": "Mix",
                "bank": str(self.bank.pk),
                "amountMixCash": "150",
                "amountMixOnline": "350",
                "remark": "mix sale",
            },
        )

        self.assertEqual(response.status_code, 200, msg=getattr(response, "content", b""))
        self.assertEqual(response.json()["message"], "success", msg=response.json())
        sale = Sales.objects.get(invoiceNumber="BCS0001")
        self.assertEqual(sale.modeOfPayment, Sales.Mode.MIX)
        self.assertEqual(sale.bankID_id, self.bank.pk)
        self.assertEqual(sale.amount, 500.0)
        self.assertEqual(sale.mixCashAmount, 150.0)
        self.assertEqual(sale.mixOnlineAmount, 350.0)
        self.assertFalse(BookTransaction.objects.filter(source_object_id=sale.pk, isDeleted=False).exists())

    def test_update_sales_mode_clears_mix_and_bank_fields(self):
        sale = Sales.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            createdBy=self.staff,
            invoiceSeries=self.sales_series,
            invoiceSequenceNumber=7,
            invoiceNumber="BCS0007",
            modeOfPayment=Sales.Mode.MIX,
            amount=500,
            mixCashAmount=200,
            mixOnlineAmount=300,
            bankID=self.bank,
            buildDate="2026-03-21",
        )

        response = self.client.post(
            reverse("homeApp:update_sales_by_admin_api"),
            data={
                "ID": str(sale.pk),
                "colDate": "21/03/2026",
                "party": str(self.party.pk),
                "paymentMode": "Cash",
                "amount": "220",
                "remark": "cash now",
            },
        )

        self.assertEqual(response.status_code, 200, msg=getattr(response, "content", b""))
        self.assertEqual(response.json()["message"], "success", msg=response.json())
        sale.refresh_from_db()
        self.assertEqual(sale.modeOfPayment, Sales.Mode.CASH)
        self.assertIsNone(sale.bankID_id)
        self.assertEqual(sale.mixCashAmount, 0.0)
        self.assertEqual(sale.mixOnlineAmount, 0.0)
        self.assertEqual(sale.amount, 220.0)

    def test_approved_cash_sale_posts_to_cashbook_with_real_timestamp(self):
        sale = Sales.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            createdBy=self.staff,
            invoiceSeries=self.sales_series,
            invoiceSequenceNumber=10,
            invoiceNumber="BCS0010",
            modeOfPayment=Sales.Mode.CASH,
            amount=275,
            buildDate="2026-03-21",
            isApproved=True,
            approvedOn=timezone.now(),
        )

        sync_book_transactions(sale)

        tx = BookTransaction.objects.get(source_object_id=sale.pk, isDeleted=False)
        self.assertEqual(tx.source, BookTransaction.Source.SALES)
        self.assertEqual(tx.direction, BookTransaction.Direction.IN)
        self.assertEqual(tx.amount, 275.0)
        self.assertIsNotNone(tx.txDateTime)
        self.assertEqual(tx.txDateTime.hour, sale.approvedOn.hour)

    def test_approved_online_and_mix_sales_post_to_books_only_after_approval(self):
        online_sale = Sales.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            createdBy=self.staff,
            invoiceSeries=self.sales_series,
            invoiceSequenceNumber=11,
            invoiceNumber="BCS0011",
            modeOfPayment=Sales.Mode.ONLINE,
            amount=420,
            bankID=self.bank,
            buildDate="2026-03-21",
            isApproved=True,
            approvedOn=timezone.now(),
        )
        mix_sale = Sales.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            createdBy=self.staff,
            invoiceSeries=self.sales_series,
            invoiceSequenceNumber=12,
            invoiceNumber="BCS0012",
            modeOfPayment=Sales.Mode.MIX,
            amount=500,
            mixCashAmount=200,
            mixOnlineAmount=300,
            bankID=self.bank,
            buildDate="2026-03-21",
            isApproved=True,
            approvedOn=timezone.now(),
        )

        sync_book_transactions(online_sale)
        sync_book_transactions(mix_sale)

        online_tx = BookTransaction.objects.get(source_object_id=online_sale.pk, isDeleted=False)
        self.assertEqual(online_tx.account.bank_id, self.bank.pk)
        self.assertEqual(online_tx.amount, 420.0)

        mix_txs = list(BookTransaction.objects.filter(source_object_id=mix_sale.pk, isDeleted=False).order_by('component'))
        self.assertEqual(len(mix_txs), 2)
        self.assertEqual(mix_txs[0].component, 'bank')
        self.assertEqual(mix_txs[0].amount, 300.0)
        self.assertEqual(mix_txs[1].component, 'cash')
        self.assertEqual(mix_txs[1].amount, 200.0)

    def test_only_credit_sales_hit_party_ledger_on_approval(self):
        cash_sale = Sales.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            createdBy=self.staff,
            invoiceSeries=self.sales_series,
            invoiceSequenceNumber=8,
            invoiceNumber="BCS0008",
            modeOfPayment=Sales.Mode.CASH,
            amount=120,
            buildDate="2026-03-21",
            isApproved=True,
        )
        credit_sale = Sales.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            createdBy=self.staff,
            invoiceSeries=self.sales_series,
            invoiceSequenceNumber=9,
            invoiceNumber="BCS0009",
            modeOfPayment=Sales.Mode.CREDIT,
            amount=180,
            buildDate="2026-03-21",
            isApproved=True,
        )

        sync_party_ledger_entries_for_object(cash_sale)
        sync_party_ledger_entries_for_object(credit_sale)

        self.assertFalse(
            PartyLedgerEntry.objects.filter(
                tenant=self.tenant,
                source_object_id=cash_sale.pk,
                isDeleted=False,
            ).exists()
        )
        ledger = PartyLedgerEntry.objects.get(
            tenant=self.tenant,
            source_object_id=credit_sale.pk,
            isDeleted=False,
        )
        self.assertEqual(ledger.debit, 180.0)
        self.assertEqual(ledger.credit, 0.0)

    def test_add_cash_counter_uses_reserved_invoice_number(self):
        entry = self._create_cash_counter(
            **{
                "invoiceSeriesId": str(self.cash_series.pk),
                "party": f"{self.party.name} @ {self.party.pk}",
                "amount": "500",
                "remark": "cash entry",
                "amountMixCash": "",
                "amountMixCard": "",
                "expenseType": "",
                "bank": "",
                "mode": "Cash",
            }
        )
        self.assertEqual(entry.invoiceSequenceNumber, 1)
        self.assertEqual(entry.invoiceNumber, "CCS0001")

    def test_add_cash_counter_failure_does_not_consume_invoice_number(self):
        with mock.patch("home.apiView.sync_book_transactions", side_effect=RuntimeError("boom")):
            response = self.client.post(
                reverse("homeApp:add_cash_counter_entry_api"),
                data={
                    "invoiceSeriesId": str(self.cash_series.pk),
                    "party": f"{self.party.name} @ {self.party.pk}",
                    "amount": "500",
                    "remark": "should rollback",
                    "amountMixCash": "",
                    "amountMixCard": "",
                    "expenseType": "",
                    "bank": "",
                    "mode": "Cash",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "error")
        self.cash_series.refresh_from_db()
        self.assertEqual(self.cash_series.nextNumber, 1)
        self.assertFalse(CashCounter.objects.filter(invoiceSeries=self.cash_series, invoiceSequenceNumber=1).exists())

    def test_add_cash_counter_can_use_selected_unused_invoice_number(self):
        entry = self._create_cash_counter(
            **{
                "invoiceSeriesId": str(self.cash_series.pk),
                "requestedInvoiceNumber": "5",
                "party": f"{self.party.name} @ {self.party.pk}",
                "amount": "500",
                "remark": "cash entry",
                "amountMixCash": "",
                "amountMixCard": "",
                "expenseType": "",
                "bank": "",
                "mode": "Cash",
            }
        )
        self.assertEqual(entry.invoiceSequenceNumber, 5)
        self.assertEqual(entry.invoiceNumber, "CCS0005")

    def test_add_cash_counter_can_use_visible_previous_gap_invoice_number(self):
        self.cash_series.nextNumber = 6
        self.cash_series.save(update_fields=["nextNumber", "lastUpdatedOn"])
        for number in [1, 5]:
            CashCounter.objects.create(
                tenant=self.tenant,
                partyID=self.party,
                createdBy=self.staff,
                invoiceSeries=self.cash_series,
                invoiceSequenceNumber=number,
                invoiceNumber=f"CCS{str(number).zfill(4)}",
                amount=10,
                mode="Cash",
            )

        entry = self._create_cash_counter(
            invoiceSeriesId=str(self.cash_series.pk),
            requestedInvoiceNumber="2",
            party=f"{self.party.name} @ {self.party.pk}",
            amount="500",
            remark="cash gap entry",
            amountMixCash="",
            amountMixCard="",
            expenseType="",
            bank="",
            mode="Cash",
        )

        self.assertEqual(entry.invoiceSequenceNumber, 2)
        self.assertEqual(entry.invoiceNumber, "CCS0002")

    def test_add_cash_counter_mix_assigns_party(self):
        entry = self._create_cash_counter(
            **{
                "invoiceSeriesId": str(self.cash_series.pk),
                "requestedInvoiceNumber": "6",
                "party": f"{self.party.name} @ {self.party.pk}",
                "amount": "",
                "remark": "mix entry",
                "amountMixCash": "200",
                "amountMixCard": "300",
                "expenseType": "",
                "bank": str(self.bank.pk),
                "mode": "Mix",
            }
        )
        self.assertEqual(entry.partyID, self.party)

    def test_add_cash_counter_mode_assignments_are_correct(self):
        cash_entry = self._create_cash_counter(
            invoiceSeriesId=str(self.cash_series.pk),
            requestedInvoiceNumber="10",
            party=f"{self.party.name} @ {self.party.pk}",
            amount="500",
            remark="cash entry",
            amountMixCash="",
            amountMixCard="",
            expenseType="",
            bank="",
            mode="Cash",
        )
        self.assertEqual(cash_entry.partyID, self.party)
        self.assertIsNone(cash_entry.bankID)
        self.assertEqual(cash_entry.amount, 500)

        credit_entry = self._create_cash_counter(
            invoiceSeriesId=str(self.cash_series.pk),
            requestedInvoiceNumber="11",
            party=f"{self.party.name} @ {self.party.pk}",
            amount="650",
            remark="credit entry",
            amountMixCash="",
            amountMixCard="",
            expenseType="",
            bank="",
            mode="Credit",
        )
        self.assertEqual(credit_entry.partyID, self.party)
        self.assertIsNone(credit_entry.bankID)
        self.assertEqual(credit_entry.amount, 650)

        card_entry = self._create_cash_counter(
            invoiceSeriesId=str(self.cash_series.pk),
            requestedInvoiceNumber="12",
            party=f"{self.party.name} @ {self.party.pk}",
            amount="700",
            remark="card entry",
            amountMixCash="",
            amountMixCard="",
            expenseType="",
            bank=str(self.bank.pk),
            mode="Card",
        )
        self.assertEqual(card_entry.partyID, self.party)
        self.assertEqual(card_entry.bankID, self.bank)
        self.assertEqual(card_entry.amount, 700)

        return_entry = self._create_cash_counter(
            invoiceSeriesId=str(self.cash_series.pk),
            requestedInvoiceNumber="13",
            party=f"{self.party.name} @ {self.party.pk}",
            amount="120",
            remark="return entry",
            amountMixCash="",
            amountMixCard="",
            expenseType="",
            bank="",
            mode="Return",
        )
        self.assertEqual(return_entry.partyID, self.party)
        self.assertIsNone(return_entry.bankID)
        self.assertEqual(return_entry.amount, 120)

        expense_entry = self._create_cash_counter(
            amount="90",
            remark="expense entry",
            amountMixCash="",
            amountMixCard="",
            expenseType="Travel",
            bank="",
            mode="Expense",
        )
        self.assertIsNone(expense_entry.partyID)
        self.assertIsNone(expense_entry.bankID)
        self.assertEqual(expense_entry.expenseType, "Travel")

        advance_entry = self._create_cash_counter(
            party=f"{self.party.name} @ {self.party.pk}",
            amount="300",
            remark="advance entry",
            amountMixCash="",
            amountMixCard="",
            expenseType="",
            bank="",
            mode="Advance",
        )
        self.assertEqual(advance_entry.partyID, self.party)
        self.assertIsNone(advance_entry.bankID)
        self.assertEqual(advance_entry.amount, 300)

    def test_update_cash_counter_mix_assigns_party(self):
        entry = CashCounter.objects.create(
            tenant=self.tenant,
            createdBy=self.staff,
            invoiceSeries=self.cash_series,
            invoiceSequenceNumber=1,
            invoiceNumber="CCS0001",
            amount=500,
            mode="Cash",
        )

        response = self.client.post(
            reverse("homeApp:update_cash_counter_by_admin_api"),
            data={
                "editID": str(entry.pk),
                "party": str(self.party.pk),
                "amount": "",
                "remark": "updated mix",
                "amountMixCash": "250",
                "amountMixCard": "150",
                "expenseType": "",
                "bank": str(self.bank.pk),
                "mode": "Mix",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "success")
        entry.refresh_from_db()
        self.assertEqual(entry.partyID, self.party)

    def test_update_cash_counter_clears_stale_party_bank_and_expense_fields(self):
        entry = CashCounter.objects.create(
            tenant=self.tenant,
            createdBy=self.staff,
            partyID=self.party,
            invoiceSeries=self.cash_series,
            invoiceSequenceNumber=20,
            invoiceNumber="CCS0020",
            amount=0,
            mixCashAmount=250,
            mixCardAmount=150,
            bankID=self.bank,
            expenseType="Old expense",
            mode="Mix",
        )

        response = self.client.post(
            reverse("homeApp:update_cash_counter_by_admin_api"),
            data={
                "editID": str(entry.pk),
                "party": str(self.party.pk),
                "amount": "999",
                "remark": "now cash",
                "amountMixCash": "",
                "amountMixCard": "",
                "expenseType": "",
                "bank": "",
                "mode": "Cash",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "success")
        entry.refresh_from_db()
        self.assertEqual(entry.partyID, self.party)
        self.assertIsNone(entry.bankID)
        self.assertEqual(entry.expenseType, "")
        self.assertEqual(entry.mixCashAmount, 0.0)
        self.assertEqual(entry.mixCardAmount, 0.0)
        self.assertEqual(entry.amount, 999)
