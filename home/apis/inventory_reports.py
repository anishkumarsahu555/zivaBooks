from collections import defaultdict
from datetime import timedelta

from django.http import JsonResponse
from django.urls import reverse

from home.inventory import stock_snapshot_for_tenant
from home.models import (
    CashCounterLine,
    InventoryAdjustmentLine,
    InventoryBatch,
    InventoryBatchStock,
    InventoryLedgerEntry,
    InventoryTransferLine,
    PurchaseLine,
    PurchaseReturnLine,
    SalesLine,
    SalesReturnLine,
)
from home.services.collections import has_any_permission
from home.services.inventory_lines import (
    available_batch_stock_queryset,
    normalize_stock_bucket,
    parse_inventory_item,
    parse_inventory_warehouse,
    safe_local_date,
)
from utils.logger import logger
from home.tenancy import get_current_tenant, tenant_qs


def _can_view_inventory_reports(user):
    return has_any_permission(
        user,
        'home.view_inventoryitem',
        'home.can_manage_inventory',
        'home.can_view_party_credentials',
    )


def get_inventory_snapshot_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    tenant = get_current_tenant(request)
    return JsonResponse({'message': 'success', 'data': stock_snapshot_for_tenant(tenant)}, safe=False)


def list_inventory_batches_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_view_inventory_reports(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    item = parse_inventory_item(request, request.GET.get('itemId'))
    stock_bucket = normalize_stock_bucket(request, request.GET.get('stockBucket'), item)
    warehouse = parse_inventory_warehouse(request, request.GET.get('warehouseId'))
    today = safe_local_date()
    stock_rows = available_batch_stock_queryset(
        item=item,
        stock_bucket=stock_bucket,
        warehouse=warehouse,
        today=today,
    ).select_related('batch', 'warehouse')
    payload = []
    for row in stock_rows:
        batch = row.batch
        payload.append({
            'ID': batch.pk,
            'BatchNumber': batch.batchNumber or '',
            'ManufactureDate': batch.manufactureDate.strftime('%d/%m/%Y') if batch.manufactureDate else '',
            'ExpiryDate': batch.expiryDate.strftime('%d/%m/%Y') if batch.expiryDate else '',
            'MRP': float(batch.mrp or 0.0),
            'PurchaseRate': float(batch.purchaseRate or 0.0),
            'QuantityOnHand': float(row.quantityOnHand or 0.0),
            'WarehouseID': row.warehouse_id,
            'WarehouseName': row.warehouse.name if row.warehouse_id else '',
        })
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def list_inventory_ledger_entries_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    tenant = get_current_tenant(request)
    rows = InventoryLedgerEntry.objects.filter(
        tenant=tenant,
        isDeleted=False,
        item__isDeleted=False,
    ).select_related('item', 'warehouse').order_by('-entryDate', '-id')[:200]
    source_ids = defaultdict(list)
    for row in rows:
        if row.source_object_id:
            source_ids[row.sourceType].append(row.source_object_id)

    detail_map = {}
    if source_ids.get(InventoryLedgerEntry.SourceType.PURCHASE):
        for line in PurchaseLine.objects.filter(pk__in=source_ids[InventoryLedgerEntry.SourceType.PURCHASE]).values('id', 'purchaseInvoice_id'):
            detail_map[(InventoryLedgerEntry.SourceType.PURCHASE, line['id'])] = {
                'DetailUrl': reverse('homeApp:purchase_detail', args=[line['purchaseInvoice_id']]),
                'DetailLabel': 'View Purchase',
            }
    if source_ids.get(InventoryLedgerEntry.SourceType.SALES):
        for line in SalesLine.objects.filter(pk__in=source_ids[InventoryLedgerEntry.SourceType.SALES]).values('id', 'sales_id'):
            detail_map[(InventoryLedgerEntry.SourceType.SALES, line['id'])] = {
                'DetailUrl': reverse('homeApp:edit_sales', args=[line['sales_id']]),
                'DetailLabel': 'View Sale',
            }
    if source_ids.get(InventoryLedgerEntry.SourceType.CASH_COUNTER):
        for line in CashCounterLine.objects.filter(pk__in=source_ids[InventoryLedgerEntry.SourceType.CASH_COUNTER]).values('id', 'cashCounter_id'):
            detail_map[(InventoryLedgerEntry.SourceType.CASH_COUNTER, line['id'])] = {
                'DetailUrl': reverse('homeApp:edit_cash_counter', args=[line['cashCounter_id']]),
                'DetailLabel': 'View Counter',
            }
    if source_ids.get(InventoryLedgerEntry.SourceType.ADJUSTMENT):
        for line in InventoryAdjustmentLine.objects.filter(pk__in=source_ids[InventoryLedgerEntry.SourceType.ADJUSTMENT]).values('id', 'voucher_id'):
            detail_map[(InventoryLedgerEntry.SourceType.ADJUSTMENT, line['id'])] = {
                'DetailAction': 'adjustment',
                'DetailId': line['voucher_id'],
                'DetailLabel': 'View Adjustment',
            }
    transfer_source_ids = source_ids.get(InventoryLedgerEntry.SourceType.TRANSFER_OUT, []) + source_ids.get(InventoryLedgerEntry.SourceType.TRANSFER_IN, [])
    if transfer_source_ids:
        for line in InventoryTransferLine.objects.filter(pk__in=transfer_source_ids).values('id', 'voucher_id'):
            detail_map[(InventoryLedgerEntry.SourceType.TRANSFER_OUT, line['id'])] = {
                'DetailAction': 'transfer',
                'DetailId': line['voucher_id'],
                'DetailLabel': 'View Transfer',
            }
            detail_map[(InventoryLedgerEntry.SourceType.TRANSFER_IN, line['id'])] = {
                'DetailAction': 'transfer',
                'DetailId': line['voucher_id'],
                'DetailLabel': 'View Transfer',
            }
    if source_ids.get(InventoryLedgerEntry.SourceType.PURCHASE_RETURN):
        for line in PurchaseReturnLine.objects.filter(pk__in=source_ids[InventoryLedgerEntry.SourceType.PURCHASE_RETURN]).values('id', 'voucher_id'):
            detail_map[(InventoryLedgerEntry.SourceType.PURCHASE_RETURN, line['id'])] = {
                'DetailAction': 'purchase_return',
                'DetailId': line['voucher_id'],
                'DetailLabel': 'View Purchase Return',
            }
    if source_ids.get(InventoryLedgerEntry.SourceType.SALES_RETURN):
        for line in SalesReturnLine.objects.filter(pk__in=source_ids[InventoryLedgerEntry.SourceType.SALES_RETURN]).values('id', 'voucher_id'):
            detail_map[(InventoryLedgerEntry.SourceType.SALES_RETURN, line['id'])] = {
                'DetailAction': 'sales_return',
                'DetailId': line['voucher_id'],
                'DetailLabel': 'View Sales Return',
            }
    payload = [{
        'ID': row.pk,
        'EntryDate': row.entryDate.strftime('%d/%m/%Y') if row.entryDate else '',
        'EntryDateSort': row.entryDate.isoformat() if row.entryDate else '',
        'ItemName': row.item.name,
        'WarehouseName': row.warehouse.name if row.warehouse_id else '',
        'StockBucket': row.stockBucket,
        'SourceType': row.sourceType,
        'QuantityDelta': float(row.quantityDelta or 0.0),
        'UnitRate': float(row.unitRate or 0.0),
        'TotalValue': float(row.totalValue or 0.0),
        'ReferenceNumber': row.referenceNumber or '',
        'Remark': row.remark or '',
        'DetailUrl': (detail_map.get((row.sourceType, row.source_object_id)) or {}).get('DetailUrl', ''),
        'DetailAction': (detail_map.get((row.sourceType, row.source_object_id)) or {}).get('DetailAction', ''),
        'DetailId': (detail_map.get((row.sourceType, row.source_object_id)) or {}).get('DetailId', ''),
        'DetailLabel': (detail_map.get((row.sourceType, row.source_object_id)) or {}).get('DetailLabel', ''),
    } for row in rows]
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def list_inventory_batch_report_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_view_inventory_reports(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    tenant = get_current_tenant(request)
    today = safe_local_date()
    warning_days = int(request.GET.get('days') or 30)
    warning_cutoff = today + timedelta(days=max(0, warning_days))
    rows = InventoryBatchStock.objects.filter(
        tenant=tenant,
        isDeleted=False,
        batch__isDeleted=False,
        item__isDeleted=False,
        quantityOnHand__gt=0,
    ).select_related('batch', 'item', 'warehouse').order_by('batch__expiryDate', 'item__name', 'batch__batchNumber')
    payload = []
    summary = {
        'TotalBatchRows': 0,
        'ExpiredBatchRows': 0,
        'ExpiringBatchRows': 0,
        'HealthyBatchRows': 0,
        'ExpiredQuantity': 0.0,
        'ExpiringQuantity': 0.0,
        'HealthyQuantity': 0.0,
        'WarningDays': max(0, warning_days),
    }
    for row in rows:
        expiry_date = row.batch.expiryDate
        if expiry_date and expiry_date < today:
            status = 'Expired'
        elif expiry_date and expiry_date <= warning_cutoff:
            status = 'Expiring Soon'
        else:
            status = 'Healthy'
        quantity = float(row.quantityOnHand or 0.0)
        summary['TotalBatchRows'] += 1
        if status == 'Expired':
            summary['ExpiredBatchRows'] += 1
            summary['ExpiredQuantity'] += quantity
        elif status == 'Expiring Soon':
            summary['ExpiringBatchRows'] += 1
            summary['ExpiringQuantity'] += quantity
        else:
            summary['HealthyBatchRows'] += 1
            summary['HealthyQuantity'] += quantity
        payload.append({
            'BatchID': row.batch_id,
            'ItemID': row.item_id,
            'ItemName': row.item.name,
            'BatchNumber': row.batch.batchNumber or '',
            'WarehouseName': row.warehouse.name if row.warehouse_id else '',
            'StockBucket': row.stockBucket,
            'QuantityOnHand': quantity,
            'ManufactureDate': row.batch.manufactureDate.strftime('%d/%m/%Y') if row.batch.manufactureDate else '',
            'ExpiryDate': expiry_date.strftime('%d/%m/%Y') if expiry_date else '',
            'ExpiryDateSort': expiry_date.isoformat() if expiry_date else '',
            'DaysToExpiry': (expiry_date - today).days if expiry_date else None,
            'MRP': float(row.batch.mrp or 0.0),
            'PurchaseRate': float(row.batch.purchaseRate or 0.0),
            'Status': status,
        })
    for key in ('ExpiredQuantity', 'ExpiringQuantity', 'HealthyQuantity'):
        summary[key] = round(summary[key], 4)
    return JsonResponse({'message': 'success', 'data': payload, 'summary': summary}, safe=False)


def list_inventory_batch_ledger_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_view_inventory_reports(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    raw_batch_id = str(request.GET.get('batchId') or '').strip()
    if not raw_batch_id.isdigit():
        return JsonResponse({'message': 'error', 'error': 'Invalid batch selection.'}, status=400)
    batch = tenant_qs(request, InventoryBatch).filter(pk=int(raw_batch_id), isDeleted=False).select_related('item').first()
    if batch is None:
        return JsonResponse({'message': 'error', 'error': 'Batch not found.'}, status=404)
    rows = list(InventoryLedgerEntry.objects.filter(
        tenant=batch.tenant,
        isDeleted=False,
        batch=batch,
    ).select_related('warehouse').order_by('entryDate', 'id'))
    stock_rows = list(InventoryBatchStock.objects.filter(
        tenant=batch.tenant,
        isDeleted=False,
        batch=batch,
        quantityOnHand__gt=0,
    ).select_related('warehouse').order_by('warehouse__name', 'stockBucket'))
    purchase_lines = list(PurchaseLine.objects.filter(
        tenant=batch.tenant,
        isDeleted=False,
        batch=batch,
        purchaseInvoice__isDeleted=False,
    ).select_related('purchaseInvoice', 'purchaseInvoice__partyID').order_by('purchaseInvoice__buildDate', 'id')[:10])
    running = 0.0
    payload = []
    for row in rows:
        running += float(row.quantityDelta or 0.0)
        payload.append({
            'ID': row.pk,
            'EntryDate': row.entryDate.strftime('%d/%m/%Y') if row.entryDate else '',
            'EntryDateSort': row.entryDate.isoformat() if row.entryDate else '',
            'WarehouseName': row.warehouse.name if row.warehouse_id else '',
            'SourceType': row.sourceType,
            'QuantityDelta': float(row.quantityDelta or 0.0),
            'RunningBalance': round(running, 4),
            'UnitRate': float(row.unitRate or 0.0),
            'TotalValue': float(row.totalValue or 0.0),
            'ReferenceNumber': row.referenceNumber or '',
            'Remark': row.remark or '',
        })
    warehouse_payload = [{
        'WarehouseID': row.warehouse_id,
        'WarehouseName': row.warehouse.name if row.warehouse_id else '',
        'StockBucket': row.stockBucket,
        'QuantityOnHand': float(row.quantityOnHand or 0.0),
    } for row in stock_rows]
    purchase_payload = [{
        'PurchaseID': line.purchaseInvoice_id,
        'PurchaseDisplayID': line.purchaseInvoice.purchaseID or '',
        'InvoiceNumber': line.purchaseInvoice.invoiceNumber or '',
        'InvoiceDate': line.purchaseInvoice.buildDate.strftime('%d/%m/%Y') if line.purchaseInvoice.buildDate else '',
        'Party': line.purchaseInvoice.partyID.name if line.purchaseInvoice.partyID_id else '',
        'Quantity': float(line.quantity or 0.0),
        'UnitCost': float(line.unitCost or 0.0),
        'LineTotal': float(line.lineTotal or 0.0),
    } for line in purchase_lines]
    today = safe_local_date()
    if batch.expiryDate and batch.expiryDate < today:
        status = 'Expired'
    elif batch.expiryDate and batch.expiryDate <= today + timedelta(days=30):
        status = 'Expiring Soon'
    else:
        status = 'Healthy'
    return JsonResponse({'message': 'success', 'data': {
        'BatchID': batch.pk,
        'ItemName': batch.item.name,
        'BatchNumber': batch.batchNumber or '',
        'ManufactureDate': batch.manufactureDate.strftime('%d/%m/%Y') if batch.manufactureDate else '',
        'ExpiryDate': batch.expiryDate.strftime('%d/%m/%Y') if batch.expiryDate else '',
        'DaysToExpiry': (batch.expiryDate - today).days if batch.expiryDate else None,
        'Status': status,
        'QuantityOnHand': float(batch.quantityOnHand or 0.0),
        'MRP': float(batch.mrp or 0.0),
        'PurchaseRate': float(batch.purchaseRate or 0.0),
        'WarehouseStocks': warehouse_payload,
        'PurchaseSources': purchase_payload,
        'Rows': payload,
    }}, safe=False)
