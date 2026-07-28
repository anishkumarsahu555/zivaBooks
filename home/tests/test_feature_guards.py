"""
Unit test suite for Feature Module Enforcement Guards & View Decorators.
"""

import json
from django.test import TestCase, Client, RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse
from home.models import LicensePlan, Tenant, TenantMembership
from home.utils.feature_guards import tenant_has_feature, require_tenant_feature
from home.navigation import build_sidebar_sections

User = get_user_model()


@require_tenant_feature('gst_einvoicing')
def dummy_einvoice_view(request):
    return HttpResponse("EINVOICE_OK")


@require_tenant_feature('ziva_ai')
def dummy_ai_view(request):
    return HttpResponse("AI_OK")


class FeatureGuardsTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()
        self.user = User.objects.create_superuser(username='testguardadmin', email='admin@test.com', password='password123')
        self.tenant = Tenant.objects.create(name="Guard Test Tenant", slug="guard-test-tenant")
        TenantMembership.objects.create(tenant=self.tenant, user=self.user, role='owner', isDefault=True)

        # Grant tenant admin permissions to tenant admin group
        group, _ = Group.objects.get_or_create(name=f"{self.tenant.slug}:Tenant Admin")
        group.permissions.set(Permission.objects.filter(content_type__app_label='home'))

        self.starter_plan = LicensePlan.objects.create(
            plan_code='guard_starter',
            name='Guard Starter Plan',
            price_per_year=9999.00,
            user_seats=2,
            inventory_enabled=True,
            gst_einvoicing_enabled=False,
            ziva_ai_enabled=False,
        )

        self.enterprise_plan = LicensePlan.objects.create(
            plan_code='guard_enterprise',
            name='Guard Enterprise Plan',
            price_per_year=49999.00,
            user_seats=0,
            inventory_enabled=True,
            gst_einvoicing_enabled=True,
            ziva_ai_enabled=True,
        )

    def _clear_caches(self, request=None):
        cache_attrs = [
            '_cached_current_tenant',
            '_sidebar_sections_cache',
            '_all_permission_names_cache',
            '_tenant_permission_names_cache',
            '_tenant_effective_perm_cache',
            '_tenant_membership_role_cache',
        ]
        for attr in cache_attrs:
            if hasattr(self.user, attr):
                delattr(self.user, attr)
            if hasattr(self.tenant, attr):
                delattr(self.tenant, attr)
            if request and hasattr(request, attr):
                delattr(request, attr)

    def test_tenant_has_feature_helper(self):
        self.tenant.apply_license_plan(self.starter_plan)
        self.assertTrue(tenant_has_feature(self.tenant, 'inventory'))
        self.assertFalse(tenant_has_feature(self.tenant, 'gst_einvoicing'))
        self.assertFalse(tenant_has_feature(self.tenant, 'ziva_ai'))

        self.tenant.apply_license_plan(self.enterprise_plan)
        self.assertTrue(tenant_has_feature(self.tenant, 'gst_einvoicing'))
        self.assertTrue(tenant_has_feature(self.tenant, 'ziva_ai'))

    def test_require_tenant_feature_decorator_blocked(self):
        self.tenant.apply_license_plan(self.starter_plan)

        request = self.factory.get('/api/ziva-ai/', HTTP_ACCEPT='application/json')
        request.user = self.user
        request.tenant = self.tenant

        response = dummy_ai_view(request)
        self.assertEqual(response.status_code, 403)
        data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(data['status'], 'error')
        self.assertEqual(data['code'], 'FEATURE_LOCKED')

    def test_require_tenant_feature_decorator_allowed(self):
        self.tenant.apply_license_plan(self.enterprise_plan)

        request = self.factory.get('/api/ziva-ai/', HTTP_ACCEPT='application/json')
        request.user = self.user
        request.tenant = self.tenant

        response = dummy_ai_view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode('utf-8'), "AI_OK")

    def test_sidebar_navigation_hides_locked_features(self):
        self.tenant.apply_license_plan(self.starter_plan)

        request = self.factory.get('/')
        request.user = self.user
        request.tenant = self.tenant

        sections = build_sidebar_sections(request)
        
        # Collect all nav item labels
        nav_labels = []
        for sec in sections:
            for item in sec.get('items', []):
                nav_labels.append(item['label'])

        # Ziva AI should be hidden under starter plan
        self.assertNotIn('Ziva AI', nav_labels)

        # Clear memory caches
        self._clear_caches(request)

        # Now apply enterprise plan to tenant
        self.tenant.apply_license_plan(self.enterprise_plan)
        self.tenant.refresh_from_db()

        request_ent = self.factory.get('/')
        request_ent.user = self.user
        request_ent.tenant = self.tenant

        sections_ent = build_sidebar_sections(request_ent)
        nav_labels_ent = []
        for sec in sections_ent:
            for item in sec.get('items', []):
                nav_labels_ent.append(item['label'])

        # Ziva AI should be visible under enterprise plan
        self.assertIn('Ziva AI', nav_labels_ent)

    def test_direct_url_visit_blocked_for_locked_plan_features(self):
        self.tenant.apply_license_plan(self.starter_plan)

        from home.views import add_attendance, staff_location_tracker, ziva_ai_page

        # Web request for /add_attendance/
        request_att = self.factory.get('/add_attendance/')
        request_att.user = self.user
        request_att.tenant = self.tenant
        response_att = add_attendance(request_att)
        self.assertEqual(response_att.status_code, 403)
        self.assertIn(b"Module Locked", response_att.content)

        # Web request for /staff_location_tracker/
        request_loc = self.factory.get('/staff_location_tracker/')
        request_loc.user = self.user
        request_loc.tenant = self.tenant
        response_loc = staff_location_tracker(request_loc)
        self.assertEqual(response_loc.status_code, 403)
        self.assertIn(b"Module Locked", response_loc.content)

        # Web request for /ziva_ai/
        request_ai = self.factory.get('/ziva-ai/')
        request_ai.user = self.user
        request_ai.tenant = self.tenant
        response_ai = ziva_ai_page(request_ai)
        self.assertEqual(response_ai.status_code, 403)
        self.assertIn(b"Module Locked", response_ai.content)

    def test_staff_user_seat_limit_validation(self):
        from home.models import StaffUser, StaffGroup
        from home.utils.feature_guards import check_staff_user_limit

        self.tenant.apply_license_plan(self.starter_plan)  # user_seats = 2

        group = StaffGroup.objects.create(tenant=self.tenant, name="Test Staff Group")

        # Create 2 active staff members (filling max seats = 2)
        s1 = StaffUser.objects.create(tenant=self.tenant, name="Staff 1", isActive="Active", isDeleted=False, staffGroup=group)
        s2 = StaffUser.objects.create(tenant=self.tenant, name="Staff 2", isActive="Active", isDeleted=False, staffGroup=group)

        # Create 1 inactive staff member
        s3_inactive = StaffUser.objects.create(tenant=self.tenant, name="Staff 3 Inactive", isActive="Inactive", isDeleted=False, staffGroup=group)

        # Create 1 deleted staff member
        s4_deleted = StaffUser.objects.create(tenant=self.tenant, name="Staff 4 Deleted", isActive="Active", isDeleted=True, staffGroup=group)

        # Check limit for a new ACTIVE staff user (should be blocked)
        is_allowed, error_msg, max_seats, active_count = check_staff_user_limit(self.tenant, target_will_be_active=True)
        self.assertFalse(is_allowed)
        self.assertEqual(max_seats, 2)
        self.assertEqual(active_count, 2)
        self.assertIn("Active staff user limit reached", error_msg)

        # Check limit for a new INACTIVE staff user (should be allowed)
        is_allowed_inactive, _, _, _ = check_staff_user_limit(self.tenant, target_will_be_active=False)
        self.assertTrue(is_allowed_inactive)

        # Check limit when editing an existing active staff user (should be allowed)
        is_allowed_edit, _, _, _ = check_staff_user_limit(self.tenant, target_will_be_active=True, editing_staff_id=s1.pk)
        self.assertTrue(is_allowed_edit)

        # Check limit when activating s3_inactive (should be blocked as seats are full)
        is_allowed_activate, error_msg_act, _, _ = check_staff_user_limit(self.tenant, target_will_be_active=True, editing_staff_id=s3_inactive.pk)
        self.assertFalse(is_allowed_activate)
        self.assertIn("Active staff user limit reached", error_msg_act)

        # Upgrade to Enterprise Plan (user_seats = 0 -> unlimited)
        self.tenant.apply_license_plan(self.enterprise_plan)
        is_allowed_ent, _, _, _ = check_staff_user_limit(self.tenant, target_will_be_active=True)
        self.assertTrue(is_allowed_ent)


