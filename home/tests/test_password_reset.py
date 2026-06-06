import re

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from home.models import AuditLog, Party, StaffUser, Tenant


User = get_user_model()


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='test@example.com',
    PASSWORD_RESET_TIMEOUT=3600,
    PASSWORD_RESET_EMAIL_ASYNC=False,
)
class PasswordResetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='reset.user',
            email='reset@example.com',
            password='oldpass123',
        )

    def _request_reset(self, identifier='reset.user'):
        return self.client.post(reverse('homeApp:forgot_password'), {'identifier': identifier})

    def _reset_link_from_email(self):
        self.assertEqual(len(mail.outbox), 1)
        match = re.search(r'http://testserver(?P<path>/reset_password/[^\s]+/)', mail.outbox[0].body)
        self.assertIsNotNone(match)
        return match.group('path')

    def test_forgot_password_page_loads(self):
        response = self.client.get(reverse('homeApp:forgot_password'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Reset password')

    def test_unknown_account_does_not_reveal_existence_or_send_email(self):
        response = self._request_reset('missing@example.com')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'If an account exists, reset instructions have been sent.')
        self.assertEqual(len(mail.outbox), 0)

    def test_valid_username_receives_reset_link(self):
        response = self._request_reset()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'If an account exists, reset instructions have been sent.')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].from_email, 'zivaBooks <test@example.com>')
        self.assertIn('Reset your zivaBooks password', mail.outbox[0].subject)
        self.assertIn('/reset_password/', mail.outbox[0].body)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        self.assertEqual(mail.outbox[0].alternatives[0][1], 'text/html')
        self.assertIn('Reset password', mail.outbox[0].alternatives[0][0])
        self.assertIn('/static/sw/images/icon-192.png', mail.outbox[0].alternatives[0][0])

    def test_staff_username_uses_staff_email_when_auth_user_email_is_blank(self):
        tenant = Tenant.objects.create(name='Staff Email Tenant', slug='staff-email-tenant', isActive=True)
        staff_user = User.objects.create_user(
            username='staff.blank.email',
            email='',
            password='oldstaff123',
        )
        StaffUser.objects.create(
            tenant=tenant,
            name='Blank Email Staff',
            email='staff-only@example.com',
            username='staff.blank.email',
            userPassword='oldstaff123',
            user_ID=staff_user,
            isActive='Active',
        )

        response = self._request_reset('staff.blank.email')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['staff-only@example.com'])
        self.assertIn('/reset_password/', mail.outbox[0].body)

    def test_email_identifier_does_not_select_duplicate_staff_account(self):
        tenant = Tenant.objects.create(name='Duplicate Staff Tenant', slug='duplicate-staff-tenant', isActive=True)
        older_user = User.objects.create_user(username='older.staff', email='', password='oldpass123')
        newer_user = User.objects.create_user(username='newer.staff', email='', password='oldpass123')
        StaffUser.objects.create(
            tenant=tenant,
            name='Older Staff',
            email='duplicate-staff@example.com',
            username='older.staff',
            userPassword='oldpass123',
            user_ID=older_user,
            isActive='Active',
        )
        StaffUser.objects.create(
            tenant=tenant,
            name='Newer Staff',
            email='duplicate-staff@example.com',
            username='newer.staff',
            userPassword='oldpass123',
            user_ID=newer_user,
            isActive='Active',
        )

        response = self._request_reset('duplicate-staff@example.com')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_token_is_rejected(self):
        response = self.client.get(reverse('homeApp:reset_password', kwargs={'uidb64': 'bad', 'token': 'bad'}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Link expired')

    def test_valid_reset_changes_password_and_invalidates_link(self):
        self._request_reset()
        reset_path = self._reset_link_from_email()

        response = self.client.post(reset_path, {
            'new_password': 'Newpass123!',
            'confirm_password': 'Newpass123!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Password updated')

        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('oldpass123'))
        self.assertTrue(self.user.check_password('Newpass123!'))
        self.assertFalse(self.client.login(username='reset.user', password='oldpass123'))
        self.assertTrue(self.client.login(username='reset.user', password='Newpass123!'))

        reused = self.client.get(reset_path)
        self.assertContains(reused, 'Link expired')
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.PASSWORD_CHANGE, object_id=self.user.pk).exists())

    def test_password_mismatch_is_rejected(self):
        self._request_reset()
        reset_path = self._reset_link_from_email()

        response = self.client.post(reset_path, {
            'new_password': 'Newpass123!',
            'confirm_password': 'Different123!',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'New password and confirm password do not match.')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('oldpass123'))

    def test_legacy_staff_and_party_password_fields_sync_on_reset(self):
        tenant = Tenant.objects.create(name='Reset Tenant', slug='reset-tenant', isActive=True)
        staff_user = User.objects.create_user(
            username='reset.staff',
            email='staff-reset@example.com',
            password='oldstaff123',
        )
        StaffUser.objects.create(
            tenant=tenant,
            name='Reset Staff',
            email='staff-reset@example.com',
            username='reset.staff',
            userPassword='oldstaff123',
            user_ID=staff_user,
        )
        party_user = User.objects.create_user(
            username='reset.party',
            email='party-reset@example.com',
            password='oldparty123',
        )
        Party.objects.create(
            tenant=tenant,
            name='Reset Party',
            username='reset.party',
            password='oldparty123',
            userID=party_user,
        )

        self._request_reset('reset.staff')
        staff_path = self._reset_link_from_email()
        self.client.post(staff_path, {'new_password': 'Staffpass123!', 'confirm_password': 'Staffpass123!'})
        self.assertEqual(StaffUser.objects.get(user_ID=staff_user).userPassword, 'Staffpass123!')

        mail.outbox = []
        self._request_reset('reset.party')
        party_path = self._reset_link_from_email()
        self.client.post(party_path, {'new_password': 'Partypass123!', 'confirm_password': 'Partypass123!'})
        self.assertEqual(Party.objects.get(userID=party_user).password, 'Partypass123!')
