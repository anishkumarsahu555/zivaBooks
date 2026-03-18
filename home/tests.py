from io import StringIO

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse
from django.test import TestCase

from home.models import AuditLog, Collection, Party, Sales, StaffGroup, StaffUser, Tenant, TenantMembership


User = get_user_model()


class SeedMockDataCommandTests(TestCase):
    def test_seed_mock_data_creates_two_tenants_with_data(self):
        stdout = StringIO()

        call_command("seed_mock_data", stdout=stdout)

        self.assertEqual(Tenant.objects.count(), 2)
        self.assertGreaterEqual(TenantMembership.objects.count(), 4)
        self.assertEqual(Party.objects.count(), 2)
        self.assertEqual(Collection.objects.count(), 2)
        self.assertGreaterEqual(StaffGroup.objects.count(), 10)
        owner = User.objects.get(username="alpha.owner")
        collector = User.objects.get(username="alphatraders.collector")
        audit_user = User.objects.get(username="alphatraders.audit")
        owner_staff = StaffUser.objects.get(user_ID=owner)
        collector_staff = StaffUser.objects.get(user_ID=collector)
        audit_staff = StaffUser.objects.get(user_ID=audit_user)
        self.assertEqual(owner_staff.staffGroup.name, "Tenant Control")
        self.assertEqual(collector_staff.staffGroup.name, "Field Collections")
        self.assertEqual(audit_staff.staffGroup.name, "Backoffice Audit")
        self.assertTrue(owner.groups.filter(name="alpha-traders:Tenant Control").exists())
        self.assertTrue(collector.groups.filter(name="alpha-traders:Field Collections").exists())
        self.assertTrue(audit_user.groups.filter(name="alpha-traders:Backoffice Audit").exists())
        self.assertTrue(owner.has_perm("home.can_manage_staff"))
        self.assertTrue(owner.has_perm("home.can_manage_staff_groups"))
        self.assertTrue(audit_user.has_perm("home.can_view_audit_logs"))

    def test_seed_mock_data_is_rerunnable(self):
        call_command("seed_mock_data")
        call_command("seed_mock_data")

        self.assertEqual(Tenant.objects.count(), 2)
        self.assertEqual(Party.objects.count(), 2)


class AuditLogSerializationTests(TestCase):
    def test_party_decimal_fields_are_json_safe_in_audit_logs(self):
        tenant = Tenant.objects.create(name="Audit Tenant", slug="audit-tenant")
        user = User.objects.create_user(username="audit-user", password="secret123")
        TenantMembership.objects.create(
            tenant=tenant,
            user=user,
            role=TenantMembership.Role.OWNER,
            isDefault=True,
        )

        party = Party.objects.create(
            tenant=tenant,
            name="Audit Party",
            phone="9999999999",
            openingBalance="1234.50",
        )
        party.openingBalance = "1500.75"
        party.save()

        logs = AuditLog.objects.filter(
            content_type=ContentType.objects.get_for_model(Party),
            object_id=party.pk,
        )
        self.assertEqual(logs.count(), 2)
        self.assertEqual(logs.first().snapshot["openingBalance"], "1500.75")


class SalesPermissionUiTests(TestCase):
    def test_view_only_sales_user_lands_on_safe_sales_page(self):
        tenant = Tenant.objects.create(name="Sales Tenant", slug="sales-tenant")
        user = User.objects.create_user(username="sales-viewer", password="sales123")
        view_sales = Permission.objects.get(codename="view_sales", content_type__app_label="home")
        django_group = Group.objects.create(name="sales-tenant:Read Only Sales")
        django_group.permissions.add(view_sales)
        staff_group = StaffGroup.objects.create(
            tenant=tenant,
            name="Read Only Sales",
            djangoGroup=django_group,
        )
        TenantMembership.objects.create(
            tenant=tenant,
            user=user,
            role=TenantMembership.Role.STAFF,
            isDefault=True,
        )
        StaffUser.objects.create(
            tenant=tenant,
            user_ID=user,
            name="Sales Viewer",
            phone="9999999998",
            staffGroup=staff_group,
            group="Read Only Sales",
            isActive="Active",
        )
        user.groups.add(django_group)

        self.client.force_login(user)

        home_response = self.client.get("/home/")
        self.assertEqual(home_response.status_code, 200)

        sales_response = self.client.get(reverse("homeApp:my_sales_list"))
        self.assertEqual(sales_response.status_code, 200)
        self.assertFalse(sales_response.context["sales_capabilities"]["can_add"])
        self.assertFalse(sales_response.context["sales_capabilities"]["can_delete"])


