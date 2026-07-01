from collections import defaultdict
from datetime import datetime

from django.db import transaction
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from home.inventory import rebuild_inventory_stock, validate_non_negative_inventory
from home.models import (
    CashCounter,
    InventoryItem,
    PurchaseInvoice,
    PurchaseReturnLine,
    PurchaseReturnVoucher,
    Sales,
    SalesReturnLine,
    SalesReturnVoucher,
    StaffUser,
    TenantSequence,
)
from home.services.cash_counter import can_filter_all_cash_counter
from home.services.cash_counter_invoicing import cash_counter_return_source_modes, parse_return_source_ref
from home.services.inventory_lines import non_gst_shadow_enabled
from home.apis.inventory_stock import _serialize_voucher_lines, _voucher_line_rows_from_payload
from home.services.sales import can_filter_all_sales, has_return_source_access, has_sales_access, parse_sales_party, sales_list_base_qs
from home.services.sequences import next_tenant_sequence_value
from home.services.collections import has_any_permission
from home.services.inventory_lines import parse_inventory_warehouse, request_line_items_payload
from home.tenancy import assign_tenant, tenant_qs


def _can_manage_inventory_records(user):
    return has_any_permission(user, 'home.can_manage_inventory', 'home.add_inventoryitem', 'home.change_inventoryitem', 'home.can_view_party_credentials')


def _staff_for_request(request):
    return tenant_qs(request, StaffUser).get(user_ID_id=request.user.pk)


def list_sales_return_source_sales_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not has_return_source_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    allow_non_gst = non_gst_shadow_enabled(request)
    data = []
    if has_sales_access(request.user):
        qs = sales_list_base_qs(request).filter(isDeleted=False).order_by('-buildDate', '-id')[:150]
        if not can_filter_all_sales(request.user):
            qs = qs.filter(createdBy__user_ID_id=request.user.pk)
        for sale in qs:
            line_qs = sale.lines.filter(isDeleted=False)
            if not allow_non_gst:
                line_qs = line_qs.exclude(stockBucket=InventoryItem.StockBucket.NON_GST)
            if not line_qs.exists():
                continue
            party_name = getattr(getattr(sale, 'partyID', None), 'name', None) or 'Walk-in'
            invoice_no = sale.invoiceNumber or sale.paymentID or f'Sale#{sale.pk}'
            build_date = sale.buildDate.strftime('%d/%m/%Y') if sale.buildDate else ''
            data.append({
                'ID': sale.pk,
                'SourceType': 'sale',
                'SourceValue': f'sale:{sale.pk}',
                'InvoiceNumber': invoice_no,
                'PartyName': party_name,
                'BuildDate': build_date,
                'Amount': float(sale.amount or 0.0),
                'DisplayDetail': f'{invoice_no} | {party_name} | {build_date}',
            })
    cash_qs = tenant_qs(request, CashCounter).filter(
        isDeleted=False,
        mode__in=cash_counter_return_source_modes(),
    ).order_by('-entryDate', '-id')[:150]
    if not can_filter_all_cash_counter(request.user):
        cash_qs = cash_qs.filter(createdBy__user_ID_id=request.user.pk)
    for counter in cash_qs:
        line_qs = counter.lines.filter(isDeleted=False)
        if not allow_non_gst:
            line_qs = line_qs.exclude(stockBucket=InventoryItem.StockBucket.NON_GST)
        if not line_qs.exists():
            continue
        party_name = getattr(getattr(counter, 'partyID', None), 'name', None) or 'Walk-in'
        invoice_no = counter.invoiceNumber or counter.counterID or f'Counter#{counter.pk}'
        build_date = counter.entryDate.strftime('%d/%m/%Y') if counter.entryDate else ''
        data.append({
            'ID': counter.pk,
            'SourceType': 'cash_counter',
            'SourceValue': f'cash_counter:{counter.pk}',
            'InvoiceNumber': invoice_no,
            'PartyName': party_name,
            'BuildDate': build_date,
            'Amount': float(counter.amount or 0.0),
            'DisplayDetail': f'{invoice_no} | {party_name} | {build_date} | Cash Counter',
        })
    data.sort(key=lambda row: (row.get('BuildDate') or '', row.get('ID') or 0), reverse=True)
    return JsonResponse({'message': 'success', 'data': data}, safe=False)


