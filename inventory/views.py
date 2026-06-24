from collections import defaultdict
from datetime import datetime, timedelta

from django.urls import reverse
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import urlencode

from home.views import permission_required_any
from home.tenancy import get_current_tenant, tenant_qs
from home.models import (
    Bank,
    InventoryCategory,
    CashCounterLine,
    InventoryItem,
    InventoryLedgerEntry,
    InventoryStock,
    PurchaseInvoice,
    SalesLine,
)


def _render_inventory_page(request, page_key, page_title):
    tenant = get_current_tenant(request)
    if tenant is not None and not getattr(tenant, 'inventoryEnabled', True):
        return redirect('homeApp:admin_home')
    return render(request, 'home/inventory/index.html', {
        'inventory_page': page_key,
        'inventory_page_title': page_title,
        'inventory_enabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advanced_invoicing_enabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'can_manage_non_gst_shadow': request.user.is_superuser or request.user.has_perm('home.can_view_party_credentials'),
    })


@permission_required_any('home.add_inventoryitem', 'home.change_inventoryitem', 'home.view_inventoryitem', 'home.can_manage_inventory', 'home.can_view_party_credentials')
def inventory_list(request):
    return _render_inventory_page(request, 'items', 'Items')


@permission_required_any('home.add_inventoryitem', 'home.change_inventoryitem', 'home.view_inventoryitem', 'home.can_manage_inventory', 'home.can_view_party_credentials')
def inventory_items_page(request):
    return _render_inventory_page(request, 'items', 'Items')


@permission_required_any('home.add_inventoryitem', 'home.change_inventoryitem', 'home.view_inventoryitem', 'home.can_manage_inventory', 'home.can_view_party_credentials')
def inventory_masters_page(request):
    return _render_inventory_page(request, 'masters', 'Masters')


@permission_required_any('home.add_inventoryitem', 'home.change_inventoryitem', 'home.view_inventoryitem', 'home.can_manage_inventory', 'home.can_view_party_credentials')
def inventory_stock_page(request):
    return _render_inventory_page(request, 'stock', 'Stock')


@permission_required_any('home.add_inventoryitem', 'home.change_inventoryitem', 'home.view_inventoryitem', 'home.can_manage_inventory', 'home.can_view_party_credentials')
def inventory_vouchers_page(request):
    return _render_inventory_page(request, 'vouchers', 'Adjustments')


def _inventory_analysis_filters(request):
    def _parse_inventory_date(value):
        raw = (value or '').strip()
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
        return None

    start = _parse_inventory_date(request.GET.get('start'))
    end = _parse_inventory_date(request.GET.get('end'))
    if start and end and start > end:
        start, end = end, start

    category_id = (request.GET.get('category') or '').strip()
    item_id = (request.GET.get('item') or '').strip()
    bucket = (request.GET.get('bucket') or '').strip()
    if bucket not in {choice[0] for choice in InventoryItem.StockBucket.choices}:
        bucket = ''
    focus = (request.GET.get('focus') or '').strip()
    if focus not in {'', 'negative', 'dead', 'low', 'missing'}:
        focus = ''
    tab = (request.GET.get('tab') or '').strip()
    if tab not in {'', 'inv-overview', 'inv-performance', 'inv-aging', 'inv-exceptions', 'inv-detail'}:
        tab = ''

    return {
        'start': start,
        'end': end,
        'category_id': int(category_id) if category_id.isdigit() else None,
        'item_id': int(item_id) if item_id.isdigit() else None,
        'bucket': bucket,
        'focus': focus,
        'tab': tab,
    }


