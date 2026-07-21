from __future__ import annotations

from collections import defaultdict

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q

from utils.logger import logger
from home.cache_keys import inventory_list_version_cache_key
from home.models import (
    CashCounterLine,
    InventoryAdjustmentLine,
    InventoryBatch,
    InventoryBatchStock,
    InventoryItem,
    InventoryLedgerEntry,
    InventoryOpeningLine,
    InventoryStock,
    InventoryTransferLine,
    InventoryTransferVoucher,
    InventoryWarehouse,
    PurchaseLine,
    PurchaseReturnLine,
    SalesLine,
    SalesReturnLine,
)


SALE_DEDUCT_MODES = {'Cash', 'Card', 'Credit', 'Mix'}
RETURN_ADD_MODES = {'Return'}
DEFAULT_WAREHOUSE_NAME = 'Main Store'
DEFAULT_WAREHOUSE_CODE = 'MAIN'


def _bump_inventory_list_cache_version(resource: str, *, tenant) -> None:
    version_key = inventory_list_version_cache_key(resource, tenant=tenant)
    version = cache.get(version_key) or 1
    cache.set(version_key, version + 1, timeout=None)


def _bucket_totals():
    return {bucket: 0.0 for bucket, _ in InventoryItem.StockBucket.choices}


def _round_qty(value: float) -> float:
    return round(float(value or 0.0), 4)


@transaction.atomic
def get_or_create_default_warehouse(tenant):
    if tenant is None:
        return None

    default_warehouse = InventoryWarehouse.objects.filter(
        tenant=tenant,
        isDeleted=False,
        isActive=True,
        isDefault=True,
    ).order_by('id').first()
    if default_warehouse:
        return default_warehouse

    first_active = InventoryWarehouse.objects.filter(
        tenant=tenant,
        isDeleted=False,
        isActive=True,
    ).order_by('id').first()
    if first_active:
        if not first_active.isDefault:
            first_active.isDefault = True
            first_active.save(update_fields=['isDefault', 'lastUpdatedOn'])
            _bump_inventory_list_cache_version('warehouses', tenant=tenant)
        return first_active

    warehouse = InventoryWarehouse.objects.create(
        tenant=tenant,
        name=DEFAULT_WAREHOUSE_NAME,
        code=DEFAULT_WAREHOUSE_CODE,
        isDefault=True,
        isActive=True,
    )
    _bump_inventory_list_cache_version('warehouses', tenant=tenant)
    return warehouse


@transaction.atomic
def set_default_warehouse(tenant, warehouse):
    if tenant is None or warehouse is None:
        return None
    InventoryWarehouse.objects.filter(tenant=tenant, isDeleted=False, isDefault=True).exclude(pk=warehouse.pk).update(
        isDefault=False,
        lastUpdatedOn=warehouse.lastUpdatedOn,
    )
    if not warehouse.isDefault:
        warehouse.isDefault = True
        warehouse.save(update_fields=['isDefault', 'lastUpdatedOn'])
        _bump_inventory_list_cache_version('warehouses', tenant=tenant)
    return warehouse


def _line_warehouse_id(line, fallback_warehouse):
    return line.warehouse_id or (fallback_warehouse.pk if fallback_warehouse else None)


def _ledger_content_type(model):
    return ContentType.objects.get_for_model(model)


def _append_ledger_entry(entries, *, tenant, item_id, warehouse_id, batch_id, stock_bucket, source_type, quantity_delta, unit_rate, total_value, entry_date, reference_number, remark, source_ct_id, source_object_id):
    entries.append(
        InventoryLedgerEntry(
            tenant=tenant,
            item_id=item_id,
            warehouse_id=warehouse_id,
            batch_id=batch_id,
            stockBucket=stock_bucket,
            sourceType=source_type,
            quantityDelta=_round_qty(quantity_delta),
            unitRate=round(float(unit_rate or 0.0), 2),
            totalValue=round(float(total_value or 0.0), 2),
            entryDate=entry_date,
            referenceNumber=reference_number,
            remark=(remark or '')[:500] or None,
            source_content_type_id=source_ct_id,
            source_object_id=source_object_id,
        )
    )


