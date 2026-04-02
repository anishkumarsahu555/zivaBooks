from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from activation.models import EcomValidity, Validity
from home.models import (
    Attendance,
    Bank,
    BookAccount,
    CashCounter,
    CashCounterLine,
    Collection,
    CollectionCashCounter,
    GeolocationPackage,
    InventoryCategory,
    InventoryItem,
    InventoryTaxProfile,
    InventoryUnit,
    InventoryWarehouse,
    InvoiceSeries,
    Order,
    Party,
    PartyGroup,
    PurchaseInvoice,
    PurchaseLine,
    Sales,
    SalesLine,
    StaffGroup,
    StaffUser,
    Tenant,
    TenantMembership,
    WhatsappMessage,
    WhatsappMessageStatus,
)


User = get_user_model()

DEFAULT_STAFF_GROUPS = {
    "Tenant Control": "__all__",
    "Operations Admin": [
        "home.can_manage_staff",
        "home.can_manage_staff_groups",
        "home.can_manage_parties",
        "home.can_view_party_credentials",
        "home.can_approve_collection",
        "home.can_view_audit_logs",
        "home.view_collection",
        "home.view_sales",
        "home.view_cashcounter",
        "home.view_attendance",
    ],
    "Field Collections": [
        "home.add_collection",
        "home.change_collection",
        "home.view_collection",
        "home.view_party",
        "home.view_attendance",
    ],
    "Sales Desk": [
        "home.add_sales",
        "home.change_sales",
        "home.view_sales",
        "home.can_manage_sales",
        "home.can_view_sales_reports",
        "home.view_party",
    ],
    "Counter Desk": [
        "home.add_cashcounter",
        "home.change_cashcounter",
        "home.view_cashcounter",
        "home.add_collectioncashcounter",
        "home.change_collectioncashcounter",
        "home.view_collectioncashcounter",
        "home.can_manage_cash_counter",
        "home.can_view_cash_counter_reports",
    ],
    "Backoffice Audit": [
        "home.view_attendance",
        "home.can_view_collection_reports",
        "home.can_view_sales_reports",
        "home.can_view_cash_counter_reports",
        "home.can_view_audit_logs",
    ],
}

SEEDED_PASSWORDS = {
    "admin": "admin123",
    "collector": "collect123",
    "sales": "sales123",
    "cash": "cash123",
    "audit": "audit123",
    "customer": "customer123",
}

PAYMENT_MODES = [Sales.Mode.CASH, Sales.Mode.CREDIT, Sales.Mode.ONLINE, Sales.Mode.MIX]