def _empty_item_metrics():
    return {
        'current_qty': 0.0,
        'current_value': 0.0,
        'current_buckets': set(),
        'inward_qty': 0.0,
        'inward_value': 0.0,
        'outward_qty': 0.0,
        'outward_value': 0.0,
        'purchase_qty': 0.0,
        'purchase_value': 0.0,
        'purchase_return_qty': 0.0,
        'purchase_return_value': 0.0,
        'sold_qty': 0.0,
        'sold_value': 0.0,
        'sales_return_qty': 0.0,
        'sales_return_value': 0.0,
        'adjustment_qty': 0.0,
        'last_inward': None,
        'last_outward': None,
        'latest_sale': None,
    }


def _format_bucket_label(bucket_values):
    ordered = []
    for value, label in InventoryItem.StockBucket.choices:
        if value in bucket_values:
            ordered.append(label)
    return ', '.join(ordered) if ordered else 'Mixed'


def _build_analysis_query(filters, **updates):
    merged = {
        'start': filters['start'].strftime('%d/%m/%Y') if filters.get('start') else '',
        'end': filters['end'].strftime('%d/%m/%Y') if filters.get('end') else '',
        'category': str(filters['category_id'] or ''),
        'item': str(filters['item_id'] or ''),
        'bucket': filters.get('bucket') or '',
        'focus': filters.get('focus') or '',
        'tab': filters.get('tab') or '',
    }
    for key, value in updates.items():
        if key == 'category_id':
            merged['category'] = str(value or '')
        elif key == 'item_id':
            merged['item'] = str(value or '')
        elif key in {'start', 'end'} and value:
            merged[key] = value.strftime('%d/%m/%Y') if hasattr(value, 'strftime') else str(value)
        elif key == 'clear_item' and value:
            merged['item'] = ''
        else:
            merged[key] = '' if value is None else str(value)
    clean_pairs = [(key, value) for key, value in merged.items() if value not in {'', None}]
    if not clean_pairs:
        return ''
    return '?' + urlencode(clean_pairs)