def _ledger_rows_for_items(tenant, item_ids):
    fallback_warehouse = get_or_create_default_warehouse(tenant)
    entries = []

    opening_ct_id = _ledger_content_type(InventoryOpeningLine).pk
    for line in InventoryOpeningLine.objects.filter(
        tenant=tenant,
        isDeleted=False,
        voucher__isDeleted=False,
        item_id__in=item_ids,
    ).select_related('voucher'):
        _append_ledger_entry(
            entries,
            tenant=tenant,
            item_id=line.item_id,
            warehouse_id=_line_warehouse_id(line, fallback_warehouse),
            batch_id=line.batch_id,
            stock_bucket=line.stockBucket,
            source_type=InventoryLedgerEntry.SourceType.OPENING,
            quantity_delta=line.quantity,
            unit_rate=line.unitCost,
            total_value=line.lineTotal or ((line.quantity or 0.0) * (line.unitCost or 0.0)),
            entry_date=line.voucher.entryDate,
            reference_number=line.voucher.voucherNumber,
            remark=line.remark or line.voucher.remark,
            source_ct_id=opening_ct_id,
            source_object_id=line.pk,
        )

    purchase_ct_id = _ledger_content_type(PurchaseLine).pk
    for line in PurchaseLine.objects.filter(
        tenant=tenant,
        isDeleted=False,
        purchaseInvoice__isDeleted=False,
        item_id__in=item_ids,
    ).select_related('purchaseInvoice'):
        _append_ledger_entry(
            entries,
            tenant=tenant,
            item_id=line.item_id,
            warehouse_id=_line_warehouse_id(line, fallback_warehouse),
            batch_id=line.batch_id,
            stock_bucket=line.stockBucket,
            source_type=InventoryLedgerEntry.SourceType.PURCHASE,
            quantity_delta=line.quantity,
            unit_rate=line.unitCost,
            total_value=line.lineTotal or ((line.quantity or 0.0) * (line.unitCost or 0.0)),
            entry_date=line.purchaseInvoice.buildDate,
            reference_number=line.purchaseInvoice.invoiceNumber,
            remark=line.remark or line.purchaseInvoice.remark,
            source_ct_id=purchase_ct_id,
            source_object_id=line.pk,
        )

    sales_ct_id = _ledger_content_type(SalesLine).pk
    for line in SalesLine.objects.filter(
        tenant=tenant,
        isDeleted=False,
        sales__isDeleted=False,
        item_id__in=item_ids,
    ).select_related('sales'):
        _append_ledger_entry(
            entries,
            tenant=tenant,
            item_id=line.item_id,
            warehouse_id=_line_warehouse_id(line, fallback_warehouse),
            batch_id=line.batch_id,
            stock_bucket=line.stockBucket,
            source_type=InventoryLedgerEntry.SourceType.SALES,
            quantity_delta=-(line.quantity or 0.0),
            unit_rate=line.unitPrice,
            total_value=-(line.lineTotal or ((line.quantity or 0.0) * (line.unitPrice or 0.0))),
            entry_date=line.sales.buildDate,
            reference_number=line.sales.invoiceNumber or line.sales.paymentID,
            remark=line.remark or line.sales.remark,
            source_ct_id=sales_ct_id,
            source_object_id=line.pk,
        )

    counter_ct_id = _ledger_content_type(CashCounterLine).pk
    for line in CashCounterLine.objects.filter(
        tenant=tenant,
        isDeleted=False,
        cashCounter__isDeleted=False,
        item_id__in=item_ids,
    ).select_related('cashCounter'):
        counter_entry_date = None
        if line.cashCounter.entryDate:
            counter_entry_date = line.cashCounter.entryDate.date() if hasattr(line.cashCounter.entryDate, 'date') else line.cashCounter.entryDate
        mode = (line.cashCounter.mode or '').strip()
        quantity_delta = 0.0
        total_value = 0.0
        if mode in SALE_DEDUCT_MODES:
            quantity_delta = -(line.quantity or 0.0)
            total_value = -(line.lineTotal or ((line.quantity or 0.0) * (line.unitPrice or 0.0)))
        elif mode in RETURN_ADD_MODES:
            if getattr(line.cashCounter, 'linkedSalesReturnVoucher_id', None):
                continue
            quantity_delta = line.quantity or 0.0
            total_value = line.lineTotal or ((line.quantity or 0.0) * (line.unitPrice or 0.0))
        if not quantity_delta:
            continue
        _append_ledger_entry(
            entries,
            tenant=tenant,
            item_id=line.item_id,
            warehouse_id=_line_warehouse_id(line, fallback_warehouse),
            batch_id=line.batch_id,
            stock_bucket=line.stockBucket,
            source_type=InventoryLedgerEntry.SourceType.CASH_COUNTER,
            quantity_delta=quantity_delta,
            unit_rate=line.unitPrice,
            total_value=total_value,
            entry_date=counter_entry_date,
            reference_number=line.cashCounter.invoiceNumber or line.cashCounter.counterID,
            remark=line.remark or line.cashCounter.remark,
            source_ct_id=counter_ct_id,
            source_object_id=line.pk,
        )

    adjustment_ct_id = _ledger_content_type(InventoryAdjustmentLine).pk
    for line in InventoryAdjustmentLine.objects.filter(
        tenant=tenant,
        isDeleted=False,
        voucher__isDeleted=False,
        item_id__in=item_ids,
    ).select_related('voucher'):
        _append_ledger_entry(
            entries,
            tenant=tenant,
            item_id=line.item_id,
            warehouse_id=_line_warehouse_id(line, fallback_warehouse),
            batch_id=line.batch_id,
            stock_bucket=line.stockBucket,
            source_type=InventoryLedgerEntry.SourceType.ADJUSTMENT,
            quantity_delta=line.quantityDelta,
            unit_rate=line.unitCost,
            total_value=line.lineTotal or ((line.quantityDelta or 0.0) * (line.unitCost or 0.0)),
            entry_date=line.voucher.entryDate,
            reference_number=line.voucher.voucherNumber,
            remark=line.remark or line.voucher.remark,
            source_ct_id=adjustment_ct_id,
            source_object_id=line.pk,
        )

    transfer_ct_id = _ledger_content_type(InventoryTransferLine).pk
    for line in InventoryTransferLine.objects.filter(
        tenant=tenant,
        isDeleted=False,
        voucher__isDeleted=False,
        item_id__in=item_ids,
    ).select_related('voucher'):
        from_warehouse_id = line.voucher.fromWarehouse_id or (fallback_warehouse.pk if fallback_warehouse else None)
        to_warehouse_id = line.voucher.toWarehouse_id or (fallback_warehouse.pk if fallback_warehouse else None)
        _append_ledger_entry(
            entries,
            tenant=tenant,
            item_id=line.item_id,
            warehouse_id=from_warehouse_id,
            batch_id=line.batch_id,
            stock_bucket=line.stockBucket,
            source_type=InventoryLedgerEntry.SourceType.TRANSFER_OUT,
            quantity_delta=-(line.quantity or 0.0),
            unit_rate=line.unitCost,
            total_value=-(line.lineTotal or ((line.quantity or 0.0) * (line.unitCost or 0.0))),
            entry_date=line.voucher.entryDate,
            reference_number=line.voucher.voucherNumber,
            remark=line.remark or line.voucher.remark,
            source_ct_id=transfer_ct_id,
            source_object_id=line.pk,
        )
        _append_ledger_entry(
            entries,
            tenant=tenant,
            item_id=line.item_id,
            warehouse_id=to_warehouse_id,
            batch_id=line.batch_id,
            stock_bucket=line.stockBucket,
            source_type=InventoryLedgerEntry.SourceType.TRANSFER_IN,
            quantity_delta=line.quantity or 0.0,
            unit_rate=line.unitCost,
            total_value=line.lineTotal or ((line.quantity or 0.0) * (line.unitCost or 0.0)),
            entry_date=line.voucher.entryDate,
            reference_number=line.voucher.voucherNumber,
            remark=line.remark or line.voucher.remark,
            source_ct_id=transfer_ct_id,
            source_object_id=line.pk,
        )

    purchase_return_ct_id = _ledger_content_type(PurchaseReturnLine).pk
    for line in PurchaseReturnLine.objects.filter(
        tenant=tenant,
        isDeleted=False,
        voucher__isDeleted=False,
        item_id__in=item_ids,
    ).select_related('voucher'):
        _append_ledger_entry(
            entries,
            tenant=tenant,
            item_id=line.item_id,
            warehouse_id=_line_warehouse_id(line, fallback_warehouse),
            batch_id=line.batch_id,
            stock_bucket=line.stockBucket,
            source_type=InventoryLedgerEntry.SourceType.PURCHASE_RETURN,
            quantity_delta=-(line.quantity or 0.0),
            unit_rate=line.unitCost,
            total_value=-(line.lineTotal or ((line.quantity or 0.0) * (line.unitCost or 0.0))),
            entry_date=line.voucher.entryDate,
            reference_number=line.voucher.voucherNumber,
            remark=line.remark or line.voucher.remark,
            source_ct_id=purchase_return_ct_id,
            source_object_id=line.pk,
        )

    sales_return_ct_id = _ledger_content_type(SalesReturnLine).pk
    for line in SalesReturnLine.objects.filter(
        tenant=tenant,
        isDeleted=False,
        voucher__isDeleted=False,
        item_id__in=item_ids,
    ).select_related('voucher'):
        _append_ledger_entry(
            entries,
            tenant=tenant,
            item_id=line.item_id,
            warehouse_id=_line_warehouse_id(line, fallback_warehouse),
            batch_id=line.batch_id,
            stock_bucket=line.stockBucket,
            source_type=InventoryLedgerEntry.SourceType.SALES_RETURN,
            quantity_delta=line.quantity or 0.0,
            unit_rate=line.unitPrice,
            total_value=line.lineTotal or ((line.quantity or 0.0) * (line.unitPrice or 0.0)),
            entry_date=line.voucher.entryDate,
            reference_number=line.voucher.voucherNumber,
            remark=line.remark or line.voucher.remark,
            source_ct_id=sales_return_ct_id,
            source_object_id=line.pk,
        )

    return entries


