from collections import defaultdict
from datetime import datetime
import os

from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from weasyprint import CSS, HTML

from home.models import ProformaInvoice, StaffUser
from home.services.collections import has_any_permission
from home.services.invoices import gst_breakup_from_invoice_subject
from home.services.proforma import can_filter_all_proforma, has_proforma_access
from home.services.reports import pdf_base_context, xlsx_report_response
from utils.logger import logger
from home.tenancy import get_current_tenant, tenant_object_or_superuser, tenant_qs


def _proforma_public_id(obj):
    return getattr(obj, 'proformaID', None) or getattr(obj, 'invoiceNumber', None) or f'PI{str(getattr(obj, "pk", 0)).zfill(6)}'


def generate_proforma_invoice_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not has_proforma_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    proforma_id = request.GET.get('id')
    if not proforma_id or not str(proforma_id).isdigit():
        return JsonResponse({'message': 'error', 'error': 'Proforma id is required.'}, status=400, safe=False)
    proforma = get_object_or_404(
        tenant_qs(request, ProformaInvoice).select_related('partyID', 'createdBy', 'tenant'),
        pk=int(proforma_id),
        isDeleted=False,
    )
    if not can_filter_all_proforma(request.user):
        if getattr(getattr(proforma, 'createdBy', None), 'user_ID_id', None) != request.user.pk:
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    tenant = get_current_tenant(request)
    lines = list(proforma.lines.filter(isDeleted=False).select_related('item').order_by('id'))
    gst_breakup = gst_breakup_from_invoice_subject(proforma, tenant)
    total_before_round_off = round(float(gst_breakup['taxable_total'] or 0.0) + float(gst_breakup['gst_total'] or 0.0), 2)
    logo_path = None
    if tenant.logo:
        try:
            if hasattr(tenant.logo, 'medium') and tenant.logo.medium and os.path.exists(tenant.logo.medium.path):
                logo_path = 'file://' + tenant.logo.medium.path
            elif os.path.exists(tenant.logo.path):
                logo_path = 'file://' + tenant.logo.path
        except Exception:
            if os.path.exists(tenant.logo.path):
                logo_path = 'file://' + tenant.logo.path
    context = {
        'proforma': proforma,
        'lines': lines,
        'logo_path': logo_path,
        'seller_name': getattr(tenant, 'legalName', None) or getattr(tenant, 'name', None) or 'zivabooks.in',
        'seller_gst_number': getattr(tenant, 'gstNumber', None) or '',
        'seller_address': getattr(tenant, 'billingAddress', None) or '',
        'seller_phone': getattr(tenant, 'phone', None) or '',
        'buyer_name': proforma.buyerLegalName or getattr(getattr(proforma, 'partyID', None), 'legalName', None) or getattr(getattr(proforma, 'partyID', None), 'name', None) or '-',
        'buyer_gst_number': proforma.buyerGstNumber or '',
        'buyer_address': proforma.buyerBillingAddress or getattr(getattr(proforma, 'partyID', None), 'billingAddress', None) or getattr(getattr(proforma, 'partyID', None), 'address', None) or '',
        'gst_total': gst_breakup['gst_total'],
        'taxable_total': gst_breakup['taxable_total'],
        'total_before_round_off': total_before_round_off,
        'round_off_amount': round(float(getattr(proforma, 'roundOffAmount', 0.0) or 0.0), 2),
        'grand_total': float(proforma.amount or 0.0),
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
        f'Proforma: {proforma.invoiceNumber or proforma.pk}',
        f'Buyer: {context["buyer_name"]}',
        f'Status: {proforma.get_status_display()}',
    ]))
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename=proforma-invoice-{proforma.invoiceNumber or proforma.pk}.pdf'
    html = render_to_string('home/report/proformaInvoicePDF.html', context)
    base_url = request.build_absolute_uri('/')
    HTML(string=html, base_url=base_url).write_pdf(response, stylesheets=[CSS(string='@page { size: A4; margin: 0.9cm; }')])
    return response


