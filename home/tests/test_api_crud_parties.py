from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone

from home.models import Party, PartyGroup, PartyLedgerEntry, TenantMembership
from home.tests.utils import SeededTenantMixin


User = get_user_model()


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

    def test_party_list_displays_current_balance(self):
        self.client.force_login(self.owner)
        self.party.openingBalance = 100
        self.party.save(update_fields=["openingBalance"])
        PartyLedgerEntry.objects.create(
            tenant=self.tenant,
            party=self.party,
            txDateTime=timezone.now(),
            particulars="Balance test sale",
            debit=250,
            credit=50,
            createdBy=self.owner_staff,
            source=PartyLedgerEntry.Source.SALES,
            reference="BAL-1",
        )

        response = self.client.get(
            "/api/PartyListJson/",
            data={
                "draw": "1",
                "start": "0",
                "length": "25",
                "search[value]": self.party.name,
                "order[0][column]": "4",
                "order[0][dir]": "desc",
            },
        )
        self.assertEqual(response.status_code, 200)
        rows = response.json()["data"]
        self.assertTrue(any("Due" in row[4] and "300.00" in row[4] for row in rows))

        summary = self.client.get("/api/party_balance_summary/")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["message"], "success")
        self.assertGreaterEqual(summary.json()["receivable"], 300)

    def test_credential_viewer_cannot_mutate_party(self):
        user = User.objects.create_user(username="credential.viewer", password="pass123")
        permission = Permission.objects.get(content_type__app_label="home", codename="can_view_party_credentials")
        user.user_permissions.add(permission)
        TenantMembership.objects.create(
            user=user,
            tenant=self.tenant,
            role=TenantMembership.Role.STAFF,
            isDefault=True,
        )
        self.client.force_login(user)

        response = self.client.post("/api/add_party_api/", data={})
        self.assertEqual(response.status_code, 403)

        response = self.client.post("/api/edit_party_api/", data={"EditId": str(self.party.pk)})
        self.assertEqual(response.status_code, 403)

        response = self.client.post("/api/delete_party/", data={"userID": str(self.party.pk)})
        self.assertEqual(response.status_code, 403)

        response = self.client.get("/api/party_balance_summary/")
        self.assertEqual(response.status_code, 200)

    def test_party_create_validation_returns_clear_error(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            "/api/add_party_api/",
            data={
                "partyName": "Invalid Party",
                "phoneNumber": "9000000099",
                "openingBalance": "not-a-number",
                "partyGroup": "",
                "staffID": str(self.owner_staff.pk),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["message"], "error")
        self.assertIn("Opening balance", response.json()["error"])

    def test_party_list_filters_by_balance_type(self):
        self.client.force_login(self.owner)
        self.party.openingBalance = 0
        self.party.partyGroupID = PartyGroup.objects.filter(tenant=self.tenant, isDeleted=False).order_by("id").first()
        self.party.assignTo = self.owner_staff
        self.party.save(update_fields=["openingBalance", "partyGroupID", "assignTo"])
        PartyLedgerEntry.objects.create(
            tenant=self.tenant,
            party=self.party,
            txDateTime=timezone.now(),
            particulars="Filter balance test",
            debit=400,
            credit=0,
            createdBy=self.owner_staff,
            source=PartyLedgerEntry.Source.SALES,
            reference="FILTER-BAL",
        )

        response = self.client.get(
            "/api/PartyListJson/",
            data={
                "draw": "1",
                "start": "0",
                "length": "25",
                "partyGroup": str(self.party.partyGroupID_id),
                "staffID": str(self.owner_staff.pk),
                "balanceType": "due",
            },
        )
        self.assertEqual(response.status_code, 200)
        rows = response.json()["data"]
        self.assertTrue(any(row[0] == self.party.name and "Due" in row[4] for row in rows))