@transaction.atomic
def rebuild_inventory_stock(tenant, item_ids=None):
    if tenant is None:
        return

    item_qs = tenant.inventory_items.filter(isDeleted=False)
    if item_ids is not None:
        item_qs = item_qs.filter(pk__in=set(int(item_id) for item_id in item_ids if item_id))
    item_ids = list(item_qs.values_list('id', flat=True))
    if not item_ids:
        return

    ledger_entries = _ledger_rows_for_items(tenant, item_ids)
    InventoryLedgerEntry.objects.filter(tenant=tenant, item_id__in=item_ids).delete()
    if ledger_entries:
        InventoryLedgerEntry.objects.bulk_create(ledger_entries, batch_size=500)

    totals_by_item = {item_id: _bucket_totals() for item_id in item_ids}
    totals_by_batch = defaultdict(float)
    totals_by_batch_stock = defaultdict(float)
    for row in InventoryLedgerEntry.objects.filter(
        tenant=tenant,
        item_id__in=item_ids,
        isDeleted=False,
    ).values('item_id', 'warehouse_id', 'stockBucket', 'batch_id', 'quantityDelta'):
        quantity_delta = float(row['quantityDelta'] or 0.0)
        totals_by_item[row['item_id']][row['stockBucket']] += quantity_delta
        if row['batch_id']:
            totals_by_batch[row['batch_id']] += quantity_delta
            totals_by_batch_stock[(row['batch_id'], row['warehouse_id'])] += quantity_delta

    existing_rows = {
        (stock.item_id, stock.stockBucket): stock
        for stock in InventoryStock.objects.filter(tenant=tenant, item_id__in=item_ids)
    }

    for item_id, bucket_totals in totals_by_item.items():
        for bucket, _label in InventoryItem.StockBucket.choices:
            stock = existing_rows.get((item_id, bucket))
            quantity = _round_qty(bucket_totals.get(bucket, 0.0))
            if stock is None:
                InventoryStock.objects.create(
                    tenant=tenant,
                    item_id=item_id,
                    stockBucket=bucket,
                    quantityOnHand=quantity,
                )
            else:
                stock.quantityOnHand = quantity
                stock.isDeleted = False
                stock.save(update_fields=['quantityOnHand', 'isDeleted', 'lastUpdatedOn'])

    batch_qs = InventoryBatch.objects.filter(tenant=tenant, item_id__in=item_ids)
    for batch in batch_qs:
        batch.quantityOnHand = _round_qty(totals_by_batch.get(batch.pk, 0.0))
        batch.isDeleted = False
        batch.save(update_fields=['quantityOnHand', 'isDeleted', 'lastUpdatedOn'])

    existing_batch_stock = {
        (row.batch_id, row.warehouse_id): row
        for row in InventoryBatchStock.objects.filter(tenant=tenant, item_id__in=item_ids)
    }
    batch_lookup = {batch.pk: batch for batch in batch_qs}
    for (batch_id, warehouse_id), quantity in totals_by_batch_stock.items():
        batch = batch_lookup.get(batch_id)
        if batch is None:
            continue
        stock_row = existing_batch_stock.get((batch_id, warehouse_id))
        quantity = _round_qty(quantity)
        if stock_row is None:
            InventoryBatchStock.objects.create(
                tenant=tenant,
                batch=batch,
                item_id=batch.item_id,
                warehouse_id=warehouse_id,
                stockBucket=batch.stockBucket,
                quantityOnHand=quantity,
            )
        else:
            stock_row.quantityOnHand = quantity
            stock_row.isDeleted = False
            stock_row.save(update_fields=['quantityOnHand', 'isDeleted', 'lastUpdatedOn'])
    active_keys = set(totals_by_batch_stock.keys())
    for key, stock_row in existing_batch_stock.items():
        if key not in active_keys and not stock_row.isDeleted:
            stock_row.quantityOnHand = 0.0
            stock_row.isDeleted = False
            stock_row.save(update_fields=['quantityOnHand', 'isDeleted', 'lastUpdatedOn'])
    _bump_inventory_list_cache_version('items', tenant=tenant)
    logger.info("[InventoryService] Rebuilt stock for tenant %s across %d items", getattr(tenant, 'pk', None), len(item_ids))


