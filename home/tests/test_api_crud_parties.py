from django.test import TestCase

from home.models import Party, PartyGroup
from home.tests.utils import SeededTenantMixin


class PartyApiCrudTests(SeededTenantMixin, TestCase):
    def test_party_crud(self):
        self.client.force_login(self.owner)

        party_group = PartyGroup.objects.filter(tenant=self.tenant, isDeleted=False).order_by("id").first()
        self.assertIsNotNone(party_group)

        create = self.client.post(
            "/api/add_party_api/",
            data={
                "partyName": "API Party",
                "legalName": "API Party Pvt Ltd",
                "phoneNumber": "9000000011",
                "gstNumber": "22AAAAA0000A1Z5",
                "isB2B": "1",
                "billingAddress": "Tax street",
                "address": "Main street",
                "openingBalance": "100.50",
                "partyGroup": str(party_group.pk),
                "staffID": str(self.owner_staff.pk),
            },
        )
        self.assertEqual(create.status_code, 200)
        self.assertEqual(create.json().get("message"), "success")

        party = Party.objects.filter(tenant=self.tenant, name="API Party", isDeleted=False).order_by("-id").first()
        self.assertIsNotNone(party)

        detail = self.client.get("/api/get_party_detail/", data={"id": str(party.pk)})
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["ID"], party.pk)
        self.assertEqual(detail.json()["data"]["GSTNumber"], "22AAAAA0000A1Z5")
        self.assertTrue(detail.json()["data"]["IsB2B"])

        edit = self.client.post(
            "/api/edit_party_api/",
            data={
                "EditId": str(party.pk),
                "partyName": "API Party (Updated)",
                "legalName": "API Party Updated LLP",
                "phoneNumber": "9000000022",
                "gstNumber": "22BBBBB0000B1Z6",
                "isB2B": "1",
                "billingAddress": "Updated billing address",
                "address": "Updated main address",
                "openingBalance": "200.75",
                "partyGroup": str(party_group.pk),
                "staffID": str(self.owner_staff.pk),
            },
        )
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.json().get("message"), "success")
        party.refresh_from_db()
        self.assertEqual(party.gstNumber, "22BBBBB0000B1Z6")
        self.assertEqual(party.legalName, "API Party Updated LLP")

        delete = self.client.post("/api/delete_party/", data={"userID": str(party.pk)})
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.json().get("message"), "success")

        party.refresh_from_db()
        self.assertTrue(party.isDeleted)
