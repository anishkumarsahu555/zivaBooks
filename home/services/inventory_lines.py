import json
from datetime import datetime

from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone
from django.utils.dateparse import parse_date

from utils.logger import logger
from home.inventory import get_or_create_default_warehouse
from home.models import InventoryBatch, InventoryBatchStock, InventoryItem, InventoryWarehouse
from home.tenancy import get_current_tenant, tenant_qs


def non_gst_shadow_enabled(request_or_user) -> bool:
    tenant = get_current_tenant(request_or_user)
    if tenant is None:
        return True
    return bool(getattr(tenant, 'nonGstShadowEnabled', False))


def inventory_feature_enabled(request_or_user) -> bool:
    tenant = get_current_tenant(request_or_user)
    if tenant is None:
        return True
    return bool(getattr(tenant, 'inventoryEnabled', True))


def advanced_invoicing_enabled(request_or_user) -> bool:
    tenant = get_current_tenant(request_or_user)
    if tenant is None:
        return True
    return bool(getattr(tenant, 'advancedInvoicingEnabled', True))


def assert_inventory_enabled(request, *, label='Inventory feature'):
    if inventory_feature_enabled(request):
        return
    raise ValueError(f'{label} is disabled for this tenant. Ask an admin to enable Inventory from Billing > Non-GST Control.')


def assert_advanced_invoicing_enabled(request, *, label='Advanced invoicing'):
    if advanced_invoicing_enabled(request):
        return
    raise ValueError(f'{label} is disabled for this tenant. Ask an admin to enable Invoicing from Billing > Non-GST Control.')


def assert_non_gst_enabled(request, *, label='Non-GST mode'):
    if non_gst_shadow_enabled(request):
        return
    raise ValueError(f'{label} is disabled for this tenant. Ask an admin to enable it from Billing > Non-GST Control.')


def safe_sales_float(value) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def safe_local_date():
    now_value = timezone.now()
    return timezone.localtime(now_value).date() if timezone.is_aware(now_value) else now_value.date()


def request_line_items_present(request) -> bool:
    return 'lineItems' in request.POST


def request_line_items_payload(request):
    raw = request.POST.get('lineItems')
    if raw is None or str(raw).strip() == '':
        return []
    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise ValueError('Invalid lineItems payload.') from exc
    if not isinstance(payload, list):
        raise ValueError('lineItems payload must be a list.')
    return payload


def line_items_total(request) -> float:
    total = 0.0
    for row in request_line_items_payload(request):
        total += safe_sales_float(row.get('lineTotal'))
    return round(total, 2)


def sales_totals_from_lines(lines):
    taxable_total = 0.0
    gst_total = 0.0
    grand_total = 0.0
    for row in lines:
        taxable_total += safe_sales_float(row.get('taxableAmount'))
        gst_total += safe_sales_float(row.get('gstAmount'))
        grand_total += safe_sales_float(row.get('lineTotal'))
    return round(taxable_total, 2), round(gst_total, 2), round(grand_total, 2)


def sales_round_off_from_total(total_amount):
    total_amount = round(safe_sales_float(total_amount), 2)
    rounded_total = float(round(total_amount))
    round_off = round(rounded_total - total_amount, 2)
    return round_off, round(total_amount + round_off, 2)


def validate_inventory_quantity_for_unit(item: InventoryItem, quantity: float, *, label='Quantity'):
    if quantity <= 0:
        return
    unit = getattr(item, 'unit', None)
    allows_decimal = True if unit is None else bool(getattr(unit, 'allowsDecimal', False))
    if allows_decimal:
        return
    if abs(quantity - round(quantity)) > 0.000001:
        unit_label = getattr(unit, 'symbol', None) or item.unitName or 'unit'
        raise ValueError(f'{label} for {item.name} must be a whole number because unit {unit_label} does not allow decimals.')


def parse_inventory_item(request, raw_item_id):
    assert_inventory_enabled(request)
    raw = str(raw_item_id or '').strip()
    if not raw.isdigit():
        raise ValueError('Invalid inventory item.')
    item = tenant_qs(request, InventoryItem).filter(isDeleted=False, pk=int(raw)).first()
    if item is None:
        raise ValueError('Inventory item not found.')
    return item


