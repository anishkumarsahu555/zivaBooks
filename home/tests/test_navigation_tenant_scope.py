from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import RequestFactory
from django.test import TestCase

from home.models import TenantMembership
from home.dashboard import build_dashboard_state
from home.navigation import build_sidebar_sections
from home.tests.utils import SeededTenantMixin


User = get_user_model()


class NavigationTenantScopeTests(SeededTenantMixin, TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _set_tenant_scope(self, *permissions):
        group, _ = Group.objects.get_or_create(name=f'{self.tenant.slug}:Tenant Admin')
        group.permissions.set(list(permissions))
        return group

    @staticmethod
    def _nav_labels(sections):
        labels = []
        for section in sections:
            for item in section.get('items', []):
                labels.append(item['label'])
        return labels

    def test_superuser_sidebar_is_limited_by_tenant_scope(self):
        orders_perms = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=['add_order', 'view_order', 'change_order', 'can_manage_orders'],
            )
        )
        self._set_tenant_scope(*orders_perms)

        labels = self._nav_labels(build_sidebar_sections(self.owner))

        self.assertIn('Add Order', labels)
        self.assertIn('Orders List', labels)
        self.assertNotIn('Add Sale', labels)
        self.assertNotIn('Party List', labels)
        self.assertNotIn('Audit Report', labels)

    def test_sidebar_does_not_fall_back_to_global_permissions_without_tenant_group(self):
        Group.objects.filter(name=f'{self.tenant.slug}:Tenant Admin').delete()

        labels = self._nav_labels(build_sidebar_sections(self.owner))

        self.assertEqual(labels, ['Dashboard', 'My Profile', 'Logout'])

    def test_tenant_admin_sidebar_uses_intersection_of_user_and_tenant_permissions(self):
        tenant_admin = User.objects.create_user(
            username='tenant.nav.admin',
            email='tenant.nav.admin@example.com',
            password='secret123',
        )
        user_perms = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=['can_manage_staff_groups', 'view_staffgroup', 'add_staffgroup', 'can_manage_parties'],
            )
        )
        tenant_scope = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=['can_manage_parties'],
            )
        )
        tenant_admin.user_permissions.set(user_perms)
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=tenant_admin,
            role=TenantMembership.Role.ADMIN,
            isDefault=True,
        )
        self._set_tenant_scope(*tenant_scope)

        labels = self._nav_labels(build_sidebar_sections(tenant_admin))

        self.assertIn('Party List', labels)
        self.assertNotIn('Staff Groups', labels)

    def test_audit_and_orders_scope_does_not_show_attendance_or_message_history(self):
        perms = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=[
                    'can_view_audit_logs',
                    'can_export_audit_logs',
                    'view_order',
                    'add_order',
                    'change_order',
                    'can_manage_orders',
                    'can_manage_staff_groups',
                    'can_manage_staff',
                    'view_staffgroup',
                    'add_staffgroup',
                    'change_staffgroup',
                    'delete_staffgroup',
                    'view_staffuser',
                    'add_staffuser',
                    'change_staffuser',
                    'delete_staffuser',
                ],
            )
        )
        self._set_tenant_scope(*perms)

        labels = self._nav_labels(build_sidebar_sections(self.owner))

        self.assertIn('Staff Groups', labels)
        self.assertIn('Orders List', labels)
        self.assertIn('Audit Report', labels)
        self.assertNotIn('Attendance Report', labels)
        self.assertNotIn("Today's Attendance", labels)
        self.assertNotIn('Message List', labels)

    def test_dashboard_uses_tenant_scoped_permissions(self):
        perms = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=['can_view_audit_logs', 'can_export_audit_logs'],
            )
        )
        self._set_tenant_scope(*perms)

        request = self.factory.get('/home/')
        request.user = self.owner
        sidebar_sections = build_sidebar_sections(request)
        dashboard_state = build_dashboard_state(request, sidebar_sections, tenant=self.tenant, staff_user=self.owner_staff)

        action_titles = [section['title'] for section in dashboard_state['action_sections']]
        metric_titles = [group['title'] for group in dashboard_state['metric_groups']]

        self.assertIn('Audit', action_titles)
        self.assertNotIn('Attendance', action_titles)
        self.assertNotIn('Attendance', metric_titles)

    def test_bank_permissions_show_bank_module_without_party_module(self):
        perms = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=['view_bank', 'add_bank'],
            )
        )
        self._set_tenant_scope(*perms)

        request = self.factory.get('/home/')
        request.user = self.owner
        labels = self._nav_labels(build_sidebar_sections(request))
        dashboard_state = build_dashboard_state(request, build_sidebar_sections(request), tenant=self.tenant, staff_user=self.owner_staff)
        metric_titles = [group['title'] for group in dashboard_state['metric_groups']]

        self.assertIn('Bank List', labels)
        self.assertNotIn('Party List', labels)
        self.assertIn('Banks', metric_titles)
        self.assertNotIn('Parties', metric_titles)

    def test_party_permissions_do_not_show_collection_or_cash_modules(self):
        perms = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=['can_manage_parties', 'view_partygroup', 'add_partygroup', 'view_party', 'add_party'],
            )
        )
        self._set_tenant_scope(*perms)

        request = self.factory.get('/home/')
        request.user = self.owner
        labels = self._nav_labels(build_sidebar_sections(request))
        dashboard_state = build_dashboard_state(request, build_sidebar_sections(request), tenant=self.tenant, staff_user=self.owner_staff)
        action_titles = [section['title'] for section in dashboard_state['action_sections']]

        self.assertIn('Party List', labels)
        self.assertNotIn('Collection List', labels)
        self.assertNotIn('Cashbook', labels)
        self.assertNotIn("Today's Cash Counter", labels)
        self.assertIn('Party', action_titles)
        self.assertNotIn('Collections', action_titles)
        self.assertNotIn('Cash Counter', action_titles)

    def test_url_is_blocked_with_message_when_tenant_scope_does_not_allow_it(self):
        perms = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=['can_view_audit_logs'],
            )
        )
        self._set_tenant_scope(*perms)
        self.client.force_login(self.owner)

        response = self.client.get('/party_list/')

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'Access denied', status_code=403)
        self.assertContains(response, 'current tenant permissions', status_code=403)

    def test_party_only_scope_blocks_collection_and_cash_urls(self):
        perms = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=['can_manage_parties', 'view_partygroup', 'view_party'],
            )
        )
        self._set_tenant_scope(*perms)
        self.client.force_login(self.owner)

        for path in ['/collection_list/', '/take_collection/', '/cashbook/', '/cash_counter/']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 403, path)
            self.assertContains(response, 'Access denied', status_code=403)

    def test_basic_attendance_permissions_do_not_show_admin_attendance_report(self):
        perms = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=['view_attendance', 'add_attendance'],
            )
        )
        self._set_tenant_scope(*perms)
        self.client.force_login(self.owner)

        request = self.factory.get('/home/')
        request.user = self.owner
        labels = self._nav_labels(build_sidebar_sections(request))

        self.assertIn("Today's Attendance", labels)
        self.assertIn('Attendance List', labels)
        self.assertNotIn('Attendance Report', labels)

        response = self.client.get('/attendance_report_admin/')
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'Access denied', status_code=403)

    def test_audit_only_scope_does_not_show_message_history_section(self):
        perms = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=['can_view_audit_logs', 'can_export_audit_logs'],
            )
        )
        self._set_tenant_scope(*perms)

        request = self.factory.get('/home/')
        request.user = self.owner
        labels = self._nav_labels(build_sidebar_sections(request))

        self.assertIn('Audit Report', labels)
        self.assertNotIn('Message List', labels)

    def test_sales_scope_does_not_show_message_history_without_message_permissions(self):
        perms = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=['view_sales', 'add_sales', 'change_sales', 'can_manage_sales'],
            )
        )
        self._set_tenant_scope(*perms)

        request = self.factory.get('/home/')
        request.user = self.owner
        labels = self._nav_labels(build_sidebar_sections(request))

        self.assertIn('Sales List', labels)
        self.assertNotIn('Message List', labels)
