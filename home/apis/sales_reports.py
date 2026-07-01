from collections import defaultdict
from datetime import datetime

from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.views.decorators.clickjacking import xframe_options_exempt
from weasyprint import CSS, HTML

from home.models import Sales, StaffUser
from home.services.invoices import gst_breakup_from_invoice_subject
from home.services.reports import formatINR, pdf_base_context, xlsx_report_response
from home.services.sales import can_filter_all_sales, has_sales_access, sales_list_base_qs
from home.tenancy import get_current_tenant, tenant_qs


def _money(value):
    return f'Rs. {formatINR(format(float(value or 0.0), ".0f"))}'


def generate_sales_report(request):
    c_date = request.GET.get('cDate')
    staff_id = request.GET.get('staffID')
    report_date = datetime.strptime(c_date, '%d/%m/%Y')
    total = 0.0
    if staff_id == 'All':
        rows = tenant_qs(request, Sales).select_related().filter(
            buildDate__exact=report_date.date(),
            isDeleted__exact=False
        ).order_by('createdBy__name')
        staff_name = 'All'
    else:
        rows = tenant_qs(request, Sales).select_related().filter(
            buildDate__exact=report_date.date(),
            isDeleted__exact=False,
            createdBy_id=int(staff_id)
        ).order_by('createdBy__name')
        user = tenant_qs(request, StaffUser).get(pk=int(staff_id))
        staff_name = user.name + ' - ' + user.partyGroupID.name
    cash_total = 0.0
    online_total = 0.0
    credit_total = 0.0
    mix_total = 0.0
    approved_count = 0
    pending_count = 0
    top_customer_map = defaultdict(float)
    for sale in rows:
        total += sale.amount
        if sale.modeOfPayment == Sales.Mode.CASH:
            cash_total += sale.amount
        elif sale.modeOfPayment == Sales.Mode.ONLINE:
            online_total += sale.amount
        elif sale.modeOfPayment == Sales.Mode.CREDIT:
            credit_total += sale.amount
        elif sale.modeOfPayment == Sales.Mode.MIX:
            mix_total += sale.amount
        if sale.isApproved:
            approved_count += 1
        else:
            pending_count += 1
        if getattr(sale, 'partyID', None):
            top_customer_map[sale.partyID.name] += float(sale.amount or 0.0)
    total_count = len(rows)
    avg_bill = total / total_count if total_count else 0.0
    top_customers = sorted(top_customer_map.items(), key=lambda item: item[1], reverse=True)[:5]
    context = {
        'date': report_date,
        'col': rows,
        'staffName': staff_name,
        'total': total,
        'total_count': total_count,
        'avg_bill': avg_bill,
        'approved_count': approved_count,
        'pending_count': pending_count,
        'cash_total': cash_total,
        'online_total': online_total,
        'credit_total': credit_total,
        'mix_total': mix_total,
        'top_customers': top_customers,
    }
    context.update(pdf_base_context(request, [
        f'Date: {report_date.strftime("%d/%m/%Y")}',
        f'Staff: {staff_name}',
        f'Invoices: {total_count}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = "report.pdf"
    html = render_to_string("home/report/salesReportPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A4 portrait; margin: .65cm; }')])
    return response


def generate_sales_invoice_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not has_sales_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    sale_id = request.GET.get('id')
    if not sale_id or not str(sale_id).isdigit():
        return JsonResponse({'message': 'error', 'error': 'Sale id is required.'}, status=400, safe=False)

    sale = get_object_or_404(
        tenant_qs(request, Sales).select_related('partyID', 'createdBy', 'approvedBy', 'bankID', 'tenant'),
        pk=int(sale_id),
        isDeleted=False,
    )
    if not can_filter_all_sales(request.user):
        if getattr(getattr(sale, 'createdBy', None), 'user_ID_id', None) != request.user.pk:
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)

    tenant = get_current_tenant(request)
    lines = list(sale.lines.filter(isDeleted=False).select_related('item').order_by('id'))
    gst_breakup = gst_breakup_from_invoice_subject(sale, tenant)
    total_before_round_off = round(float(gst_breakup['taxable_total'] or 0.0) + float(gst_breakup['gst_total'] or 0.0), 2)
    round_off_amount = round(float(getattr(sale, 'roundOffAmount', 0.0) or 0.0), 2)
    context = {
        'sale': sale,
        'lines': lines,
        'seller_name': getattr(tenant, 'legalName', None) or getattr(tenant, 'name', None) or 'zivabooks.in',
        'seller_gst_number': getattr(tenant, 'gstNumber', None) or '',
        'seller_address': getattr(tenant, 'billingAddress', None) or '',
        'seller_phone': getattr(tenant, 'phone', None) or '',
        'buyer_name': sale.buyerLegalName or getattr(getattr(sale, 'partyID', None), 'legalName', None) or getattr(getattr(sale, 'partyID', None), 'name', None) or '-',
        'buyer_gst_number': sale.buyerGstNumber or '',
        'buyer_address': sale.buyerBillingAddress or getattr(getattr(sale, 'partyID', None), 'billingAddress', None) or getattr(getattr(sale, 'partyID', None), 'address', None) or '',
        'gst_total': gst_breakup['gst_total'],
        'taxable_total': gst_breakup['taxable_total'],
        'total_before_round_off': total_before_round_off,
        'round_off_amount': round_off_amount,
        'grand_total': float(sale.amount or 0.0),
        'tax_mode_label': gst_breakup['tax_mode_label'],
        'seller_state_code': gst_breakup['seller_state_code'],
        'buyer_state_code': gst_breakup['buyer_state_code'],
        'place_of_supply_state_code': gst_breakup['place_of_supply_state_code'],
        'effective_buyer_state_code': gst_breakup['effective_buyer_state_code'],
        'cgst_total': gst_breakup['cgst_total'],
        'sgst_total': gst_breakup['sgst_total'],
        'utgst_total': gst_breakup['utgst_total'],
        'igst_total': gst_breakup['igst_total'],
    }
    context.update(pdf_base_context(request, [
        f'Invoice: {sale.invoiceNumber or sale.pk}',
        f'Buyer: {context["buyer_name"]}',
        f'Kind: {sale.get_invoiceKind_display()}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename=sales-invoice-{sale.invoiceNumber or sale.pk}.pdf'
    html = render_to_string("home/report/salesInvoicePDF.html", context)
    base_url = request.build_absolute_uri('/')
    HTML(string=html, base_url=base_url).write_pdf(response, stylesheets=[CSS(string='@page { size: A4; margin: 0.9cm; }')])
    return response


@xframe_options_exempt
def print_sales_thermal_challan(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not has_sales_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    sale_id = request.GET.get('id')
    if not sale_id or not str(sale_id).isdigit():
        return JsonResponse({'message': 'error', 'error': 'Sale id is required.'}, status=400, safe=False)

    sale = get_object_or_404(
        tenant_qs(request, Sales).select_related('partyID', 'createdBy', 'bankID', 'tenant'),
        pk=int(sale_id),
        isDeleted=False,
    )
    if not can_filter_all_sales(request.user):
        if getattr(getattr(sale, 'createdBy', None), 'user_ID_id', None) != request.user.pk:
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)

    tenant = get_current_tenant(request)
    context = {
        'sale': sale,
        'lines': list(sale.lines.filter(isDeleted=False).select_related('item').order_by('id')),
        'seller_name': getattr(tenant, 'legalName', None) or getattr(tenant, 'name', None) or 'zivabooks.in',
        'seller_phone': getattr(tenant, 'phone', None) or '',
        'seller_address': getattr(tenant, 'billingAddress', None) or '',
        'buyer_name': sale.buyerLegalName or getattr(getattr(sale, 'partyID', None), 'legalName', None) or getattr(getattr(sale, 'partyID', None), 'name', None) or '-',
        'buyer_phone': getattr(getattr(sale, 'partyID', None), 'phone', None) or '',
        'mode_label': sale.modeOfPayment or '-',
        'created_by': getattr(getattr(sale, 'createdBy', None), 'name', None) or '-',
    }
    html = render_to_string('home/report/salesThermalChallan.html', context, request=request)
    return HttpResponse(html)


def generate_sales_report_excel(request):
    c_date = request.GET.get('cDate')
    staff_id = request.GET.get('staffID')
    report_date = datetime.strptime(c_date, '%d/%m/%Y')
    if staff_id == 'All':
        rows_qs = tenant_qs(request, Sales).select_related('partyID', 'createdBy', 'bankID').filter(
            buildDate__exact=report_date.date(),
            isDeleted__exact=False
        ).order_by('createdBy__name', 'invoiceNumber')
        staff_name = 'All'
    else:
        rows_qs = tenant_qs(request, Sales).select_related('partyID', 'createdBy', 'bankID').filter(
            buildDate__exact=report_date.date(),
            isDeleted__exact=False,
            createdBy_id=int(staff_id)
        ).order_by('createdBy__name', 'invoiceNumber')
        user = tenant_qs(request, StaffUser).get(pk=int(staff_id))
        staff_name = user.name + ' - ' + user.partyGroupID.name
    cash_total = 0.0
    online_total = 0.0
    credit_total = 0.0
    mix_total = 0.0
    approved_count = 0
    pending_count = 0
    total = 0.0
    rows = []
    for sale in rows_qs:
        amount = float(sale.amount or 0.0)
        total += amount
        if sale.modeOfPayment == Sales.Mode.CASH:
            cash_total += amount
        elif sale.modeOfPayment == Sales.Mode.ONLINE:
            online_total += amount
        elif sale.modeOfPayment == Sales.Mode.CREDIT:
            credit_total += amount
        elif sale.modeOfPayment == Sales.Mode.MIX:
            mix_total += amount
        if sale.isApproved:
            approved_count += 1
        else:
            pending_count += 1
        rows.append([
            sale.invoiceNumber or '-',
            sale.paymentID or '-',
            sale.partyID.name if sale.partyID_id else '-',
            sale.createdBy.name if sale.createdBy_id else '-',
            sale.modeOfPayment or '-',
            getattr(sale.bankID, 'name', '-') if getattr(sale, 'bankID', None) else '-',
            amount,
            sale.buildDate.strftime('%d/%m/%Y') if sale.buildDate else '-',
            'Approved' if sale.isApproved else 'Pending',
            sale.remark or '-',
        ])
    total_count = len(rows)
    avg_bill = total / total_count if total_count else 0.0
    return xlsx_report_response('sales-report', [{
        'title': 'Sales Report',
        'report_title': 'Sales Report',
        'report_subtitle': 'Invoice-wise sales export',
        'metadata': [
            ('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')),
            ('Date', report_date.strftime('%d/%m/%Y')),
            ('Staff', staff_name),
            ('Generated By', pdf_base_context(request).get('pdf_generated_by')),
        ],
        'tables': [
            {
                'title': 'Summary',
                'headers': ['Metric', 'Value'],
                'rows': [
                    ['Total Sales', total],
                    ['Invoices', total_count],
                    ['Average Bill', avg_bill],
                    ['Approved', approved_count],
                    ['Pending', pending_count],
                    ['Cash', cash_total],
                    ['Online', online_total],
                    ['Credit', credit_total],
                    ['Mix', mix_total],
                ],
            },
            {
                'title': 'Invoice Detail',
                'headers': ['Invoice No', 'Payment ID', 'Customer', 'Staff', 'Mode', 'Bank', 'Amount', 'Date', 'Approval', 'Remark'],
                'rows': rows,
            },
        ],
    }])


def export_sales_list_excel(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    qs = sales_list_base_qs(request).filter(isDeleted=False)
    if not can_filter_all_sales(request.user):
        qs = qs.filter(createdBy__user_ID_id=request.user.pk)
        staff_name = 'My Sales'
    else:
        staff_id = request.GET.get('staffID')
        if staff_id and staff_id != 'All':
            qs = qs.filter(createdBy_id=int(staff_id))
            staff = tenant_qs(request, StaffUser).filter(pk=int(staff_id)).first()
            staff_name = staff.name if staff else 'Selected Staff'
        else:
            staff_name = 'All'
    try:
        start_date = datetime.strptime(request.GET.get('startDate'), '%d/%m/%Y').date()
        end_date = datetime.strptime(request.GET.get('endDate'), '%d/%m/%Y').date()
    except Exception:
        start_date = end_date = datetime.today().date()
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    qs = qs.filter(buildDate__range=[start_date, end_date])
    search = (request.GET.get('search') or '').strip()
    if search:
        qs = qs.filter(
            Q(paymentID__icontains=search) | Q(partyID__name__icontains=search) |
            Q(amount__icontains=search) | Q(invoiceNumber__icontains=search) |
            Q(createdBy__name__icontains=search) | Q(remark__icontains=search) |
            Q(modeOfPayment__icontains=search) | Q(bankID__name__icontains=search)
        )
    rows = []
    for item in qs.order_by('-datetime'):
        bank_name = item.bankID.name if getattr(item, 'bankID', None) else '-'
        payment_mode = item.modeOfPayment or '-'
        if payment_mode == Sales.Mode.MIX:
            payment_detail = f"Cash: {_money(item.mixCashAmount or 0.0)} | Online: {_money(item.mixOnlineAmount or 0.0)}"
        elif payment_mode == Sales.Mode.ONLINE:
            payment_detail = f"Online via {bank_name}"
        elif payment_mode == Sales.Mode.CREDIT:
            payment_detail = 'Credit sale'
        else:
            payment_detail = 'Cash sale'
        rows.append([
            item.paymentID or '-',
            item.partyID.name if item.partyID_id else '-',
            item.invoiceNumber or '-',
            payment_mode,
            payment_detail,
            float(item.amount or 0.0),
            item.buildDate.strftime('%d/%m/%Y') if item.buildDate else '-',
            item.createdBy.name if item.createdBy_id else '-',
            'Yes' if item.isApproved else 'No',
            item.approvedBy.name if item.approvedBy_id else '-',
            item.approvedOn.strftime('%d/%m/%Y %I:%M %p') if item.approvedOn else '-',
            item.datetime.strftime('%d/%m/%Y %I:%M %p') if item.datetime else '-',
            item.remark or '-',
        ])
    return xlsx_report_response('sales-list', [{
        'title': 'Sales',
        'report_title': 'Sales List Export',
        'report_subtitle': 'Detailed filtered sales rows',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('From', start_date.strftime('%d/%m/%Y')), ('To', end_date.strftime('%d/%m/%Y')), ('Staff', staff_name), ('Search', search or '-')],
        'tables': [{'title': 'Sales', 'headers': ['Payment ID', 'Party', 'Invoice Number', 'Mode', 'Payment Detail', 'Amount', 'Invoice Date', 'Created By', 'Approved', 'Approved By', 'Approved On', 'Added On', 'Remark'], 'rows': rows}],
    }])