def get_sales_return_source_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not has_return_source_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    source_type, source_id = parse_return_source_ref(request.GET.get('sourceId') or request.GET.get('saleId'))
    if not source_id:
        return JsonResponse({'message': 'error', 'error': 'Original invoice is required.'}, status=400, safe=False)
    allow_non_gst = non_gst_shadow_enabled(request)
    exclude_voucher_id = request.GET.get('excludeVoucherId')
    if source_type == 'cash_counter':
        source_obj = get_object_or_404(
            tenant_qs(request, CashCounter).select_related('partyID', 'createdBy', 'invoiceSeries'),
            pk=source_id,
            isDeleted=False,
        )
        if not can_filter_all_cash_counter(request.user):
            if getattr(getattr(source_obj, 'createdBy', None), 'user_ID_id', None) != request.user.pk:
                return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        return_qs = tenant_qs(request, SalesReturnVoucher).filter(isDeleted=False, cashCounter=source_obj)
        line_qs = source_obj.lines.filter(isDeleted=False).select_related('item', 'batch').order_by('id')
        if not allow_non_gst:
            line_qs = line_qs.exclude(stockBucket=InventoryItem.StockBucket.NON_GST)
    else:
        source_obj = get_object_or_404(
            tenant_qs(request, Sales).select_related('partyID', 'createdBy', 'invoiceSeries'),
            pk=source_id,
            isDeleted=False,
        )
        if not can_filter_all_sales(request.user):
            if getattr(getattr(source_obj, 'createdBy', None), 'user_ID_id', None) != request.user.pk:
                return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        return_qs = tenant_qs(request, SalesReturnVoucher).filter(isDeleted=False, sales=source_obj)
        line_qs = source_obj.lines.filter(isDeleted=False).select_related('item', 'batch').order_by('id')
        if not allow_non_gst:
            line_qs = line_qs.exclude(stockBucket=InventoryItem.StockBucket.NON_GST)
    if exclude_voucher_id and str(exclude_voucher_id).isdigit():
        return_qs = return_qs.exclude(pk=int(exclude_voucher_id))
    returned_map = defaultdict(float)
    for row in SalesReturnLine.objects.filter(
        tenant=source_obj.tenant,
        isDeleted=False,
        voucher__in=return_qs,
    ).values('item_id', 'stockBucket').annotate(total_qty=Sum('quantity')):
        returned_map[(row['item_id'], row['stockBucket'])] = float(row['total_qty'] or 0.0)

    lines = []
    for line in line_qs:
        key = (line.item_id, line.stockBucket)
        sold_qty = float(line.quantity or 0.0)
        prior_remaining = float(returned_map.get(key, 0.0))
        already_returned = min(prior_remaining, sold_qty)
        returned_map[key] = max(0.0, prior_remaining - already_returned)
        remaining_qty = max(0.0, sold_qty - already_returned)
        lines.append({
            'itemId': line.item_id,
            'itemName': line.item.name,
            'batchId': line.batch_id,
            'batchNumber': line.batch.batchNumber if line.batch_id else '',
            'manufactureDate': line.batch.manufactureDate.strftime('%d/%m/%Y') if line.batch_id and line.batch.manufactureDate else '',
            'expiryDate': line.batch.expiryDate.strftime('%d/%m/%Y') if line.batch_id and line.batch.expiryDate else '',
            'batchMrp': float(line.batch.mrp or 0.0) if line.batch_id else 0.0,
            'stockBucket': line.stockBucket,
            'quantity': sold_qty,
            'returnedQuantity': already_returned,
            'remainingQuantity': remaining_qty,
            'unitPrice': float(line.unitPrice or 0.0),
            'discountPercent': float(line.discountPercent or 0.0),
            'taxableAmount': float(line.taxableAmount or 0.0),
            'gstRate': float(line.gstRate or 0.0),
            'gstAmount': float(line.gstAmount or 0.0),
            'lineTotal': float(line.lineTotal or 0.0),
            'remark': line.remark or '',
        })
    return JsonResponse({'message': 'success', 'data': {
        'ID': source_obj.pk,
        'SourceType': source_type or 'sale',
        'SourceValue': f'{source_type or "sale"}:{source_obj.pk}',
        'InvoiceNumber': (
            source_obj.invoiceNumber or source_obj.paymentID or f'Sale#{source_obj.pk}'
            if source_type != 'cash_counter'
            else source_obj.invoiceNumber or source_obj.counterID or f'Counter#{source_obj.pk}'
        ),
        'BuildDate': (
            source_obj.buildDate.strftime('%d/%m/%Y') if getattr(source_obj, 'buildDate', None)
            else (source_obj.entryDate.strftime('%d/%m/%Y') if getattr(source_obj, 'entryDate', None) else '')
        ),
        'PartyID': source_obj.partyID_id,
        'PartyName': getattr(getattr(source_obj, 'partyID', None), 'name', None) or '',
        'BuyerLegalName': source_obj.buyerLegalName or getattr(getattr(source_obj, 'partyID', None), 'legalName', None) or '',
        'BuyerGstNumber': source_obj.buyerGstNumber or getattr(getattr(source_obj, 'partyID', None), 'gstNumber', None) or '',
        'BuyerBillingAddress': source_obj.buyerBillingAddress or getattr(getattr(source_obj, 'partyID', None), 'billingAddress', None) or getattr(getattr(source_obj, 'partyID', None), 'address', None) or '',
        'PlaceOfSupply': source_obj.placeOfSupply or '',
        'InvoiceKind': source_obj.invoiceKind or Sales.InvoiceKind.B2C,
        'Lines': lines,
    }}, safe=False)


