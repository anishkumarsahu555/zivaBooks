from datetime import date, timedelta

from django.test import TestCase

from home.tests.utils import SeededTenantMixin


class SeededTenantSmokeTests(SeededTenantMixin, TestCase):
    def test_owner_sees_license_reminder_message(self):
        self.client.force_login(self.owner)
        response = self.client.get("/home/")
        self.assertEqual(response.status_code, 200)
        expected_expiry = (date.today() + timedelta(days=365)).strftime("%d-%m-%Y")
        self.assertContains(response, f"Your App License is Valid till {expected_expiry}")

    def test_owner_can_open_major_modules(self):
        self.client.force_login(self.owner)
        for path in [
            "/home/",
            "/admin_home/",
            "/staff_group_list/",
            "/party_list/",
            "/bank_list/",
            "/collection_list/",
            "/cheque_reminder_list/",
            "/sales_list/",
            "/orders_list/",
            "/cash_counter_home/",
            "/message_list/",
            "/cashbook/",
            "/bankbook/",
        ]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_collector_can_open_collection_pages(self):
        self.client.force_login(self.collector)
        for path in ["/home/", "/collection_home/", "/add_collection/", "/my_collection/", "/staff_home/"]:
            with self.subTest(path=path):
                response = self.client.get(path, follow=True)
                self.assertEqual(response.status_code, 200)

    def test_sales_user_can_open_sales_pages(self):
        self.client.force_login(self.sales)
        for path in ["/home/", "/add_sales/", "/my_sales_list/"]:
            with self.subTest(path=path):
                response = self.client.get(path, follow=True)
                self.assertEqual(response.status_code, 200)

    def test_cash_user_can_open_cash_counter_pages(self):
        self.client.force_login(self.cash)
        for path in ["/home/", "/cash_counter_home/", "/my_cash_counter_list/"]:
            with self.subTest(path=path):
                response = self.client.get(path, follow=True)
                self.assertEqual(response.status_code, 200)

    def test_audit_user_can_open_audit_pages(self):
        self.client.force_login(self.audit)
        for path in ["/home/", "/attendance_report/"]:
            with self.subTest(path=path):
                response = self.client.get(path, follow=True)
                self.assertEqual(response.status_code, 200)

    def test_customer_redirects_to_customer_portal(self):
        self.client.force_login(self.customer)
        response = self.client.get("/home/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/customer_home/")

        portal = self.client.get("/customer_home/")
        self.assertEqual(portal.status_code, 200)
        self.assertContains(portal, self.party.name)

