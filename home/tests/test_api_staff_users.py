from django.test import TestCase

from home.models import StaffUser
from home.tests.utils import SeededTenantMixin, tiny_png


class StaffUserApiCrudTests(SeededTenantMixin, TestCase):
    def test_staff_user_create_without_optional_uploads(self):
        self.client.force_login(self.owner)

        staff_group = self.any_staff_group()
        self.assertIsNotNone(staff_group)

        create = self.client.post(
            "/api/add_staff_api/",
            data={
                "CompanyUserName": "API Staff No Uploads",
                "UserPhoneNo": "9000000001",
                "UserEmail": "nouploads@example.com",
                "UserAddress": "No Upload Address",
                "UserGroup": str(staff_group.pk),
                "UserStatus": "Active",
                "UserPwd": "secret123",
            },
        )
        self.assertEqual(create.status_code, 200)
        self.assertEqual(create.json().get("message"), "success")

        staff = StaffUser.objects.filter(tenant=self.tenant, name="API Staff No Uploads").order_by("-id").first()
        self.assertIsNotNone(staff)
        self.assertFalse(bool(staff.photo))
        self.assertFalse(bool(staff.idProof))

    def test_staff_user_create_edit_delete(self):
        self.client.force_login(self.owner)

        staff_group = self.any_staff_group()
        self.assertIsNotNone(staff_group)

        create = self.client.post(
            "/api/add_staff_api/",
            data={
                "CompanyUserName": "API Staff",
                "UserPhoneNo": "9000000099",
                "UserEmail": "api.staff@example.com",
                "UserAddress": "API Address",
                "UserGroup": str(staff_group.pk),
                "UserStatus": "Active",
                "UserPwd": "secret123",
                "imageUpload": tiny_png("photo.png"),
                "imageUploadID": tiny_png("id.png"),
            },
        )
        self.assertEqual(create.status_code, 200)
        self.assertEqual(create.json().get("message"), "success")

        staff = StaffUser.objects.filter(tenant=self.tenant, name="API Staff").order_by("-id").first()
        self.assertIsNotNone(staff)

        detail = self.client.get("/api/get_staff_user_detail/", data={"id": str(staff.pk)})
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["ID"], staff.pk)

        edit = self.client.post(
            "/api/edit_staff_api/",
            data={
                "EditUserId": str(staff.pk),
                "CompanyUserName": "API Staff (Updated)",
                "UserPhoneNo": "9000000088",
                "UserEmail": "api.staff2@example.com",
                "UserAddress": "API Address 2",
                "UserGroup": str(staff_group.pk),
                "UserStatus": "Inactive",
                "UserPwd": "secret123",
            },
        )
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.json().get("message"), "success")

        list_resp = self.client.get("/api/StaffUserListJson/", data={"draw": "1", "start": "0", "length": "5"})
        self.assertEqual(list_resp.status_code, 200)

        delete = self.client.post("/api/delete_staff_user/", data={"userID": str(staff.pk)})
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.json().get("message"), "success")

        staff.refresh_from_db()
        self.assertTrue(staff.isDeleted)
