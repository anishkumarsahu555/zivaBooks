from datetime import datetime

from django.core.cache import cache
from django.db import transaction
from django.http import JsonResponse

from home.cache_keys import inventory_list_cache_key, inventory_list_version_cache_key
from home.inventory import get_or_create_default_warehouse, rebuild_inventory_stock, validate_non_negative_inventory
from home.models import (
    CashCounterLine,
    InventoryAdjustmentLine,
    InventoryAdjustmentVoucher,
    InventoryBatch,
    InventoryBatchStock,
    InventoryLedgerEntry,
    InventoryOpeningLine,
    InventoryOpeningVoucher,
    InventoryTransferLine,
    InventoryTransferVoucher,
    InventoryWarehouse,
    PurchaseLine,
    SalesLine,
    StaffUser,
    TenantSequence,
)
from home.services.collections import has_any_permission
from home.services.inventory_lines import (
    normalize_stock_bucket,
    parse_inventory_item,
    parse_inventory_warehouse,
    request_line_items_payload,
    resolve_inventory_batch,
    safe_local_date,
    safe_sales_float,
)
from home.services.sequences import next_tenant_sequence_value
from home.tenancy import assign_tenant, get_current_tenant, tenant_qs


def _can_manage_inventory_records(user):
    return has_any_permission(user, 'home.can_manage_inventory', 'home.add_inventoryitem', 'home.change_inventoryitem', 'home.can_view_party_credentials')


def _staff_for_request(request):
    return tenant_qs(request, StaffUser).get(user_ID_id=request.user.pk)


def _get_inventory_list_cache_version(resource, request_or_user=None, *, tenant=None):
    version_key = inventory_list_version_cache_key(resource, request_or_user=request_or_user, tenant=tenant)
    version = cache.get(version_key)
    if version is None:
        version = 1
        cache.set(version_key, version, timeout=None)
    return version


def _bump_inventory_list_cache_version(resource, request_or_user=None, *, tenant=None):
    version_key = inventory_list_version_cache_key(resource, request_or_user=request_or_user, tenant=tenant)
    version = cache.get(version_key) or 1
    cache.set(version_key, version + 1, timeout=None)


def _inventory_cache_key(resource, *parts, request_or_user=None, tenant=None):
    version = _get_inventory_list_cache_version(resource, request_or_user=request_or_user, tenant=tenant)
    return inventory_list_cache_key(resource, f'v{version}', *parts, request_or_user=request_or_user, tenant=tenant)