def _proforma_list_export_queryset(request):
    qs = tenant_qs(request, ProformaInvoice).filter(isDeleted=False).select_related('partyID', 'createdBy', 'convertedSales')
    if not can_filter_all_proforma(request.user):
        qs = qs.filter(createdBy__user_ID_id=request.user.pk)
        staff_name = 'My Proforma'
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
    status = (request.GET.get('status') or '').strip()
    if status and status != 'All':
        qs = qs.filter(status=status)
    search = (request.GET.get('search') or '').strip()
    if search:
        qs = qs.filter(
            Q(proformaID__icontains=search) | Q(invoiceNumber__icontains=search) |
            Q(partyID__name__icontains=search) | Q(status__icontains=search) |
            Q(amount__icontains=search) | Q(createdBy__name__icontains=search) |
            Q(remark__icontains=search)
        )
    return qs, start_date, end_date, staff_name, status or 'All', search


def export_proforma_list_excel(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not has_any_permission(request.user, 'home.can_view_proforma_invoice_reports', 'home.can_manage_proforma_invoice', 'home.can_view_audit_logs'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    qs, start_date, end_date, staff_name, status, search = _proforma_list_export_queryset(request)
    rows = []
    total_amount = 0.0
    for item in qs.order_by('-datetime'):
        amount = float(item.amount or 0.0)
        total_amount += amount
        rows.append([
            _proforma_public_id(item),
            item.partyID.name if item.partyID_id else '-',
            item.invoiceNumber or '-',
            item.get_status_display(),
            amount,
            item.buildDate.strftime('%d/%m/%Y') if item.buildDate else '-',
            item.validUntil.strftime('%d/%m/%Y') if item.validUntil else '-',
            item.createdBy.name if item.createdBy_id else '-',
            item.convertedSales.invoiceNumber if item.convertedSales_id else '-',
            item.datetime.strftime('%d/%m/%Y %I:%M %p') if item.datetime else '-',
            item.remark or '-',
        ])
    return xlsx_report_response('proforma-list', [{
        'title': 'Proforma',
        'report_title': 'Proforma Invoice List Export',
        'report_subtitle': 'Detailed filtered proforma invoice rows',
        'metadata': [
            ('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')),
            ('From', start_date.strftime('%d/%m/%Y')),
            ('To', end_date.strftime('%d/%m/%Y')),
            ('Staff', staff_name),
            ('Status', status),
            ('Search', search or '-'),
            ('Total Amount', total_amount),
        ],
        'tables': [{'title': 'Proforma Invoices', 'headers': ['Proforma ID', 'Party', 'Proforma Number', 'Status', 'Amount', 'Date', 'Valid Until', 'Created By', 'Sales Invoice', 'Added On', 'Remark'], 'rows': rows}],
    }])


def export_proforma_list_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not has_any_permission(request.user, 'home.can_view_proforma_invoice_reports', 'home.can_manage_proforma_invoice', 'home.can_view_audit_logs'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    qs, start_date, end_date, staff_name, status, search = _proforma_list_export_queryset(request)
    rows = []
    total_amount = 0.0
    status_counts = defaultdict(int)
    for item in qs.order_by('-datetime'):
        amount = float(item.amount or 0.0)
        total_amount += amount
        status_counts[item.get_status_display()] += 1
        rows.append({
            'proforma_id': _proforma_public_id(item),
            'party_name': item.partyID.name if item.partyID_id else '-',
            'invoice_number': item.invoiceNumber or '-',
            'status': item.get_status_display(),
            'amount': amount,
            'build_date': item.buildDate.strftime('%d/%m/%Y') if item.buildDate else '-',
            'valid_until': item.validUntil.strftime('%d/%m/%Y') if item.validUntil else '-',
            'created_by': item.createdBy.name if item.createdBy_id else '-',
            'sales_invoice': item.convertedSales.invoiceNumber if item.convertedSales_id else '-',
            'remark': item.remark or '-',
        })
    context = {
        'start_date': start_date,
        'end_date': end_date,
        'staff_name': staff_name,
        'status': status,
        'search': search or '-',
        'rows': rows,
        'total_count': len(rows),
        'total_amount': total_amount,
        'status_counts': sorted(status_counts.items()),
    }
    context.update(pdf_base_context(request, [
        f'From: {start_date.strftime("%d/%m/%Y")}',
        f'To: {end_date.strftime("%d/%m/%Y")}',
        f'Staff: {staff_name}',
        f'Status: {status}',
    ]))
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename=proforma-list.pdf'
    html = render_to_string('home/report/proformaListPDF.html', context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A4 landscape; margin: 0.7cm; }')])
    return response
