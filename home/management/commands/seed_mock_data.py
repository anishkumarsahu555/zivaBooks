from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from activation.models import EcomValidity, Validity
from home.apiView import seed_default_groups
from home.models import (
    Attendance,
    Bank,
    CashCounter,
    Collection,
    CollectionCashCounter,
    GeolocationPackage,
    Party,
    PartyGroup,
    Sales,
    StaffGroup,
    StaffUser,
    Tenant,
    TenantMembership,
    WhatsappMessage,
    WhatsappMessageStatus,
)


User = get_user_model()
DEFAULT_STAFF_GROUPS = [
    "Owner",
    "Admin",
    "Moderator",
    "Collection",
    "SalesAdmin",
    "CashCounter",
    "NormalStaff",
    "Viewer",
]


class Command(BaseCommand):
    help = "Seed mock SaaS data for two tenants."

    @transaction.atomic
    def handle(self, *args, **options):
        seed_default_groups()
        seeded = [
            self._seed_tenant(
                tenant_name="Alpha Traders",
                owner_username="alpha.owner",
                owner_email="owner@alpha.example.com",
                owner_password="alpha123",
                station_name="Retail Alpha",
                bank_name="Alpha Bank",
                bank_account="ALPHA-001",
                package_name="Alpha Geo Pack",
                whatsapp_name="Alpha Whatsapp",
            ),
            self._seed_tenant(
                tenant_name="Beta Distributors",
                owner_username="beta.owner",
                owner_email="owner@beta.example.com",
                owner_password="beta123",
                station_name="Wholesale Beta",
                bank_name="Beta Bank",
                bank_account="BETA-001",
                package_name="Beta Geo Pack",
                whatsapp_name="Beta Whatsapp",
            ),
        ]
        self.stdout.write(self.style.SUCCESS(f"Seeded mock data for {len(seeded)} tenants."))

    def _seed_tenant(
        self,
        *,
        tenant_name,
        owner_username,
        owner_email,
        owner_password,
        station_name,
        bank_name,
        bank_account,
        package_name,
        whatsapp_name,
    ):
        tenant, _ = Tenant.objects.get_or_create(
            slug=slugify(tenant_name),
            defaults={"name": tenant_name, "isActive": True},
        )
        if tenant.name != tenant_name:
            tenant.name = tenant_name
            tenant.isActive = True
            tenant.save(update_fields=["name", "isActive", "lastUpdatedOn"])

        staff_groups = {
            group_name: self._ensure_staff_group(tenant, group_name)
            for group_name in DEFAULT_STAFF_GROUPS
        }

        owner, created = User.objects.get_or_create(
            username=owner_username,
            defaults={"email": owner_email, "is_active": True},
        )
        if created or not owner.check_password(owner_password):
            owner.set_password(owner_password)
            owner.email = owner_email
            owner.is_active = True
            owner.save()
        self._grant_all_permissions(owner)

        TenantMembership.objects.update_or_create(
            tenant=tenant,
            user=owner,
            defaults={"role": TenantMembership.Role.OWNER, "isDefault": True},
        )

        party_group, _ = PartyGroup.objects.get_or_create(
            tenant=tenant,
            name=station_name,
            defaults={"description": f"Primary station for {tenant_name}", "isDeleted": False},
        )
        bank, _ = Bank.objects.get_or_create(
            tenant=tenant,
            accountNumber=bank_account,
            defaults={"name": bank_name, "description": f"Settlement bank for {tenant_name}", "isDeleted": False},
        )
        location_pack, _ = GeolocationPackage.objects.get_or_create(
            tenant=tenant,
            name=package_name,
            defaults={"balance": 500, "used": 25, "description": "Mock geolocation credits", "isDeleted": False},
        )
        whatsapp_pack, _ = WhatsappMessage.objects.get_or_create(
            tenant=tenant,
            name=whatsapp_name,
            defaults={
                "instanceID": f"{slugify(tenant_name)}-instance",
                "apiKey": f"{slugify(tenant_name)}-key",
                "balance": 1000,
                "used": 32,
                "isDeleted": False,
            },
        )

        staff_user = self._ensure_staff_user(
            tenant=tenant,
            user=owner,
            role_name="Owner",
            membership_role=TenantMembership.Role.OWNER,
            name=f"{tenant_name} Owner",
            phone="9000000001",
            email=owner_email,
            address=f"HQ - {tenant_name}",
            party_group=party_group,
            staff_group=staff_groups["Owner"],
        )

        admin_user = self._ensure_auth_user(
            username=f"{slugify(tenant_name).replace('-', '')}.admin",
            email=f"admin@{slugify(tenant_name)}.example.com",
            password="admin123",
        )
        admin_staff = self._ensure_staff_user(
            tenant=tenant,
            user=admin_user,
            role_name="Admin",
            membership_role=TenantMembership.Role.ADMIN,
            name=f"{tenant_name} Admin",
            phone="9000000009",
            email=admin_user.email,
            address=f"Admin Office - {tenant_name}",
            party_group=party_group,
            staff_group=staff_groups["Admin"],
        )

        collector_user = self._ensure_auth_user(
            username=f"{slugify(tenant_name).replace('-', '')}.collector",
            email=f"collector@{slugify(tenant_name)}.example.com",
            password="collect123",
        )
        collector = self._ensure_staff_user(
            tenant=tenant,
            user=collector_user,
            role_name="Collection",
            membership_role=TenantMembership.Role.STAFF,
            name=f"{tenant_name} Collector",
            phone="9000000002",
            email=collector_user.email,
            address=f"Field Office - {tenant_name}",
            party_group=party_group,
            staff_group=staff_groups["Collection"],
        )

        sales_user = self._ensure_auth_user(
            username=f"{slugify(tenant_name).replace('-', '')}.sales",
            email=f"sales@{slugify(tenant_name)}.example.com",
            password="sales123",
        )
        sales_staff = self._ensure_staff_user(
            tenant=tenant,
            user=sales_user,
            role_name="SalesAdmin",
            membership_role=TenantMembership.Role.STAFF,
            name=f"{tenant_name} Sales",
            phone="9000000003",
            email=sales_user.email,
            address=f"Sales Desk - {tenant_name}",
            party_group=party_group,
            staff_group=staff_groups["SalesAdmin"],
        )

        cash_user = self._ensure_auth_user(
            username=f"{slugify(tenant_name).replace('-', '')}.cash",
            email=f"cash@{slugify(tenant_name)}.example.com",
            password="cash123",
        )
        cash_staff = self._ensure_staff_user(
            tenant=tenant,
            user=cash_user,
            role_name="CashCounter",
            membership_role=TenantMembership.Role.STAFF,
            name=f"{tenant_name} Cash Counter",
            phone="9000000004",
            email=cash_user.email,
            address=f"Cash Desk - {tenant_name}",
            party_group=party_group,
            staff_group=staff_groups["CashCounter"],
        )

        party_login = self._ensure_auth_user(
            username=f"{slugify(tenant_name).replace('-', '')}.customer",
            email=f"customer@{slugify(tenant_name)}.example.com",
            password="customer123",
        )
        TenantMembership.objects.update_or_create(
            tenant=tenant,
            user=party_login,
            defaults={"role": TenantMembership.Role.PARTY, "isDefault": False},
        )
        party, _ = Party.objects.get_or_create(
            tenant=tenant,
            userID=party_login,
            defaults={
                "name": f"{tenant_name} Main Customer",
                "phone": "9876543210",
                "address": f"Market Area - {tenant_name}",
                "openingBalance": Decimal("5000.00"),
                "username": party_login.username,
                "password": "customer123",
                "partyGroupID": party_group,
                "assignTo": collector,
                "isDeleted": False,
            },
        )

        today = date.today()
        collection, _ = Collection.objects.get_or_create(
            tenant=tenant,
            paymentID=f"{tenant.id:02d}000001",
            defaults={
                "partyID": party,
                "modeOfPayment": "Cash",
                "paidAmount": float(Decimal("1250.00")),
                "bankID": bank,
                "detail": "Mock collection",
                "remark": "Collected during seeding",
                "collectionAddress": "Mock field visit",
                "collectedBy": collector,
                "approvedOn": datetime.now(),
                "isApproved": True,
                "isTallied": True,
                "approvedBy": staff_user,
                "collectionDateTime": datetime.now(),
                "isDeleted": False,
            },
        )
        sales, _ = Sales.objects.get_or_create(
            tenant=tenant,
            paymentID=f"S{tenant.id:02d}000001",
            defaults={
                "partyID": party,
                "invoiceNumber": f"INV/1/{today.year}",
                "amount": float(Decimal("3500.00")),
                "remark": "Mock sale",
                "createdBy": sales_staff,
                "buildDate": today,
                "approvedOn": datetime.now(),
                "isApproved": True,
                "approvedBy": admin_staff,
                "isDeleted": False,
            },
        )
        cash_counter, _ = CashCounter.objects.get_or_create(
            tenant=tenant,
            counterID=f"CC{tenant.id:02d}000001",
            defaults={
                "partyID": party,
                "invoiceNumber": f"CC/1/{today.year}",
                "mode": "Cash",
                "amount": float(Decimal("1800.00")),
                "remark": "Mock counter entry",
                "createdBy": cash_staff,
                "entryDate": datetime.now(),
                "isDeleted": False,
            },
        )
        CollectionCashCounter.objects.get_or_create(
            tenant=tenant,
            paymentID=f"CCC{tenant.id:02d}000001",
            defaults={
                "partyID": party,
                "modeOfPayment": "Cash",
                "paidAmount": float(Decimal("950.00")),
                "bankID": bank,
                "detail": "Mock counter collection",
                "remark": "Seeded counter collection",
                "collectionAddress": "Front desk",
                "collectedBy": collector,
                "approvedOn": datetime.now(),
                "isApproved": True,
                "isTallied": True,
                "approvedBy": admin_staff,
                "collectionDateTime": datetime.now(),
                "isDeleted": False,
            },
        )
        Attendance.objects.get_or_create(
            tenant=tenant,
            staffID=collector,
            datetime__date=today,
            defaults={
                "registerType": "Daily",
                "isLogIn": True,
                "isLogOut": True,
                "loginDateTime": datetime.now() - timedelta(hours=8),
                "logoutDateTime": datetime.now(),
                "login_location": "Seed login location",
                "logout_location": "Seed logout location",
                "login_remark": "On time",
                "logout_remark": "Shift completed",
                "isDeleted": False,
            },
        )
        WhatsappMessageStatus.objects.get_or_create(
            tenant=tenant,
            phone=party.phone,
            messageTo=party.name,
            status="Success",
            defaults={
                "message": f"Seeded notification for {tenant_name}",
                "isDeleted": False,
            },
        )
        Validity.objects.get_or_create(
            tenant=tenant,
            activationType="SaaS",
            defaults={
                "activationDate": today,
                "expiryDate": today + timedelta(days=365),
            },
        )
        EcomValidity.objects.get_or_create(
            tenant=tenant,
            activationType="Ecom SaaS",
            defaults={
                "activationDate": today,
                "expiryDate": today + timedelta(days=365),
            },
        )

        return {
            "tenant": tenant,
            "owner": owner,
            "staff_groups": staff_groups,
            "party_group": party_group,
            "bank": bank,
            "location_pack": location_pack,
            "whatsapp_pack": whatsapp_pack,
            "staff_user": staff_user,
            "admin_staff": admin_staff,
            "collector": collector,
            "sales_staff": sales_staff,
            "cash_staff": cash_staff,
            "party": party,
            "collection": collection,
            "sales": sales,
            "cash_counter": cash_counter,
        }

    def _ensure_auth_user(self, *, username, email, password):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": email, "is_active": True},
        )
        if created or not user.check_password(password) or user.email != email or not user.is_active:
            user.set_password(password)
            user.email = email
            user.is_active = True
            user.save()
        return user

    def _ensure_staff_group(self, tenant, group_name):
        from django.contrib.auth.models import Group

        base_group = Group.objects.filter(name=group_name).first()
        tenant_group_name = f"{tenant.slug}:{group_name}"
        django_group, _ = Group.objects.get_or_create(name=tenant_group_name)
        if base_group is not None:
            django_group.permissions.set(base_group.permissions.all())
        staff_group, created = StaffGroup.objects.get_or_create(
            tenant=tenant,
            name=group_name,
            defaults={
                "description": f"{group_name} role for {tenant.name}",
                "djangoGroup": django_group,
                "isDeleted": False,
            },
        )
        update_fields = []
        if staff_group.isDeleted:
            staff_group.isDeleted = False
            update_fields.append("isDeleted")
        if staff_group.djangoGroup_id != getattr(django_group, "id", None):
            staff_group.djangoGroup = django_group
            update_fields.append("djangoGroup")
        if not staff_group.description:
            staff_group.description = f"{group_name} role for {tenant.name}"
            update_fields.append("description")
        if update_fields:
            update_fields.append("lastUpdatedOn")
            staff_group.save(update_fields=update_fields)
        return staff_group

    def _ensure_staff_user(
        self,
        *,
        tenant,
        user,
        role_name,
        membership_role,
        name,
        phone,
        email,
        address,
        party_group,
        staff_group,
    ):
        TenantMembership.objects.update_or_create(
            tenant=tenant,
            user=user,
            defaults={"role": membership_role, "isDefault": membership_role == TenantMembership.Role.OWNER},
        )
        staff_user, created = StaffUser.objects.get_or_create(
            tenant=tenant,
            user_ID=user,
            defaults={
                "name": name,
                "phone": phone,
                "email": email,
                "address": address,
                "username": user.username,
                "group": role_name,
                "staffGroup": staff_group,
                "partyGroupID": party_group,
                "isActive": "Active",
                "isDeleted": False,
            },
        )
        update_fields = []
        desired_values = {
            "name": name,
            "phone": phone,
            "email": email,
            "address": address,
            "username": user.username,
            "group": role_name,
            "staffGroup": staff_group,
            "partyGroupID": party_group,
            "isActive": "Active",
            "isDeleted": False,
        }
        for field_name, expected_value in desired_values.items():
            current_value = getattr(staff_user, field_name)
            if current_value != expected_value:
                setattr(staff_user, field_name, expected_value)
                update_fields.append(field_name)
        if created:
            user.groups.clear()
        linked_group = staff_group.djangoGroup
        if linked_group is not None:
            user.groups.set([linked_group])
        if update_fields:
            update_fields.append("lastUpdatedOn")
            staff_user.save(update_fields=update_fields)
        return staff_user

    def _grant_all_permissions(self, user):
        all_permissions = Permission.objects.exclude(
            content_type__app_label__in=["admin", "contenttypes", "sessions"]
        )
        user.user_permissions.set(all_permissions)
