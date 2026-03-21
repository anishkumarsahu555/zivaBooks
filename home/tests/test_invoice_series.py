from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from home.models import Bank, CashCounter, InvoiceSeries, Party, Sales, StaffUser, Tenant, TenantMembership


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

        self.assertEqual(response.status_code, 200)
        sale = Sales.objects.exclude(invoiceSequenceNumber=1).get()
        self.assertEqual(sale.invoiceSequenceNumber, 2)
        self.assertEqual(sale.invoiceNumber, "BCS0002")

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

        self.assertEqual(response.status_code, 200)
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

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "success")
        sale = Sales.objects.get(invoiceSequenceNumber=2)
        self.assertEqual(sale.invoiceNumber, "BCS0002")

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
