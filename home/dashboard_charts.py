from datetime import datetime

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.views.decorators.cache import cache_page

from .models import Bank, CashCounter, Collection, Order, Party, Sales
from .navigation import _effective_permission_names
from .tenancy import tenant_qs


def _has_any_perm(request_or_user, perm_names):
    effective_permissions = _effective_permission_names(request_or_user)
    return any(perm_name in effective_permissions for perm_name in perm_names)


def build_dashboard_chart_cards(request):
    charts = []
    if _has_any_perm(request, ['home.view_sales', 'home.add_sales', 'home.change_sales', 'home.can_manage_sales', 'home.view_collection', 'home.add_collection', 'home.change_collection', 'home.can_approve_collection']):
        charts.append({
            'id': 'dashboardRevenueTrendChart',
            'key': 'revenue_trend',
            'title': 'Sales vs Collections',
            'subtitle': 'Six-month movement for accessible sales and collection records.',
            'type': 'line',
            'size': 'wide',
        })
    if _has_any_perm(request, ['home.view_order', 'home.add_order', 'home.change_order', 'home.can_manage_orders', 'home.can_approve_orders']):
        charts.append({
            'id': 'dashboardOrderStatusChart',
            'key': 'order_status',
            'title': 'Order Status Mix',
            'subtitle': 'Current order pipeline split by status.',
            'type': 'doughnut',
        })
    if _has_any_perm(request, ['home.can_manage_parties', 'home.can_view_party_credentials']):
        charts.append({
            'id': 'dashboardPartyAssignmentChart',
            'key': 'party_assignment',
            'title': 'Party Assignment',
            'subtitle': 'Coverage health for assigned and unassigned parties.',
            'type': 'doughnut',
        })
    if _has_any_perm(request, ['home.view_cashcounter', 'home.view_collectioncashcounter', 'home.can_manage_cash_counter']):
        charts.append({
            'id': 'dashboardCashMixChart',
            'key': 'cash_mix',
            'title': 'Cash Counter Mix',
            'subtitle': 'Current month counter entries by payment mode.',
            'type': 'bar',
        })
    if _has_any_perm(request, ['home.view_bank', 'home.add_bank', 'home.change_bank', 'home.delete_bank', 'home.can_view_party_credentials']):
        charts.append({
            'id': 'dashboardBankHealthChart',
            'key': 'bank_health',
            'title': 'Bank Data Health',
            'subtitle': 'Bank masters with and without account numbers.',
            'type': 'polarArea',
        })
    return charts


def _month_start(value):
    return value.replace(day=1)


def _shift_month(value, offset):
    month_index = value.month - 1 + offset
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return value.replace(year=year, month=month, day=1)


def _dashboard_month_axis(month_count=6):
    today = datetime.today().date()
    first_month = _shift_month(_month_start(today), -(month_count - 1))
    months = [_shift_month(first_month, index) for index in range(month_count)]
    return {
        'start': first_month,
        'months': months,
        'labels': [month.strftime('%b %y') for month in months],
        'keys': [month.isoformat() for month in months],
    }


def _month_key(value):
    if hasattr(value, 'date'):
        value = value.date()
    return _month_start(value).isoformat() if value else None


def _monthly_sum(qs, date_field, amount_field, axis):
    rows = (
        qs
        .filter(**{f'{date_field}__gte': axis['start']})
        .annotate(month=TruncMonth(date_field))
        .values('month')
        .annotate(total=Sum(amount_field))
        .order_by('month')
    )
    totals = {_month_key(row['month']): float(row['total'] or 0) for row in rows}
    return [round(totals.get(key, 0), 2) for key in axis['keys']]


def _status_label(choices, value):
    return dict(choices).get(value, value or 'Unknown')