def _inventory_analysis_context(request, include_trend=True, include_report=True):
    tenant = get_current_tenant(request)
    filters = _inventory_analysis_filters(request)
    now_value = timezone.now()
    if timezone.is_naive(now_value):
        now_value = timezone.make_aware(now_value, timezone.get_current_timezone())
    today = timezone.localtime(now_value).date()

    base_items_qs = tenant_qs(request, InventoryItem).filter(isDeleted=False).select_related('category', 'unit', 'taxProfile')
    category_options = list(
        tenant_qs(request, InventoryCategory).filter(isDeleted=False, isActive=True).order_by('name')
    )
    item_filter_qs = base_items_qs
    if filters['category_id']:
        item_filter_qs = item_filter_qs.filter(category_id=filters['category_id'])
    item_options = list(item_filter_qs.order_by('name'))

    items_qs = base_items_qs
    if filters['category_id']:
        items_qs = items_qs.filter(category_id=filters['category_id'])
    if filters['item_id']:
        items_qs = items_qs.filter(pk=filters['item_id'])
    items = list(items_qs.order_by('name', 'id'))
    item_ids = [item.id for item in items]

    metrics = defaultdict(_empty_item_metrics)
    bucket_totals = {
        value: {'label': label, 'qty': 0.0, 'value': 0.0, 'item_ids': set()}
        for value, label in InventoryItem.StockBucket.choices
    }

    trend_window_end = filters['end'] or today
    trend_window_start = filters['start'] or (trend_window_end - timedelta(days=29))
    trend_daily = defaultdict(lambda: {'inward': 0.0, 'outward': 0.0})
    trend_weekly = defaultdict(lambda: {'inward': 0.0, 'outward': 0.0})
    trend_monthly = defaultdict(lambda: {'inward': 0.0, 'outward': 0.0})

    if item_ids:
        stock_qs = tenant_qs(request, InventoryStock).filter(isDeleted=False, item_id__in=item_ids)
        if filters['bucket']:
            stock_qs = stock_qs.filter(stockBucket=filters['bucket'])
        for stock in stock_qs:
            item_metrics = metrics[stock.item_id]
            qty = float(stock.quantityOnHand or 0.0)
            value = qty * float(stock.item.defaultPurchasePrice or 0.0)
            item_metrics['current_qty'] += qty
            item_metrics['current_value'] += value
            item_metrics['current_buckets'].add(stock.stockBucket)
            bucket_totals[stock.stockBucket]['qty'] += qty
            bucket_totals[stock.stockBucket]['value'] += value
            bucket_totals[stock.stockBucket]['item_ids'].add(stock.item_id)

        ledger_qs = tenant_qs(request, InventoryLedgerEntry).filter(isDeleted=False, item_id__in=item_ids)
        if filters['bucket']:
            ledger_qs = ledger_qs.filter(stockBucket=filters['bucket'])

        for row in ledger_qs.values('item_id', 'entryDate', 'quantityDelta', 'totalValue', 'sourceType'):
            item_metrics = metrics[row['item_id']]
            entry_date = row['entryDate']
            quantity_delta = float(row['quantityDelta'] or 0.0)
            total_value = float(row['totalValue'] or 0.0)
            source_type = row['sourceType']

            if quantity_delta > 0 and entry_date and (item_metrics['last_inward'] is None or entry_date > item_metrics['last_inward']):
                item_metrics['last_inward'] = entry_date
            if quantity_delta < 0 and entry_date and (item_metrics['last_outward'] is None or entry_date > item_metrics['last_outward']):
                item_metrics['last_outward'] = entry_date

            in_range = True
            if filters['start'] and (entry_date is None or entry_date < filters['start']):
                in_range = False
            if filters['end'] and (entry_date is None or entry_date > filters['end']):
                in_range = False
            if not in_range:
                if not (entry_date and trend_window_start <= entry_date <= trend_window_end):
                    continue

            if in_range:
                if quantity_delta > 0:
                    item_metrics['inward_qty'] += quantity_delta
                    item_metrics['inward_value'] += abs(total_value)
                elif quantity_delta < 0:
                    item_metrics['outward_qty'] += abs(quantity_delta)
                    item_metrics['outward_value'] += abs(total_value)

                if source_type == InventoryLedgerEntry.SourceType.PURCHASE:
                    item_metrics['purchase_qty'] += max(quantity_delta, 0.0)
                    item_metrics['purchase_value'] += abs(total_value)
                elif source_type == InventoryLedgerEntry.SourceType.PURCHASE_RETURN:
                    item_metrics['purchase_return_qty'] += abs(quantity_delta)
                    item_metrics['purchase_return_value'] += abs(total_value)
                elif source_type in {InventoryLedgerEntry.SourceType.SALES, InventoryLedgerEntry.SourceType.CASH_COUNTER}:
                    if quantity_delta < 0:
                        item_metrics['sold_qty'] += abs(quantity_delta)
                        item_metrics['sold_value'] += abs(total_value)
                    elif quantity_delta > 0:
                        item_metrics['sales_return_qty'] += quantity_delta
                        item_metrics['sales_return_value'] += abs(total_value)
                elif source_type == InventoryLedgerEntry.SourceType.SALES_RETURN:
                    item_metrics['sales_return_qty'] += max(quantity_delta, 0.0)
                    item_metrics['sales_return_value'] += abs(total_value)
                elif source_type == InventoryLedgerEntry.SourceType.ADJUSTMENT:
                    item_metrics['adjustment_qty'] += quantity_delta

            if include_trend and entry_date and trend_window_start <= entry_date <= trend_window_end:
                if quantity_delta > 0:
                    trend_daily[entry_date]['inward'] += quantity_delta
                elif quantity_delta < 0:
                    trend_daily[entry_date]['outward'] += abs(quantity_delta)
                week_start = entry_date - timedelta(days=entry_date.weekday())
                month_key = entry_date.replace(day=1)
                if quantity_delta > 0:
                    trend_weekly[week_start]['inward'] += quantity_delta
                    trend_monthly[month_key]['inward'] += quantity_delta
                elif quantity_delta < 0:
                    trend_weekly[week_start]['outward'] += abs(quantity_delta)
                    trend_monthly[month_key]['outward'] += abs(quantity_delta)

        if include_report:
            sales_line_qs = tenant_qs(request, SalesLine).filter(
                isDeleted=False,
                sales__isDeleted=False,
                item_id__in=item_ids,
            ).select_related('sales')
            if filters['bucket']:
                sales_line_qs = sales_line_qs.filter(stockBucket=filters['bucket'])
            for line in sales_line_qs.order_by('-sales__buildDate', '-id'):
                item_metrics = metrics[line.item_id]
                if item_metrics['latest_sale'] is not None:
                    continue
                item_metrics['latest_sale'] = {
                    'date': line.sales.buildDate,
                    'quantity': round(float(line.quantity or 0.0), 2),
                    'rate': round(float(line.unitPrice or 0.0), 2),
                    'bill_number': line.sales.invoiceNumber or line.sales.salesID or line.sales.paymentID or f'Sale #{line.sales_id}',
                    'url': reverse('homeApp:edit_sales', args=[line.sales_id]),
                    'source': 'Sales',
                }

            counter_line_qs = tenant_qs(request, CashCounterLine).filter(
                isDeleted=False,
                cashCounter__isDeleted=False,
                cashCounter__mode__in=['Cash', 'Card', 'Credit', 'Mix'],
                item_id__in=item_ids,
            ).select_related('cashCounter')
            if filters['bucket']:
                counter_line_qs = counter_line_qs.filter(stockBucket=filters['bucket'])
            for line in counter_line_qs.order_by('-cashCounter__entryDate', '-id'):
                item_metrics = metrics[line.item_id]
                if item_metrics['latest_sale'] is not None:
                    continue
                entry_date = line.cashCounter.entryDate.date() if getattr(line.cashCounter, 'entryDate', None) and hasattr(line.cashCounter.entryDate, 'date') else line.cashCounter.entryDate
                item_metrics['latest_sale'] = {
                    'date': entry_date,
                    'quantity': round(float(line.quantity or 0.0), 2),
                    'rate': round(float(line.unitPrice or 0.0), 2),
                    'bill_number': line.cashCounter.invoiceNumber or line.cashCounter.counterID or f'Counter #{line.cashCounter_id}',
                    'url': reverse('homeApp:edit_cash_counter', args=[line.cashCounter_id]),
                    'source': 'Cash Counter',
                }

    summary = {
        'current_qty': 0.0,
        'current_value': 0.0,
        'inward_qty': 0.0,
        'inward_value': 0.0,
        'outward_qty': 0.0,
        'outward_value': 0.0,
        'negative_count': 0,
        'low_stock_count': 0,
        'zero_stock_count': 0,
        'missing_master_count': 0,
        'dead_stock_count': 0,
    }
    aging_buckets = [
        {'key': '0_30', 'label': '0-30 Days', 'count': 0, 'qty': 0.0, 'value': 0.0},
        {'key': '31_60', 'label': '31-60 Days', 'count': 0, 'qty': 0.0, 'value': 0.0},
        {'key': '61_90', 'label': '61-90 Days', 'count': 0, 'qty': 0.0, 'value': 0.0},
        {'key': '91_plus', 'label': '90+ Days', 'count': 0, 'qty': 0.0, 'value': 0.0},
    ]
    age_lookup = {bucket['key']: bucket for bucket in aging_buckets}
    category_summary = defaultdict(lambda: {'category_id': None, 'name': 'Uncategorised', 'qty': 0.0, 'value': 0.0, 'items': 0})
    all_detail_rows = []
    negative_rows = []
    low_stock_rows = []
    missing_master_rows = []
    below_margin_rows = []

    for item in items:
        item_metrics = metrics[item.id]
        current_qty = round(item_metrics['current_qty'], 2)
        current_value = round(item_metrics['current_value'], 2)
        sold_qty = round(item_metrics['sold_qty'], 2)
        purchase_qty = round(item_metrics['purchase_qty'], 2)
        sales_return_qty = round(item_metrics['sales_return_qty'], 2)
        purchase_return_qty = round(item_metrics['purchase_return_qty'], 2)
        avg_purchase_rate = round((item_metrics['purchase_value'] / item_metrics['purchase_qty']) if item_metrics['purchase_qty'] else float(item.defaultPurchasePrice or 0.0), 2)
        avg_sale_rate = round((item_metrics['sold_value'] / item_metrics['sold_qty']) if item_metrics['sold_qty'] else float(item.defaultSalesPrice or 0.0), 2)
        margin = round(avg_sale_rate - avg_purchase_rate, 2)
        margin_pct = round((margin / avg_sale_rate) * 100.0, 2) if avg_sale_rate else 0.0
        reorder_level = float(item.reorderLevel or item.minimumStockLevel or 0.0)
        low_stock = reorder_level > 0 and current_qty <= reorder_level and current_qty > 0
        negative_stock = current_qty < 0
        zero_stock = current_qty == 0
        missing_master = not item.category_id or not item.unit_id or (item.defaultStockBucket == InventoryItem.StockBucket.GST and not item.taxProfile_id)
        last_inward = item_metrics['last_inward']
        age_days = (today - last_inward).days if last_inward else None
        dead_stock = current_qty > 0 and (item_metrics['last_outward'] is None or (today - item_metrics['last_outward']).days >= 90)

        if negative_stock:
            status = 'Negative'
        elif low_stock:
            status = 'Low Stock'
        elif zero_stock:
            status = 'Out of Stock'
        elif dead_stock:
            status = 'Dead Stock'
        elif sold_qty == 0 and current_qty > 0:
            status = 'Idle'
        else:
            status = 'Healthy'

        if current_qty > 0:
            if age_days is None or age_days > 90:
                age_key = '91_plus'
            elif age_days > 60:
                age_key = '61_90'
            elif age_days > 30:
                age_key = '31_60'
            else:
                age_key = '0_30'
            age_lookup[age_key]['count'] += 1
            age_lookup[age_key]['qty'] += current_qty
            age_lookup[age_key]['value'] += current_value

        summary['current_qty'] += current_qty
        summary['current_value'] += current_value
        summary['inward_qty'] += item_metrics['inward_qty']
        summary['inward_value'] += item_metrics['inward_value']
        summary['outward_qty'] += item_metrics['outward_qty']
        summary['outward_value'] += item_metrics['outward_value']
        summary['negative_count'] += 1 if negative_stock else 0
        summary['low_stock_count'] += 1 if low_stock else 0
        summary['zero_stock_count'] += 1 if zero_stock else 0
        summary['missing_master_count'] += 1 if missing_master else 0
        summary['dead_stock_count'] += 1 if dead_stock else 0

        category_key = item.category_id or 0
        category_summary[category_key]['category_id'] = item.category_id
        category_summary[category_key]['name'] = item.category.name if item.category_id else 'Uncategorised'
        category_summary[category_key]['qty'] += current_qty
        category_summary[category_key]['value'] += current_value
        category_summary[category_key]['items'] += 1

        row = {
            'item': item,
            'bucket_label': _format_bucket_label(item_metrics['current_buckets']) if not filters['bucket'] else dict(InventoryItem.StockBucket.choices).get(filters['bucket'], 'Mixed'),
            'current_qty': current_qty,
            'current_value': current_value,
            'purchase_qty': purchase_qty,
            'sold_qty': sold_qty,
            'sales_return_qty': sales_return_qty,
            'purchase_return_qty': purchase_return_qty,
            'avg_purchase_rate': avg_purchase_rate,
            'avg_sale_rate': avg_sale_rate,
            'margin': margin,
            'margin_pct': margin_pct,
            'age_days': age_days,
            'status': status,
            'missing_master': missing_master,
            'low_stock': low_stock,
            'negative_stock': negative_stock,
            'return_ratio': round((sales_return_qty / sold_qty) * 100.0, 2) if sold_qty else 0.0,
            'latest_sale': item_metrics['latest_sale'],
        }
        all_detail_rows.append(row)
        if negative_stock:
            negative_rows.append(row)
        if low_stock:
            low_stock_rows.append(row)
        if missing_master:
            missing_master_rows.append(row)
        if sold_qty > 0 and margin < 0:
            below_margin_rows.append(row)

    all_detail_rows.sort(key=lambda row: row['current_value'], reverse=True)
    detail_rows = []
    if include_report:
        detail_rows = all_detail_rows
        if filters['focus'] == 'negative':
            detail_rows = [row for row in all_detail_rows if row['negative_stock']]
        elif filters['focus'] == 'dead':
            detail_rows = [row for row in all_detail_rows if row['status'] == 'Dead Stock']
        elif filters['focus'] == 'low':
            detail_rows = [row for row in all_detail_rows if row['low_stock']]
        elif filters['focus'] == 'missing':
            detail_rows = [row for row in all_detail_rows if row['missing_master']]

    best_sellers = sorted(all_detail_rows, key=lambda row: (row['sold_qty'], row['current_value']), reverse=True)[:8]
    slow_movers = [row for row in sorted(all_detail_rows, key=lambda row: (row['sold_qty'], row['current_qty'])) if row['current_qty'] > 0 and row['sold_qty'] > 0][:8]
    dead_stock_rows = [row for row in sorted(all_detail_rows, key=lambda row: (row['age_days'] or 9999), reverse=True) if row['status'] == 'Dead Stock'][:8]
    high_return_rows = [row for row in sorted(all_detail_rows, key=lambda row: row['return_ratio'], reverse=True) if row['return_ratio'] > 0][:8]
    category_rows = sorted(category_summary.values(), key=lambda row: row['value'], reverse=True)[:8]

    category_total_value = sum(row['value'] for row in category_rows)
    top_three_categories = category_rows[:3]
    top_three_share = round((sum(row['value'] for row in top_three_categories) / category_total_value) * 100.0, 1) if category_total_value else 0.0

    def _series_payload(source, label_formatter):
        labels, inward, outward = [], [], []
        for key in sorted(source.keys()):
            labels.append(label_formatter(key))
            inward.append(round(source[key]['inward'], 2))
            outward.append(round(source[key]['outward'], 2))
        return {'labels': labels, 'inward': inward, 'outward': outward}

    trend_payload = {
        'day': {'labels': [], 'inward': [], 'outward': []},
        'week': {'labels': [], 'inward': [], 'outward': []},
        'month': {'labels': [], 'inward': [], 'outward': []},
    }
    if include_trend:
        trend_payload = {
            'day': _series_payload(trend_daily, lambda key: key.strftime('%d %b')),
            'week': _series_payload(trend_weekly, lambda key: f"Week of {key.strftime('%d %b')}"),
            'month': _series_payload(trend_monthly, lambda key: key.strftime('%b %Y')),
        }

    insight_lines = [
        f"Inventory health today: {summary['current_qty']:.2f} units worth Rs. {summary['current_value']:.2f} are currently on hand.",
        f"{len(top_three_categories)} categories hold {top_three_share:.1f}% of visible stock value." if top_three_categories else 'No category concentration signal is available yet.',
        f"{summary['low_stock_count']} items are below reorder level.",
        f"{summary['dead_stock_count']} items have no outward movement for 90+ days.",
        f"{len(below_margin_rows)} items are being sold below average purchase rate.",
    ]

    filter_badges = []
    if filters['start'] or filters['end']:
        if filters['start'] and filters['end']:
            filter_badges.append(f"{filters['start'].strftime('%d %b %Y')} - {filters['end'].strftime('%d %b %Y')}")
        elif filters['start']:
            filter_badges.append(f"From {filters['start'].strftime('%d %b %Y')}")
        else:
            filter_badges.append(f"Until {filters['end'].strftime('%d %b %Y')}")
    if filters['category_id']:
        selected_category = next((category for category in category_options if category.id == filters['category_id']), None)
        if selected_category:
            filter_badges.append(selected_category.name)
    if filters['item_id']:
        selected_item = next((item for item in item_options if item.id == filters['item_id']), None)
        if selected_item:
            filter_badges.append(selected_item.name)
    if filters['bucket']:
        filter_badges.append(dict(InventoryItem.StockBucket.choices).get(filters['bucket'], filters['bucket']))

    return {
        'inventory_enabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advanced_invoicing_enabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'can_manage_non_gst_shadow': request.user.is_superuser or request.user.has_perm('home.can_view_party_credentials'),
        'filters': filters,
        'filter_badges': filter_badges,
        'drilldowns': {
            'negative': _build_analysis_query(filters, focus='negative', tab='inv-detail'),
            'dead': _build_analysis_query(filters, focus='dead', tab='inv-detail'),
            'low': _build_analysis_query(filters, focus='low', tab='inv-detail'),
            'missing': _build_analysis_query(filters, focus='missing', tab='inv-detail'),
        },
        'today': today,
        'category_options': category_options,
        'item_options': item_options,
        'bucket_choices': InventoryItem.StockBucket.choices,
        'summary': summary,
        'bucket_rows': [
            {
                'label': data['label'],
                'qty': round(data['qty'], 2),
                'value': round(data['value'], 2),
                'items': len(data['item_ids']),
                'drilldown_url': _build_analysis_query(filters, bucket=value, clear_item=True, tab='inv-detail', focus=''),
            }
            for value, data in bucket_totals.items()
        ],
        'category_rows': [
            {
                **row,
                'drilldown_url': _build_analysis_query(filters, category_id=row['category_id'] or '', clear_item=True, tab='inv-detail', focus=''),
            }
            for row in category_rows
        ],
        'best_sellers': best_sellers,
        'slow_movers': slow_movers,
        'dead_stock_rows': dead_stock_rows,
        'high_return_rows': high_return_rows,
        'aging_buckets': aging_buckets,
        'negative_rows': sorted(negative_rows, key=lambda row: row['current_qty'])[:8],
        'low_stock_rows': sorted(low_stock_rows, key=lambda row: (row['item'].reorderLevel or row['item'].minimumStockLevel or 0.0) - row['current_qty'], reverse=True)[:8],
        'missing_master_rows': missing_master_rows[:8],
        'detail_rows': detail_rows,
        'trend_payload': trend_payload,
        'insight_lines': insight_lines,
        'page_title': 'Inventory Analysis',
        'page_subtitle': 'One screen for current stock, movement, profitability, aging, and exceptions.',
        'range_days': ((filters['end'] or today) - (filters['start'] or max(today - timedelta(days=29), today - timedelta(days=29)))).days + 1,
    }


