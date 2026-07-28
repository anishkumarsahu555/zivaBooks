from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from home.models import StaffGroup, TenantMembership
from home.tests.utils import SeededTenantMixin


User = get_user_model()


class StaffGroupPermissionCatalogTests(SeededTenantMixin, TestCase):
    def _set_tenant_scope(self, *permissions):
        group, _ = Group.objects.get_or_create(name=f'{self.tenant.slug}:Tenant Admin')
        group.permissions.set(list(permissions))
        return group

    def test_owner_sees_audit_permissions_in_staff_group_form(self):
        audit_permissions = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=[
                    'can_manage_staff_groups',
                    'can_view_audit_logs',
                    'can_export_audit_logs',
                    'view_auditlog',
                    'add_auditlog',
                    'change_auditlog',
                    'delete_auditlog',
                ],
            )
        )
        self._set_tenant_scope(*audit_permissions)
        self.client.force_login(self.owner)

        response = self.client.get(reverse('homeApp:staff_group_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'can_view_audit_logs')
        self.assertContains(response, 'Audit Admin')
        self.assertContains(response, 'Audit Viewer')

    def test_tenant_admin_sees_only_their_assignable_permissions(self):
        limited_admin = User.objects.create_user(
            username='limited.staffgroups',
            email='limited.staffgroups@example.com',
            password='secret123',
        )
        allowed_perms = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=[
                    'can_manage_staff_groups',
                    'view_staffgroup',
                    'add_staffgroup',
                    'change_staffgroup',
                    'delete_staffgroup',
                    'view_party',
                ],
            )
        )
        self._set_tenant_scope(*allowed_perms)
        limited_admin.user_permissions.set(allowed_perms)
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=limited_admin,
            role=TenantMembership.Role.ADMIN,
            isDefault=True,
        )

        self.client.force_login(limited_admin)
        response = self.client.get(reverse('homeApp:staff_group_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'can_manage_staff_groups')
        self.assertContains(response, 'view_party')
        self.assertNotContains(response, 'can_view_audit_logs')
        self.assertNotContains(response, 'Enable Audit')

    def test_location_permissions_are_assignable_to_staff_groups(self):
        manage_groups_perm = Permission.objects.get(content_type__app_label='home', codename='can_manage_staff_groups')
        tracker_perm = Permission.objects.get(content_type__app_label='home', codename='can_track_staff_location')
        viewer_perm = Permission.objects.get(content_type__app_label='home', codename='can_view_staff_locations')
        self._set_tenant_scope(manage_groups_perm)
        self.client.force_login(self.owner)

        response = self.client.get(reverse('homeApp:staff_group_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'can_track_staff_location')
        self.assertContains(response, 'can_view_staff_locations')
        self.assertContains(response, 'Location Tracker')
        self.assertContains(response, 'Staff Location Viewer')

        create = self.client.post(
            reverse('homeApp:add_staff_group_api'),
            data={
                'groupName': 'Location Enabled Group',
                'description': 'Can track and view staff locations',
                'permission_ids[]': [str(tracker_perm.pk), str(viewer_perm.pk)],
            },
        )

        self.assertEqual(create.status_code, 200)
        self.assertEqual(create.json().get('message'), 'success')

    def test_ziva_ai_permission_is_assignable_to_staff_groups(self):
        manage_groups_perm = Permission.objects.get(content_type__app_label='home', codename='can_manage_staff_groups')
        ziva_ai_perm = Permission.objects.get(content_type__app_label='home', codename='can_access_ziva_ai')
        self._set_tenant_scope(manage_groups_perm)
        self.client.force_login(self.owner)

        response = self.client.get(reverse('homeApp:staff_group_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'can_access_ziva_ai')
        self.assertContains(response, 'Can access Ziva AI')
        self.assertContains(response, 'Ziva AI')
        self.assertContains(response, 'Ziva AI Access')

        create = self.client.post(
            reverse('homeApp:add_staff_group_api'),
            data={
                'groupName': 'Ziva AI Enabled Group',
                'description': 'Can open Ziva AI',
                'permission_ids[]': [str(ziva_ai_perm.pk)],
            },
        )

        self.assertEqual(create.status_code, 200)
        self.assertEqual(create.json().get('message'), 'success')

    def test_api_rejects_permissions_outside_tenant_admin_scope(self):
        limited_admin = User.objects.create_user(
            username='limited.staffgroups.api',
            email='limited.staffgroups.api@example.com',
            password='secret123',
        )
        allowed_perm = Permission.objects.get(content_type__app_label='home', codename='view_party')
        manage_groups_perm = Permission.objects.get(content_type__app_label='home', codename='can_manage_staff_groups')
        self._set_tenant_scope(allowed_perm, manage_groups_perm)
        limited_admin.user_permissions.set([allowed_perm, manage_groups_perm])
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=limited_admin,
            role=TenantMembership.Role.ADMIN,
            isDefault=True,
        )

        self.client.force_login(limited_admin)
        audit_perm = Permission.objects.get(content_type__app_label='home', codename='can_view_audit_logs')
        response = self.client.post(
            reverse('homeApp:add_staff_group_api'),
            data={
                'groupName': 'Limited Admin Group',
                'description': 'Should not be able to assign audit access',
                'permission_ids[]': [str(allowed_perm.pk), str(audit_perm.pk)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get('message'), 'error')
        self.assertIn('tenant admin access', response.json().get('error', ''))

    def test_edit_form_does_not_show_tenant_module_presets(self):
        permissions = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=[
                    'can_manage_staff_groups',
                    'view_auditlog',
                    'add_auditlog',
                    'change_auditlog',
                    'delete_auditlog',
                    'can_view_audit_logs',
                    'can_export_audit_logs',
                ],
            )
        )
        self._set_tenant_scope(*permissions)
        django_group = Group.objects.create(name='Audit Focus Group')
        django_group.permissions.set(permissions)
        staff_group = StaffGroup.objects.create(
            tenant=self.tenant,
            name='Audit Focus Group',
            description='Audit only',
            djangoGroup=django_group,
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse('homeApp:staff_group_edit', args=[staff_group.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'value="tenant_audit"')
        self.assertNotContains(response, 'Enable Audit')

    def test_books_and_related_model_permissions_are_covered_by_presets(self):
        permissions = list(
            Permission.objects.filter(
                content_type__app_label='home',
                codename__in=[
                    'view_attendance',
                    'add_attendance',
                    'change_attendance',
                    'delete_attendance',
                    'can_view_all_attendance',
                    'can_view_attendance_reports',
                    'can_manage_staff_groups',
                    'can_view_staff_group_permissions',
                    'can_manage_staff',
                    'can_reset_staff_password',
                    'can_assign_staff_groups',
                    'view_staffgroup',
                    'add_staffgroup',
                    'change_staffgroup',
                    'delete_staffgroup',
                    'view_staffuser',
                    'add_staffuser',
                    'change_staffuser',
                    'delete_staffuser',
                    'view_bookaccount',
                    'add_bookaccount',
                    'change_bookaccount',
                    'delete_bookaccount',
                    'view_booktransaction',
                    'add_booktransaction',
                    'change_booktransaction',
                    'delete_booktransaction',
                    'can_view_bank_books',
                    'can_manage_books',
                    'view_order',
                    'add_order',
                    'change_order',
                    'delete_order',
                    'view_orderattachment',
                    'add_orderattachment',
                    'change_orderattachment',
                    'delete_orderattachment',
                    'can_manage_orders',
                    'can_approve_orders',
                    'can_view_order_reports',
                    'view_party',
                    'add_party',
                    'change_party',
                    'delete_party',
                    'view_partyledgerentry',
                    'add_partyledgerentry',
                    'change_partyledgerentry',
                    'delete_partyledgerentry',
                    'can_manage_parties',
                    'can_view_party_credentials',
                    'view_partygroup',
                    'add_partygroup',
                    'change_partygroup',
                    'delete_partygroup',
                    'view_bank',
                    'add_bank',
                    'change_bank',
                    'delete_bank',
                    'view_invoiceseries',
                    'add_invoiceseries',
                    'change_invoiceseries',
                    'delete_invoiceseries',
                    'view_auditlog',
                    'add_auditlog',
                    'change_auditlog',
                    'delete_auditlog',
                    'can_view_audit_logs',
                    'can_export_audit_logs',
                    'view_whatsappmessagestatus',
                    'add_whatsappmessagestatus',
                    'change_whatsappmessagestatus',
                    'delete_whatsappmessagestatus',
                    'view_sales',
                    'add_sales',
                    'change_sales',
                    'delete_sales',
                    'can_manage_sales',
                    'can_approve_sales',
                    'can_view_sales_reports',
                    'view_collection',
                    'add_collection',
                    'change_collection',
                    'delete_collection',
                    'can_approve_collection',
                    'can_tally_collection',
                    'can_view_collection_reports',
                    'view_cashcounter',
                    'add_cashcounter',
                    'change_cashcounter',
                    'delete_cashcounter',
                    'view_collectioncashcounter',
                    'add_collectioncashcounter',
                    'change_collectioncashcounter',
                    'delete_collectioncashcounter',
                    'can_manage_cash_counter',
                    'can_view_cash_counter_reports',
                ],
            )
        )
        self._set_tenant_scope(*permissions)
        self.client.force_login(self.owner)

        response = self.client.get(reverse('homeApp:staff_group_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sales Manager')
        self.assertContains(response, 'Orders Manager')
        self.assertContains(response, 'Collections Manager')
        self.assertContains(response, 'Cash Counter Manager')
        self.assertContains(response, 'Books Manager')
        self.assertContains(response, 'Books Viewer')
        self.assertContains(response, 'Books Admin')
        self.assertContains(response, 'Books Editor')
        self.assertContains(response, 'Invoice Series Admin')
        self.assertContains(response, 'Invoice Series Manager')
        self.assertContains(response, 'Invoice Series Viewer')
        self.assertContains(response, 'Attendance Admin')
        self.assertContains(response, 'Attendance Manager')
        self.assertContains(response, 'Orders Admin')
        self.assertContains(response, 'Parties Admin')
        self.assertContains(response, 'Parties Editor')
        self.assertContains(response, 'delete_attendance')
        self.assertContains(response, 'Party Groups Admin')
        self.assertContains(response, 'Party Groups Manager')
        self.assertContains(response, 'Party Groups Editor')
        self.assertContains(response, 'Party Groups Viewer')
        self.assertContains(response, 'Banks Admin')
        self.assertContains(response, 'Banks Manager')
        self.assertContains(response, 'Banks Editor')
        self.assertContains(response, 'Banks Viewer')
        self.assertContains(response, 'Messages Admin')
        self.assertContains(response, 'Messages Manager')
        self.assertContains(response, 'Messages Editor')
        self.assertContains(response, 'Messages Viewer')
        self.assertContains(response, 'Staff Manager')
        self.assertContains(response, 'Staff Editor')
        self.assertContains(response, 'Staff Viewer')
        self.assertContains(response, 'Staff Groups Manager')
        self.assertContains(response, 'Staff Groups Editor')
        self.assertContains(response, 'Staff Groups Viewer')
        self.assertContains(response, 'Staff Users Manager')
        self.assertContains(response, 'Staff Users Editor')
        self.assertContains(response, 'Staff Users Viewer')
        self.assertContains(response, 'Audit Admin')
        self.assertContains(response, 'Audit Manager')
        self.assertContains(response, 'Audit Editor')
        self.assertContains(response, 'Party Ledger Manager')
        self.assertContains(response, 'Party Ledger Viewer')
        self.assertContains(response, 'Party Ledger Admin')
        self.assertContains(response, 'Party Ledger Editor')
        self.assertContains(response, 'view_orderattachment')
        self.assertContains(response, 'view_partyledgerentry')