@cache_page(60 * 5)
def get_dashboard_charts_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'forbidden'}, status=403)

    axis = _dashboard_month_axis()
    charts = {}
    user = request.user
    effective_permissions = _effective_permission_names(request)

    def can_view(*perm_names):
        return any(perm_name in effective_permissions for perm_name in perm_names)

    can_view_sales_chart = can_view(
        'home.view_sales',
        'home.add_sales',
        'home.change_sales',
        'home.can_manage_sales',
        'home.can_approve_sales',
    )
    can_view_collection_chart = can_view(
        'home.view_collection',
        'home.add_collection',
        'home.change_collection',
        'home.can_approve_collection',
    )
    if can_view_sales_chart or can_view_collection_chart:
        sales_qs = tenant_qs(request, Sales).filter(isDeleted=False)
        collection_qs = tenant_qs(request, Collection).filter(isDeleted=False, collectionDateTime__isnull=False)
        can_filter_all_sales_chart = can_view(
            'home.can_manage_staff',
            'home.can_view_party_credentials',
            'home.can_manage_sales',
            'home.can_approve_sales',
        )
        can_filter_all_collections_chart = can_view('home.can_approve_collection', 'home.can_view_party_credentials')
        if can_view_sales_chart and not can_filter_all_sales_chart:
            sales_qs = sales_qs.filter(createdBy__user_ID_id=user.pk)
        if can_view_collection_chart and not can_filter_all_collections_chart:
            collection_qs = collection_qs.filter(collectedBy__user_ID_id=user.pk)
        charts['revenue_trend'] = {
            'labels': axis['labels'],
            'sales': _monthly_sum(sales_qs, 'buildDate', 'amount', axis) if can_view_sales_chart else [0] * len(axis['labels']),
            'collections': _monthly_sum(collection_qs, 'collectionDateTime', 'paidAmount', axis) if can_view_collection_chart else [0] * len(axis['labels']),
        }

    if can_view('home.view_order', 'home.add_order', 'home.change_order', 'home.can_manage_orders', 'home.can_approve_orders'):
        orders_qs = tenant_qs(request, Order).filter(isDeleted=False)
        can_filter_all_orders_chart = can_view('home.can_manage_orders', 'home.can_approve_orders', 'home.can_view_party_credentials')
        if not can_filter_all_orders_chart:
            orders_qs = orders_qs.filter(createdBy__user_ID_id=user.pk)
        order_rows = orders_qs.values('status').annotate(total=Count('id')).order_by('status')
        charts['order_status'] = {
            'labels': [_status_label(Order.Status.choices, row['status']) for row in order_rows],
            'values': [row['total'] for row in order_rows],
        }

    if can_view('home.can_manage_parties', 'home.can_view_party_credentials'):
        parties_qs = tenant_qs(request, Party).filter(isDeleted=False)
        assigned_count = parties_qs.filter(assignTo__isnull=False).count()
        unassigned_count = parties_qs.filter(assignTo__isnull=True).count()
        charts['party_assignment'] = {
            'labels': ['Assigned', 'Unassigned'],
            'values': [assigned_count, unassigned_count],
        }

        banks_qs = tenant_qs(request, Bank).filter(isDeleted=False)
        with_account = banks_qs.exclude(accountNumber__isnull=True).exclude(accountNumber__exact='').count()
        missing_account = max(banks_qs.count() - with_account, 0)
        charts['bank_health'] = {
            'labels': ['With Account No', 'Missing Account No'],
            'values': [with_account, missing_account],
        }

    if can_view('home.view_cashcounter', 'home.view_collectioncashcounter', 'home.can_manage_cash_counter'):
        today = datetime.today().date()
        month_start = today.replace(day=1)
        cash_qs = tenant_qs(request, CashCounter).filter(isDeleted=False, entryDate__date__gte=month_start)
        can_filter_all_cash_counter_chart = can_view('home.can_manage_cash_counter', 'home.can_view_party_credentials', 'home.can_approve_collection')
        if not can_filter_all_cash_counter_chart:
            cash_qs = cash_qs.filter(createdBy__user_ID_id=user.pk)
        cash_rows = cash_qs.values('mode').annotate(total=Sum('amount')).order_by('mode')
        charts['cash_mix'] = {
            'labels': [row['mode'] or 'Unknown' for row in cash_rows],
            'values': [round(float(row['total'] or 0), 2) for row in cash_rows],
        }

    return JsonResponse({'charts': charts}, safe=False)
