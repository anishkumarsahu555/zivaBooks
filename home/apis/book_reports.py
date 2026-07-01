from datetime import datetime

from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import CSS, HTML

from home.services.books import (
    bankbook_export_account,
    bankbook_statement_rows,
    can_access_bankbook,
    can_access_cashbook,
    cashbook_export_account,
    cashbook_statement_rows,
    parse_books_date,
)
from home.services.reports import (
    pdf_base_context as _pdf_base_context,
    xlsx_report_response as _xlsx_report_response,
)
from home.tenancy import get_current_tenant


def generate_cashbook_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not can_access_cashbook(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    account = cashbook_export_account(request)
    if account is None:
        return JsonResponse({'message': 'not_found'}, status=404, safe=False)
    start_date = parse_books_date(request.GET.get('startDate')) or datetime.today().date()
    end_date = parse_books_date(request.GET.get('endDate')) or start_date
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    statement = cashbook_statement_rows(request, account, start_date, end_date)
    context = {
        'account': account,
        'start_date': start_date,
        'end_date': end_date,
        'opening': statement['opening'],
        'rows': statement['rows'],
        'total_in': statement['total_in'],
        'total_out': statement['total_out'],
        'closing': statement['closing'],
        'generated_on': timezone.now(),
    }
    context.update(_pdf_base_context(request, [
        f'Account: {account.name}',
        f'From: {start_date.strftime("%d/%m/%Y")}',
        f'To: {end_date.strftime("%d/%m/%Y")}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    safe_name = (account.name or f'cashbook-{account.pk}').replace('/', '-')
    response['Content-Disposition'] = f'attachment; filename=cashbook-{safe_name}.pdf'
    html = render_to_string("home/books/cashbookPDF.html", context)
    base_url = request.build_absolute_uri('/')
    HTML(string=html, base_url=base_url).write_pdf(
        response,
        stylesheets=[CSS(string='@page { size: A4 portrait; margin: 1cm 0.75cm 1.1cm; }')]
    )
    return response


def generate_cashbook_excel(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not can_access_cashbook(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    account = cashbook_export_account(request)
    if account is None:
        return JsonResponse({'message': 'not_found'}, status=404, safe=False)
    start_date = parse_books_date(request.GET.get('startDate')) or datetime.today().date()
    end_date = parse_books_date(request.GET.get('endDate')) or start_date
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    statement = cashbook_statement_rows(request, account, start_date, end_date)
    return _xlsx_report_response('cashbook', [{
        'title': 'Cashbook',
        'report_title': 'Cashbook Statement',
        'report_subtitle': account.name,
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('Account', account.name), ('From', start_date.strftime('%d/%m/%Y')), ('To', end_date.strftime('%d/%m/%Y'))],
        'tables': [
            {'title': 'Summary', 'headers': ['Metric', 'Value'], 'rows': [['Opening', statement['opening']], ['Total In', statement['total_in']], ['Total Out', statement['total_out']], ['Closing', statement['closing']]]},
            {'title': 'Transaction Detail', 'headers': ['Date', 'Particulars', 'Party', 'Receipt', 'Payment', 'Balance', 'Source'], 'rows': [[row['date'].strftime('%d/%m/%Y %I:%M %p') if row['date'] else '-', row['particulars'], row['party'], row['receipt'], row['payment'], row['balance'], row['source']] for row in statement['rows']]},
        ],
    }])


def generate_bankbook_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not can_access_bankbook(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    account = bankbook_export_account(request)
    if account is None:
        return JsonResponse({'message': 'not_found'}, status=404, safe=False)
    start_date = parse_books_date(request.GET.get('startDate')) or datetime.today().date()
    end_date = parse_books_date(request.GET.get('endDate')) or start_date
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    statement = bankbook_statement_rows(request, account, start_date, end_date)
    context = {
        'account': account,
        'bank': getattr(account, 'bank', None),
        'start_date': start_date,
        'end_date': end_date,
        'opening': statement['opening'],
        'rows': statement['rows'],
        'total_in': statement['total_in'],
        'total_out': statement['total_out'],
        'closing': statement['closing'],
        'generated_on': timezone.now(),
    }
    context.update(_pdf_base_context(request, [
        f'Bank: {getattr(getattr(account, "bank", None), "name", account.name)}',
        f'From: {start_date.strftime("%d/%m/%Y")}',
        f'To: {end_date.strftime("%d/%m/%Y")}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    safe_name = (account.name or f'bankbook-{account.pk}').replace('/', '-')
    response['Content-Disposition'] = f'attachment; filename=bankbook-{safe_name}.pdf'
    html = render_to_string("home/books/bankbookPDF.html", context)
    base_url = request.build_absolute_uri('/')
    HTML(string=html, base_url=base_url).write_pdf(
        response,
        stylesheets=[CSS(string='@page { size: A4 portrait; margin: 1cm 0.75cm 1.1cm; }')]
    )
    return response


def generate_bankbook_excel(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not can_access_bankbook(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    account = bankbook_export_account(request)
    if account is None:
        return JsonResponse({'message': 'not_found'}, status=404, safe=False)
    start_date = parse_books_date(request.GET.get('startDate')) or datetime.today().date()
    end_date = parse_books_date(request.GET.get('endDate')) or start_date
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    statement = bankbook_statement_rows(request, account, start_date, end_date)
    bank_name = getattr(getattr(account, 'bank', None), 'name', account.name)
    return _xlsx_report_response('bankbook', [{
        'title': 'Bankbook',
        'report_title': 'Bankbook Statement',
        'report_subtitle': bank_name,
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('Bank', bank_name), ('From', start_date.strftime('%d/%m/%Y')), ('To', end_date.strftime('%d/%m/%Y'))],
        'tables': [
            {'title': 'Summary', 'headers': ['Metric', 'Value'], 'rows': [['Opening', statement['opening']], ['Total In', statement['total_in']], ['Total Out', statement['total_out']], ['Closing', statement['closing']]]},
            {'title': 'Transaction Detail', 'headers': ['Date', 'Particulars', 'Party', 'Receipt', 'Payment', 'Balance', 'Source'], 'rows': [[row['date'].strftime('%d/%m/%Y %I:%M %p') if row['date'] else '-', row['particulars'], row['party'], row['receipt'], row['payment'], row['balance'], row['source']] for row in statement['rows']]},
        ],
    }])
