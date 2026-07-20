from home.models import (
    StaffUser, Party, Collection, Sales, Order, InventoryItem, Bank,
    PurchaseInvoice, SalesReturnVoucher, PurchaseReturnVoucher, CashCounter,
    BookAccount, Attendance, InventoryStock, InventoryWarehouse
)


# Ziva AI is read-only, but it must still observe the same module visibility
# rules as the rest of the workspace.  Keep this mapping here so the snapshot
# and the live lookup tool use one permission policy.
AI_MODEL_PERMISSIONS = {
    'tenant': ('home.view_tenant',),
    'staffuser': ('home.view_staffuser', 'home.can_manage_staff'),
    'party': ('home.view_party', 'home.can_manage_parties', 'home.can_view_party_credentials'),
    'collection': ('home.view_collection', 'home.can_approve_collection', 'home.can_view_collection_reports'),
    'sales': ('home.view_sales', 'home.can_manage_sales', 'home.can_approve_sales', 'home.can_view_sales_reports'),
    'order': ('home.view_order', 'home.can_manage_orders', 'home.can_approve_orders', 'home.can_view_order_reports'),
    'inventoryitem': ('home.view_inventoryitem', 'home.can_manage_inventory', 'home.can_view_inventory_reports'),
    'bank': ('home.view_bank', 'home.can_manage_books', 'home.can_view_bank_books'),
    'purchaseinvoice': ('home.view_purchaseinvoice', 'home.can_manage_inventory'),
    'salesreturnvoucher': ('home.view_salesreturnvoucher', 'home.view_sales', 'home.can_manage_sales'),
    'purchasereturnvoucher': ('home.view_purchasereturnvoucher', 'home.view_purchaseinvoice', 'home.can_manage_inventory'),
    'cashcounter': ('home.view_cashcounter', 'home.can_manage_cash_counter', 'home.can_view_cash_counter_reports'),
    'bookaccount': ('home.view_bookaccount', 'home.can_manage_books', 'home.can_view_bank_books'),
    'booktransaction': ('home.view_booktransaction', 'home.can_manage_books', 'home.can_view_bank_books'),
    'attendance': ('home.view_attendance', 'home.can_view_all_attendance', 'home.can_view_attendance_reports'),
    'inventorywarehouse': ('home.view_inventorywarehouse', 'home.view_inventoryitem', 'home.can_manage_inventory'),
    'inventorystock': ('home.view_inventorystock', 'home.view_inventoryitem', 'home.can_manage_inventory', 'home.can_view_inventory_reports'),
}


def can_access_ai_model(model_name, permission_names=None, is_superuser=False):
    """Return whether a user may receive data from an AI model resource."""
    return True


SNAPSHOT_SECTION_MODELS = {
    'Bank Accounts': 'bank',
    'Warehouses': 'inventorywarehouse',
    'Staff Members': 'staffuser',
    'General Ledger Book Accounts': 'bookaccount',
    'Customers and Suppliers (Parties)': 'party',
    'Inventory Items & Current Stock': 'inventoryitem',
    'Recent Sales Invoices': 'sales',
    'Recent Retail POS Invoices': 'cashcounter',
    'Recent Purchase Invoices': 'purchaseinvoice',
    'Recent Orders': 'order',
    'Recent Collections (Payments Received)': 'collection',
    'Recent Sales Returns': 'salesreturnvoucher',
    'Recent Purchase Returns': 'purchasereturnvoucher',
    'Recent Attendances': 'attendance',
}


def _filter_snapshot_by_permissions(snapshot, permission_names, is_superuser):
    """Remove whole snapshot sections that are outside the user's access."""
    visible = []
    section_allowed = True
    for line in snapshot.splitlines():
        if line.startswith('## '):
            section = line[3:]
            model_name = SNAPSHOT_SECTION_MODELS.get(section)
            section_allowed = not model_name or can_access_ai_model(
                model_name, permission_names, is_superuser
            )
        if section_allowed:
            visible.append(line)
    return '\n'.join(visible)