class Command(BaseCommand):
    help = "Seed mock SaaS data for two tenants with 200+ deterministic records."

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
        self._print_seeded_credentials(seeded)
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
        if tenant.name != tenant_name or not tenant.isActive:
            tenant.name = tenant_name
            tenant.isActive = True
            tenant.save(update_fields=["name", "isActive", "lastUpdatedOn"])

        staff_groups = {
            group_name: self._ensure_staff_group(tenant, group_name)
            for group_name in DEFAULT_STAFF_GROUPS
        }

        owner = self._ensure_auth_user(
            username=owner_username,
            email=owner_email,
            password=owner_password,
        )
        self._grant_all_permissions(owner)

        owner_staff = self._ensure_staff_user(
            tenant=tenant,
            user=owner,
            role_name="Tenant Control",
            membership_role=TenantMembership.Role.OWNER,
            name=f"{tenant_name} Owner",
            phone="9000000001",
            email=owner_email,
            address=f"HQ - {tenant_name}",
            party_group=self._ensure_party_group(tenant, station_name, f"Primary station for {tenant_name}"),
            staff_group=staff_groups["Tenant Control"],
        )

        admin_user = self._ensure_auth_user(
            username=f"{tenant.slug.replace('-', '')}.admin",
            email=f"admin@{tenant.slug}.example.com",
            password=SEEDED_PASSWORDS["admin"],
        )
        collector_user = self._ensure_auth_user(
            username=f"{tenant.slug.replace('-', '')}.collector",
            email=f"collector@{tenant.slug}.example.com",
            password=SEEDED_PASSWORDS["collector"],
        )
        sales_user = self._ensure_auth_user(
            username=f"{tenant.slug.replace('-', '')}.sales",
            email=f"sales@{tenant.slug}.example.com",
            password=SEEDED_PASSWORDS["sales"],
        )
        cash_user = self._ensure_auth_user(
            username=f"{tenant.slug.replace('-', '')}.cash",
            email=f"cash@{tenant.slug}.example.com",
            password=SEEDED_PASSWORDS["cash"],
        )
        audit_user = self._ensure_auth_user(
            username=f"{tenant.slug.replace('-', '')}.audit",
            email=f"audit@{tenant.slug}.example.com",
            password=SEEDED_PASSWORDS["audit"],
        )
        party_login = self._ensure_auth_user(
            username=f"{tenant.slug.replace('-', '')}.customer",
            email=f"customer@{tenant.slug}.example.com",
            password=SEEDED_PASSWORDS["customer"],
        )

        retail_group = self._ensure_party_group(tenant, station_name, f"Primary station for {tenant_name}")
        wholesale_group = self._ensure_party_group(tenant, f"{tenant_name} Wholesale", "Wholesale accounts")
        online_group = self._ensure_party_group(tenant, f"{tenant_name} Online", "Online accounts")
        party_groups = [retail_group, wholesale_group, online_group]

        admin_staff = self._ensure_staff_user(
            tenant=tenant,
            user=admin_user,
            role_name="Operations Admin",
            membership_role=TenantMembership.Role.ADMIN,
            name=f"{tenant_name} Admin",
            phone="9000000009",
            email=admin_user.email,
            address=f"Admin Office - {tenant_name}",
            party_group=retail_group,
            staff_group=staff_groups["Operations Admin"],
        )
        collector = self._ensure_staff_user(
            tenant=tenant,
            user=collector_user,
            role_name="Field Collections",
            membership_role=TenantMembership.Role.STAFF,
            name=f"{tenant_name} Collector",
            phone="9000000002",
            email=collector_user.email,
            address=f"Field Office - {tenant_name}",
            party_group=retail_group,
            staff_group=staff_groups["Field Collections"],
        )
        sales_staff = self._ensure_staff_user(
            tenant=tenant,
            user=sales_user,
            role_name="Sales Desk",
            membership_role=TenantMembership.Role.STAFF,
            name=f"{tenant_name} Sales",
            phone="9000000003",
            email=sales_user.email,
            address=f"Sales Desk - {tenant_name}",
            party_group=retail_group,
            staff_group=staff_groups["Sales Desk"],
        )
        cash_staff = self._ensure_staff_user(
            tenant=tenant,
            user=cash_user,
            role_name="Counter Desk",
            membership_role=TenantMembership.Role.STAFF,
            name=f"{tenant_name} Cash Counter",
            phone="9000000004",
            email=cash_user.email,
            address=f"Cash Desk - {tenant_name}",
            party_group=retail_group,
            staff_group=staff_groups["Counter Desk"],
        )
        audit_staff = self._ensure_staff_user(
            tenant=tenant,
            user=audit_user,
            role_name="Backoffice Audit",
            membership_role=TenantMembership.Role.STAFF,
            name=f"{tenant_name} Audit",
            phone="9000000005",
            email=audit_user.email,
            address=f"Audit Desk - {tenant_name}",
            party_group=retail_group,
            staff_group=staff_groups["Backoffice Audit"],
        )

        primary_bank = self._ensure_bank(
            tenant=tenant,
            account_number=bank_account,
            name=bank_name,
            description=f"Settlement bank for {tenant_name}",
        )
        secondary_bank = self._ensure_bank(
            tenant=tenant,
            account_number=f"{tenant.slug.upper()}-002",
            name=f"{tenant_name} Reserve Bank",
            description=f"Reserve account for {tenant_name}",
        )
        banks = [primary_bank, secondary_bank]

        location_pack, _ = GeolocationPackage.objects.get_or_create(
            tenant=tenant,
            name=package_name,
            defaults={"balance": 500, "used": 25, "description": "Mock geolocation credits", "isDeleted": False},
        )
        whatsapp_pack, _ = WhatsappMessage.objects.get_or_create(
            tenant=tenant,
            name=whatsapp_name,
            defaults={
                "instanceID": f"{tenant.slug}-instance",
                "apiKey": f"{tenant.slug}-key",
                "balance": 1000,
                "used": 32,
                "isDeleted": False,
            },
        )

        self._ensure_book_accounts(tenant, banks)
        sales_series = self._ensure_invoice_series(tenant, InvoiceSeries.Module.SALES, "Sales Main", "SAL")
        cash_series = self._ensure_invoice_series(tenant, InvoiceSeries.Module.CASH_COUNTER, "Counter Main", "CTR")

        customer_party = self._ensure_party(
            tenant=tenant,
            name=f"{tenant_name} Main Customer",
            phone=self._phone_for(tenant, 1),
            address=f"Market Area - {tenant_name}",
            opening_balance=Decimal("5000.00"),
            party_group=retail_group,
            assignee=collector,
            user=party_login,
            username=party_login.username,
            password=SEEDED_PASSWORDS["customer"],
        )
        TenantMembership.objects.update_or_create(
            tenant=tenant,
            user=party_login,
            defaults={"role": TenantMembership.Role.PARTY, "isDefault": False},
        )

        parties = [customer_party]
        for idx in range(2, 31):
            parties.append(
                self._ensure_party(
                    tenant=tenant,
                    name=f"{tenant_name} Customer {idx:02d}",
                    phone=self._phone_for(tenant, idx),
                    address=f"Route {idx:02d}, {tenant_name}",
                    opening_balance=Decimal(1500 + idx * 125),
                    party_group=party_groups[(idx - 1) % len(party_groups)],
                    assignee=collector if idx % 2 else sales_staff,
                )
            )

        categories = [
            self._ensure_inventory_category(tenant, "Beverages", "BEV"),
            self._ensure_inventory_category(tenant, "Snacks", "SNK"),
            self._ensure_inventory_category(tenant, "Household", "HOU"),
        ]
        units = [
            self._ensure_inventory_unit(tenant, "Pieces", "pcs", False),
            self._ensure_inventory_unit(tenant, "Box", "box", False),
        ]
        tax_profiles = [
            self._ensure_inventory_tax_profile(tenant, "GST 5%", "GST5", 5.0),
            self._ensure_inventory_tax_profile(tenant, "GST 12%", "GST12", 12.0),
            self._ensure_inventory_tax_profile(tenant, "GST 18%", "GST18", 18.0),
        ]
        warehouses = [
            self._ensure_inventory_warehouse(tenant, "Main Store", "MAIN", True),
            self._ensure_inventory_warehouse(tenant, "Transit Store", "TRN", False),
        ]

        items = []
        for idx in range(1, 13):
            category = categories[(idx - 1) % len(categories)]
            unit = units[(idx - 1) % len(units)]
            tax = tax_profiles[(idx - 1) % len(tax_profiles)]
            items.append(
                self._ensure_inventory_item(
                    tenant=tenant,
                    idx=idx,
                    category=category,
                    unit=unit,
                    tax_profile=tax,
                )
            )

        today = date.today()
        for idx in range(1, 25):
            party = parties[(idx - 1) % len(parties)]
            tx_date = today - timedelta(days=idx)
            collected_at = self._dt_at(tx_date, 11 + (idx % 5), 10 + (idx % 45))
            paid_amount = round(900 + idx * 37.5, 2)
            bank = banks[idx % len(banks)]

            Collection.objects.update_or_create(
                tenant=tenant,
                paymentID=f"{tenant.id:02d}COL{idx:04d}",
                defaults={
                    "partyID": party,
                    "modeOfPayment": "Cash" if idx % 3 else "Online",
                    "paidAmount": paid_amount,
                    "bankID": bank,
                    "detail": f"Mock collection cycle {idx}",
                    "remark": f"Seeded field collection {idx}",
                    "collectionAddress": f"Beat {((idx - 1) % 6) + 1}",
                    "collectedBy": collector,
                    "approvedOn": collected_at + timedelta(hours=1),
                    "isApproved": True,
                    "isTallied": idx % 4 != 0,
                    "approvedBy": owner_staff,
                    "collectionDateTime": collected_at,
                    "isDeleted": False,
                },
            )

            sales_mode = PAYMENT_MODES[(idx - 1) % len(PAYMENT_MODES)]
            sales_amount = round(1800 + idx * 92.5, 2)
            mix_cash = round(sales_amount * 0.55, 2) if sales_mode == Sales.Mode.MIX else 0.0
            mix_online = round(sales_amount - mix_cash, 2) if sales_mode == Sales.Mode.MIX else 0.0
            sales_obj, _ = Sales.objects.update_or_create(
                tenant=tenant,
                paymentID=f"{tenant.id:02d}SAL{idx:04d}",
                defaults={
                    "partyID": party,
                    "invoiceSeries": sales_series,
                    "invoiceSequenceNumber": idx,
                    "invoiceNumber": f"{sales_series.seriesCode}/{today.year}/{idx:04d}",
                    "modeOfPayment": sales_mode,
                    "amount": sales_amount,
                    "mixCashAmount": float(mix_cash),
                    "mixOnlineAmount": float(mix_online),
                    "bankID": bank if sales_mode in [Sales.Mode.ONLINE, Sales.Mode.MIX] else None,
                    "remark": f"Seeded sale {idx}",
                    "createdBy": sales_staff,
                    "buildDate": tx_date,
                    "approvedOn": self._dt_at(tx_date, 18, 0),
                    "isApproved": True,
                    "approvedBy": admin_staff,
                    "isDeleted": False,
                },
            )
            self._ensure_sales_line(
                tenant=tenant,
                sales=sales_obj,
                item=items[(idx - 1) % len(items)],
                warehouse=warehouses[idx % len(warehouses)],
                quantity=float((idx % 5) + 1),
                unit_price=round(sales_amount / float((idx % 5) + 1), 2),
            )

            counter_amount = round(850 + idx * 61.25, 2)
            counter_mode = "Return" if idx % 10 == 0 else ("Card" if idx % 4 == 0 else "Cash")
            counter_obj, _ = CashCounter.objects.update_or_create(
                tenant=tenant,
                counterID=f"{tenant.id:02d}CTR{idx:04d}",
                defaults={
                    "partyID": party,
                    "invoiceSeries": cash_series,
                    "invoiceSequenceNumber": idx,
                    "invoiceNumber": f"{cash_series.seriesCode}/{today.year}/{idx:04d}",
                    "mode": counter_mode,
                    "amount": counter_amount,
                    "mixCashAmount": counter_amount if counter_mode == "Cash" else 0.0,
                    "mixCardAmount": counter_amount if counter_mode == "Card" else 0.0,
                    "bankID": bank if counter_mode == "Card" else None,
                    "remark": f"Seeded counter entry {idx}",
                    "createdBy": cash_staff,
                    "entryDate": self._dt_at(tx_date, 16, 15),
                    "isDeleted": False,
                },
            )
            self._ensure_cash_counter_line(
                tenant=tenant,
                cash_counter=counter_obj,
                item=items[idx % len(items)],
                warehouse=warehouses[(idx + 1) % len(warehouses)],
                quantity=float((idx % 4) + 1),
                unit_price=round(counter_amount / float((idx % 4) + 1), 2),
            )

            CollectionCashCounter.objects.update_or_create(
                tenant=tenant,
                paymentID=f"{tenant.id:02d}CCC{idx:04d}",
                defaults={
                    "partyID": party,
                    "modeOfPayment": "Cash" if idx % 2 else "Online",
                    "paidAmount": round(500 + idx * 32.75, 2),
                    "bankID": bank,
                    "detail": f"Counter collection cycle {idx}",
                    "remark": f"Seeded counter collection {idx}",
                    "collectionAddress": "Front desk",
                    "collectedBy": cash_staff,
                    "approvedOn": self._dt_at(tx_date, 17, 30),
                    "isApproved": True,
                    "isTallied": True,
                    "approvedBy": admin_staff,
                    "collectionDateTime": self._dt_at(tx_date, 17, 0),
                    "isDeleted": False,
                },
            )

        for idx in range(1, 19):
            party = parties[(idx + 2) % len(parties)]
            build_date = today - timedelta(days=idx * 2)
            status = [
                Order.Status.DRAFT,
                Order.Status.SUBMITTED,
                Order.Status.APPROVED,
                Order.Status.FULFILLED,
            ][(idx - 1) % 4]
            Order.objects.update_or_create(
                tenant=tenant,
                orderNumber=f"{tenant.id:02d}ORD{idx:04d}",
                defaults={
                    "partyID": party,
                    "buildDate": build_date,
                    "orderText": f"{2 + (idx % 4)} cases of SKU-{items[idx % len(items)].sku}",
                    "remark": f"Seeded order {idx}",
                    "location": f"Route {((idx - 1) % 6) + 1}",
                    "status": status,
                    "createdBy": sales_staff,
                    "approvedOn": self._dt_at(build_date, 14, 45) if status in [Order.Status.APPROVED, Order.Status.FULFILLED] else None,
                    "approvedBy": admin_staff if status in [Order.Status.APPROVED, Order.Status.FULFILLED] else None,
                    "isDeleted": False,
                },
            )

        staff_cycle = [owner_staff, admin_staff, collector, sales_staff, cash_staff, audit_staff]
        for idx in range(1, 21):
            staff = staff_cycle[(idx - 1) % len(staff_cycle)]
            day = today - timedelta(days=idx - 1)
            Attendance.objects.get_or_create(
                tenant=tenant,
                staffID=staff,
                loginDateTime=self._dt_at(day, 9 + (idx % 2), 0),
                defaults={
                    "registerType": "Daily",
                    "isLogIn": True,
                    "isLogOut": True,
                    "logoutDateTime": self._dt_at(day, 18, 15),
                    "login_location": f"{tenant_name} login point {idx}",
                    "logout_location": f"{tenant_name} logout point {idx}",
                    "login_remark": "On time",
                    "logout_remark": "Shift completed",
                    "isDeleted": False,
                },
            )

        for idx in range(1, 13):
            party = parties[(idx - 1) % len(parties)]
            WhatsappMessageStatus.objects.get_or_create(
                tenant=tenant,
                phone=party.phone,
                messageTo=party.name,
                status="Success",
                defaults={
                    "message": f"Seeded collection reminder {idx} for {tenant_name}",
                    "isDeleted": False,
                },
            )

        for idx in range(1, 5):
            supplier = parties[-idx]
            purchase_invoice, _ = PurchaseInvoice.objects.update_or_create(
                tenant=tenant,
                invoiceNumber=f"PUR-{tenant.id:02d}-{idx:04d}",
                defaults={
                    "partyID": supplier,
                    "buildDate": today - timedelta(days=idx * 3),
                    "gstTreatment": PurchaseInvoice.GstTreatment.GST if idx % 2 else PurchaseInvoice.GstTreatment.NON_GST,
                    "claimStatus": PurchaseInvoice.ClaimStatus.CLAIMED if idx % 2 else PurchaseInvoice.ClaimStatus.NOT_APPLICABLE,
                    "subtotalAmount": round(4500 + idx * 800, 2),
                    "gstAmount": round((4500 + idx * 800) * 0.12, 2),
                    "totalAmount": round((4500 + idx * 800) * 1.12, 2),
                    "remark": f"Seeded purchase invoice {idx}",
                    "createdBy": admin_staff,
                    "isDeleted": False,
                },
            )
            for line_idx in range(1, 4):
                item = items[((idx - 1) * 3 + line_idx - 1) % len(items)]
                quantity = float(line_idx + idx)
                unit_cost = round(95 + idx * 12 + line_idx * 5.5, 2)
                gst_amount = round(quantity * unit_cost * (item.gstRate / 100.0), 2)
                taxable_amount = round(quantity * unit_cost, 2)
                PurchaseLine.objects.update_or_create(
                    tenant=tenant,
                    purchaseInvoice=purchase_invoice,
                    item=item,
                    defaults={
                        "warehouse": warehouses[(line_idx - 1) % len(warehouses)],
                        "stockBucket": item.defaultStockBucket,
                        "quantity": quantity,
                        "unitCost": unit_cost,
                        "taxableAmount": taxable_amount,
                        "gstRate": item.gstRate,
                        "gstAmount": gst_amount,
                        "lineTotal": round(taxable_amount + gst_amount, 2),
                        "remark": f"Purchase line {line_idx}",
                        "isDeleted": False,
                    },
                )

        Validity.objects.update_or_create(
            tenant=tenant,
            activationType="SaaS",
            defaults={
                "activationDate": today,
                "expiryDate": today + timedelta(days=365),
            },
        )
        EcomValidity.objects.update_or_create(
            tenant=tenant,
            activationType="Ecom SaaS",
            defaults={
                "activationDate": today,
                "expiryDate": today + timedelta(days=365),
            },
        )

        summary = {
            "parties": Party.objects.filter(tenant=tenant, isDeleted=False).count(),
            "collections": Collection.objects.filter(tenant=tenant, isDeleted=False).count(),
            "sales": Sales.objects.filter(tenant=tenant, isDeleted=False).count(),
            "cash_counters": CashCounter.objects.filter(tenant=tenant, isDeleted=False).count(),
            "counter_collections": CollectionCashCounter.objects.filter(tenant=tenant, isDeleted=False).count(),
            "orders": Order.objects.filter(tenant=tenant, isDeleted=False).count(),
            "attendance": Attendance.objects.filter(tenant=tenant, isDeleted=False).count(),
            "inventory_items": InventoryItem.objects.filter(tenant=tenant, isDeleted=False).count(),
            "purchase_invoices": PurchaseInvoice.objects.filter(tenant=tenant, isDeleted=False).count(),
        }

        return {
            "tenant": tenant,
            "owner_password": owner_password,
            "owner": owner,
            "summary": summary,
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
        tenant_group_name = f"{tenant.slug}:{group_name}"
        django_group, _ = Group.objects.get_or_create(name=tenant_group_name)
        permission_codenames = DEFAULT_STAFF_GROUPS[group_name]
        if permission_codenames == "__all__":
            permissions = Permission.objects.exclude(
                content_type__app_label__in=["admin", "contenttypes", "sessions"]
            )
        else:
            permissions = []
            for perm_name in permission_codenames:
                app_label, codename = perm_name.split(".", 1)
                permission = Permission.objects.filter(
                    content_type__app_label=app_label,
                    codename=codename,
                ).first()
                if permission:
                    permissions.append(permission)
        django_group.permissions.set(permissions)
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
        if staff_group.djangoGroup_id != django_group.id:
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
            if getattr(staff_user, field_name) != expected_value:
                setattr(staff_user, field_name, expected_value)
                update_fields.append(field_name)
        if created:
            user.groups.clear()
        if staff_group.djangoGroup_id:
            user.groups.set([staff_group.djangoGroup])
        if update_fields:
            update_fields.append("lastUpdatedOn")
            staff_user.save(update_fields=update_fields)
        return staff_user

    def _ensure_party_group(self, tenant, name, description):
        group, _ = PartyGroup.objects.get_or_create(
            tenant=tenant,
            name=name,
            defaults={"description": description, "isDeleted": False},
        )
        update_fields = []
        if group.description != description:
            group.description = description
            update_fields.append("description")
        if group.isDeleted:
            group.isDeleted = False
            update_fields.append("isDeleted")
        if update_fields:
            update_fields.append("lastUpdatedOn")
            group.save(update_fields=update_fields)
        return group

    def _ensure_bank(self, *, tenant, account_number, name, description):
        bank, _ = Bank.objects.get_or_create(
            tenant=tenant,
            accountNumber=account_number,
            defaults={"name": name, "description": description, "isDeleted": False},
        )
        update_fields = []
        if bank.name != name:
            bank.name = name
            update_fields.append("name")
        if bank.description != description:
            bank.description = description
            update_fields.append("description")
        if bank.isDeleted:
            bank.isDeleted = False
            update_fields.append("isDeleted")
        if update_fields:
            update_fields.append("lastUpdatedOn")
            bank.save(update_fields=update_fields)
        return bank

    def _ensure_book_accounts(self, tenant, banks):
        BookAccount.objects.update_or_create(
            tenant=tenant,
            type=BookAccount.Type.CASH,
            isDefault=True,
            defaults={
                "name": "Cash Till",
                "openingBalance": 25000.0,
                "openingDate": date.today() - timedelta(days=30),
                "isDeleted": False,
            },
        )
        for bank in banks:
            BookAccount.objects.update_or_create(
                tenant=tenant,
                type=BookAccount.Type.BANK,
                bank=bank,
                defaults={
                    "name": f"{bank.name} - {bank.accountNumber}",
                    "openingBalance": 50000.0,
                    "openingDate": date.today() - timedelta(days=30),
                    "isDefault": False,
                    "isDeleted": False,
                },
            )

    def _ensure_invoice_series(self, tenant, module, name, code):
        series, _ = InvoiceSeries.objects.get_or_create(
            tenant=tenant,
            module=module,
            seriesCode=code,
            defaults={
                "name": name,
                "startNumber": 1,
                "endNumber": 9999,
                "nextNumber": 25,
                "paddingLength": 4,
                "isActive": True,
                "isDefault": True,
                "isDeleted": False,
            },
        )
        update_fields = []
        desired_values = {
            "name": name,
            "nextNumber": 25,
            "isActive": True,
            "isDefault": True,
            "isDeleted": False,
        }
        for field_name, expected_value in desired_values.items():
            if getattr(series, field_name) != expected_value:
                setattr(series, field_name, expected_value)
                update_fields.append(field_name)
        if update_fields:
            update_fields.append("lastUpdatedOn")
            series.save(update_fields=update_fields)
        return series

    def _ensure_party(
        self,
        *,
        tenant,
        name,
        phone,
        address,
        opening_balance,
        party_group,
        assignee,
        user=None,
        username=None,
        password=None,
    ):
        party, _ = Party.objects.get_or_create(
            tenant=tenant,
            phone=phone,
            defaults={
                "name": name,
                "address": address,
                "openingBalance": opening_balance,
                "partyGroupID": party_group,
                "assignTo": assignee,
                "userID": user,
                "username": username,
                "password": password,
                "isDeleted": False,
            },
        )
        update_fields = []
        desired_values = {
            "name": name,
            "address": address,
            "openingBalance": opening_balance,
            "partyGroupID": party_group,
            "assignTo": assignee,
            "userID": user,
            "username": username,
            "password": password,
            "isDeleted": False,
        }
        for field_name, expected_value in desired_values.items():
            if getattr(party, field_name) != expected_value:
                setattr(party, field_name, expected_value)
                update_fields.append(field_name)
        if update_fields:
            update_fields.append("lastUpdatedOn")
            party.save(update_fields=update_fields)
        return party

    def _ensure_inventory_category(self, tenant, name, code):
        category, _ = InventoryCategory.objects.get_or_create(
            tenant=tenant,
            name=name,
            defaults={"code": code, "description": f"{name} category", "isActive": True, "isDeleted": False},
        )
        update_fields = []
        if category.code != code:
            category.code = code
            update_fields.append("code")
        if category.isDeleted:
            category.isDeleted = False
            update_fields.append("isDeleted")
        if not category.isActive:
            category.isActive = True
            update_fields.append("isActive")
        if update_fields:
            update_fields.append("lastUpdatedOn")
            category.save(update_fields=update_fields)
        return category

    def _ensure_inventory_unit(self, tenant, name, symbol, allows_decimal):
        unit, _ = InventoryUnit.objects.get_or_create(
            tenant=tenant,
            symbol=symbol,
            defaults={
                "name": name,
                "allowsDecimal": allows_decimal,
                "description": f"{name} unit",
                "isActive": True,
                "isDeleted": False,
            },
        )
        update_fields = []
        desired_values = {
            "name": name,
            "allowsDecimal": allows_decimal,
            "isActive": True,
            "isDeleted": False,
        }
        for field_name, expected_value in desired_values.items():
            if getattr(unit, field_name) != expected_value:
                setattr(unit, field_name, expected_value)
                update_fields.append(field_name)
        if update_fields:
            update_fields.append("lastUpdatedOn")
            unit.save(update_fields=update_fields)
        return unit

    def _ensure_inventory_tax_profile(self, tenant, name, hsn_code, gst_rate):
        profile, _ = InventoryTaxProfile.objects.get_or_create(
            tenant=tenant,
            name=name,
            defaults={
                "hsnCode": hsn_code,
                "gstRate": gst_rate,
                "description": name,
                "isActive": True,
                "isDeleted": False,
            },
        )
        update_fields = []
        desired_values = {
            "hsnCode": hsn_code,
            "gstRate": gst_rate,
            "isActive": True,
            "isDeleted": False,
        }
        for field_name, expected_value in desired_values.items():
            if getattr(profile, field_name) != expected_value:
                setattr(profile, field_name, expected_value)
                update_fields.append(field_name)
        if update_fields:
            update_fields.append("lastUpdatedOn")
            profile.save(update_fields=update_fields)
        return profile

    def _ensure_inventory_warehouse(self, tenant, name, code, is_default):
        warehouse, _ = InventoryWarehouse.objects.get_or_create(
            tenant=tenant,
            code=code,
            defaults={
                "name": name,
                "address": f"{name}, {tenant.name}",
                "isDefault": is_default,
                "isActive": True,
                "isDeleted": False,
            },
        )
        update_fields = []
        desired_values = {
            "name": name,
            "isDefault": is_default,
            "isActive": True,
            "isDeleted": False,
        }
        for field_name, expected_value in desired_values.items():
            if getattr(warehouse, field_name) != expected_value:
                setattr(warehouse, field_name, expected_value)
                update_fields.append(field_name)
        if update_fields:
            update_fields.append("lastUpdatedOn")
            warehouse.save(update_fields=update_fields)
        return warehouse

    def _ensure_inventory_item(self, *, tenant, idx, category, unit, tax_profile):
        sku = f"{tenant.slug[:4].upper()}-SKU-{idx:03d}"
        item, _ = InventoryItem.objects.get_or_create(
            tenant=tenant,
            sku=sku,
            defaults={
                "name": f"{category.name} Item {idx:02d}",
                "barcode": f"{tenant.id:02d}{idx:010d}",
                "category": category,
                "unit": unit,
                "taxProfile": tax_profile,
                "categoryName": category.name,
                "brandName": tenant.name.split()[0],
                "unitName": unit.symbol,
                "hsnCode": tax_profile.hsnCode,
                "description": f"Seeded inventory item {idx}",
                "defaultStockBucket": InventoryItem.StockBucket.GST,
                "gstRate": tax_profile.gstRate,
                "defaultPurchasePrice": round(45 + idx * 4.75, 2),
                "defaultSalesPrice": round(65 + idx * 6.5, 2),
                "mrp": round(75 + idx * 7.25, 2),
                "minimumStockLevel": 10 + idx,
                "reorderLevel": 18 + idx,
                "isActive": True,
                "isDeleted": False,
            },
        )
        update_fields = []
        desired_values = {
            "name": f"{category.name} Item {idx:02d}",
            "category": category,
            "unit": unit,
            "taxProfile": tax_profile,
            "categoryName": category.name,
            "brandName": tenant.name.split()[0],
            "unitName": unit.symbol,
            "hsnCode": tax_profile.hsnCode,
            "defaultStockBucket": InventoryItem.StockBucket.GST,
            "gstRate": tax_profile.gstRate,
            "isActive": True,
            "isDeleted": False,
        }
        for field_name, expected_value in desired_values.items():
            if getattr(item, field_name) != expected_value:
                setattr(item, field_name, expected_value)
                update_fields.append(field_name)
        if update_fields:
            update_fields.append("lastUpdatedOn")
            item.save(update_fields=update_fields)
        return item

    def _ensure_sales_line(self, *, tenant, sales, item, warehouse, quantity, unit_price):
        SalesLine.objects.update_or_create(
            tenant=tenant,
            sales=sales,
            item=item,
            defaults={
                "warehouse": warehouse,
                "stockBucket": item.defaultStockBucket,
                "quantity": quantity,
                "unitPrice": unit_price,
                "lineTotal": round(quantity * unit_price, 2),
                "remark": "Seeded sales line",
                "isDeleted": False,
            },
        )

    def _ensure_cash_counter_line(self, *, tenant, cash_counter, item, warehouse, quantity, unit_price):
        CashCounterLine.objects.update_or_create(
            tenant=tenant,
            cashCounter=cash_counter,
            item=item,
            defaults={
                "warehouse": warehouse,
                "stockBucket": item.defaultStockBucket,
                "quantity": quantity,
                "unitPrice": unit_price,
                "lineTotal": round(quantity * unit_price, 2),
                "remark": "Seeded cash counter line",
                "isDeleted": False,
            },
        )

    def _grant_all_permissions(self, user):
        all_permissions = Permission.objects.exclude(
            content_type__app_label__in=["admin", "contenttypes", "sessions"]
        )
        user.user_permissions.set(all_permissions)

    def _phone_for(self, tenant, idx):
        tenant_seed = 10 + (tenant.id % 80)
        return f"9{tenant_seed:02d}{idx:07d}"

    def _dt_at(self, day, hour, minute):
        return datetime.combine(day, time(hour=hour, minute=minute))

    def _print_seeded_credentials(self, seeded):
        self.stdout.write("Seeded credentials:")
        for item in seeded:
            tenant = item["tenant"]
            tenant_key = tenant.slug.replace("-", "")
            self.stdout.write(f"- {tenant.name}")
            self.stdout.write(f"  owner: {item['owner'].username} / {item['owner_password']}")
            self.stdout.write(f"  admin: {tenant_key}.admin / {SEEDED_PASSWORDS['admin']}")
            self.stdout.write(f"  collector: {tenant_key}.collector / {SEEDED_PASSWORDS['collector']}")
            self.stdout.write(f"  sales: {tenant_key}.sales / {SEEDED_PASSWORDS['sales']}")
            self.stdout.write(f"  cash: {tenant_key}.cash / {SEEDED_PASSWORDS['cash']}")
            self.stdout.write(f"  audit: {tenant_key}.audit / {SEEDED_PASSWORDS['audit']}")
            self.stdout.write(f"  customer: {tenant_key}.customer / {SEEDED_PASSWORDS['customer']}")
            summary = ", ".join(f"{key}={value}" for key, value in item["summary"].items())
            self.stdout.write(f"  summary: {summary}")
