from utils.logger import logger
from home.inventory import rebuild_inventory_stock, validate_non_negative_inventory
from home.models import (
    CashCounter,
    CashCounterLine,
    InventoryItem,
    ProformaInvoice,
    ProformaInvoiceLine,
    PurchaseInvoice,
    PurchaseLine,
    Sales,
    SalesLine,
    SalesReturnLine,
    SalesReturnVoucher,
    TenantSequence,
)
from home.services.cash_counter_invoicing import cash_counter_return_source_modes, parse_return_source_ref
from home.services.inventory_lines import (
    inventory_feature_enabled,
    normalize_stock_bucket,
    parse_inventory_item,
    parse_inventory_warehouse,
    request_line_items_payload,
    request_line_items_present,
    resolve_inventory_batch,
    safe_sales_float,
    validate_inventory_quantity_for_unit,
)
from home.services.sequences import next_tenant_sequence_value
from home.tenancy import tenant_qs


def sync_sales_lines(request, sale: Sales):
    if not inventory_feature_enabled(request) or not request_line_items_present(request):
        return
    existing_pairs = list(sale.lines.filter(isDeleted=False).values_list('item_id', 'stockBucket'))
    existing_item_ids = [item_id for item_id, _bucket in existing_pairs]
    sale.lines.filter(isDeleted=False).update(isDeleted=True)
    new_item_ids = []
    new_pairs = []
    warehouse = parse_inventory_warehouse(request, request.POST.get('warehouseId'))
    for row in request_line_items_payload(request):
        item = parse_inventory_item(request, row.get('itemId'))
        if item.isSaleBlocked:
            raise ValueError(f'Sale is blocked for inventory item: {item.name}.')
        quantity = safe_sales_float(row.get('quantity'))
        if quantity <= 0:
            raise ValueError('Sales quantity must be greater than zero.')
        validate_inventory_quantity_for_unit(item, quantity, label='Sales quantity')
        stock_bucket = normalize_stock_bucket(request, row.get('stockBucket'), item)
        batch = resolve_inventory_batch(request, row, item=item, stock_bucket=stock_bucket, warehouse=warehouse, quantity=quantity, direction='out')
        unit_price = safe_sales_float(row.get('unitPrice'))
        discount_percent = max(0.0, min(100.0, safe_sales_float(row.get('discountPercent'))))
        gross_amount = round(quantity * unit_price, 2)
        taxable_amount = round(gross_amount - ((gross_amount * discount_percent) / 100.0), 2)
        gst_rate = safe_sales_float(row.get('gstRate'))
        if stock_bucket == InventoryItem.StockBucket.NON_GST:
            gst_rate = 0.0
            gst_amount = 0.0
        else:
            gst_amount = round(taxable_amount * gst_rate / 100.0, 2)
        line_total = round(taxable_amount + gst_amount, 2)
        SalesLine.objects.create(
            tenant=sale.tenant,
            sales=sale,
            warehouse=warehouse,
            item=item,
            batch=batch,
            hsnCode=item.hsnCode,
            stockBucket=stock_bucket,
            quantity=quantity,
            unitPrice=unit_price,
            discountPercent=discount_percent,
            taxableAmount=taxable_amount,
            gstRate=gst_rate,
            gstAmount=gst_amount,
            lineTotal=line_total,
            remark=(row.get('remark') or '')[:500],
        )
        new_item_ids.append(item.pk)
        new_pairs.append((item.pk, stock_bucket))
    affected_item_ids = set(existing_item_ids + new_item_ids)
    rebuild_inventory_stock(sale.tenant, affected_item_ids)
    validate_non_negative_inventory(sale.tenant, affected_item_ids, item_bucket_pairs=list(set(existing_pairs + new_pairs)))
    logger.info("[InventoryMutations] Synced %d sales lines for sale %s", len(new_item_ids), sale.pk)