class MyListScopeUiTests(TestCase):
    def _make_staff_user(self, username, permission_codenames):
        tenant = Tenant.objects.create(name=f"{username} Tenant", slug=f"{username}-tenant")
        user = User.objects.create_user(username=username, password="secret123")
        permissions = list(
            Permission.objects.filter(
                codename__in=permission_codenames,
                content_type__app_label="home",
            )
        )
        django_group = Group.objects.create(name=f"{tenant.slug}:Scoped Group")
        django_group.permissions.set(permissions)
        staff_group = StaffGroup.objects.create(
            tenant=tenant,
            name="Scoped Group",
            djangoGroup=django_group,
        )
        TenantMembership.objects.create(
            tenant=tenant,
            user=user,
            role=TenantMembership.Role.STAFF,
            isDefault=True,
        )
        StaffUser.objects.create(
            tenant=tenant,
            user_ID=user,
            name="Scoped User",
            phone="9999999997",
            staffGroup=staff_group,
            group="Scoped Group",
            isActive="Active",
        )
        user.groups.add(django_group)
        return user

    def test_collection_mylist_user_gets_self_scoped_ui(self):
        user = self._make_staff_user("collection-viewer", ["view_collection"])
        self.client.force_login(user)

        response = self.client.get(reverse("homeApp:my_collection"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["collection_capabilities"]["can_filter_staff"])
        self.assertFalse(response.context["collection_capabilities"]["can_delete"])

    def test_cash_counter_mylist_user_gets_self_scoped_ui(self):
        user = self._make_staff_user("cash-viewer", ["view_cashcounter", "view_collectioncashcounter"])
        self.client.force_login(user)

        response = self.client.get(reverse("homeApp:my_cash_counter_list"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["cash_counter_capabilities"]["can_filter_staff"])
        self.assertFalse(response.context["cash_counter_capabilities"]["can_delete"])


class SalesEditLockTests(TestCase):
    def _make_sales_staff(self, username, permission_codenames):
        tenant = Tenant.objects.create(name=f"{username} Tenant", slug=f"{username}-tenant")
        user = User.objects.create_user(username=username, password="secret123")
        permissions = list(
            Permission.objects.filter(
                codename__in=permission_codenames,
                content_type__app_label="home",
            )
        )
        django_group = Group.objects.create(name=f"{tenant.slug}:Sales Group")
        django_group.permissions.set(permissions)
        staff_group = StaffGroup.objects.create(
            tenant=tenant,
            name="Sales Group",
            djangoGroup=django_group,
        )
        TenantMembership.objects.create(
            tenant=tenant,
            user=user,
            role=TenantMembership.Role.STAFF,
            isDefault=True,
        )
        staff = StaffUser.objects.create(
            tenant=tenant,
            user_ID=user,
            name="Sales Staff",
            phone="9999999996",
            staffGroup=staff_group,
            group="Sales Group",
            isActive="Active",
        )
        user.groups.add(django_group)
        return tenant, user, staff

    def test_staff_can_edit_only_before_approval(self):
        tenant, user, staff = self._make_sales_staff("sales-editor", ["view_sales", "change_sales"])
        party = Party.objects.create(tenant=tenant, name="P1", phone="9999999991", openingBalance="0")
        sale = Sales.objects.create(
            tenant=tenant,
            partyID=party,
            invoiceNumber="A/1/2026",
            amount=10.0,
            remark="old",
            buildDate="2026-03-18",
            createdBy=staff,
            isApproved=False,
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse("homeApp:update_sales_by_admin_api"),
            data={
                "invoiceSeriesSelect": "A",
                "invoiceNo": "2",
                "invoiceYearSelect": "2026",
                "colDate": "18/03/2026",
                "party": str(party.pk),
                "amount": "20",
                "remark": "new",
                "ID": str(sale.pk),
            },
        )
        self.assertEqual(response.status_code, 200)
        sale.refresh_from_db()
        self.assertEqual(sale.invoiceNumber, "A/2/2026")
        self.assertEqual(sale.amount, 20.0)

        sale.isApproved = True
        sale.save(update_fields=["isApproved", "lastUpdatedOn"])

        response = self.client.post(
            reverse("homeApp:update_sales_by_admin_api"),
            data={
                "invoiceSeriesSelect": "A",
                "invoiceNo": "3",
                "invoiceYearSelect": "2026",
                "colDate": "18/03/2026",
                "party": str(party.pk),
                "amount": "30",
                "remark": "blocked",
                "ID": str(sale.pk),
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_like_can_edit_even_after_approval(self):
        tenant, user, staff = self._make_sales_staff("sales-admin", ["can_manage_sales"])
        party = Party.objects.create(tenant=tenant, name="P1", phone="9999999992", openingBalance="0")
        sale = Sales.objects.create(
            tenant=tenant,
            partyID=party,
            invoiceNumber="A/1/2026",
            amount=10.0,
            remark="old",
            buildDate="2026-03-18",
            createdBy=staff,
            isApproved=True,
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse("homeApp:update_sales_by_admin_api"),
            data={
                "invoiceSeriesSelect": "A",
                "invoiceNo": "9",
                "invoiceYearSelect": "2026",
                "colDate": "18/03/2026",
                "party": str(party.pk),
                "amount": "99",
                "remark": "admin-ok",
                "ID": str(sale.pk),
            },
        )
        self.assertEqual(response.status_code, 200)
        sale.refresh_from_db()
        self.assertEqual(sale.amount, 99.0)
