from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import Client, TestCase
from django.test.utils import override_settings
from django.urls import reverse

from home.apiView import _sync_staff_user_group_membership, send_whatsapp_message
from home.models import AuditLog, Collection, Party, Sales, StaffGroup, StaffUser, Tenant, TenantMembership, WhatsappMessageStatus
from home.otp import get_collection_otp_payload


User = get_user_model()


class SeedMockDataCommandTests(TestCase):
    def test_seed_mock_data_creates_two_tenants_with_data(self):
        stdout = StringIO()

        call_command("seed_mock_data", stdout=stdout)

        self.assertEqual(Tenant.objects.count(), 2)
        self.assertGreaterEqual(TenantMembership.objects.count(), 12)
        self.assertGreaterEqual(Party.objects.count(), 60)
        self.assertGreaterEqual(Collection.objects.count(), 48)
        self.assertGreaterEqual(Sales.objects.count(), 48)
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
        counts_after_first_run = {
            "tenants": Tenant.objects.count(),
            "parties": Party.objects.count(),
            "collections": Collection.objects.count(),
            "sales": Sales.objects.count(),
        }
        call_command("seed_mock_data")

        self.assertEqual(
            counts_after_first_run,
            {
                "tenants": Tenant.objects.count(),
                "parties": Party.objects.count(),
                "collections": Collection.objects.count(),
                "sales": Sales.objects.count(),
            },
        )


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
        
        tenant_admin_group = Group.objects.create(name=f"{tenant.slug}:Tenant Admin")
        tenant_admin_group.permissions.add(view_sales)

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

        sales_response = self.client.get(reverse("homeApp:my_sales_list"), follow=True)
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
        tenant_admin_group = Group.objects.create(name=f"{tenant.slug}:Tenant Admin")
        tenant_admin_group.permissions.set(permissions)
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

        response = self.client.get(reverse("homeApp:my_collection"), follow=True)

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
        self.assertEqual(sale.invoiceNumber, "A/1/2026")
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


class StaffGroupMembershipSyncTests(TestCase):
    def test_sync_does_not_clear_unrelated_groups(self):
        tenant = Tenant.objects.create(name="Sync Tenant", slug="sync-tenant")
        user = User.objects.create_user(username="sync-user", password="secret123")

        customer_group = Group.objects.create(name="Customer")
        other_group = Group.objects.create(name="Unrelated")
        user.groups.add(customer_group, other_group)

        g1 = Group.objects.create(name=f"{tenant.slug}:Old Staff Group")
        g2 = Group.objects.create(name=f"{tenant.slug}:New Staff Group")
        StaffGroup.objects.create(tenant=tenant, name="Old Staff Group", djangoGroup=g1)
        sg2 = StaffGroup.objects.create(tenant=tenant, name="New Staff Group", djangoGroup=g2)

        staff_user = StaffUser.objects.create(
            tenant=tenant,
            user_ID=user,
            name="Sync User",
            phone="9999999988",
            staffGroup=sg2,
            group=sg2.name,
            isActive="Active",
        )

        user.groups.add(g1)

        _sync_staff_user_group_membership(staff_user)

        self.assertTrue(user.groups.filter(pk=customer_group.pk).exists())
        self.assertTrue(user.groups.filter(pk=other_group.pk).exists())
        self.assertFalse(user.groups.filter(pk=g1.pk).exists())
        self.assertTrue(user.groups.filter(pk=g2.pk).exists())


