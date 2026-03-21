from datetime import date

from django.test import TestCase
from django.urls import reverse

from home.models import Sales
from home.tests.utils import SeededTenantMixin


class SalesApprovalPermissionTests(SeededTenantMixin, TestCase):
    def test_sales_approver_gets_clear_success_message(self):
        sale = Sales.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            paymentID="TEST-SALES-SUCCESS",
            invoiceNumber="TS/2/2026",
            amount=1200,
            createdBy=self.owner_staff,
            buildDate=date.today(),
            isApproved=False,
        )

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("homeApp:send_message_sales"),
            data={"userID": str(sale.pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "success")
        self.assertEqual(response.json()["detail"], "Sale approved successfully.")
        sale.refresh_from_db()
        self.assertTrue(sale.isApproved)

    def test_sales_manager_without_approve_permission_cannot_approve_sale(self):
        sale = Sales.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            paymentID="TEST-SALES-APPROVAL",
            invoiceNumber="TS/1/2026",
            amount=1000,
            createdBy=self.owner_staff,
            buildDate=date.today(),
            isApproved=False,
        )
        self.assertFalse(sale.isApproved)

        self.client.force_login(self.sales)
        response = self.client.post(
            reverse("homeApp:send_message_sales"),
            data={"userID": str(sale.pk)},
        )

        self.assertEqual(response.status_code, 403)
        sale.refresh_from_db()
        self.assertFalse(sale.isApproved)