@transaction.atomic
def add_inventory_warehouse_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        warehouse = InventoryWarehouse()
        assign_tenant(warehouse, request)
        warehouse.name = (request.POST.get('name') or '').strip()
        warehouse.code = (request.POST.get('code') or '').strip() or None
        warehouse.address = (request.POST.get('address') or '').strip() or None
        warehouse.isActive = str(request.POST.get('isActive') or '1').lower() not in {'0', 'false', 'no', 'off'}
        warehouse.isDefault = str(request.POST.get('isDefault') or '').lower() in {'1', 'true', 'yes', 'on'}
        if not warehouse.name:
            raise ValueError('Warehouse name is required.')
        warehouse.save()
        if warehouse.isDefault:
            InventoryWarehouse.objects.filter(tenant=warehouse.tenant, isDeleted=False).exclude(pk=warehouse.pk).update(isDefault=False)
        elif not InventoryWarehouse.objects.filter(tenant=warehouse.tenant, isDeleted=False, isDefault=True).exists():
            warehouse.isDefault = True
            warehouse.save(update_fields=['isDefault', 'lastUpdatedOn'])
        _bump_inventory_list_cache_version('warehouses', tenant=warehouse.tenant)
        return JsonResponse({'message': 'success', 'warehouseId': warehouse.pk}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_warehouse_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        warehouse = tenant_qs(request, InventoryWarehouse).get(pk=int(request.POST.get('warehouseId')), isDeleted=False)
        warehouse.name = (request.POST.get('name') or '').strip()
        warehouse.code = (request.POST.get('code') or '').strip() or None
        warehouse.address = (request.POST.get('address') or '').strip() or None
        warehouse.isDefault = str(request.POST.get('isDefault') or '').lower() in {'1', 'true', 'yes', 'on'}
        if not warehouse.name:
            raise ValueError('Warehouse name is required.')
        warehouse.save()
        if warehouse.isDefault:
            InventoryWarehouse.objects.filter(tenant=warehouse.tenant, isDeleted=False).exclude(pk=warehouse.pk).update(isDefault=False)
        _bump_inventory_list_cache_version('warehouses', tenant=warehouse.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_warehouse_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        warehouse = tenant_qs(request, InventoryWarehouse).get(pk=int(request.POST.get('warehouseId')), isDeleted=False)
        replacement = InventoryWarehouse.objects.filter(tenant=warehouse.tenant, isDeleted=False).exclude(pk=warehouse.pk).order_by('-isDefault', 'id').first()
        InventoryOpeningLine.objects.filter(tenant=warehouse.tenant, warehouse=warehouse, isDeleted=False).update(warehouse=replacement)
        PurchaseLine.objects.filter(tenant=warehouse.tenant, warehouse=warehouse, isDeleted=False).update(warehouse=replacement)
        SalesLine.objects.filter(tenant=warehouse.tenant, warehouse=warehouse, isDeleted=False).update(warehouse=replacement)
        CashCounterLine.objects.filter(tenant=warehouse.tenant, warehouse=warehouse, isDeleted=False).update(warehouse=replacement)
        InventoryLedgerEntry.objects.filter(tenant=warehouse.tenant, warehouse=warehouse, isDeleted=False).update(warehouse=replacement)
        warehouse.isDeleted = True
        warehouse.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        if replacement and not InventoryWarehouse.objects.filter(tenant=warehouse.tenant, isDeleted=False, isDefault=True).exists():
            replacement.isDefault = True
            replacement.save(update_fields=['isDefault', 'lastUpdatedOn'])
        _bump_inventory_list_cache_version('warehouses', tenant=warehouse.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_warehouses_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    tenant = get_current_tenant(request)
    cache_key = _inventory_cache_key('warehouses', 'active', request_or_user=request, tenant=tenant)
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse({'message': 'success', 'data': cached}, safe=False)
    warehouses = InventoryWarehouse.objects.filter(tenant=tenant, isDeleted=False, isActive=True).order_by('-isDefault', 'name')
    if not warehouses.exists():
        get_or_create_default_warehouse(tenant)
        warehouses = InventoryWarehouse.objects.filter(tenant=tenant, isDeleted=False, isActive=True).order_by('-isDefault', 'name')
    payload = [{
        'ID': warehouse.pk,
        'Name': warehouse.name,
        'Code': warehouse.code or '',
        'Address': warehouse.address or '',
        'IsDefault': bool(warehouse.isDefault),
    } for warehouse in warehouses]
    cache.set(cache_key, payload, timeout=60 * 30)
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


@transaction.atomic
def deactivate_expired_zero_stock_batches_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    tenant = get_current_tenant(request)
    today = safe_local_date()
    batch_ids_with_stock = InventoryBatchStock.objects.filter(
        tenant=tenant,
        isDeleted=False,
        quantityOnHand__gt=0,
    ).values('batch_id')
    updated = InventoryBatch.objects.filter(
        tenant=tenant,
        isDeleted=False,
        isActive=True,
        expiryDate__lt=today,
        quantityOnHand__lte=0,
    ).exclude(pk__in=batch_ids_with_stock).update(isActive=False)
    _bump_inventory_list_cache_version('items', tenant=tenant)
    return JsonResponse({'message': 'success', 'deactivatedCount': updated})


@transaction.atomic
def add_inventory_opening_stock_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        lines_payload = request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one opening stock line is required.')
        voucher = InventoryOpeningVoucher()
        assign_tenant(voucher, request)
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        voucher.remark = request.POST.get('remark')
        voucher.createdBy = _staff_for_request(request)
        voucher.save()
        voucher.voucherNumber = next_tenant_sequence_value(request, TenantSequence.Scope.INVENTORY_OPENING)
        voucher.save(update_fields=['voucherNumber', 'lastUpdatedOn'])
        warehouse = parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        item_ids = []
        for row in lines_payload:
            item = parse_inventory_item(request, row.get('itemId'))
            quantity = safe_sales_float(row.get('quantity'))
            if quantity <= 0:
                raise ValueError('Opening quantity must be greater than zero.')
            stock_bucket = normalize_stock_bucket(request, row.get('stockBucket'), item)
            unit_cost = safe_sales_float(row.get('unitCost') or row.get('unitPrice'))
            line_total = safe_sales_float(row.get('lineTotal')) or round(quantity * unit_cost, 2)
            batch = resolve_inventory_batch(request, row, item=item, stock_bucket=stock_bucket, warehouse=warehouse, quantity=quantity, direction='in')
            InventoryOpeningLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                warehouse=warehouse,
                item=item,
                batch=batch,
                stockBucket=stock_bucket,
                quantity=quantity,
                unitCost=unit_cost,
                lineTotal=line_total,
                remark=(row.get('remark') or '')[:500],
            )
            item_ids.append(item.pk)
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success', 'voucherNumber': voucher.voucherNumber}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_opening_vouchers_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    rows = tenant_qs(request, InventoryOpeningVoucher).filter(isDeleted=False).select_related('createdBy').order_by('-entryDate', '-id')[:100]
    payload = [{
        'ID': voucher.pk,
        'VoucherNumber': voucher.voucherNumber or '',
        'EntryDate': voucher.entryDate.strftime('%d/%m/%Y') if voucher.entryDate else '',
        'Remark': voucher.remark or '',
        'CreatedBy': voucher.createdBy.name if voucher.createdBy_id else '-',
        'LineCount': voucher.lines.filter(isDeleted=False).count(),
    } for voucher in rows]
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def _voucher_line_rows_from_payload(request, lines_payload, *, quantity_field='quantity', rate_field='unitCost', batch_direction='in', warehouse=None):
    rows = []
    item_ids = []
    for row in lines_payload:
        item = parse_inventory_item(request, row.get('itemId'))
        quantity = safe_sales_float(row.get(quantity_field))
        if quantity <= 0:
            raise ValueError('Voucher quantity must be greater than zero.')
        stock_bucket = normalize_stock_bucket(request, row.get('stockBucket'), item)
        unit_rate = safe_sales_float(row.get(rate_field) or row.get('unitPrice') or row.get('unitCost'))
        line_total = safe_sales_float(row.get('lineTotal')) or round(quantity * unit_rate, 2)
        batch = resolve_inventory_batch(request, row, item=item, stock_bucket=stock_bucket, warehouse=warehouse, quantity=quantity, direction=batch_direction)
        rows.append({
            'item': item,
            'batch': batch,
            'quantity': quantity,
            'stockBucket': stock_bucket,
            'unitRate': unit_rate,
            'lineTotal': line_total,
            'remark': (row.get('remark') or '')[:500],
        })
        item_ids.append(item.pk)
    return rows, item_ids


def _serialize_voucher_lines(lines_qs, *, quantity_field='quantity', rate_field='unitCost'):
    payload = []
    for line in lines_qs.select_related('item', 'batch').order_by('id'):
        payload.append({
            'itemId': line.item_id,
            'itemName': line.item.name,
            'batchId': line.batch_id,
            'batchNumber': line.batch.batchNumber if line.batch_id else '',
            'manufactureDate': line.batch.manufactureDate.strftime('%d/%m/%Y') if line.batch_id and line.batch.manufactureDate else '',
            'expiryDate': line.batch.expiryDate.strftime('%d/%m/%Y') if line.batch_id and line.batch.expiryDate else '',
            'batchMrp': float(line.batch.mrp or 0.0) if line.batch_id else 0.0,
            'stockBucket': line.stockBucket,
            'quantity': float(getattr(line, quantity_field) or 0.0),
            'unitCost': float(getattr(line, rate_field) or 0.0),
            'unitPrice': float(getattr(line, rate_field) or 0.0),
            'lineTotal': float(line.lineTotal or 0.0),
            'remark': line.remark or '',
        })
    return payload


def _save_adjustment_lines(request, voucher, lines_payload, warehouse, direction):
    batch_direction = 'out' if direction == 'decrease' else 'in'
    rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitCost', batch_direction=batch_direction, warehouse=warehouse)
    for row in rows:
        quantity_delta = row['quantity']
        if direction == 'decrease':
            quantity_delta = -quantity_delta
        InventoryAdjustmentLine.objects.create(
            tenant=voucher.tenant,
            voucher=voucher,
            warehouse=warehouse,
            item=row['item'],
            batch=row['batch'],
            stockBucket=row['stockBucket'],
            quantityDelta=quantity_delta,
            unitCost=row['unitRate'],
            lineTotal=row['lineTotal'] if quantity_delta >= 0 else -row['lineTotal'],
            remark=row['remark'],
        )
    return item_ids


@transaction.atomic
def add_inventory_adjustment_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        lines_payload = request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one adjustment line is required.')
        voucher = InventoryAdjustmentVoucher()
        assign_tenant(voucher, request)
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        voucher.remark = request.POST.get('remark')
        voucher.createdBy = _staff_for_request(request)
        voucher.save()
        voucher.voucherNumber = next_tenant_sequence_value(request, TenantSequence.Scope.INVENTORY_ADJUSTMENT)
        voucher.save(update_fields=['voucherNumber', 'lastUpdatedOn'])
        warehouse = parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        direction = str(request.POST.get('direction') or 'increase').lower()
        item_ids = _save_adjustment_lines(request, voucher, lines_payload, warehouse, direction)
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success', 'voucherId': voucher.pk, 'voucherNumber': voucher.voucherNumber}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_adjustment_vouchers_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    rows = tenant_qs(request, InventoryAdjustmentVoucher).filter(isDeleted=False).order_by('-entryDate', '-id')[:100]
    payload = [{
        'ID': row.pk,
        'VoucherNumber': row.voucherNumber or '',
        'EntryDate': row.entryDate.strftime('%d/%m/%Y') if row.entryDate else '',
        'Remark': row.remark or '',
        'LineCount': row.lines.filter(isDeleted=False).count(),
    } for row in rows]
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def get_inventory_adjustment_voucher_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, InventoryAdjustmentVoucher).get(pk=int(request.GET.get('voucherId')), isDeleted=False)
        lines = _serialize_voucher_lines(voucher.lines.filter(isDeleted=False), quantity_field='quantityDelta', rate_field='unitCost')
        for line in lines:
            line['quantity'] = abs(float(line.get('quantity') or 0.0))
        return JsonResponse({'message': 'success', 'data': {
            'ID': voucher.pk,
            'VoucherNumber': voucher.voucherNumber or '',
            'EntryDate': voucher.entryDate.strftime('%d/%m/%Y') if voucher.entryDate else '',
            'WarehouseID': voucher.lines.filter(isDeleted=False).values_list('warehouse_id', flat=True).first(),
            'Direction': 'decrease' if voucher.lines.filter(isDeleted=False, quantityDelta__lt=0).exists() else 'increase',
            'Remark': voucher.remark or '',
            'Lines': lines,
        }}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_adjustment_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, InventoryAdjustmentVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        prior_item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        lines_payload = request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one adjustment line is required.')
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        voucher.remark = request.POST.get('remark')
        voucher.save(update_fields=['entryDate', 'remark', 'lastUpdatedOn'])
        voucher.lines.filter(isDeleted=False).update(isDeleted=True)
        warehouse = parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        direction = str(request.POST.get('direction') or 'increase').lower()
        item_ids = _save_adjustment_lines(request, voucher, lines_payload, warehouse, direction)
        affected = list(set(prior_item_ids + item_ids))
        rebuild_inventory_stock(voucher.tenant, affected)
        validate_non_negative_inventory(voucher.tenant, affected)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_adjustment_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, InventoryAdjustmentVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        voucher.isDeleted = True
        voucher.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def _save_transfer_lines(request, voucher, lines_payload):
    rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitCost', batch_direction='out', warehouse=voucher.fromWarehouse)
    for row in rows:
        InventoryTransferLine.objects.create(
            tenant=voucher.tenant,
            voucher=voucher,
            item=row['item'],
            batch=row['batch'],
            stockBucket=row['stockBucket'],
            quantity=row['quantity'],
            unitCost=row['unitRate'],
            lineTotal=row['lineTotal'],
            remark=row['remark'],
        )
    return item_ids


@transaction.atomic
def add_inventory_transfer_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        lines_payload = request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one transfer line is required.')
        voucher = InventoryTransferVoucher()
        assign_tenant(voucher, request)
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        voucher.fromWarehouse = parse_inventory_warehouse(request, request.POST.get('fromWarehouseId'))
        voucher.toWarehouse = parse_inventory_warehouse(request, request.POST.get('toWarehouseId'))
        if voucher.fromWarehouse_id == voucher.toWarehouse_id:
            raise ValueError('From and To warehouse cannot be same.')
        voucher.remark = request.POST.get('remark')
        voucher.createdBy = _staff_for_request(request)
        voucher.save()
        voucher.voucherNumber = next_tenant_sequence_value(request, TenantSequence.Scope.INVENTORY_TRANSFER)
        voucher.save(update_fields=['voucherNumber', 'lastUpdatedOn'])
        item_ids = _save_transfer_lines(request, voucher, lines_payload)
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success', 'voucherId': voucher.pk, 'voucherNumber': voucher.voucherNumber}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_transfer_vouchers_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    rows = tenant_qs(request, InventoryTransferVoucher).filter(isDeleted=False).select_related('fromWarehouse', 'toWarehouse').order_by('-entryDate', '-id')[:100]
    payload = [{
        'ID': row.pk,
        'VoucherNumber': row.voucherNumber or '',
        'EntryDate': row.entryDate.strftime('%d/%m/%Y') if row.entryDate else '',
        'FromWarehouse': row.fromWarehouse.name if row.fromWarehouse_id else '-',
        'ToWarehouse': row.toWarehouse.name if row.toWarehouse_id else '-',
        'Remark': row.remark or '',
        'LineCount': row.lines.filter(isDeleted=False).count(),
    } for row in rows]
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def get_inventory_transfer_voucher_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, InventoryTransferVoucher).select_related('fromWarehouse', 'toWarehouse').get(pk=int(request.GET.get('voucherId')), isDeleted=False)
        return JsonResponse({'message': 'success', 'data': {
            'ID': voucher.pk,
            'VoucherNumber': voucher.voucherNumber or '',
            'EntryDate': voucher.entryDate.strftime('%d/%m/%Y') if voucher.entryDate else '',
            'FromWarehouseID': voucher.fromWarehouse_id,
            'ToWarehouseID': voucher.toWarehouse_id,
            'Remark': voucher.remark or '',
            'Lines': _serialize_voucher_lines(voucher.lines.filter(isDeleted=False), quantity_field='quantity', rate_field='unitCost'),
        }}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_transfer_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, InventoryTransferVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        prior_item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        lines_payload = request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one transfer line is required.')
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        voucher.fromWarehouse = parse_inventory_warehouse(request, request.POST.get('fromWarehouseId'))
        voucher.toWarehouse = parse_inventory_warehouse(request, request.POST.get('toWarehouseId'))
        if voucher.fromWarehouse_id == voucher.toWarehouse_id:
            raise ValueError('From and To warehouse cannot be same.')
        voucher.remark = request.POST.get('remark')
        voucher.save(update_fields=['entryDate', 'fromWarehouse', 'toWarehouse', 'remark', 'lastUpdatedOn'])
        voucher.lines.filter(isDeleted=False).update(isDeleted=True)
        item_ids = _save_transfer_lines(request, voucher, lines_payload)
        affected = list(set(prior_item_ids + item_ids))
        rebuild_inventory_stock(voucher.tenant, affected)
        validate_non_negative_inventory(voucher.tenant, affected)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_transfer_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, InventoryTransferVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        voucher.isDeleted = True
        voucher.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