class CollectionOtpForStaffTests(TestCase):
    @override_settings(
        CACHES={
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        }
    )
    def test_staff_can_request_and_use_collection_otp(self):
        tenant = Tenant.objects.create(name="OTP Tenant", slug="otp-tenant")
        user = User.objects.create_user(username="otp-staff", password="secret123")
        add_collection_perm = Permission.objects.get(codename="add_collection", content_type__app_label="home")
        group = Group.objects.create(name=f"{tenant.slug}:Collector")
        group.permissions.add(add_collection_perm)
        staff_group = StaffGroup.objects.create(tenant=tenant, name="Collector", djangoGroup=group)
        TenantMembership.objects.create(
            tenant=tenant,
            user=user,
            role=TenantMembership.Role.STAFF,
            isDefault=True,
        )
        staff_user = StaffUser.objects.create(
            tenant=tenant,
            user_ID=user,
            name="OTP Staff",
            phone="9999999977",
            staffGroup=staff_group,
            group="Collector",
            isActive="Active",
        )
        user.groups.add(group)

        party = Party.objects.create(tenant=tenant, name="P1", phone="9999999990", openingBalance="0")

        self.client.force_login(user)
        resp = self.client.post(
            reverse("homeApp:request_collection_otp_api"),
            data={
                "party": str(party.pk),
                "paymentMode": "Cash",
                "amountPaid": "100",
                "collectDate": "19/03/2026",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("message"), "success")

        payload = get_collection_otp_payload(party.tenant_id, party.pk)
        self.assertIsNotNone(payload)
        otp = payload.get("otp")
        self.assertTrue(isinstance(otp, str) and len(otp) == 6)

        verify = self.client.post(reverse("homeApp:verify_collection_otp_api"), data={"party": str(party.pk), "otp": otp})
        self.assertEqual(verify.status_code, 200)
        self.assertEqual(verify.json().get("message"), "success")

        add_resp = self.client.post(
            reverse("homeApp:add_collection_by_staff_api"),
            data={
                "party": str(party.pk),
                "paymentMode": "Cash",
                "amountPaid": "100",
                "bank": "",
                "detail": "",
                "remark": "",
                "lat": "0.0",
                "lng": "0.0",
                "chequeDate": "",
                "collectDate": "19/03/2026",
                "TransferredParty": "",
                "otpRequired": "1",
                "otpCode": otp,
            },
        )
        self.assertEqual(add_resp.status_code, 200)
        self.assertEqual(add_resp.json().get("message"), "success")

        collection = Collection.objects.filter(tenant=tenant, partyID=party, collectedBy=staff_user).order_by("-id").first()
        self.assertIsNotNone(collection)
        self.assertTrue(collection.otpVerified)
        self.assertIsNotNone(collection.otpVerifiedOn)


@override_settings(
    CACHES={
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    }
)
class CollectionOtpEndToEndTests(TestCase):
    def test_end_to_end_staff_generates_customer_sees_and_staff_submits(self):
        tenant = Tenant.objects.create(name="E2E Tenant", slug="e2e-tenant")

        staff_auth = User.objects.create_user(username="e2e-staff", password="secret123")
        add_collection_perm = Permission.objects.get(codename="add_collection", content_type__app_label="home")
        view_party_perm = Permission.objects.get(codename="view_party", content_type__app_label="home")
        tenant_admin_group = Group.objects.create(name=f"{tenant.slug}:Tenant Admin")
        tenant_admin_group.permissions.add(add_collection_perm, view_party_perm)
        collector_group = Group.objects.create(name=f"{tenant.slug}:Collector")
        collector_group.permissions.add(add_collection_perm)
        staff_group = StaffGroup.objects.create(tenant=tenant, name="Collector", djangoGroup=collector_group)
        TenantMembership.objects.create(
            tenant=tenant,
            user=staff_auth,
            role=TenantMembership.Role.STAFF,
            isDefault=True,
        )
        staff_profile = StaffUser.objects.create(
            tenant=tenant,
            user_ID=staff_auth,
            name="E2E Staff",
            phone="9999999966",
            staffGroup=staff_group,
            group="Collector",
            isActive="Active",
        )
        staff_auth.groups.add(collector_group)

        party = Party.objects.create(tenant=tenant, name="P1", phone="9999999990", openingBalance="0")

        customer_auth = User.objects.create_user(username="e2e-customer", password="cust123")
        view_party_perm = Permission.objects.get(codename="view_party", content_type__app_label="home")
        customer_auth.user_permissions.add(view_party_perm)
        TenantMembership.objects.create(
            tenant=tenant,
            user=customer_auth,
            role=TenantMembership.Role.PARTY,
            isDefault=True,
        )
        party.userID_id = customer_auth.pk
        party.save(update_fields=["userID", "lastUpdatedOn"])

        staff_client = Client()
        staff_client.force_login(staff_auth)
        staff_client.get(reverse("homeApp:add_collection"))

        resp = staff_client.post(
            reverse("homeApp:request_collection_otp_api"),
            data={
                "party": str(party.pk),
                "paymentMode": "Cash",
                "amountPaid": "100",
                "collectDate": "19/03/2026",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("message"), "success")

        payload = get_collection_otp_payload(party.tenant_id, party.pk)
        otp = payload.get("otp")
        self.assertTrue(isinstance(otp, str) and len(otp) == 6)

        customer_client = Client()
        customer_client.force_login(customer_auth)
        customer_client.get(reverse("homeApp:customer_home"))
        otp_view = customer_client.get(reverse("homeApp:customer_active_collection_otp"))
        self.assertEqual(otp_view.status_code, 200)
        otp_payload = otp_view.json()
        self.assertEqual(otp_payload.get("message"), "success")
        self.assertTrue(otp_payload.get("active"))
        self.assertEqual(otp_payload.get("otp"), otp)

        verify = staff_client.post(reverse("homeApp:verify_collection_otp_api"), data={"party": str(party.pk), "otp": otp})
        self.assertEqual(verify.status_code, 200)
        self.assertEqual(verify.json().get("message"), "success")

        add_resp = staff_client.post(
            reverse("homeApp:add_collection_by_staff_api"),
            data={
                "party": str(party.pk),
                "paymentMode": "Cash",
                "amountPaid": "100",
                "bank": "",
                "detail": "",
                "remark": "",
                "lat": "0.0",
                "lng": "0.0",
                "chequeDate": "",
                "collectDate": "19/03/2026",
                "TransferredParty": "",
                "otpRequired": "1",
                "otpCode": otp,
            },
        )
        self.assertEqual(add_resp.status_code, 200)
        self.assertEqual(add_resp.json().get("message"), "success")

        collection = Collection.objects.filter(tenant=tenant, partyID=party, collectedBy=staff_profile).order_by("-id").first()
        self.assertIsNotNone(collection)
        self.assertTrue(collection.otpVerified)


class WhatsappMessageSafetyTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="WhatsApp Tenant", slug="whatsapp-tenant")
        self.user = User.objects.create_user(username="wa-user", password="secret123")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=TenantMembership.Role.STAFF,
            isDefault=True,
        )
        StaffUser.objects.create(
            tenant=self.tenant,
            user_ID=self.user,
            name="WA Staff",
            phone="9999999999",
            isActive="Active",
        )
        self.client.force_login(self.user)

    def test_send_whatsapp_message_without_package_does_not_raise(self):
        send_whatsapp_message("9876543210", "hello")

    def test_resend_message_sales_without_package_does_not_crash(self):
        status = WhatsappMessageStatus.objects.create(
            tenant=self.tenant,
            phone="9876543210",
            message="hello",
            messageTo="Customer",
            status="Fail",
        )

        response = self.client.post(
            reverse("homeApp:re_send_message_sales"),
            data={"userID": str(status.pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "success")