@permission_required_any(
    'home.can_view_inventory_reports',
    'home.view_inventoryitem',
    'home.add_inventoryitem',
    'home.change_inventoryitem',
    'home.can_manage_inventory',
    'home.can_view_party_credentials',
)
def inventory_analysis_page(request):
    tenant = get_current_tenant(request)
    if tenant is not None and not getattr(tenant, 'inventoryEnabled', True):
        return redirect('homeApp:admin_home')
    return render(request, 'home/inventory/inventory_analysis.html', _inventory_analysis_context(request, include_trend=False, include_report=False))


@permission_required_any(
    'home.can_view_inventory_reports',
    'home.view_inventoryitem',
    'home.add_inventoryitem',
    'home.change_inventoryitem',
    'home.can_manage_inventory',
    'home.can_view_party_credentials',
)
def inventory_analysis_trend_api(request):
    tenant = get_current_tenant(request)
    if tenant is not None and not getattr(tenant, 'inventoryEnabled', True):
        return JsonResponse({'message': 'Inventory is disabled for this tenant.'}, status=403)
    context = _inventory_analysis_context(request, include_trend=True, include_report=False)
    return JsonResponse({'message': 'success', 'trend': context['trend_payload']})


@permission_required_any(
    'home.can_view_inventory_reports',
    'home.view_inventoryitem',
    'home.add_inventoryitem',
    'home.change_inventoryitem',
    'home.can_manage_inventory',
    'home.can_view_party_credentials',
)
def inventory_analysis_item_report_api(request):
    tenant = get_current_tenant(request)
    if tenant is not None and not getattr(tenant, 'inventoryEnabled', True):
        return JsonResponse({'message': 'Inventory is disabled for this tenant.'}, status=403)
    context = _inventory_analysis_context(request, include_trend=False, include_report=True)
    rows_html = render_to_string(
        'home/inventory/partials/inventory_analysis_item_rows.html',
        {'detail_rows': context['detail_rows']},
        request=request,
    )
    return JsonResponse({'message': 'success', 'rows_html': rows_html})


