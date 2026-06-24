import json
from datetime import date, timedelta

from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from home.models import StaffLocationLog, StaffUser
from home.context_processors import app_navigation
from home.navigation import build_sidebar_sections
from home.tests.utils import SeededTenantMixin


class StaffLocationApiTests(SeededTenantMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.collector_staff = StaffUser.objects.get(tenant=self.tenant, user_ID=self.collector)
        StaffLocationLog.objects.filter(tenant=self.tenant).delete()
        self.request_factory = RequestFactory()

    def post_json(self, url_name, payload):
        return self.client.post(
            reverse(url_name),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_staff_can_push_location_ping(self):
        self.client.force_login(self.collector)

        response = self.post_json("homeApp:add_staff_location_ping_api", {
            "client_sample_id": "sample-1",
            "latitude": 20.2961,
            "longitude": 85.8245,
            "accuracy": 12,
            "recorded_at": timezone.now().isoformat(),
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("message"), "success")
        log = StaffLocationLog.objects.get(tenant=self.tenant, staff=self.collector_staff)
        self.assertEqual(log.client_sample_id, "sample-1")
        self.assertEqual(float(log.latitude), 20.2961)

    def test_bulk_sync_skips_duplicate_client_sample(self):
        self.client.force_login(self.collector)
        recorded_at = timezone.now().isoformat()

        first = self.post_json("homeApp:bulk_staff_location_sync_api", {
            "locations": [
                {"client_sample_id": "dup-1", "latitude": 20.1, "longitude": 85.1, "recorded_at": recorded_at},
                {"client_sample_id": "dup-1", "latitude": 20.1, "longitude": 85.1, "recorded_at": recorded_at},
            ]
        })

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json().get("created"), 1)
        self.assertEqual(first.json().get("skipped"), 1)
        self.assertEqual(StaffLocationLog.objects.filter(tenant=self.tenant, staff=self.collector_staff).count(), 1)

    def test_admin_can_read_staff_daily_route(self):
        StaffLocationLog.objects.create(
            tenant=self.tenant,
            staff=self.collector_staff,
            latitude=20.2961,
            longitude=85.8245,
            recorded_at=timezone.now() - timedelta(minutes=5),
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("homeApp:staff_location_daily_route_api"), {
            "staff_id": self.collector_staff.pk,
            "date": date.today().isoformat(),
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("message"), "success")
        self.assertEqual(data.get("count"), 1)
        self.assertEqual(data.get("staff", {}).get("id"), self.collector_staff.pk)

    def test_customer_without_staff_profile_cannot_push_location(self):
        self.client.force_login(self.customer)

        response = self.post_json("homeApp:add_staff_location_ping_api", {
            "latitude": 20.2961,
            "longitude": 85.8245,
        })

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json().get("message"), "forbidden")

    def test_staff_tracker_page_renders(self):
        self.client.force_login(self.collector)

        response = self.client.get(reverse("homeApp:staff_location_tracker"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start Tracking")

    def test_admin_route_map_page_renders(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("homeApp:staff_location_map"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Staff Route Map")

    def test_non_admin_staff_gets_auto_tracking_script(self):
        self.client.force_login(self.collector)

        response = self.client.get(reverse("homeApp:staff_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "staff-location-autotrack.js")
        self.assertContains(response, "/api/staff-location/ping/")

    def test_admin_does_not_get_auto_tracking_script(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("homeApp:homepage"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "staff-location-autotrack.js")

    def test_customer_does_not_get_auto_tracking_script(self):
        request = self.request_factory.get("/customer_home/")
        request.user = self.customer

        context = app_navigation(request)

        self.assertFalse(context["staff_location_autotrack_enabled"])

    def test_attendance_nav_order_and_location_permissions(self):
        request = self.request_factory.get("/home/")
        request.user = self.collector

        attendance_section = next(
            section for section in build_sidebar_sections(request)
            if section.get("title") == "Attendance"
        )
        labels = [item["label"] for item in attendance_section["items"]]

        self.assertEqual(labels, ["Today's Attendance", "Attendance List", "Location Tracker"])
        self.assertNotIn("Staff Route Map", labels)

    def test_admin_route_map_nav_requires_view_location_permission(self):
        request = self.request_factory.get("/home/")
        request.user = self.admin

        attendance_section = next(
            section for section in build_sidebar_sections(request)
            if section.get("title") == "Attendance"
        )
        labels = [item["label"] for item in attendance_section["items"]]

        self.assertIn("Staff Route Map", labels)

    def test_user_without_location_permissions_does_not_see_location_links(self):
        request = self.request_factory.get("/home/")
        request.user = self.sales

        labels = [
            item["label"]
            for section in build_sidebar_sections(request)
            for item in section.get("items", [])
        ]

        self.assertNotIn("Location Tracker", labels)
        self.assertNotIn("Staff Route Map", labels)

    def test_location_nav_works_when_tenant_admin_group_lacks_new_permission(self):
        tracker_perm = Permission.objects.get(content_type__app_label="home", codename="can_track_staff_location")
        django_group = self.collector_staff.staffGroup.djangoGroup
        django_group.permissions.add(tracker_perm)

        admin_group = django_group.__class__.objects.get(name=f"{self.tenant.slug}:Tenant Admin")
        admin_group.permissions.remove(tracker_perm)
        for obj, attr in (
            (self.tenant, "_tenant_permission_names_cache"),
            (self.collector, "_all_permission_names_cache"),
            (self.collector, "_tenant_effective_perm_cache"),
        ):
            if hasattr(obj, attr):
                delattr(obj, attr)

        request = self.request_factory.get("/home/")
        request.user = self.collector
        labels = [
            item["label"]
            for section in build_sidebar_sections(request)
            for item in section.get("items", [])
        ]

        self.assertIn("Location Tracker", labels)
