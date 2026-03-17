from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from activation.models import EcomValidity, Validity
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


class Command(BaseCommand):
    help = "Seed mock SaaS data for two tenants."

    @transaction.atomic
    def handle(self, *args, **options):
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

        owner, created = User.objects.get_or_create(
            username=owner_username,
            defaults={"email": owner_email, "is_active": True},
        )
        if created or not owner.check_password(owner_password):
            owner.set_password(owner_password)
            owner.email = owner_email
            owner.is_active = True
            owner.save()

        for group_name in ["Admin", "Collection", "SalesAdmin", "CashCounter", "Customer"]:
            group, _ = Group.objects.get_or_create(name=group_name)
            if group_name == "Admin":
                group.user_set.add(owner)

        TenantMembership.objects.update_or_create(
            tenant=tenant,
            user=owner,
            defaults={"role": TenantMembership.Role.OWNER, "isDefault": True},
        )

        staff_group, _ = StaffGroup.objects.get_or_create(
            tenant=tenant,
            name="Operations",
            defaults={"isDeleted": False},
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

        staff_user, _ = StaffUser.objects.get_or_create(
            tenant=tenant,
            user_ID=owner,
            defaults={
                "name": f"{tenant_name} Owner",
                "phone": "9000000001",
                "email": owner_email,
                "address": f"HQ - {tenant_name}",
                "username": owner_username,
                "group": "Admin",
                "partyGroupID": party_group,
                "isActive": "Active",
                "isDeleted": False,
            },
        )

        collector_user, _ = User.objects.get_or_create(
            username=f"{slugify(tenant_name).replace('-', '')}.collector",
            defaults={"email": f"collector@{slugify(tenant_name)}.example.com", "is_active": True},
        )
        collector_user.set_password("collect123")
        collector_user.save()
        Group.objects.get(name="Collection").user_set.add(collector_user)
        TenantMembership.objects.update_or_create(
            tenant=tenant,
            user=collector_user,
            defaults={"role": TenantMembership.Role.STAFF, "isDefault": False},
        )
        collector, _ = StaffUser.objects.get_or_create(
            tenant=tenant,
            user_ID=collector_user,
            defaults={
                "name": f"{tenant_name} Collector",
                "phone": "9000000002",
                "email": f"collector@{slugify(tenant_name)}.example.com",
                "address": f"Field Office - {tenant_name}",
                "username": collector_user.username,
                "group": "Collection",
                "partyGroupID": party_group,
                "isActive": "Active",
                "isDeleted": False,
            },
        )

        party_login, _ = User.objects.get_or_create(
            username=f"{slugify(tenant_name).replace('-', '')}.customer",
            defaults={"email": f"customer@{slugify(tenant_name)}.example.com", "is_active": True},
        )
        party_login.set_password("customer123")
        party_login.save()
        Group.objects.get(name="Customer").user_set.add(party_login)
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
                "createdBy": staff_user,
                "buildDate": today,
                "approvedOn": datetime.now(),
                "isApproved": True,
                "approvedBy": staff_user,
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
                "createdBy": staff_user,
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
                "approvedBy": staff_user,
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
            "staff_group": staff_group,
            "party_group": party_group,
            "bank": bank,
            "location_pack": location_pack,
            "whatsapp_pack": whatsapp_pack,
            "staff_user": staff_user,
            "collector": collector,
            "party": party,
            "collection": collection,
            "sales": sales,
            "cash_counter": cash_counter,
        }