def sync_proforma_lines(request, proforma: ProformaInvoice):
    if not inventory_feature_enabled(request) or not request_line_items_present(request):
        return
    proforma.lines.filter(isDeleted=False).update(isDeleted=True)
    warehouse = parse_inventory_warehouse(request, request.POST.get('warehouseId'))
    for row in request_line_items_payload(request):
        item = parse_inventory_item(request, row.get('itemId'))
        if item.isSaleBlocked:
            raise ValueError(f'Sale is blocked for inventory item: {item.name}.')
        quantity = safe_sales_float(row.get('quantity'))
        if quantity <= 0:
            raise ValueError('Proforma quantity must be greater than zero.')
        validate_inventory_quantity_for_unit(item, quantity, label='Proforma quantity')
        stock_bucket = normalize_stock_bucket(request, row.get('stockBucket'), item)
        batch = resolve_inventory_batch(request, row, item=item, stock_bucket=stock_bucket, warehouse=warehouse, quantity=quantity, direction='out')
        unit_price = safe_sales_float(row.get('unitPrice'))
        discount_percent = max(0.0, min(100.0, safe_sales_float(row.get('discountPercent'))))
        gross_amount = round(quantity * unit_price, 2)
        taxable_amount = round(gross_amount - ((gross_amount * discount_percent) / 100.0), 2)
        gst_rate = safe_sales_float(row.get('gstRate'))
        if stock_bucket == InventoryItem.StockBucket.NON_GST:
            gst_rate = 0.0
            gst_amount = 0.0
        else:
            gst_amount = round(taxable_amount * gst_rate / 100.0, 2)
        line_total = round(taxable_amount + gst_amount, 2)
        ProformaInvoiceLine.objects.create(
            tenant=proforma.tenant,
            proformaInvoice=proforma,
            warehouse=warehouse,
            item=item,
            batch=batch,
            hsnCode=item.hsnCode,
            stockBucket=stock_bucket,
            quantity=quantity,
            unitPrice=unit_price,
            discountPercent=discount_percent,
            taxableAmount=taxable_amount,
            gstRate=gst_rate,
            gstAmount=gst_amount,
            lineTotal=line_total,
            remark=(row.get('remark') or '')[:500],
        )


def copy_proforma_lines_to_sale(proforma: ProformaInvoice, sale: Sales) -> list[int]:
    item_ids = []
    for line in proforma.lines.filter(isDeleted=False).select_related('item', 'warehouse', 'batch').order_by('id'):
        SalesLine.objects.create(
            tenant=sale.tenant,
            sales=sale,
            warehouse=line.warehouse,
            item=line.item,
            batch=line.batch,
            hsnCode=line.hsnCode,
            stockBucket=line.stockBucket,
            quantity=line.quantity,
            unitPrice=line.unitPrice,
            discountPercent=line.discountPercent,
            taxableAmount=line.taxableAmount,
            gstRate=line.gstRate,
            gstAmount=line.gstAmount,
            lineTotal=line.lineTotal,
            remark=line.remark,
        )
        item_ids.append(line.item_id)
    return item_ids


def sync_cash_counter_lines(request, counter: CashCounter):
    if not request_line_items_present(request):
        return
    existing_pairs = list(counter.lines.filter(isDeleted=False).values_list('item_id', 'stockBucket'))
    existing_item_ids = [item_id for item_id, _bucket in existing_pairs]
    counter.lines.filter(isDeleted=False).update(isDeleted=True)
    new_item_ids = []
    new_pairs = []
    warehouse = parse_inventory_warehouse(request, request.POST.get('warehouseId'))
    for row in request_line_items_payload(request):
        item = parse_inventory_item(request, row.get('itemId'))
        if item.isSaleBlocked:
            raise ValueError(f'Sale is blocked for inventory item: {item.name}.')
        quantity = safe_sales_float(row.get('quantity'))
        if quantity <= 0:
            raise ValueError('Cash counter quantity must be greater than zero.')
        validate_inventory_quantity_for_unit(item, quantity, label='Cash counter quantity')
        stock_bucket = normalize_stock_bucket(request, row.get('stockBucket'), item)
        batch = resolve_inventory_batch(request, row, item=item, stock_bucket=stock_bucket, warehouse=warehouse, quantity=quantity, direction='out')
        unit_price = safe_sales_float(row.get('unitPrice'))
        discount_percent = max(0.0, min(100.0, safe_sales_float(row.get('discountPercent'))))
        gross_amount = round(quantity * unit_price, 2)
        taxable_amount = round(gross_amount - ((gross_amount * discount_percent) / 100.0), 2)
        gst_rate = safe_sales_float(row.get('gstRate'))
        if stock_bucket == InventoryItem.StockBucket.NON_GST:
            gst_rate = 0.0
            gst_amount = 0.0
        else:
            gst_amount = round(taxable_amount * gst_rate / 100.0, 2)
        line_total = round(taxable_amount + gst_amount, 2)
        CashCounterLine.objects.create(
            tenant=counter.tenant,
            cashCounter=counter,
            warehouse=warehouse,
            item=item,
            batch=batch,
            hsnCode=item.hsnCode,
            stockBucket=stock_bucket,
            quantity=quantity,
            unitPrice=unit_price,
            discountPercent=discount_percent,
            taxableAmount=taxable_amount,
            gstRate=gst_rate,
            gstAmount=gst_amount,
            lineTotal=line_total,
            remark=(row.get('remark') or '')[:500],
        )
        new_item_ids.append(item.pk)
        new_pairs.append((item.pk, stock_bucket))
    affected_item_ids = set(existing_item_ids + new_item_ids)
    rebuild_inventory_stock(counter.tenant, affected_item_ids)
    validate_non_negative_inventory(counter.tenant, affected_item_ids, item_bucket_pairs=list(set(existing_pairs + new_pairs)))