def parse_inventory_warehouse(request, raw_warehouse_id=None):
    assert_inventory_enabled(request)
    tenant = get_current_tenant(request)
    raw = str(raw_warehouse_id or '').strip()
    if not raw:
        return get_or_create_default_warehouse(tenant)
    if not raw.isdigit():
        raise ValueError('Invalid warehouse selection.')
    warehouse = InventoryWarehouse.objects.filter(
        tenant=tenant,
        isDeleted=False,
        isActive=True,
        pk=int(raw),
    ).first()
    if warehouse is None:
        raise ValueError('Warehouse not found.')
    return warehouse


def normalize_stock_bucket(request, raw_bucket, item: InventoryItem) -> str:
    bucket = str(raw_bucket or '').strip().lower()
    valid = {choice[0] for choice in InventoryItem.StockBucket.choices}
    if bucket in valid:
        if bucket == InventoryItem.StockBucket.NON_GST:
            assert_non_gst_enabled(request, label='Non-GST stock bucket')
        return bucket
    bucket = item.defaultStockBucket or InventoryItem.StockBucket.GST
    if bucket == InventoryItem.StockBucket.NON_GST:
        assert_non_gst_enabled(request, label='Non-GST stock bucket')
    return bucket


def parse_inventory_batch_date(value):
    raw = str(value or '').strip()
    if not raw:
        return None
    parsed = parse_date(raw)
    if parsed:
        return parsed
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%d-%m-%Y'):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError('Invalid batch date. Use DD/MM/YYYY.')


