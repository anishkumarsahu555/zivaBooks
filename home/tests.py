from io import StringIO

from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from home.models import AuditLog, Collection, Party, StaffGroup, StaffUser, Tenant, TenantMembership


User = get_user_model()


class SeedMockDataCommandTests(TestCase):
    def test_seed_mock_data_creates_two_tenants_with_data(self):
        stdout = StringIO()

        call_command("seed_mock_data", stdout=stdout)

        self.assertEqual(Tenant.objects.count(), 2)
        self.assertGreaterEqual(TenantMembership.objects.count(), 4)
        self.assertEqual(Party.objects.count(), 2)
        self.assertEqual(Collection.objects.count(), 2)
        self.assertGreaterEqual(StaffGroup.objects.count(), 16)
        owner = User.objects.get(username="alpha.owner")
        collector = User.objects.get(username="alphatraders.collector")
        owner_staff = StaffUser.objects.get(user_ID=owner)
        collector_staff = StaffUser.objects.get(user_ID=collector)
        self.assertEqual(owner_staff.staffGroup.name, "Owner")
        self.assertEqual(collector_staff.staffGroup.name, "Collection")
        self.assertTrue(owner.groups.filter(name="alpha-traders:Owner").exists())
        self.assertTrue(collector.groups.filter(name="alpha-traders:Collection").exists())
        self.assertTrue(owner.has_perm("home.can_manage_staff"))
        self.assertTrue(owner.has_perm("home.can_manage_staff_groups"))

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
