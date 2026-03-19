from django.contrib.auth.models import Permission
from django.test import TestCase

from home.models import StaffGroup
from home.tests.utils import SeededTenantMixin


class StaffGroupApiCrudTests(SeededTenantMixin, TestCase):
    def test_staff_group_crud(self):
        self.client.force_login(self.owner)

        perm = Permission.objects.filter(content_type__app_label="home").order_by("id").first()
        perm_id = str(perm.id) if perm else ""

        create = self.client.post(
            "/api/add_staff_group_api/",
            data={
                "groupName": "API Test Group",
                "description": "Created via API test",
                "permission_ids[]": [perm_id] if perm_id else [],
            },
        )
        self.assertEqual(create.status_code, 200)
        payload = create.json()
        self.assertEqual(payload.get("message"), "success")
        group_id = payload.get("group_id")
        self.assertTrue(group_id)

        detail = self.client.get("/api/get_staff_group_detail/", data={"id": str(group_id)})
        self.assertEqual(detail.status_code, 200)
        detail_payload = detail.json()
        self.assertIn("data", detail_payload)
        self.assertIn("permission_catalog", detail_payload)

        edit = self.client.post(
            "/api/edit_staff_group_api/",
            data={
                "EditId": str(group_id),
                "groupName": "API Test Group (Updated)",
                "description": "Updated via API test",
                "permission_ids[]": [perm_id] if perm_id else [],
            },
        )
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.json().get("message"), "success")

        delete = self.client.post("/api/delete_staff_group_api/", data={"userID": str(group_id)})
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.json().get("message"), "success")

        self.assertTrue(StaffGroup.objects.get(pk=group_id).isDeleted)