def available_batch_stock_queryset(*, item, stock_bucket, warehouse=None, today=None):
    today = today or safe_local_date()
    qs = InventoryBatchStock.objects.filter(
        tenant=item.tenant,
        isDeleted=False,
        item=item,
        stockBucket=stock_bucket,
        quantityOnHand__gt=0,
        batch__isDeleted=False,
        batch__isActive=True,
    ).filter(
        Q(batch__expiryDate__isnull=True) | Q(batch__expiryDate__gte=today)
    ).annotate(
        expiry_missing=Case(
            When(batch__expiryDate__isnull=True, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    ).order_by('expiry_missing', 'batch__expiryDate', 'batch__batchNumber', 'batch_id')
    if warehouse is not None:
        qs = qs.filter(warehouse=warehouse)
    return qs


def earliest_available_batch_for_fefo(*, item, stock_bucket, warehouse=None, today=None):
    row = available_batch_stock_queryset(
        item=item,
        stock_bucket=stock_bucket,
        warehouse=warehouse,
        today=today,
    ).select_related('batch').first()
    return row.batch if row else None


def resolve_inventory_batch(request, row, *, item, stock_bucket, warehouse=None, quantity=0.0, direction='in'):
    raw_batch_id = str(row.get('batchId') or row.get('batch_id') or '').strip()
    batch_number = str(row.get('batchNumber') or row.get('batchNo') or row.get('batch') or '').strip()
    manufacture_date = parse_inventory_batch_date(row.get('manufactureDate') or row.get('mfgDate'))
    expiry_date = parse_inventory_batch_date(row.get('expiryDate') or row.get('expDate'))
    purchase_rate = safe_sales_float(row.get('purchaseRate') or row.get('unitCost') or row.get('unitPrice'))
    mrp = safe_sales_float(row.get('batchMrp') or row.get('mrp'))
    if manufacture_date and expiry_date and expiry_date < manufacture_date:
        raise ValueError(f'Expiry date cannot be before manufacture date for {item.name}.')

    if raw_batch_id:
        if not raw_batch_id.isdigit():
            raise ValueError('Invalid batch selection.')
        batch = tenant_qs(request, InventoryBatch).filter(isDeleted=False, pk=int(raw_batch_id), item=item).first()
        if batch is None:
            raise ValueError('Selected batch was not found.')
        if direction == 'out' and batch.expiryDate and batch.expiryDate < safe_local_date():
            raise ValueError(f'Batch {batch.batchNumber or batch.pk} for {item.name} is expired.')
        if direction == 'out' and warehouse is not None:
            available = InventoryBatchStock.objects.filter(
                tenant=item.tenant,
                isDeleted=False,
                batch=batch,
                warehouse=warehouse,
            ).values_list('quantityOnHand', flat=True).first()
            if available is None or float(available or 0.0) <= 0:
                raise ValueError(f'Batch {batch.batchNumber or batch.pk} for {item.name} is not available in {warehouse.name}.')
        if direction == 'out' and item.requiresBatchTracking:
            fefo_batch = earliest_available_batch_for_fefo(item=item, stock_bucket=stock_bucket, warehouse=warehouse)
            if fefo_batch is not None and fefo_batch.pk != batch.pk:
                raise ValueError(
                    f'FEFO requires selling batch {fefo_batch.batchNumber or fefo_batch.pk} for {item.name} before {batch.batchNumber or batch.pk}.'
                )
        return batch

    has_batch_details = bool(batch_number or manufacture_date or expiry_date)
    if direction == 'out':
        if not has_batch_details and not item.requiresBatchTracking:
            return None
        batch_stock_qs = available_batch_stock_queryset(
            item=item,
            stock_bucket=stock_bucket,
            warehouse=warehouse,
            today=safe_local_date(),
        )
        if batch_number:
            batch_stock_qs = batch_stock_qs.filter(batch__batchNumber=batch_number)
        batch_stock = batch_stock_qs.select_related('batch').first()
        if batch_stock is None:
            raise ValueError(f'No available non-expired batch found for {item.name}.')
        return batch_stock.batch

    if not has_batch_details and not item.requiresBatchTracking:
        return None
    if item.requiresBatchTracking and not batch_number:
        raise ValueError(f'Batch number is required for {item.name}.')
    if item.requiresBatchTracking and not expiry_date:
        raise ValueError(f'Expiry date is required for batch-tracked item {item.name}.')
    if batch_number:
        existing_batch = InventoryBatch.objects.filter(
            tenant=item.tenant,
            item=item,
            stockBucket=stock_bucket,
            batchNumber=batch_number,
            isDeleted=False,
        ).first()
        has_date_conflict = existing_batch is not None and (
            existing_batch.manufactureDate != manufacture_date or existing_batch.expiryDate != expiry_date
        )
        has_rate_conflict = (
            existing_batch is not None
            and purchase_rate
            and float(existing_batch.purchaseRate or 0.0)
            and round(float(existing_batch.purchaseRate or 0.0), 2) != round(purchase_rate, 2)
        )
        has_mrp_conflict = (
            existing_batch is not None
            and mrp
            and float(existing_batch.mrp or 0.0)
            and round(float(existing_batch.mrp or 0.0), 2) != round(mrp, 2)
        )
        if has_date_conflict or has_rate_conflict or has_mrp_conflict:
            raise ValueError(
                f'Batch {batch_number} already exists for {item.name} with different manufacture, expiry, rate, or MRP details.'
            )
    batch, _created = InventoryBatch.objects.get_or_create(
        tenant=item.tenant,
        item=item,
        stockBucket=stock_bucket,
        batchNumber=batch_number or None,
        manufactureDate=manufacture_date,
        expiryDate=expiry_date,
        defaults={
            'warehouse': warehouse,
            'purchaseRate': purchase_rate,
            'mrp': mrp,
            'isActive': True,
        },
    )
    update_fields = []
    if warehouse and batch.warehouse_id is None:
        batch.warehouse = warehouse
        update_fields.append('warehouse')
    if purchase_rate and float(batch.purchaseRate or 0.0) != purchase_rate:
        batch.purchaseRate = purchase_rate
        update_fields.append('purchaseRate')
    if mrp and float(batch.mrp or 0.0) != mrp:
        batch.mrp = mrp
        update_fields.append('mrp')
    if update_fields:
        update_fields.append('lastUpdatedOn')
        batch.save(update_fields=update_fields)
    return batch