def sync_cash_counter_return_voucher(request, counter: CashCounter):
    linked_voucher = getattr(counter, 'linkedSalesReturnVoucher', None)
    prior_pairs = list(linked_voucher.lines.filter(isDeleted=False).values_list('item_id', 'stockBucket')) if linked_voucher else []
    prior_item_ids = [item_id for item_id, _bucket in prior_pairs]
    current_lines = list(counter.lines.filter(isDeleted=False).select_related('item', 'warehouse').order_by('id'))
    current_item_ids = [line.item_id for line in current_lines]
    current_pairs = [(line.item_id, line.stockBucket) for line in current_lines]

    if (counter.mode or '') != 'Return' or not current_lines:
        if linked_voucher and not linked_voucher.isDeleted:
            linked_voucher.isDeleted = True
            linked_voucher.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        if counter.linkedSalesReturnVoucher_id:
            counter.linkedSalesReturnVoucher = None
            counter.save(update_fields=['linkedSalesReturnVoucher', 'lastUpdatedOn'])
        affected_item_ids = set(prior_item_ids + current_item_ids)
        if affected_item_ids:
            rebuild_inventory_stock(counter.tenant, affected_item_ids)
            validate_non_negative_inventory(
                counter.tenant,
                affected_item_ids,
                item_bucket_pairs=list(set(prior_pairs + current_pairs)),
            )
        return

    voucher = linked_voucher or SalesReturnVoucher(tenant=counter.tenant)
    source_type, source_id = parse_return_source_ref(request.POST.get('salesId'))
    if not source_id:
        raise ValueError('Original invoice is required for sales return.')
    voucher.sales = None
    voucher.cashCounter = None
    if source_type == 'cash_counter':
        voucher.cashCounter = tenant_qs(request, CashCounter).filter(
            pk=source_id,
            isDeleted=False,
            mode__in=cash_counter_return_source_modes(),
        ).first()
        if voucher.cashCounter is None:
            raise ValueError('Selected cash counter invoice was not found.')
    else:
        voucher.sales = tenant_qs(request, Sales).filter(pk=source_id, isDeleted=False).first()
        if voucher.sales is None:
            raise ValueError('Selected sales invoice was not found.')
    voucher.entryDate = counter.entryDate.date() if getattr(counter, 'entryDate', None) and hasattr(counter.entryDate, 'date') else getattr(counter, 'entryDate', None)
    voucher.partyID = counter.partyID
    voucher.remark = counter.remark
    voucher.createdBy = counter.createdBy
    voucher.totalAmount = 0.0
    voucher.isDeleted = False
    if voucher.pk:
        voucher.save(update_fields=['sales', 'cashCounter', 'entryDate', 'partyID', 'remark', 'createdBy', 'totalAmount', 'isDeleted', 'lastUpdatedOn'])
    else:
        voucher.save()
        voucher.voucherNumber = next_tenant_sequence_value(request, TenantSequence.Scope.SALES_RETURN)
        voucher.save(update_fields=['voucherNumber', 'lastUpdatedOn'])

    voucher.lines.filter(isDeleted=False).update(isDeleted=True)
    total_amount = 0.0
    for line in current_lines:
        SalesReturnLine.objects.create(
            tenant=voucher.tenant,
            voucher=voucher,
            warehouse=line.warehouse,
            item=line.item,
            batch=line.batch,
            stockBucket=line.stockBucket,
            quantity=line.quantity,
            unitPrice=line.unitPrice,
            lineTotal=line.lineTotal,
            remark=line.remark,
        )
        total_amount += float(line.lineTotal or 0.0)
    voucher.totalAmount = round(total_amount, 2)
    voucher.save(update_fields=['totalAmount', 'lastUpdatedOn'])

    if counter.linkedSalesReturnVoucher_id != voucher.pk:
        counter.linkedSalesReturnVoucher = voucher
        counter.save(update_fields=['linkedSalesReturnVoucher', 'lastUpdatedOn'])

    affected_item_ids = set(prior_item_ids + current_item_ids)
    if affected_item_ids:
        rebuild_inventory_stock(
            counter.tenant,
            affected_item_ids,
        )
        validate_non_negative_inventory(
            counter.tenant,
            affected_item_ids,
            item_bucket_pairs=list(set(prior_pairs + current_pairs)),
        )


