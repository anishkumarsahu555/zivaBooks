"""
Unit test suite for dynamic LicensePlan model, Tenant integration, Tenant Admin Access sync, and list_license_plans_api.
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import Group
from home.models import LicensePlan, Tenant


class LicensePlanTestCase(TestCase):
    def setUp(self):
        self.client = Client()

    def test_seed_default_plans(self):
        plans = LicensePlan.get_active_plans()
        self.assertGreaterEqual(plans.count(), 3)
        starter = plans.get(plan_code='starter')
        self.assertEqual(float(starter.price_per_year), 19999.0)
        self.assertEqual(float(starter.original_price_per_year), 25000.0)
        self.assertEqual(starter.user_seats, 5)
        self.assertEqual(starter.ziva_ai_max_queries_per_month, 0)
        self.assertFalse(starter.ziva_ai_enabled)
        self.assertFalse(starter.geofenced_attendance_enabled)
        self.assertFalse(starter.gps_tracking_enabled)
        self.assertTrue(starter.inventory_enabled)
        self.assertTrue(starter.sales_billing_enabled)
        self.assertTrue(starter.gst_einvoicing_enabled)

    def test_custom_dynamic_plan_creation(self):
        custom_plan = LicensePlan.objects.create(
            plan_code='custom_vip_plan',
            name='VIP Custom Plan',
            tagline='VIP Enterprise',
            price_per_year=79999.00,
            original_price_per_year=99999.00,
            user_seats=0,
            is_featured=True,
            display_order=4,
            is_active=True,
            is_custom=True,
            sales_billing_enabled=True,
            gst_einvoicing_enabled=True,
            eway_bill_enabled=True,
            inventory_enabled=True,
            fefo_batch_tracking_enabled=True,
            multi_warehouse_enabled=True,
            dual_otp_enabled=True,
            gps_tracking_enabled=True,
            route_history_map_enabled=True,
            ziva_ai_enabled=True,
            ziva_ai_max_queries_per_month=50000,
            features_list=["Custom Feature A", "Custom Feature B"]
        )
        self.assertIsNotNone(custom_plan.id)
        public_plans = LicensePlan.get_active_plans()
        self.assertNotIn(custom_plan, public_plans)
        all_plans = LicensePlan.get_active_plans(include_custom=True)
        self.assertIn(custom_plan, all_plans)
        self.assertEqual(custom_plan.ziva_ai_max_queries_per_month, 50000)

    def test_tenant_license_plan_sync(self):
        tenant = Tenant.objects.create(name="Test Sync Enterprise", slug="test-sync-ent")
        growth_plan = LicensePlan.get_active_plans().get(plan_code='growth')
        
        # Apply growth plan to tenant
        tenant.apply_license_plan(growth_plan)
        tenant.refresh_from_db()
        
        self.assertEqual(tenant.license_plan, growth_plan)
        self.assertTrue(tenant.einvoice_enabled)
        self.assertTrue(tenant.nonGstShadowEnabled)
        
        summary = tenant.get_license_plan_summary()
        self.assertEqual(summary['plan_name'], 'Growth License')
        self.assertEqual(summary['user_seats'], 20)
        self.assertTrue(summary['features']['gst_einvoicing_enabled'])

    def test_tenant_admin_access_license_plan_sync(self):
        tenant = Tenant.objects.create(name="Admin Access Sync Co", slug="admin-access-sync-co")
        starter_plan = LicensePlan.get_active_plans().get(plan_code='starter')
        enterprise_plan = LicensePlan.get_active_plans().get(plan_code='enterprise')

        # Apply Starter Plan
        tenant.apply_license_plan(starter_plan)
        group_name = f"{tenant.slug}:Tenant Admin"
        group = Group.objects.get(name=group_name)

        starter_codenames = list(group.permissions.values_list('codename', flat=True))
        self.assertNotIn('can_track_staff_location', starter_codenames)

        # Upgrade to Enterprise Plan
        tenant.apply_license_plan(enterprise_plan)
        group.refresh_from_db()
        ent_codenames = list(group.permissions.values_list('codename', flat=True))

        self.assertIn('can_access_ziva_ai', ent_codenames)
        self.assertIn('can_track_staff_location', ent_codenames)

    def test_list_license_plans_api(self):
        url = reverse('homeApp:list_license_plans_api')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertIn('plans', data)
        self.assertGreaterEqual(len(data['plans']), 3)

        first_plan = data['plans'][0]
        self.assertIn('original_price_per_year', first_plan)
        self.assertIn('features', first_plan)
        self.assertIn('sales_billing_enabled', first_plan['features'])
        self.assertIn('ziva_ai_max_queries_per_month', first_plan['features'])

    def test_attendance_dashboard_and_navigation_plan_toggle(self):
        from home.dashboard import build_dashboard_state
        from django.contrib.auth.models import User, Permission
        from home.models import TenantMembership

        tenant = Tenant.objects.create(name="Attendance Test Tenant", slug="att-test-tenant")
        starter_plan = LicensePlan.get_active_plans().get(plan_code='starter')
        growth_plan = LicensePlan.get_active_plans().get(plan_code='growth')

        user = User.objects.create_user(username='att_user', password='password123')
        TenantMembership.objects.create(user=user, tenant=tenant, role=TenantMembership.Role.ADMIN)

        perms = Permission.objects.filter(codename__in=['add_attendance', 'view_attendance', 'can_view_all_attendance'])
        user.user_permissions.set(perms)

        request = self.client.get('/home/').wsgi_request
        request.user = user
        request.tenant = tenant

        # 1. On Starter Plan (staff_attendance_enabled=False)
        tenant.apply_license_plan(starter_plan)

        dashboard = build_dashboard_state(request, [])
        metric_group_titles = [group['title'] for group in dashboard.get('metric_groups', [])]
        self.assertNotIn('Attendance', metric_group_titles)

        # 2. On Growth Plan (staff_attendance_enabled=True)
        tenant.apply_license_plan(growth_plan)
        if hasattr(tenant, '_tenant_permission_names_cache'):
            delattr(tenant, '_tenant_permission_names_cache')
        if hasattr(request, '_tenant_permission_names_cache'):
            delattr(request, '_tenant_permission_names_cache')
        if hasattr(request, '_dashboard_state_cache'):
            delattr(request, '_dashboard_state_cache')
        if hasattr(request, '_tenant_effective_perm_cache'):
            delattr(request, '_tenant_effective_perm_cache')
        if hasattr(user, '_tenant_effective_perm_cache'):
            delattr(user, '_tenant_effective_perm_cache')

        req_growth = self.client.get('/home/').wsgi_request
        req_growth.user = user
        req_growth.tenant = tenant

        dashboard_growth = build_dashboard_state(req_growth, [])
        metric_group_titles_growth = [group['title'] for group in dashboard_growth.get('metric_groups', [])]
        self.assertIn('Attendance', metric_group_titles_growth)
