from django.test import TestCase

from home.models import PartyGroup
from home.tests.utils import SeededTenantMixin


class PartyGroupApiCrudTests(SeededTenantMixin, TestCase):
    def test_party_group_crud(self):
        self.client.force_login(self.owner)

        create = self.client.post(
            "/api/add_party_group_api/",
            data={"groupName": "API PartyGroup", "description": "Created via API test"},
        )
        self.assertEqual(create.status_code, 200)
        self.assertEqual(create.json().get("message"), "success")

        pg = PartyGroup.objects.filter(tenant=self.tenant, name="API PartyGroup", isDeleted=False).order_by("-id").first()
        self.assertIsNotNone(pg)

        list_resp = self.client.get("/api/PartyGroupListJson/", data={"draw": "1", "start": "0", "length": "5"})
        self.assertEqual(list_resp.status_code, 200)

        detail = self.client.get("/api/get_party_group_detail/", data={"id": str(pg.pk)})
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["ID"], pg.pk)

        edit = self.client.post(
            "/api/edit_part_group_api/",
            data={"EditId": str(pg.pk), "groupName": "API PartyGroup (Updated)", "description": "Updated"},
        )
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.json().get("message"), "success")

        delete = self.client.post("/api/delete_part_group/", data={"userID": str(pg.pk)})
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.json().get("message"), "success")

        pg.refresh_from_db()
        self.assertTrue(pg.isDeleted)

