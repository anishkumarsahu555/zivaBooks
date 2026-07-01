from calendar import month_name, monthrange
from datetime import datetime

from django.db.models import Sum
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import CSS, HTML

from home.models import CashCounter, Collection, Sales
from home.services.cash_counter import can_filter_all_cash_counter, cash_counter_list_base_qs
from home.services.collections import can_filter_all_collections
from home.services.reports import pdf_base_context, xlsx_report_response
from home.services.sales import can_filter_all_sales
from home.tenancy import get_current_tenant, tenant_qs


def _month_reference(request):
    raw_date = request.GET.get('cDate') or request.GET.get('monthDate')
    if raw_date:
        try:
            return datetime.strptime(raw_date, '%d/%m/%Y').date()
        except Exception:
            pass
    return datetime.today().date()


def _daily_business_querysets(request, report_date):
    sales_qs = tenant_qs(request, Sales).filter(isDeleted=False, buildDate=report_date)
    if not can_filter_all_sales(request.user):
        sales_qs = sales_qs.filter(createdBy__user_ID_id=request.user.pk)
    collection_qs = tenant_qs(request, Collection).filter(isDeleted=False, isApproved=True, collectionDateTime__date=report_date)
    if not can_filter_all_collections(request.user):
        collection_qs = collection_qs.filter(collectedBy__user_ID_id=request.user.pk)
    cash_counter_qs = cash_counter_list_base_qs(request).filter(isDeleted=False, datetime__date=report_date)
    if not can_filter_all_cash_counter(request.user):
        cash_counter_qs = cash_counter_qs.filter(createdBy__user_ID_id=request.user.pk)
    return sales_qs, collection_qs, cash_counter_qs


def _daily_summary_data(request, report_date):
    sales_qs, collection_qs, cash_counter_qs = _daily_business_querysets(request, report_date)
    sales_total = float(sales_qs.aggregate(total=Sum('amount'))['total'] or 0.0)
    collection_total = float(collection_qs.aggregate(total=Sum('paidAmount'))['total'] or 0.0)
    cash_counter_total = float(cash_counter_qs.aggregate(total=Sum('amount'))['total'] or 0.0)
    expense_total = float(cash_counter_qs.filter(mode='Expense').aggregate(total=Sum('amount'))['total'] or 0.0)
    return_total = float(cash_counter_qs.filter(mode='Return').aggregate(total=Sum('amount'))['total'] or 0.0)
    pending_sales = sales_qs.filter(isApproved=False).count()
    pending_collection_qs = tenant_qs(request, Collection).filter(
        isDeleted=False,
        collectionDateTime__date=report_date,
        isApproved=False,
    )
    if not can_filter_all_collections(request.user):
        pending_collection_qs = pending_collection_qs.filter(collectedBy__user_ID_id=request.user.pk)
    pending_collections = pending_collection_qs.count()
    return {
        'sales_qs': sales_qs,
        'collection_qs': collection_qs,
        'cash_counter_qs': cash_counter_qs,
        'sales_total': sales_total,
        'collection_total': collection_total,
        'cash_counter_total': cash_counter_total,
        'expense_total': expense_total,
        'return_total': return_total,
        'pending_sales': pending_sales,
        'pending_collections': pending_collections,
        'top_sales_staff': list(sales_qs.values('createdBy__name').annotate(total=Sum('amount')).order_by('-total')[:5]),
        'top_collection_staff': list(collection_qs.values('collectedBy__name').annotate(total=Sum('paidAmount')).order_by('-total')[:5]),
    }