@transaction.atomic
def add_purchase_return_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        lines_payload = request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one purchase return line is required.')
        voucher = PurchaseReturnVoucher()
        assign_tenant(voucher, request)
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        raw_purchase_id = str(request.POST.get('purchaseInvoiceId') or '').strip()
        if raw_purchase_id.isdigit():
            voucher.purchaseInvoice = tenant_qs(request, PurchaseInvoice).filter(pk=int(raw_purchase_id), isDeleted=False).first()
        voucher.partyID = parse_sales_party(request, request.POST.get('party')) if request.POST.get('party') else None
        if voucher.partyID_id is None and voucher.purchaseInvoice_id:
            voucher.partyID = voucher.purchaseInvoice.partyID
        voucher.remark = request.POST.get('remark')
        voucher.createdBy = _staff_for_request(request)
        voucher.save()
        voucher.voucherNumber = next_tenant_sequence_value(request, TenantSequence.Scope.PURCHASE_RETURN)
        voucher.save(update_fields=['voucherNumber', 'lastUpdatedOn'])
        warehouse = parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitCost', batch_direction='out', warehouse=warehouse)
        voucher.totalAmount = 0.0
        for row in rows:
            PurchaseReturnLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                warehouse=warehouse,
                item=row['item'],
                batch=row['batch'],
                stockBucket=row['stockBucket'],
                quantity=row['quantity'],
                unitCost=row['unitRate'],
                lineTotal=row['lineTotal'],
                remark=row['remark'],
            )
            voucher.totalAmount += row['lineTotal']
        voucher.save(update_fields=['totalAmount', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success', 'voucherId': voucher.pk, 'voucherNumber': voucher.voucherNumber}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_purchase_return_vouchers_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    rows = tenant_qs(request, PurchaseReturnVoucher).filter(isDeleted=False).select_related('partyID', 'purchaseInvoice').order_by('-entryDate', '-id')[:100]
    payload = [{
        'ID': row.pk,
        'VoucherNumber': row.voucherNumber or '',
        'EntryDate': row.entryDate.strftime('%d/%m/%Y') if row.entryDate else '',
        'Party': row.partyID.name if row.partyID_id else '-',
        'PurchaseInvoice': row.purchaseInvoice.invoiceNumber if row.purchaseInvoice_id else '-',
        'TotalAmount': float(row.totalAmount or 0.0),
        'LineCount': row.lines.filter(isDeleted=False).count(),
    } for row in rows]
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def get_purchase_return_voucher_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, PurchaseReturnVoucher).select_related('partyID', 'purchaseInvoice').get(pk=int(request.GET.get('voucherId')), isDeleted=False)
        warehouse_id = voucher.lines.filter(isDeleted=False).values_list('warehouse_id', flat=True).first()
        return JsonResponse({'message': 'success', 'data': {
            'ID': voucher.pk,
            'VoucherNumber': voucher.voucherNumber or '',
            'EntryDate': voucher.entryDate.strftime('%d/%m/%Y') if voucher.entryDate else '',
            'WarehouseID': warehouse_id,
            'PurchaseInvoiceID': voucher.purchaseInvoice_id,
            'PartyID': voucher.partyID_id,
            'Remark': voucher.remark or '',
            'Lines': _serialize_voucher_lines(voucher.lines.filter(isDeleted=False), quantity_field='quantity', rate_field='unitCost'),
        }}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_purchase_return_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, PurchaseReturnVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        prior_item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        lines_payload = request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one purchase return line is required.')
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        raw_purchase_id = str(request.POST.get('purchaseInvoiceId') or '').strip()
        voucher.purchaseInvoice = tenant_qs(request, PurchaseInvoice).filter(pk=int(raw_purchase_id), isDeleted=False).first() if raw_purchase_id.isdigit() else None
        voucher.partyID = parse_sales_party(request, request.POST.get('party')) if request.POST.get('party') else None
        if voucher.partyID_id is None and voucher.purchaseInvoice_id:
            voucher.partyID = voucher.purchaseInvoice.partyID
        voucher.remark = request.POST.get('remark')
        voucher.save(update_fields=['entryDate', 'purchaseInvoice', 'partyID', 'remark', 'lastUpdatedOn'])
        voucher.lines.filter(isDeleted=False).update(isDeleted=True)
        warehouse = parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitCost', batch_direction='out', warehouse=warehouse)
        voucher.totalAmount = 0.0
        for row in rows:
            PurchaseReturnLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                warehouse=warehouse,
                item=row['item'],
                batch=row['batch'],
                stockBucket=row['stockBucket'],
                quantity=row['quantity'],
                unitCost=row['unitRate'],
                lineTotal=row['lineTotal'],
                remark=row['remark'],
            )
            voucher.totalAmount += row['lineTotal']
        voucher.save(update_fields=['totalAmount', 'lastUpdatedOn'])
        affected = list(set(prior_item_ids + item_ids))
        rebuild_inventory_stock(voucher.tenant, affected)
        validate_non_negative_inventory(voucher.tenant, affected)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_purchase_return_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, PurchaseReturnVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        voucher.isDeleted = True
        voucher.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def add_sales_return_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        lines_payload = request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one sales return line is required.')
        voucher = SalesReturnVoucher()
        assign_tenant(voucher, request)
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        source_type, source_id = parse_return_source_ref(request.POST.get('salesId'))
        if source_type == 'cash_counter' and source_id:
            voucher.cashCounter = tenant_qs(request, CashCounter).filter(pk=source_id, isDeleted=False).first()
        elif source_id:
            voucher.sales = tenant_qs(request, Sales).filter(pk=source_id, isDeleted=False).first()
        voucher.partyID = parse_sales_party(request, request.POST.get('party')) if request.POST.get('party') else None
        if voucher.partyID_id is None and voucher.sales_id:
            voucher.partyID = voucher.sales.partyID
        if voucher.partyID_id is None and voucher.cashCounter_id:
            voucher.partyID = voucher.cashCounter.partyID
        voucher.remark = request.POST.get('remark')
        voucher.createdBy = _staff_for_request(request)
        voucher.save()
        voucher.voucherNumber = next_tenant_sequence_value(request, TenantSequence.Scope.SALES_RETURN)
        voucher.save(update_fields=['voucherNumber', 'lastUpdatedOn'])
        warehouse = parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitPrice', batch_direction='in', warehouse=warehouse)
        voucher.totalAmount = 0.0
        for row in rows:
            SalesReturnLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                warehouse=warehouse,
                item=row['item'],
                batch=row['batch'],
                stockBucket=row['stockBucket'],
                quantity=row['quantity'],
                unitPrice=row['unitRate'],
                lineTotal=row['lineTotal'],
                remark=row['remark'],
            )
            voucher.totalAmount += row['lineTotal']
        voucher.save(update_fields=['totalAmount', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success', 'voucherId': voucher.pk, 'voucherNumber': voucher.voucherNumber}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_sales_return_vouchers_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    rows = tenant_qs(request, SalesReturnVoucher).filter(isDeleted=False).select_related('partyID', 'sales', 'cashCounter').order_by('-entryDate', '-id')[:100]
    payload = [{
        'ID': row.pk,
        'VoucherNumber': row.voucherNumber or '',
        'EntryDate': row.entryDate.strftime('%d/%m/%Y') if row.entryDate else '',
        'Party': row.partyID.name if row.partyID_id else '-',
        'SalesInvoice': row.sales.invoiceNumber if row.sales_id else (row.cashCounter.invoiceNumber if row.cashCounter_id else '-'),
        'TotalAmount': float(row.totalAmount or 0.0),
        'LineCount': row.lines.filter(isDeleted=False).count(),
    } for row in rows]
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def get_sales_return_voucher_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, SalesReturnVoucher).select_related('partyID', 'sales', 'cashCounter').get(pk=int(request.GET.get('voucherId')), isDeleted=False)
        warehouse_id = voucher.lines.filter(isDeleted=False).values_list('warehouse_id', flat=True).first()
        return JsonResponse({'message': 'success', 'data': {
            'ID': voucher.pk,
            'VoucherNumber': voucher.voucherNumber or '',
            'EntryDate': voucher.entryDate.strftime('%d/%m/%Y') if voucher.entryDate else '',
            'WarehouseID': warehouse_id,
            'SalesID': voucher.sales_id if voucher.sales_id else (f'cash_counter:{voucher.cashCounter_id}' if voucher.cashCounter_id else None),
            'PartyID': voucher.partyID_id,
            'Remark': voucher.remark or '',
            'Lines': _serialize_voucher_lines(voucher.lines.filter(isDeleted=False), quantity_field='quantity', rate_field='unitPrice'),
        }}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_sales_return_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, SalesReturnVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        prior_item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        lines_payload = request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one sales return line is required.')
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        source_type, source_id = parse_return_source_ref(request.POST.get('salesId'))
        voucher.sales = None
        voucher.cashCounter = None
        if source_type == 'cash_counter' and source_id:
            voucher.cashCounter = tenant_qs(request, CashCounter).filter(pk=source_id, isDeleted=False).first()
        elif source_id:
            voucher.sales = tenant_qs(request, Sales).filter(pk=source_id, isDeleted=False).first()
        voucher.partyID = parse_sales_party(request, request.POST.get('party')) if request.POST.get('party') else None
        if voucher.partyID_id is None and voucher.sales_id:
            voucher.partyID = voucher.sales.partyID
        if voucher.partyID_id is None and voucher.cashCounter_id:
            voucher.partyID = voucher.cashCounter.partyID
        voucher.remark = request.POST.get('remark')
        voucher.save(update_fields=['entryDate', 'sales', 'cashCounter', 'partyID', 'remark', 'lastUpdatedOn'])
        voucher.lines.filter(isDeleted=False).update(isDeleted=True)
        warehouse = parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitPrice', batch_direction='in', warehouse=warehouse)
        voucher.totalAmount = 0.0
        for row in rows:
            SalesReturnLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                warehouse=warehouse,
                item=row['item'],
                batch=row['batch'],
                stockBucket=row['stockBucket'],
                quantity=row['quantity'],
                unitPrice=row['unitRate'],
                lineTotal=row['lineTotal'],
                remark=row['remark'],
            )
            voucher.totalAmount += row['lineTotal']
        voucher.save(update_fields=['totalAmount', 'lastUpdatedOn'])
        affected = list(set(prior_item_ids + item_ids))
        rebuild_inventory_stock(voucher.tenant, affected)
        validate_non_negative_inventory(voucher.tenant, affected)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_sales_return_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, SalesReturnVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        voucher.isDeleted = True
        voucher.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
