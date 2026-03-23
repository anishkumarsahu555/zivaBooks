from __future__ import annotations

from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from home.models import CredentialShareToken, Party, StaffGroup, StaffUser, Tenant


User = get_user_model()


class CredentialShareLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name='Claim Tenant', slug='claim-tenant')
        cls.admin = User.objects.create_superuser(username='claim.admin', password='adminpass', email='admin@example.com')
        cls.staff_auth = User.objects.create_user(username='u1234567', password='oldstaff')
        cls.party_auth = User.objects.create_user(username='1234567', password='oldparty')
        cls.staff_group = StaffGroup.objects.create(tenant=cls.tenant, name='Ops')
        cls.staff = StaffUser.objects.create(
            tenant=cls.tenant,
            name='Staff User',
            username=cls.staff_auth.username,
            userPassword='oldstaff',
            staffGroup=cls.staff_group,
            group='Ops',
            user_ID=cls.staff_auth,
            isActive='Active',
        )
        cls.party = Party.objects.create(
            tenant=cls.tenant,
            name='Customer User',
            phone='9999999999',
            username=cls.party_auth.username,
            password='oldparty',
            userID=cls.party_auth,
        )

    def test_party_claim_link_is_generated_and_consumed_once(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('homeApp:generate_party_access_link_api'), {'userID': self.party.pk})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['message'], 'success')
        raw_token = urlparse(payload['claimUrl']).path.rstrip('/').split('/')[-1]

        token = CredentialShareToken.objects.get(user=self.party_auth, party=self.party)
        self.assertIsNone(token.usedAt)

        claim_response = self.client.post(
            reverse('homeApp:claim_access', args=[raw_token]),
            {'new_password': 'secure123', 'confirm_password': 'secure123'},
        )
        self.assertEqual(claim_response.status_code, 200)

        token.refresh_from_db()
        self.party_auth.refresh_from_db()
        self.party.refresh_from_db()
        self.assertIsNotNone(token.usedAt)
        self.assertTrue(self.party_auth.check_password('secure123'))
        self.assertEqual(self.party.password, 'secure123')

        second_response = self.client.get(reverse('homeApp:claim_access', args=[raw_token]))
        self.assertEqual(second_response.status_code, 410)

    def test_staff_claim_link_is_generated_and_consumed_once(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('homeApp:generate_staff_access_link_api'), {'userID': self.staff.pk})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['message'], 'success')
        raw_token = urlparse(payload['claimUrl']).path.rstrip('/').split('/')[-1]

        token = CredentialShareToken.objects.get(user=self.staff_auth, staffUser=self.staff)
        self.assertIsNone(token.usedAt)

        claim_response = self.client.post(
            reverse('homeApp:claim_access', args=[raw_token]),
            {'new_password': 'staff123', 'confirm_password': 'staff123'},
        )
        self.assertEqual(claim_response.status_code, 200)

        token.refresh_from_db()
        self.staff_auth.refresh_from_db()
        self.staff.refresh_from_db()
        self.assertIsNotNone(token.usedAt)
        self.assertTrue(self.staff_auth.check_password('staff123'))
        self.assertEqual(self.staff.userPassword, 'staff123')
