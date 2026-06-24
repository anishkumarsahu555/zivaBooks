from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from home.models import StaffUser, Tenant
from home.tests.utils import SeededTenantMixin


User = get_user_model()


class RecoveryEmailTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Recovery Tenant', slug='recovery-tenant', isActive=True)
        self.user = User.objects.create_user(username='recovery.user', email='', password='oldpass123')
        self.staff = StaffUser.objects.create(
            tenant=self.tenant,
            name='Recovery User',
            email='',
            username='recovery.user',
            userPassword='oldpass123',
            user_ID=self.user,
            isActive='Active',
        )

    def test_save_recovery_email_requires_login(self):
        response = self.client.post(reverse('homeApp:save_recovery_email'), {'email': 'new@example.com'})

        self.assertEqual(response.status_code, 401)

    def test_save_recovery_email_validates_email(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('homeApp:save_recovery_email'), {'email': 'not-an-email'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['message'], 'invalid_email')

    def test_save_recovery_email_updates_auth_user_and_staff_profile(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('homeApp:save_recovery_email'), {'email': 'recovery@example.com'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')
        self.user.refresh_from_db()
        self.staff.refresh_from_db()
        self.assertEqual(self.user.email, 'recovery@example.com')
        self.assertEqual(self.staff.email, 'recovery@example.com')


class CustomerRecoveryEmailPromptTests(SeededTenantMixin, TestCase):
    def test_customer_home_prompts_for_recovery_email_when_missing(self):
        self.customer.email = ''
        self.customer.save(update_fields=['email'])
        self.client.force_login(self.customer)
        self.client.get('/home/')

        response = self.client.get('/customer_home/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Add recovery email')
        self.assertContains(response, 'name@example.com')


class ProfileRecoveryEmailTests(SeededTenantMixin, TestCase):
    def test_my_profile_shows_account_recovery_card(self):
        self.owner.email = ''
        self.owner.save(update_fields=['email'])
        self.owner_staff.email = ''
        self.owner_staff.save(update_fields=['email'])
        self.client.force_login(self.owner)

        response = self.client.get(reverse('homeApp:my_profile'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Account Recovery')
        self.assertContains(response, 'Update Email')
        self.assertContains(response, 'name@example.com')