def generate_daily_summary_pdf(request):
    raw_date = request.GET.get('cDate')
    report_date = datetime.strptime(raw_date, '%d/%m/%Y').date() if raw_date else datetime.today().date()
    data = _daily_summary_data(request, report_date)
    context = {
        'report_date': report_date,
        'sales_total': data['sales_total'],
        'sales_count': data['sales_qs'].count(),
        'collection_total': data['collection_total'],
        'collection_count': data['collection_qs'].count(),
        'cash_counter_total': data['cash_counter_total'],
        'expense_total': data['expense_total'],
        'return_total': data['return_total'],
        'pending_sales': data['pending_sales'],
        'pending_collections': data['pending_collections'],
        'top_sales_staff': data['top_sales_staff'],
        'top_collection_staff': data['top_collection_staff'],
        'generated_on': timezone.now(),
    }
    context.update(pdf_base_context(request, [
        f'Date: {report_date.strftime("%d/%m/%Y")}',
        'Scope: Daily business summary',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename=daily-summary-{report_date.strftime("%Y-%m-%d")}.pdf'
    html = render_to_string("home/report/dailySummaryPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A4 portrait; margin: .7cm; }')])
    return response


def generate_daily_summary_excel(request):
    raw_date = request.GET.get('cDate')
    report_date = datetime.strptime(raw_date, '%d/%m/%Y').date() if raw_date else datetime.today().date()
    data = _daily_summary_data(request, report_date)
    return xlsx_report_response('daily-summary', [{
        'title': 'Daily Summary',
        'report_title': 'Daily Summary Report',
        'report_subtitle': 'Daily owner snapshot',
        'metadata': [
            ('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')),
            ('Date', report_date.strftime('%d/%m/%Y')),
            ('Generated By', pdf_base_context(request).get('pdf_generated_by')),
        ],
        'tables': [
            {'title': 'Business Summary', 'headers': ['Metric', 'Value'], 'rows': [
                ['Sales Total', data['sales_total']],
                ['Sales Count', data['sales_qs'].count()],
                ['Collection Total', data['collection_total']],
                ['Collection Count', data['collection_qs'].count()],
                ['Cash Counter Total', data['cash_counter_total']],
                ['Expense Total', data['expense_total']],
                ['Return Total', data['return_total']],
                ['Pending Sales', data['pending_sales']],
                ['Pending Collections', data['pending_collections']],
            ]},
            {'title': 'Top Sales Staff', 'headers': ['Staff', 'Amount'], 'rows': [[row.get('createdBy__name') or '-', float(row.get('total') or 0.0)] for row in data['top_sales_staff']]},
            {'title': 'Top Collection Staff', 'headers': ['Staff', 'Amount'], 'rows': [[row.get('collectedBy__name') or '-', float(row.get('total') or 0.0)] for row in data['top_collection_staff']]},
        ],
    }])


def generate_monthly_business_summary_excel(request):
    reference = _month_reference(request)
    month_start = reference.replace(day=1)
    month_end = reference.replace(day=monthrange(reference.year, reference.month)[1])
    sales_qs = tenant_qs(request, Sales).filter(isDeleted=False, buildDate__range=[month_start, month_end])
    if not can_filter_all_sales(request.user):
        sales_qs = sales_qs.filter(createdBy__user_ID_id=request.user.pk)
    collection_qs = tenant_qs(request, Collection).filter(isDeleted=False, isApproved=True, collectionDateTime__date__range=[month_start, month_end])
    if not can_filter_all_collections(request.user):
        collection_qs = collection_qs.filter(collectedBy__user_ID_id=request.user.pk)
    daily_sales = {item['buildDate']: float(item['total'] or 0.0) for item in sales_qs.values('buildDate').annotate(total=Sum('amount'))}
    daily_collections = {item['collectionDateTime__date']: float(item['total'] or 0.0) for item in collection_qs.values('collectionDateTime__date').annotate(total=Sum('paidAmount'))}
    rows = []
    for day in range(1, month_end.day + 1):
        current = month_start.replace(day=day)
        rows.append([current.strftime('%d/%m/%Y'), daily_sales.get(current, 0.0), daily_collections.get(current, 0.0)])
    return xlsx_report_response('monthly-business-summary', [{
        'title': 'Monthly Business',
        'report_title': 'Monthly Business Summary',
        'report_subtitle': f'{month_name[reference.month]} {reference.year}',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('Month', f'{month_name[reference.month]} {reference.year}'), ('Generated By', pdf_base_context(request).get('pdf_generated_by'))],
        'tables': [
            {'title': 'Monthly Totals', 'headers': ['Metric', 'Value'], 'rows': [['Sales Total', float(sales_qs.aggregate(total=Sum('amount'))['total'] or 0.0)], ['Collection Total', float(collection_qs.aggregate(total=Sum('paidAmount'))['total'] or 0.0)], ['Sales Count', sales_qs.count()], ['Collection Count', collection_qs.count()]]},
            {'title': 'Day Wise Movement', 'headers': ['Date', 'Sales', 'Collections'], 'rows': rows},
        ],
    }])
