from home.models import (
    StaffUser, Party, Collection, Sales, Order, InventoryItem, Bank,
    PurchaseInvoice, SalesReturnVoucher, PurchaseReturnVoucher, CashCounter,
    BookAccount, Attendance, InventoryStock, InventoryWarehouse
)

def get_tenant_business_data_snapshot(tenant):
    if not tenant:
        return "No tenant context available."

    lines = []
    lines.append(f"# COMPLETE BUSINESS LEDGER SNAPSHOT: {tenant.name}")
    lines.append(f"- Legal Name: {tenant.legalName or 'N/A'}")
    lines.append(f"- GST Number: {tenant.gstNumber or 'N/A'}")
    lines.append(f"- Phone: {tenant.phone or 'N/A'}")
    lines.append(f"- Billing Address: {tenant.billingAddress or 'N/A'}")
    lines.append("")

    # 1. Banks
    banks = Bank.objects.filter(tenant=tenant, isDeleted=False)
    lines.append("## Bank Accounts")
    if banks.exists():
        for b in banks:
            lines.append(f"- Name: {b.name} | Account Number: {b.accountNumber or 'N/A'} | Description: {b.description or 'None'}")
    else:
        lines.append("- No bank accounts configured.")
    lines.append("")

    # 2. Warehouses
    warehouses = InventoryWarehouse.objects.filter(tenant=tenant, isDeleted=False)
    lines.append("## Warehouses")
    if warehouses.exists():
        for w in warehouses:
            lines.append(f"- Name: {w.name} (Code: {w.code or 'N/A'}) | Address: {w.address or 'N/A'} | Active: {'Yes' if w.isActive else 'No'} | Default: {'Yes' if w.isDefault else 'No'}")
    else:
        lines.append("- No warehouses configured.")
    lines.append("")

    # 3. Staff
    staff = StaffUser.objects.filter(tenant=tenant, isDeleted=False)[:25]
    lines.append("## Staff Members")
    if staff.exists():
        for s in staff:
            lines.append(f"- Name: {s.name} | Phone: {s.phone or 'N/A'} | Email: {s.email or 'N/A'} | Group/Role: {s.group or 'N/A'} | Active: {'Yes' if s.isActive else 'No'}")
    else:
        lines.append("- No active staff members.")
    lines.append("")

    # 4. Book Accounts (Cash/Bank Books)
    book_accounts = BookAccount.objects.filter(tenant=tenant, isDeleted=False).select_related('bank')
    lines.append("## General Ledger Book Accounts")
    if book_accounts.exists():
        for ba in book_accounts:
            bank_label = f" (Bank: {ba.bank.name})" if ba.bank else ""
            lines.append(f"- Account: {ba.name}{bank_label} | Type: {ba.get_type_display()} | Opening Balance: ₹{ba.openingBalance} | Default: {'Yes' if ba.isDefault else 'No'}")
    else:
        lines.append("- No general ledger accounts configured.")
    lines.append("")

    # 5. Parties (Customers/Suppliers)
    parties = Party.objects.filter(tenant=tenant, isDeleted=False).select_related('partyGroupID', 'assignTo')[:35]
    lines.append("## Customers and Suppliers (Parties)")
    if parties.exists():
        for p in parties:
            role_label = "B2B" if p.isB2B else "B2C"
            group_label = p.partyGroupID.name if p.partyGroupID else "None"
            assigned_staff = p.assignTo.name if p.assignTo else "None"
            lines.append(f"- Name: {p.name} | Phone: {p.phone or 'N/A'} | Balance: ₹{p.openingBalance} | Type: {role_label} | Group: {group_label} | Assigned Staff: {assigned_staff} | Address: {p.address or 'N/A'}")
    else:
        lines.append("- No parties registered.")
    lines.append("")

    # 6. Inventory Items & Stock levels
    stocks = InventoryStock.objects.filter(tenant=tenant, isDeleted=False).select_related('item')
    stock_map = {}
    for st in stocks:
        item_id = st.item_id
        if item_id not in stock_map:
            stock_map[item_id] = []
        stock_map[item_id].append(f"{st.quantityOnHand} units ({st.get_stockBucket_display()})")

    items = InventoryItem.objects.filter(tenant=tenant, isDeleted=False).select_related('category')[:45]
    lines.append("## Inventory Items & Current Stock")
    if items.exists():
        for i in items:
            cat_label = i.category.name if i.category else 'N/A'
            item_stock = ", ".join(stock_map.get(i.pk, ["0 units (Normal)"]))
            lines.append(f"- Item: {i.name} (SKU: {i.sku or 'N/A'}) | Category: {cat_label} | Sale Price: ₹{i.defaultSalesPrice} | Purchase Price: ₹{i.defaultPurchasePrice} | MRP: ₹{i.mrp} | GST Rate: {i.gstRate}% | Current Stock: {item_stock}")
        if items.count() > 45:
            lines.append("- ... (showing top 45 items; other catalog items exist)")
    else:
        lines.append("- No inventory items in catalog.")
    lines.append("")

    # 7. Recent Invoices (Sales)
    sales = Sales.objects.filter(tenant=tenant, isDeleted=False).select_related('partyID', 'createdBy', 'approvedBy').order_by('-buildDate')[:25]
    lines.append("## Recent Sales Invoices")
    if sales.exists():
        for s in sales:
            party_name = s.partyID.name if s.partyID else 'Unknown'
            status_label = "Confirmed" if s.isApproved else "Pending Approval"
            creator_name = s.createdBy.name if s.createdBy else 'N/A'
            approver_name = s.approvedBy.name if s.approvedBy else 'N/A'
            lines.append(f"- Invoice #{s.invoiceNumber or s.salesID or s.pk} | Customer: {party_name} | Date: {s.buildDate} | Amount: ₹{s.amount} (Taxable: ₹{s.taxableAmount}, GST: ₹{s.gstAmount}) | Payment: {s.modeOfPayment} | Status: {status_label} | Created By: {creator_name} | Approved By: {approver_name}")
    else:
        lines.append("- No recent sales invoices.")
    lines.append("")

    # 8. Retail / Cash Counter POS Invoices
    retail_sales = CashCounter.objects.filter(tenant=tenant, isDeleted=False).select_related('partyID').order_by('-id')[:20]
    lines.append("## Recent Retail POS Invoices")
    if retail_sales.exists():
        for rs in retail_sales:
            customer_name = rs.partyID.name if rs.partyID else (rs.buyerLegalName or 'Cash Sale')
            lines.append(f"- POS Invoice #{rs.invoiceNumber or rs.counterID or rs.pk} | Customer: {customer_name} | GST: {rs.buyerGstNumber or 'None'} | Taxable: ₹{rs.taxableAmount} | GST Amt: ₹{rs.gstAmount} | Mode: {rs.mode or 'N/A'}")
    else:
        lines.append("- No recent retail invoices.")
    lines.append("")

    # 9. Recent Purchase Invoices
    purchases = PurchaseInvoice.objects.filter(tenant=tenant, isDeleted=False).select_related('partyID', 'createdBy').order_by('-buildDate')[:25]
    lines.append("## Recent Purchase Invoices")
    if purchases.exists():
        for p in purchases:
            supplier_name = p.partyID.name if p.partyID else 'Unknown'
            creator_name = p.createdBy.name if p.createdBy else 'N/A'
            lines.append(f"- Purchase Invoice #{p.invoiceNumber or p.purchaseID or p.pk} | Supplier: {supplier_name} | Date: {p.buildDate} | GST Treatment: {p.get_gstTreatment_display()} | Status: {p.get_claimStatus_display()} | Created By: {creator_name}")
    else:
        lines.append("- No recent purchase invoices.")
    lines.append("")

    # 10. Recent Orders
    orders = Order.objects.filter(tenant=tenant, isDeleted=False).select_related('partyID', 'createdBy', 'assignedTo', 'approvedBy').order_by('-buildDate')[:25]
    lines.append("## Recent Orders")
    if orders.exists():
        for o in orders:
            party_name = o.partyID.name if o.partyID else 'Unknown'
            creator_name = o.createdBy.name if o.createdBy else 'N/A'
            assignee_name = o.assignedTo.name if o.assignedTo else 'N/A'
            approver_name = o.approvedBy.name if o.approvedBy else 'N/A'
            lines.append(f"- Order #{o.orderNumber or o.pk} | Customer: {party_name} | Date: {o.buildDate} | Status: {o.get_status_display()} | Items: {o.orderText or 'None'} | Assigned To: {assignee_name} | Created By: {creator_name} | Confirmed By: {approver_name} | Remark: {o.remark or 'None'}")
    else:
        lines.append("- No recent orders.")
    lines.append("")

    # 11. Recent Collections (Payments Received)
    collections = Collection.objects.filter(tenant=tenant, isDeleted=False).select_related('partyID', 'collectedBy', 'approvedBy').order_by('-collectionDateTime')[:25]
    lines.append("## Recent Collections (Payments Received)")
    if collections.exists():
        for c in collections:
            party_name = c.partyID.name if c.partyID else 'Unknown'
            status_label = "Approved" if c.isApproved else "Pending Approval"
            collector_name = c.collectedBy.name if c.collectedBy else 'N/A'
            approver_name = c.approvedBy.name if c.approvedBy else 'N/A'
            lines.append(f"- Receipt #{c.paymentID or c.pk} | Customer: {party_name} | Date: {c.collectionDateTime or c.datetime} | Amount: ₹{c.paidAmount} | Mode: {c.modeOfPayment} | Status: {status_label} | Collected By: {collector_name} | Approved By: {approver_name} | Remark: {c.remark or 'None'}")
    else:
        lines.append("- No recent collections.")
    lines.append("")

    # 12. Recent Return Vouchers (Sales and Purchases Returns)
    sales_returns = SalesReturnVoucher.objects.filter(tenant=tenant, isDeleted=False).select_related('partyID', 'createdBy').order_by('-entryDate')[:15]
    lines.append("## Recent Sales Returns")
    if sales_returns.exists():
        for sr in sales_returns:
            party_name = sr.partyID.name if sr.partyID else 'Unknown'
            creator_name = sr.createdBy.name if sr.createdBy else 'N/A'
            lines.append(f"- Sales Return #{sr.voucherNumber or sr.pk} | Customer: {party_name} | Date: {sr.entryDate} | Amount: ₹{sr.totalAmount} | Created By: {creator_name} | Remark: {sr.remark or 'None'}")
    else:
        lines.append("- No recent sales returns.")
    lines.append("")

    purchase_returns = PurchaseReturnVoucher.objects.filter(tenant=tenant, isDeleted=False).select_related('partyID', 'createdBy').order_by('-entryDate')[:15]
    lines.append("## Recent Purchase Returns")
    if purchase_returns.exists():
        for pr in purchase_returns:
            party_name = pr.partyID.name if pr.partyID else 'Unknown'
            creator_name = pr.createdBy.name if pr.createdBy else 'N/A'
            lines.append(f"- Purchase Return #{pr.voucherNumber or pr.pk} | Supplier: {party_name} | Date: {pr.entryDate} | Amount: ₹{pr.totalAmount} | Created By: {creator_name} | Remark: {pr.remark or 'None'}")
    else:
        lines.append("- No recent purchase returns.")
    lines.append("")

    # 13. Recent Attendances
    attendances = Attendance.objects.filter(tenant=tenant, isDeleted=False).select_related('staffID').order_by('-datetime')[:25]
    lines.append("## Recent Attendances")
    if attendances.exists():
        for att in attendances:
            staff_name = att.staffID.name if att.staffID else 'Unknown'
            status_str = "Logged In" if att.isLogIn else "Logged Out"
            lines.append(f"- Staff: {staff_name} | DateTime: {att.datetime} | Status: {status_str} | Check-In: {att.loginDateTime or 'N/A'} | Check-Out: {att.logoutDateTime or 'N/A'}")
    else:
        lines.append("- No recent attendance logs found.")
    lines.append("")

    return "\n".join(lines)