def purchase_totals_from_lines(lines):
    subtotal = 0.0
    gst_total = 0.0
    grand_total = 0.0
    for row in lines:
        subtotal += safe_sales_float(row.get('taxableAmount'))
        gst_total += safe_sales_float(row.get('gstAmount'))
        grand_total += safe_sales_float(row.get('lineTotal'))
    return round(subtotal, 2), round(gst_total, 2), round(grand_total, 2)


def normalize_purchase_lines_for_treatment(gst_treatment, lines):
    normalized = []
    force_non_gst = gst_treatment in {'non_gst', 'non_gst_claimable'}
    for row in lines:
        quantity = safe_sales_float(row.get('quantity'))
        unit_cost = safe_sales_float(row.get('unitCost'))
        taxable_amount = round(quantity * unit_cost, 2)
        gst_rate = safe_sales_float(row.get('gstRate'))
        gst_amount = safe_sales_float(row.get('gstAmount'))
        stock_bucket = row.get('stockBucket')
        if force_non_gst:
            gst_rate = 0.0
            gst_amount = 0.0
            stock_bucket = 'non_gst'
        else:
            gst_amount = round(taxable_amount * gst_rate / 100.0, 2)
        line_total = round(taxable_amount + gst_amount, 2)
        normalized.append({
            'itemId': row.get('itemId'),
            'stockBucket': stock_bucket,
            'batchId': row.get('batchId') or row.get('batch_id') or '',
            'batchNumber': row.get('batchNumber') or row.get('batchNo') or row.get('batch') or '',
            'manufactureDate': row.get('manufactureDate') or row.get('mfgDate') or '',
            'expiryDate': row.get('expiryDate') or row.get('expDate') or '',
            'batchMrp': row.get('batchMrp') or row.get('mrp') or '',
            'quantity': quantity,
            'unitCost': unit_cost,
            'taxableAmount': round(taxable_amount, 2),
            'gstRate': round(gst_rate, 2),
            'gstAmount': round(gst_amount, 2),
            'lineTotal': round(line_total, 2),
            'remark': row.get('remark') or '',
        })
    return normalized


def sync_purchase_lines(request, purchase: PurchaseInvoice, lines_payload):
    existing_pairs = list(purchase.lines.filter(isDeleted=False).values_list('item_id', 'stockBucket'))
    existing_item_ids = [item_id for item_id, _bucket in existing_pairs]
    purchase.lines.filter(isDeleted=False).update(isDeleted=True)
    new_item_ids = []
    new_pairs = []
    warehouse = parse_inventory_warehouse(request, request.POST.get('warehouseId'))
    for row in lines_payload:
        item = parse_inventory_item(request, row.get('itemId'))
        quantity = safe_sales_float(row.get('quantity'))
        if quantity <= 0:
            raise ValueError('Purchase quantity must be greater than zero.')
        stock_bucket = normalize_stock_bucket(request, row.get('stockBucket'), item)
        batch = resolve_inventory_batch(request, row, item=item, stock_bucket=stock_bucket, warehouse=warehouse, quantity=quantity, direction='in')
        unit_cost = safe_sales_float(row.get('unitCost'))
        taxable_amount = safe_sales_float(row.get('taxableAmount')) or round(quantity * unit_cost, 2)
        gst_rate = safe_sales_float(row.get('gstRate'))
        gst_amount = safe_sales_float(row.get('gstAmount'))
        line_total = safe_sales_float(row.get('lineTotal')) or round(taxable_amount + gst_amount, 2)
        PurchaseLine.objects.create(
            tenant=purchase.tenant,
            purchaseInvoice=purchase,
            warehouse=warehouse,
            item=item,
            batch=batch,
            stockBucket=stock_bucket,
            quantity=quantity,
            unitCost=unit_cost,
            taxableAmount=taxable_amount,
            gstRate=gst_rate,
            gstAmount=gst_amount,
            lineTotal=line_total,
            remark=(row.get('remark') or '')[:500],
        )
        new_item_ids.append(item.pk)
        new_pairs.append((item.pk, stock_bucket))
    affected_item_ids = set(existing_item_ids + new_item_ids)
    rebuild_inventory_stock(purchase.tenant, affected_item_ids)
    validate_non_negative_inventory(
        purchase.tenant,
        affected_item_ids,
        item_bucket_pairs=list(set(existing_pairs + new_pairs)),
    )
