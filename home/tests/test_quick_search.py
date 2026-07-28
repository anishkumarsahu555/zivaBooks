from django.test import TestCase
from django.urls import reverse
from home.tests.utils import SeededTenantMixin


class QuickSearchTests(SeededTenantMixin, TestCase):
    def test_quick_search_requires_login(self):
        response = self.client.get(reverse('homeApp:quick_search_api'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'], [])

    def test_quick_search_ignores_customer_login(self):
        # Log in as a customer
        self.client.force_login(self.customer)
        response = self.client.get(reverse('homeApp:quick_search_api'), {'q': 'test'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'], [])

    def test_quick_search_returns_shortcuts_on_empty_query(self):
        # Log in as owner (who has full privileges)
        self.client.force_login(self.owner)
        response = self.client.get(reverse('homeApp:quick_search_api'))
        self.assertEqual(response.status_code, 200)
        results = response.json()['results']
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0]['category'], 'Quick Actions')

    def test_quick_search_returns_filtered_results(self):
        # Log in as owner
        self.client.force_login(self.owner)
        # Search for seeded data (e.g. 'alpha' is in the tenant, party group name, or staff user name)
        response = self.client.get(reverse('homeApp:quick_search_api'), {'q': 'alpha'})
        self.assertEqual(response.status_code, 200)
        results = response.json()['results']
        self.assertTrue(len(results) > 0)