def validate_non_negative_inventory(tenant, item_ids=None, item_bucket_pairs=None):
    if tenant is None:
        return
    qs = InventoryStock.objects.filter(tenant=tenant, isDeleted=False)
    if item_ids is not None:
        qs = qs.filter(item_id__in=set(int(item_id) for item_id in item_ids if item_id))
    if item_bucket_pairs:
        pair_filter = Q()
        for item_id, bucket in item_bucket_pairs:
            if not item_id or not bucket:
                continue
            pair_filter |= Q(item_id=int(item_id), stockBucket=bucket)
        if pair_filter:
            qs = qs.filter(pair_filter)
    negative_row = qs.select_related('item').filter(quantityOnHand__lt=0).order_by('quantityOnHand').first()
    if negative_row is None:
        negative_batch_stock = InventoryBatchStock.objects.filter(
            tenant=tenant,
            isDeleted=False,
            quantityOnHand__lt=0,
        )
        if item_ids is not None:
            negative_batch_stock = negative_batch_stock.filter(item_id__in=set(int(item_id) for item_id in item_ids if item_id))
        negative_batch_row = negative_batch_stock.select_related('item', 'batch', 'warehouse').order_by('quantityOnHand').first()
        if negative_batch_row is None:
            return
        warehouse_label = negative_batch_row.warehouse.name if negative_batch_row.warehouse_id else 'default warehouse'
        batch_label = negative_batch_row.batch.batchNumber or f'Batch #{negative_batch_row.batch_id}'
        err_msg = (
            f'Insufficient batch stock for {negative_batch_row.item.name}, {batch_label} in {warehouse_label}. '
            f'Current batch balance is {negative_batch_row.quantityOnHand}.'
        )
        logger.error("[InventoryService] Validation error: %s", err_msg)
        raise ValueError(err_msg)
    alternate_note = ''
    if getattr(tenant, 'nonGstShadowEnabled', False) or negative_row.stockBucket == InventoryItem.StockBucket.NON_GST:
        alternate_bucket = (
            InventoryItem.StockBucket.NON_GST
            if negative_row.stockBucket == InventoryItem.StockBucket.GST
            else InventoryItem.StockBucket.GST
        )
        if alternate_bucket != InventoryItem.StockBucket.NON_GST or getattr(tenant, 'nonGstShadowEnabled', False):
            alternate_row = InventoryStock.objects.filter(
                tenant=tenant,
                isDeleted=False,
                item_id=negative_row.item_id,
                stockBucket=alternate_bucket,
            ).first()
            if alternate_row is not None and float(alternate_row.quantityOnHand or 0.0) > 0:
                alternate_label = 'Non-GST' if alternate_bucket == InventoryItem.StockBucket.NON_GST else 'GST'
                alternate_note = f' {alternate_label} stock available: {alternate_row.quantityOnHand}.'
    err_msg = (
        f'Insufficient {negative_row.get_stockBucket_display()} stock for {negative_row.item.name}. '
        f'Current on-hand balance in this bucket is {negative_row.quantityOnHand}. '
        f'Please add stock, use the correct bucket, or adjust inventory first.'
        f'{alternate_note}'
    )
    logger.error("[InventoryService] Validation error: %s", err_msg)
    raise ValueError(err_msg)


def stock_snapshot_for_tenant(tenant):
    if tenant is None:
        return []
    rows = InventoryStock.objects.filter(
        tenant=tenant,
        isDeleted=False,
        item__isDeleted=False,
    ).select_related('item').order_by('item__name', 'stockBucket')
    payload = []
    grouped = defaultdict(dict)
    for row in rows:
        grouped[row.item_id].update({
            'itemId': row.item_id,
            'name': row.item.name,
            'sku': row.item.sku or '',
            row.stockBucket: float(row.quantityOnHand or 0.0),
        })
    for _item_id, data in grouped.items():
        data.setdefault(InventoryItem.StockBucket.GST, 0.0)
        data.setdefault(InventoryItem.StockBucket.NON_GST, 0.0)
        payload.append(data)
    return payload