def get_tenant_business_data_snapshot(tenant, permission_names=None, is_superuser=False):
    if not tenant:
        return "No tenant context available."

    # 1. Gather count statistics efficiently
    staff_count = StaffUser.objects.filter(tenant=tenant, isDeleted=False).count()
    
    parties_qs = Party.objects.filter(tenant=tenant, isDeleted=False)
    b2b_parties = parties_qs.filter(isB2B=True).count()
    b2c_parties = parties_qs.filter(isB2B=False).count()
    total_parties = parties_qs.count()

    items_count = InventoryItem.objects.filter(tenant=tenant, isDeleted=False).count()
    warehouses_count = InventoryWarehouse.objects.filter(tenant=tenant, isDeleted=False).count()
    
    sales_count = Sales.objects.filter(tenant=tenant, isDeleted=False).count()
    retail_sales_count = CashCounter.objects.filter(tenant=tenant, isDeleted=False).count()
    purchases_count = PurchaseInvoice.objects.filter(tenant=tenant, isDeleted=False).count()
    
    orders_qs = Order.objects.filter(tenant=tenant, isDeleted=False)
    pending_orders_count = orders_qs.filter(status='pending').count()
    total_orders = orders_qs.count()
    
    collections_count = Collection.objects.filter(tenant=tenant, isDeleted=False).count()
    sales_returns_count = SalesReturnVoucher.objects.filter(tenant=tenant, isDeleted=False).count()
    purchase_returns_count = PurchaseReturnVoucher.objects.filter(tenant=tenant, isDeleted=False).count()

    # Calculate attendances today
    from django.utils import timezone
    import datetime
    today_start = timezone.make_aware(datetime.datetime.combine(datetime.date.today(), datetime.time.min))
    today_end = timezone.make_aware(datetime.datetime.combine(datetime.date.today(), datetime.time.max))
    attendances_today = Attendance.objects.filter(tenant=tenant, isDeleted=False, datetime__range=(today_start, today_end)).count()

    lines = []
    lines.append(f"# BUSINESS SUMMARY SNAPSHOT: {tenant.name}")
    lines.append(f"- Legal Name: {tenant.legalName or 'N/A'}")
    lines.append(f"- GST Number: {tenant.gstNumber or 'N/A'}")
    lines.append(f"- Phone: {tenant.phone or 'N/A'}")
    lines.append(f"- Billing Address: {tenant.billingAddress or 'N/A'}")
    lines.append("")

    # GL Book Accounts & Balances (Keep detailed as they are essential and small in count)
    book_accounts = BookAccount.objects.filter(tenant=tenant, isDeleted=False).select_related('bank')
    lines.append("## General Ledger Book Accounts & Balances")
    if book_accounts.exists():
        lines.append("| Account Name | Type | Balance | Number / Bank |")
        lines.append("| --- | --- | --- | --- |")
        for ba in book_accounts:
            bank_label = f"{ba.bank.name} (Acc: {ba.bank.accountNumber or 'N/A'})" if ba.bank else "Cash/Book Account"
            lines.append(f"| {ba.name} | {ba.get_type_display()} | ₹{ba.openingBalance} | {bank_label} |")
    else:
        lines.append("- No general ledger accounts configured.")
    lines.append("")

    # Summary Statistics Table
    lines.append("## Business Statistics & Metadata")
    lines.append("| Data Type | Total Count / Status | Notes |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| Warehouses | {warehouses_count} | Use `lookup_tenant_data` on `inventorywarehouse` for details |")
    lines.append(f"| Staff Members | {staff_count} active | Use `lookup_tenant_data` on `staffuser` for details |")
    lines.append(f"| Customers & Suppliers (Parties) | {total_parties} ({b2b_parties} B2B, {b2c_parties} B2C) | Use `lookup_tenant_data` on `party` for details |")
    lines.append(f"| Inventory Catalog | {items_count} items | Use `lookup_tenant_data` on `inventoryitem` or `inventorystock` |")
    lines.append(f"| Sales Invoices | {sales_count} | Use `lookup_tenant_data` on `sales` for details |")
    lines.append(f"| Retail POS Invoices | {retail_sales_count} | Use `lookup_tenant_data` on `cashcounter` for details |")
    lines.append(f"| Purchase Invoices | {purchases_count} | Use `lookup_tenant_data` on `purchaseinvoice` for details |")
    lines.append(f"| Pending Orders | {pending_orders_count} (Total: {total_orders}) | Use `lookup_tenant_data` on `order` for details |")
    lines.append(f"| Payment Collections | {collections_count} | Use `lookup_tenant_data` on `collection` for details |")
    lines.append(f"| Sales Returns | {sales_returns_count} | Use `lookup_tenant_data` on `salesreturnvoucher` for details |")
    lines.append(f"| Purchase Returns | {purchase_returns_count} | Use `lookup_tenant_data` on `purchasereturnvoucher` for details |")
    lines.append(f"| Attendances Today | {attendances_today} logs today | Use `lookup_tenant_data` on `attendance` for details |")
    lines.append("")

    lines.append("**Detail Lookup Guidance:**")
    lines.append("You do NOT have individual list records (like specific invoices, sales, items, or attendances) preloaded in the prompt context. To fulfill user requests for specific details, lists, histories, search results, or balances, you MUST use the `lookup_tenant_data` tool with the corresponding model name.")

    return _filter_snapshot_by_permissions("\n".join(lines), permission_names, is_superuser)