@permission_required_any('home.add_purchaseinvoice', 'home.change_purchaseinvoice', 'home.view_purchaseinvoice', 'home.can_view_party_credentials')
def purchase_list(request):
    tenant = get_current_tenant(request)
    if tenant is not None and not getattr(tenant, 'inventoryEnabled', True):
        return redirect('homeApp:admin_home')
    context = {
        'banks': tenant_qs(request, Bank).filter(isDeleted=False).order_by('name'),
        'inventory_enabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advanced_invoicing_enabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'can_manage_non_gst_shadow': request.user.is_superuser or request.user.has_perm('home.can_view_party_credentials'),
    }
    return render(request, 'home/inventory/purchase.html', context)


@permission_required_any('home.add_purchaseinvoice', 'home.change_purchaseinvoice', 'home.view_purchaseinvoice', 'home.can_view_party_credentials')
def purchase_detail(request, id):
    tenant = get_current_tenant(request)
    if tenant is not None and not getattr(tenant, 'inventoryEnabled', True):
        return redirect('homeApp:admin_home')
    purchase = get_object_or_404(
        tenant_qs(request, PurchaseInvoice).select_related('partyID', 'createdBy'),
        pk=id,
        isDeleted=False,
    )
    context = {
        'purchase': purchase,
        'purchase_lines': purchase.lines.filter(isDeleted=False).select_related('item', 'batch').order_by('id'),
        'purchase_revisions': purchase.revisions.filter(isDeleted=False).select_related('changedBy').order_by('-datetime'),
        'inventory_enabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advanced_invoicing_enabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
    }
    return render(request, 'home/inventory/purchase_detail.html', context)


@permission_required_any('home.can_view_party_credentials', 'home.can_manage_staff')
def non_gst_shadow_page(request):
    tenant = get_current_tenant(request)
    inventory_enabled = bool(getattr(tenant, 'inventoryEnabled', True))
    advanced_invoicing_enabled = bool(getattr(tenant, 'advancedInvoicingEnabled', True))
    if tenant is not None and not inventory_enabled and not advanced_invoicing_enabled:
        return redirect('homeApp:admin_home')
    return render(request, 'home/inventory/non_gst_shadow.html', {
        'tenant': tenant,
        'inventory_enabled': inventory_enabled,
        'advanced_invoicing_enabled': advanced_invoicing_enabled,
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'non_gst_shadow_has_code': bool(getattr(tenant, 'nonGstShadowCodeHash', '')),
    })
