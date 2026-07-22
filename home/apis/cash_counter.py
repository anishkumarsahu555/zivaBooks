from collections import defaultdict
from datetime import datetime, timedelta
import os

from django.db import transaction
from django.db.models import Case, Q, Sum, Value, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.cache import cache_page
from django.views.decorators.clickjacking import xframe_options_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView
from weasyprint import CSS, HTML

import home.apiView as legacy_api
from home.inventory import rebuild_inventory_stock, validate_non_negative_inventory
from home.models import *
from home.services.cash_counter import (
    can_filter_all_cash_counter as _can_filter_all_cash_counter,
    can_manage_cash_counter_records as _can_manage_cash_counter_records,
    cash_counter_collection_list_base_qs as _cash_counter_collection_list_base_qs,
    cash_counter_list_base_qs as _cash_counter_list_base_qs,
)
from home.services.collections import has_any_permission as _has_any_permission
from home.services.cash_counter_invoicing import (
    apply_cash_counter_invoice_payload as _apply_cash_counter_invoice_payload,
    cash_counter_billing_modes as _cash_counter_billing_modes,
    cash_counter_invoice_action_buttons as _cash_counter_invoice_action_buttons,
    gst_breakup_from_cash_counter as _gst_breakup_from_cash_counter,
    sync_cash_counter_lines as _sync_cash_counter_lines,
    sync_cash_counter_return_voucher as _sync_cash_counter_return_voucher,
)
from home.services.inventory_lines import (
    inventory_feature_enabled as _inventory_feature_enabled,
    line_items_total as _line_items_total,
    request_line_items_present as _request_line_items_present,
)
from home.services.reports import (
    formatINR,
    pdf_base_context as _pdf_base_context,
    xlsx_report_response as _xlsx_report_response,
)
from home.services.sequences import next_tenant_sequence_value as _next_tenant_sequence_value
from utils.logger import logger
from home.tenancy import assign_tenant, get_current_tenant, tenant_qs

# Runtime legacy lookups are retained for tests and ledger/book sync monkeypatches.
from home.apiView import (
    _cash_counter_public_id,
    _reserve_invoice_number,
)


def _staff_for_request(request):
    return tenant_qs(request, StaffUser).get(user_ID_id=request.user.pk)


def search_cash_counter_invoice_lookup_api(request):
    if request.method != 'GET':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.can_manage_cash_counter', 'home.view_cashcounter'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)

    query = (request.GET.get('q') or '').strip()
    today_only = str(request.GET.get('today') or '').strip().lower() in {'1', 'true', 'yes'}
    recent_only = str(request.GET.get('recent') or '').strip().lower() in {'1', 'true', 'yes'}

    qs = tenant_qs(request, CashCounter).filter(
        isDeleted=False,
        mode__in=_cash_counter_billing_modes(),
    ).exclude(invoiceNumber__isnull=True).exclude(invoiceNumber__exact='').select_related('partyID', 'createdBy', 'bankID')

    if today_only and not query:
        qs = qs.filter(entryDate__date=datetime.now().date()).order_by('-entryDate', '-id')[:20]
    elif recent_only and not query:
        qs = qs.order_by('-entryDate', '-id')
    elif query:
        if len(query) < 2:
            return JsonResponse({'message': 'success', 'data': []}, safe=False)
        qs = qs.annotate(
            match_rank=Case(
                When(invoiceNumber__iexact=query, then=Value(0)),
                When(partyID__name__iexact=query, then=Value(1)),
                When(invoiceNumber__istartswith=query, then=Value(2)),
                When(partyID__name__istartswith=query, then=Value(3)),
                When(invoiceNumber__icontains=query, then=Value(4)),
                When(partyID__name__icontains=query, then=Value(5)),
                When(remark__icontains=query, then=Value(6)),
                default=Value(99),
            )
        ).filter(
            Q(invoiceNumber__icontains=query) |
            Q(partyID__name__icontains=query) |
            Q(remark__icontains=query)
        ).order_by('match_rank', '-entryDate', '-id')[:10]
    else:
        return JsonResponse({'message': 'success', 'data': []}, safe=False)

    rows = []
    for counter in qs:
        total_amount = float(counter.amount or 0.0)
        if counter.mode == 'Mix':
            total_amount = float(counter.mixCashAmount or 0.0) + float(counter.mixCardAmount or 0.0)
        rows.append({
            'ID': counter.pk,
            'InvoiceNumber': counter.invoiceNumber or '-',
            'PartyName': getattr(getattr(counter, 'partyID', None), 'name', None) or '-',
            'Amount': total_amount,
            'BuildDate': counter.entryDate.strftime('%d/%m/%Y') if counter.entryDate else '-',
            'Mode': counter.mode or '-',
            'CreatedBy': getattr(getattr(counter, 'createdBy', None), 'name', None) or '-',
            'Remark': counter.remark or '',
            'ThermalPrintUrl': reverse('homeApp:print_cash_counter_thermal_challan') + f'?id={counter.pk}',
        })
    return JsonResponse({'message': 'success', 'data': rows}, safe=False)




@xframe_options_exempt
def print_cash_counter_thermal_challan(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.can_manage_cash_counter', 'home.view_cashcounter'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)

    counter_id = request.GET.get('id')
    if not counter_id or not str(counter_id).isdigit():
        return JsonResponse({'message': 'error', 'error': 'Cash counter id is required.'}, status=400, safe=False)

    counter = get_object_or_404(
        tenant_qs(request, CashCounter).select_related('partyID', 'createdBy', 'bankID', 'tenant'),
        pk=int(counter_id),
        isDeleted=False,
    )
    if counter.mode not in _cash_counter_billing_modes() or not getattr(counter, 'invoiceNumber', None):
        return JsonResponse({'message': 'error', 'error': 'Thermal print is only available for billing entries.'}, status=400, safe=False)
    if not _can_filter_all_cash_counter(request.user):
        if getattr(getattr(counter, 'createdBy', None), 'user_ID_id', None) != request.user.pk:
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)

    tenant = get_current_tenant(request)
    total_amount = float(counter.amount or 0.0)
    if counter.mode == 'Mix':
        total_amount = float(counter.mixCashAmount or 0.0) + float(counter.mixCardAmount or 0.0)
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
        'counter': counter,
        'lines': list(counter.lines.filter(isDeleted=False).select_related('item').order_by('id')),
        'logo_path': logo_path,
        'seller_name': getattr(tenant, 'legalName', None) or getattr(tenant, 'name', None) or 'zivabooks.in',
        'seller_phone': getattr(tenant, 'phone', None) or '',
        'seller_address': getattr(tenant, 'billingAddress', None) or '',
        'buyer_name': counter.buyerLegalName or getattr(getattr(counter, 'partyID', None), 'legalName', None) or getattr(getattr(counter, 'partyID', None), 'name', None) or '-',
        'buyer_phone': getattr(getattr(counter, 'partyID', None), 'phone', None) or '',
        'mode_label': counter.mode or '-',
        'created_by': getattr(getattr(counter, 'createdBy', None), 'name', None) or '-',
        'amount_value': total_amount,
    }
    html = render_to_string('home/report/cashCounterThermalChallan.html', context, request=request)
    return HttpResponse(html)


def generate_cash_counter_invoice_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.can_manage_cash_counter', 'home.view_cashcounter'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    counter_id = request.GET.get('id')
    if not counter_id or not str(counter_id).isdigit():
        return JsonResponse({'message': 'error', 'error': 'Cash counter id is required.'}, status=400, safe=False)

    counter = get_object_or_404(
        tenant_qs(request, CashCounter).select_related('partyID', 'createdBy', 'bankID', 'tenant'),
        pk=int(counter_id),
        isDeleted=False,
    )
    if counter.mode not in _cash_counter_billing_modes() or not getattr(counter, 'invoiceNumber', None):
        return JsonResponse({'message': 'error', 'error': 'Invoice download is only available for billing entries.'}, status=400, safe=False)
    if not _can_filter_all_cash_counter(request.user):
        if getattr(getattr(counter, 'createdBy', None), 'user_ID_id', None) != request.user.pk:
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)

    tenant = get_current_tenant(request)
    lines = list(counter.lines.filter(isDeleted=False).select_related('item').order_by('id'))
    gst_breakup = _gst_breakup_from_cash_counter(counter, tenant)
    total_before_round_off = round(float(gst_breakup['taxable_total'] or 0.0) + float(gst_breakup['gst_total'] or 0.0), 2)
    round_off_amount = round(float(getattr(counter, 'roundOffAmount', 0.0) or 0.0), 2)
    grand_total = round(float(counter.amount or 0.0), 2)
    if counter.mode == 'Mix':
        grand_total = round(float(counter.mixCashAmount or 0.0) + float(counter.mixCardAmount or 0.0), 2)
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
        'counter': counter,
        'lines': lines,
        'logo_path': logo_path,
        'seller_name': getattr(tenant, 'legalName', None) or getattr(tenant, 'name', None) or 'zivabooks.in',
        'seller_gst_number': getattr(tenant, 'gstNumber', None) or '',
        'seller_address': getattr(tenant, 'billingAddress', None) or '',
        'seller_phone': getattr(tenant, 'phone', None) or '',
        'buyer_name': counter.buyerLegalName or getattr(getattr(counter, 'partyID', None), 'legalName', None) or getattr(getattr(counter, 'partyID', None), 'name', None) or '-',
        'buyer_gst_number': counter.buyerGstNumber or '',
        'buyer_address': counter.buyerBillingAddress or getattr(getattr(counter, 'partyID', None), 'billingAddress', None) or getattr(getattr(counter, 'partyID', None), 'address', None) or '',
        'gst_total': gst_breakup['gst_total'],
        'taxable_total': gst_breakup['taxable_total'],
        'total_before_round_off': total_before_round_off,
        'round_off_amount': round_off_amount,
        'grand_total': grand_total,
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
    context.update(_pdf_base_context(request, [
        f'Invoice: {counter.invoiceNumber or counter.pk}',
        f'Buyer: {context["buyer_name"]}',
        f'Mode: {counter.mode or "-"}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename=cash-counter-invoice-{counter.invoiceNumber or counter.pk}.pdf'
    html = render_to_string("home/report/cashCounterInvoicePDF.html", context)
    base_url = request.build_absolute_uri('/')
    HTML(string=html, base_url=base_url).write_pdf(response, stylesheets=[CSS(string='@page { size: A4; margin: 0.9cm; }')])
    return response




# cash counter api
def add_cash_counter_entry_api(request):
    if request.method == 'POST':
        try:
            with transaction.atomic():
                invoiceSeriesId = request.POST.get("invoiceSeriesId")
                requestedInvoiceNumber = request.POST.get("requestedInvoiceNumber")
                party = request.POST.get("party")
                amount = request.POST.get("amount")
                remark = request.POST.get("remark")
                amountMixCash = request.POST.get("amountMixCash")
                amountMixCard = request.POST.get("amountMixCard")
                expenseType = request.POST.get("expenseType")
                bank = request.POST.get("bank")
                mode = request.POST.get("mode")
                inventory_enabled = _inventory_feature_enabled(request)
                line_items_total = _line_items_total(request) if (inventory_enabled and _request_line_items_present(request)) else 0.0
                party_obj = None
                obj = CashCounter()
                assign_tenant(obj, request)
                if mode in {"Cash", "Credit", "Card", "Collection", "Advance", "Mix", "Return"}:
                    c = str(party).split('@')
                    party_obj = tenant_qs(request, Party).select_related().get(pk=int(c[1]))
                    obj.partyID_id = party_obj.pk
                else:
                    obj.partyID = None
                if mode == "Cash" or mode == "Mix" or mode == "Card" or mode == "Credit" or mode == "Return":
                    series, sequence_number, invoice_number = _reserve_invoice_number(
                        request,
                        InvoiceSeries.Module.CASH_COUNTER,
                        invoiceSeriesId,
                        preferred_number=requestedInvoiceNumber or None,
                    )
                    obj.invoiceSeries = series
                    obj.invoiceSequenceNumber = sequence_number
                    obj.invoiceNumber = invoice_number
                if mode == "Mix":
                    obj.mixCashAmount = float(amountMixCash)
                    obj.mixCardAmount = float(amountMixCard)
                    obj.amount = 0.0
                else:
                    obj.amount = float(amount or line_items_total or 0.0)
                    obj.mixCashAmount = 0.0
                    obj.mixCardAmount = 0.0
                if mode == "Expense":
                    obj.expenseType = expenseType
                else:
                    obj.expenseType = ''
                if mode == "Card" or mode == "Mix":
                    obj.bankID_id = int(bank)
                else:
                    obj.bankID = None
                obj.mode = mode
                obj.remark = remark
                obj.entryDate = datetime.now()
                user = _staff_for_request(request)
                obj.createdBy_id = user.pk
                _apply_cash_counter_invoice_payload(request, obj, party_obj)
                obj.save()
                obj.counterID = _next_tenant_sequence_value(request, TenantSequence.Scope.CASH_COUNTER)
                obj.save(update_fields=['counterID', 'lastUpdatedOn'])
                if inventory_enabled:
                    _sync_cash_counter_lines(request, obj)
                    _sync_cash_counter_return_voucher(request, obj)
                legacy_api.sync_book_transactions(obj)
                legacy_api.sync_party_ledger_entries_for_object(obj)
            return JsonResponse({'message': 'success', 'invoiceNumber': obj.invoiceNumber}, safe=False)
        except Exception as exc:
            return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
class CashCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'invoiceNumber', 'amount', 'createdBy.name',
                     'datetime', 'remark'
                     ]
    def get_initial_queryset(self):
        search_value = self.request.GET.get('search[value]', None)
        if search_value and search_value.strip():
            qs = _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False, mode__iexact="Cash")
            if not _can_filter_all_cash_counter(self.request.user):
                qs = qs.filter(createdBy__user_ID_id=self.request.user.pk)
            return qs
        if _can_filter_all_cash_counter(self.request.user):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Cash")
            except:
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Cash")
        else:
            return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               createdBy__user_ID_id=self.request.user.pk,
                                                               mode__iexact="Cash")
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(
                    amount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = _cash_counter_invoice_action_buttons(item, self.request.user)
            try:
                createdBy = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.createdBy_id}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.createdBy.name)}</span>'''
            except:
                createdBy = '-'
            json_data.append([
                escape(_cash_counter_public_id(item)),
                escape(item.invoiceNumber),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
                action,
            ])
        return json_data
class CardCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'invoiceNumber', 'amount', 'createdBy.name',
                     'datetime', 'remark'
                     ]
    def get_initial_queryset(self):
        search_value = self.request.GET.get('search[value]', None)
        if search_value and search_value.strip():
            qs = _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False, mode__iexact="Card")
            if not _can_filter_all_cash_counter(self.request.user):
                qs = qs.filter(createdBy__user_ID_id=self.request.user.pk)
            return qs
        if _can_filter_all_cash_counter(self.request.user):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Card")
            except:
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Card")
        else:
            return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               mode__iexact="Card",
                                                               createdBy__user_ID_id=self.request.user.pk)
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(bankID__name__icontains=search) | Q(
                    amount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = _cash_counter_invoice_action_buttons(item, self.request.user)
            try:
                createdBy = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.createdBy_id}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.createdBy.name)}</span>'''
            except:
                createdBy = '-'
            try:
                remark = escape(item.bankID.name) + ' - ' + escape(item.bankID.accountNumber) + ' - ' + escape(
                    item.remark)
            except:
                remark = escape(item.remark)
            json_data.append([
                escape(_cash_counter_public_id(item)),
                escape(item.invoiceNumber),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                remark,
                action,
            ])
        return json_data
class ReturnCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'invoiceNumber', 'amount', 'createdBy.name',
                     'datetime', 'remark'
                     ]
    def get_initial_queryset(self):
        search_value = self.request.GET.get('search[value]', None)
        if search_value and search_value.strip():
            qs = _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False, mode__iexact="Return")
            if not _can_filter_all_cash_counter(self.request.user):
                qs = qs.filter(createdBy__user_ID_id=self.request.user.pk)
            return qs
        if _can_filter_all_cash_counter(self.request.user):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Return")
            except:
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Return")
        else:
            return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               mode__iexact="Return",
                                                               createdBy__user_ID_id=self.request.user.pk)
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(
                    amount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = _cash_counter_invoice_action_buttons(item, self.request.user)
            try:
                createdBy = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.createdBy_id}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.createdBy.name)}</span>'''
            except:
                createdBy = '-'
            json_data.append([
                escape(_cash_counter_public_id(item)),
                escape(item.invoiceNumber),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
                action,
            ])
        return json_data
class CreditCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'partyID.name', 'invoiceNumber', 'amount', 'createdBy.name',
                     'datetime', 'remark'
                     ]
    def get_initial_queryset(self):
        search_value = self.request.GET.get('search[value]', None)
        if search_value and search_value.strip():
            qs = _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False, mode__iexact="Credit")
            if not _can_filter_all_cash_counter(self.request.user):
                qs = qs.filter(createdBy__user_ID_id=self.request.user.pk)
            return qs
        if _can_filter_all_cash_counter(self.request.user):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Credit")
            except:
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Credit")
        else:
            return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               mode__iexact="Credit",
                                                               createdBy__user_ID_id=self.request.user.pk)
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(
                    partyID__name__icontains=search) | Q(
                    amount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = _cash_counter_invoice_action_buttons(item, self.request.user)
            try:
                createdBy = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.createdBy_id}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.createdBy.name)}</span>'''
            except:
                createdBy = '-'
            try:
                party = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('party', '{item.partyID_id}')" style="cursor: pointer;">{escape(item.partyID.name)}</span>'''
            except:
                party = 'N/A'
            json_data.append([
                escape(_cash_counter_public_id(item)),
                escape(party),
                escape(item.invoiceNumber),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
                action,
            ])
        return json_data
class MixCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'invoiceNumber', 'mixCashAmount', 'mixCardAmount', 'createdBy.name',
                     'datetime', 'remark'
                     ]
    def get_initial_queryset(self):
        search_value = self.request.GET.get('search[value]', None)
        if search_value and search_value.strip():
            qs = _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False, mode__iexact="Mix")
            if not _can_filter_all_cash_counter(self.request.user):
                qs = qs.filter(createdBy__user_ID_id=self.request.user.pk)
            return qs
        if _can_filter_all_cash_counter(self.request.user):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Mix")
            except:
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Mix",
                                                                   createdBy__user_ID_id=self.request.user.pk)
        else:
            return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               createdBy__user_ID_id=self.request.user.pk,
                                                               mode__iexact="Mix")
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(bankID__name__icontains=search) | Q(
                    mixCashAmount__icontains=search) | Q(
                    mixCardAmount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = _cash_counter_invoice_action_buttons(item, self.request.user)
            try:
                createdBy = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.createdBy_id}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.createdBy.name)}</span>'''
            except:
                createdBy = '-'
            try:
                remark = escape(item.bankID.name) + ' - ' + escape(item.bankID.accountNumber) + ' - ' + escape(
                    item.remark)
            except:
                remark = escape(item.remark)
            json_data.append([
                escape(_cash_counter_public_id(item)),
                escape(item.invoiceNumber),
                formatINR(format(item.mixCashAmount, '.0f')),
                formatINR(format(item.mixCardAmount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                remark,
                action,
            ])
        return json_data
class CollectionCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'partyID.name', 'amount', 'createdBy.name',
                     'datetime', 'remark'
                     ]
    def get_initial_queryset(self):
        search_value = self.request.GET.get('search[value]', None)
        if search_value and search_value.strip():
            qs = _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False, mode__iexact="Collection")
            if not _can_filter_all_cash_counter(self.request.user):
                qs = qs.filter(createdBy__user_ID_id=self.request.user.pk)
            return qs
        if _can_filter_all_cash_counter(self.request.user):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Collection")
            except:
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Collection")
        else:
            return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               mode__iexact="Collection",
                                                               createdBy__user_ID_id=self.request.user.pk)
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(
                    partyID__name__icontains=search) | Q(
                    amount__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if _can_manage_cash_counter_records(self.request.user):
                action = '''
              <a href="/edit_cash_counter/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
              Denied
            </div>'''
            try:
                createdBy = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.createdBy_id}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.createdBy.name)}</span>'''
            except:
                createdBy = '-'
            try:
                party = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('party', '{item.partyID_id}')" style="cursor: pointer;">{escape(item.partyID.name)}</span>'''
            except:
                party = 'N/A'
            json_data.append([
                escape(_cash_counter_public_id(item)),
                escape(party),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
                action,
            ])
        return json_data
class AdvanceCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'partyID.name', 'amount', 'createdBy.name',
                     'datetime', 'remark'
                     ]
    def get_initial_queryset(self):
        search_value = self.request.GET.get('search[value]', None)
        if search_value and search_value.strip():
            qs = _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False, mode__iexact="Advance")
            if not _can_filter_all_cash_counter(self.request.user):
                qs = qs.filter(createdBy__user_ID_id=self.request.user.pk)
            return qs
        if _can_filter_all_cash_counter(self.request.user):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Advance")
            except:
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Advance")
        else:
            return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               mode__iexact="Advance",
                                                               createdBy__user_ID_id=self.request.user.pk)
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(
                    partyID__name__icontains=search) | Q(
                    amount__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if _can_manage_cash_counter_records(self.request.user):
                action = '''
              <a href="/edit_cash_counter/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
              Denied
            </div>'''
            try:
                createdBy = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.createdBy_id}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.createdBy.name)}</span>'''
            except:
                createdBy = '-'
            try:
                party = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('party', '{item.partyID_id}')" style="cursor: pointer;">{escape(item.partyID.name)}</span>'''
            except:
                party = 'N/A'
            json_data.append([
                escape(_cash_counter_public_id(item)),
                escape(party),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
                action,
            ])
        return json_data
class ExpenseCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'remark', 'amount', 'createdBy.name',
                     'datetime'
                     ]
    def get_initial_queryset(self):
        search_value = self.request.GET.get('search[value]', None)
        if search_value and search_value.strip():
            qs = _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False, mode__iexact="Expense")
            if not _can_filter_all_cash_counter(self.request.user):
                qs = qs.filter(createdBy__user_ID_id=self.request.user.pk)
            return qs
        if _can_filter_all_cash_counter(self.request.user):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__range=[sDate.date(),
                                                                                    eDate.date() + timedelta(days=1)],
                                                                   mode__iexact="Expense")
            except:
                return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                   datetime__date=datetime.today().date(),
                                                                   mode__iexact="Expense")
        else:
            return _cash_counter_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                               datetime__date=datetime.today().date(),
                                                               mode__iexact="Expense",
                                                               createdBy__user_ID_id=self.request.user.pk)
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(counterID__icontains=search) | Q(expenseType__icontains=search) | Q(
                    amount__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if _can_manage_cash_counter_records(self.request.user):
                action = '''
              <a href="/edit_cash_counter/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button>'''.format(item.pk, item.pk, item.pk),
            else:
                action = '''<div class="ui tiny label">
              Denied
            </div>'''
            try:
                createdBy = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.createdBy_id}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.createdBy.name)}</span>'''
            except:
                createdBy = '-'
            json_data.append([
                escape(_cash_counter_public_id(item)),
                escape(item.expenseType) + ' - ' + escape(item.remark),
                formatINR(format(item.amount, '.0f')),
                createdBy,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,
            ])
        return json_data
@cache_page(60 * 30)
def get_cash_counter_dashboard_report_api(request):
    today = datetime.today().date()
    base_qs = tenant_qs(request, CashCounter).filter(
        isDeleted=False,
        datetime__date=today,
        createdBy__user_ID=request.user.pk
    )
    totals = base_qs.aggregate(
        cash_total=Sum('amount', filter=Q(mode="Cash")),
        card_total=Sum('amount', filter=Q(mode="Card")),
        credit_total=Sum('amount', filter=Q(mode="Credit")),
        collection_total=Sum('amount', filter=Q(mode="Collection")),
        expense_total=Sum('amount', filter=Q(mode="Expense")),
        advance_total=Sum('amount', filter=Q(mode="Advance")),
        return_total=Sum('amount', filter=Q(mode="Return")),
        mix_cash_total=Sum('mixCashAmount', filter=Q(mode="Mix")),
        mix_card_total=Sum('mixCardAmount', filter=Q(mode="Mix")),
    )
    data = {
        'cash_total': formatINR(totals['cash_total'] or 0.0),
        'card_total': formatINR(totals['card_total'] or 0.0),
        'credit_total': formatINR(totals['credit_total'] or 0.0),
        'collection_total': formatINR(totals['collection_total'] or 0.0),
        'expense_total': formatINR(totals['expense_total'] or 0.0),
        'advance_total': formatINR(totals['advance_total'] or 0.0),
        'return_total': formatINR(totals['return_total'] or 0.0),
        'mix_cash_total': formatINR(totals['mix_cash_total'] or 0.0),
        'mix_card_total': formatINR(totals['mix_card_total'] or 0.0)
    }
    return JsonResponse({'data': data}, safe=False)
@cache_page(60 * 30)
def get_cash_counter_dashboard_report_admin_api(request):
    today = datetime.today().date()
    totals = tenant_qs(request, CashCounter).filter(
        isDeleted=False,
        datetime__date=today
    ).aggregate(
        cash_total=Sum('amount', filter=Q(mode="Cash")),
        card_total=Sum('amount', filter=Q(mode="Card")),
        credit_total=Sum('amount', filter=Q(mode="Credit")),
        collection_total=Sum('amount', filter=Q(mode="Collection")),
        expense_total=Sum('amount', filter=Q(mode="Expense")),
        advance_total=Sum('amount', filter=Q(mode="Advance")),
        return_total=Sum('amount', filter=Q(mode="Return")),
        mix_cash_total=Sum('mixCashAmount', filter=Q(mode="Mix")),
        mix_card_total=Sum('mixCardAmount', filter=Q(mode="Mix")),
    )
    data = {
        'cash_total': formatINR(totals['cash_total'] or 0.0),
        'card_total': formatINR(totals['card_total'] or 0.0),
        'credit_total': formatINR(totals['credit_total'] or 0.0),
        'collection_total': formatINR(totals['collection_total'] or 0.0),
        'expense_total': formatINR(totals['expense_total'] or 0.0),
        'advance_total': formatINR(totals['advance_total'] or 0.0),
        'return_total': formatINR(totals['return_total'] or 0.0),
        'mix_cash_total': formatINR(totals['mix_cash_total'] or 0.0),
        'mix_card_total': formatINR(totals['mix_card_total'] or 0.0)
    }
    return JsonResponse({'data': data}, safe=False)
@transaction.atomic
def delete_cash_counter(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            if 'c' in id:
                id = id.split("c")[1]
                obj = tenant_qs(request, CollectionCashCounter).get(pk=int(id))
            else:
                id = id
                obj = tenant_qs(request, CashCounter).select_related('linkedSalesReturnVoucher').get(pk=int(id))
            # Output: 56
            item_ids = list(getattr(obj, 'lines', CashCounterLine.objects.none()).filter(isDeleted=False).values_list('item_id', flat=True)) if isinstance(obj, CashCounter) else []
            if isinstance(obj, CashCounter) and getattr(obj, 'linkedSalesReturnVoucher_id', None):
                item_ids.extend(
                    obj.linkedSalesReturnVoucher.lines.filter(isDeleted=False).values_list('item_id', flat=True)
                )
            obj.isDeleted = True
            obj.save()
            if isinstance(obj, CashCounter):
                if getattr(obj, 'linkedSalesReturnVoucher', None) and not obj.linkedSalesReturnVoucher.isDeleted:
                    obj.linkedSalesReturnVoucher.isDeleted = True
                    obj.linkedSalesReturnVoucher.save(update_fields=['isDeleted', 'lastUpdatedOn'])
                rebuild_inventory_stock(obj.tenant, item_ids)
                validate_non_negative_inventory(obj.tenant, item_ids)
            legacy_api.sync_book_transactions(obj)
            legacy_api.sync_party_ledger_entries_for_object(obj)
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def update_cash_counter_by_admin_api(request):
    if request.method == 'POST':
        try:
            party = request.POST.get("party")
            amount = request.POST.get("amount")
            remark = request.POST.get("remark")
            expenseType = request.POST.get("expenseType")
            amountMixCash = request.POST.get("amountMixCash")
            amountMixCard = request.POST.get("amountMixCard")
            mode = request.POST.get("mode")
            editID = request.POST.get("editID")
            bank = request.POST.get("bank")
            inventory_enabled = _inventory_feature_enabled(request)
            line_items_total = _line_items_total(request) if (inventory_enabled and _request_line_items_present(request)) else 0.0
            party_obj = None
            obj = tenant_qs(request, CashCounter).get(pk=int(editID))
            if mode in {"Cash", "Credit", "Card", "Collection", "Advance", "Mix", "Return"}:
                party_obj = tenant_qs(request, Party).select_related().get(pk=int(party))
                obj.partyID_id = party_obj.pk
            else:
                obj.partyID = None
            if mode == "Mix":
                obj.mixCashAmount = float(amountMixCash)
                obj.mixCardAmount = float(amountMixCard)
                obj.amount = 0.0
            else:
                obj.amount = float(amount or line_items_total or 0.0)
                obj.mixCashAmount = 0.0
                obj.mixCardAmount = 0.0
            if mode == "Expense":
                obj.expenseType = expenseType
            else:
                obj.expenseType = ''
            if mode == "Card" or mode == "Mix":
                obj.bankID_id = int(bank)
            else:
                obj.bankID = None
            obj.mode = mode
            obj.remark = remark
            obj.entryDate = datetime.now()
            user = _staff_for_request(request)
            obj.createdBy_id = user.pk
            _apply_cash_counter_invoice_payload(request, obj, party_obj)
            obj.save()
            if inventory_enabled:
                _sync_cash_counter_lines(request, obj)
                _sync_cash_counter_return_voucher(request, obj)
            legacy_api.sync_book_transactions(obj)
            legacy_api.sync_party_ledger_entries_for_object(obj)
            return JsonResponse({'message': 'success'}, safe=False)
        except Exception as exc:
            return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
def generate_cash_counter_report(request):
    cDate = request.GET.get('cDate')
    staffID = request.GET.get('staffID')
    colDate = datetime.strptime(cDate, '%d/%m/%Y')
    a_total_cash = 0.0
    a_total_cheque = 0.0
    a_total_online = 0.0
    a_total_cheque_cc = 0.0
    a_total_party = 0.0
    ccol = _cash_counter_collection_list_base_qs(request).filter(
        collectionDateTime__date=colDate.date(),
        isDeleted__exact=False
    )
    if not _can_filter_all_cash_counter(request.user):
        ccol = ccol.filter(collectedBy__user_ID_id=request.user.pk)
    elif staffID and staffID != 'All':
        ccol = ccol.filter(collectedBy_id=int(staffID))
    ccol = ccol.order_by('collectedBy__name')
    for a in ccol:
        if a.modeOfPayment == 'Cash':
            a_total_cash = a_total_cash + a.paidAmount
        if a.modeOfPayment == 'Cheque':
            a_total_cheque = a_total_cheque + a.paidAmount
        if a.modeOfPayment == 'Online':
            a_total_online = a_total_online + a.paidAmount
        if a.modeOfPayment == 'Cheque CC':
            a_total_cheque_cc = a_total_cheque_cc + a.paidAmount
        if a.modeOfPayment == 'Party':
            a_total_party = a_total_party + a.paidAmount
    context = {
    }
    cash_total = 0.0
    card_total = 0.0
    credit_total = 0.0
    return_total = 0.0
    expense_total = 0.0
    advance_total = 0.0
    mix_cash_total = 0.0
    mix_card_total = 0.0
    collection_total = 0.0
    col = _cash_counter_list_base_qs(request).filter(
        datetime__date=colDate.date(),
        isDeleted__exact=False
    )
    if not _can_filter_all_cash_counter(request.user):
        col = col.filter(createdBy__user_ID_id=request.user.pk)
    elif staffID and staffID != 'All':
        col = col.filter(createdBy_id=int(staffID))
    col = col.order_by('mode')
    for a in col:
        if a.mode == 'Cash':
            cash_total += a.amount
        if a.mode == 'Card':
            card_total += a.amount
        if a.mode == 'Credit':
            credit_total += a.amount
        if a.mode == 'Return':
            return_total += a.amount
        if a.mode == 'Expense':
            expense_total = + a.amount
        if a.mode == 'Advance':
            advance_total = + a.amount
        if a.mode == 'Collection':
            collection_total = + a.amount
        if a.mode == 'Mix':
            mix_cash_total = + a.mixCashAmount
            mix_card_total = + a.mixCardAmount
    context = {
        'date': colDate,
        'col': col,
        'cash_total': cash_total,
        'card_total': card_total,
        'credit_total': credit_total,
        'expense_total': expense_total,
        'return_total': return_total,
        'advance_total': advance_total,
        'collection_total': collection_total,
        'mix_cash_total': mix_cash_total,
        'mix_card_total': mix_card_total,
        'RokadValue': (cash_total + collection_total + mix_cash_total) - (expense_total + advance_total + return_total),
        'total': (
                cash_total + card_total + credit_total + collection_total + mix_cash_total + mix_card_total + return_total + expense_total + advance_total),
        'ccol': ccol,
        'a_total_cash': a_total_cash,
        'a_total_cheque': a_total_cheque,
        'a_total_online': a_total_online,
        'a_total_cheque_cc': a_total_cheque_cc,
        'a_total_party': a_total_party,
        'a_total': a_total_cash + a_total_cheque + a_total_online + a_total_cheque_cc + a_total_party
    }
    context.update(_pdf_base_context(request, [
        f'Date: {colDate.strftime("%d/%m/%Y")}',
        f'Staff: {staffID if staffID else "All"}',
        'Counter + counter collection summary',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = "report.pdf"
    html = render_to_string("home/report/cashCounterReportPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A5; margin: .3cm ; }')])
    return response


def generate_cash_counter_report_excel(request):
    cDate = request.GET.get('cDate')
    staffID = request.GET.get('staffID')
    colDate = datetime.strptime(cDate, '%d/%m/%Y')
    ccol = _cash_counter_collection_list_base_qs(request).filter(
        collectionDateTime__date=colDate.date(),
        isDeleted__exact=False
    )
    if not _can_filter_all_cash_counter(request.user):
        ccol = ccol.filter(collectedBy__user_ID_id=request.user.pk)
        staff_name = 'MyList'
    elif staffID and staffID != 'All':
        ccol = ccol.filter(collectedBy_id=int(staffID))
        staff_name = tenant_qs(request, StaffUser).get(pk=int(staffID)).name
    else:
        staff_name = 'All'
    ccol = ccol.order_by('collectedBy__name', 'collectionDateTime')
    counter_rows = _cash_counter_list_base_qs(request).filter(
        datetime__date=colDate.date(),
        isDeleted__exact=False
    )
    if not _can_filter_all_cash_counter(request.user):
        counter_rows = counter_rows.filter(createdBy__user_ID_id=request.user.pk)
    elif staffID and staffID != 'All':
        counter_rows = counter_rows.filter(createdBy_id=int(staffID))
    counter_rows = counter_rows.order_by('mode', 'datetime')

    mode_totals = defaultdict(float)
    detail_rows = []
    for item in counter_rows:
        if item.mode == 'Mix':
            amount = float(item.mixCashAmount or 0.0) + float(item.mixCardAmount or 0.0)
        else:
            amount = float(item.amount or 0.0)
        mode_totals[item.mode or 'Unknown'] += amount
        detail_rows.append([
            item.counterID or '-',
            item.mode or '-',
            item.partyID.name if item.partyID_id else '-',
            getattr(item.bankID, 'name', '-') if getattr(item, 'bankID', None) else '-',
            float(item.amount or 0.0),
            float(item.mixCashAmount or 0.0),
            float(item.mixCardAmount or 0.0),
            item.createdBy.name if item.createdBy_id else '-',
            item.datetime.strftime('%d/%m/%Y %I:%M %p') if item.datetime else '-',
            item.remark or '-',
        ])

    collection_mode_totals = defaultdict(float)
    collection_detail_rows = []
    for item in ccol:
        amount = float(item.paidAmount or 0.0)
        collection_mode_totals[item.modeOfPayment or 'Unknown'] += amount
        collection_detail_rows.append([
            item.paymentID or '-',
            item.partyID.name if item.partyID_id else '-',
            item.collectedBy.name if item.collectedBy_id else '-',
            item.modeOfPayment or '-',
            amount,
            item.collectionDateTime.strftime('%d/%m/%Y %I:%M %p') if item.collectionDateTime else '-',
            item.remark or '-',
        ])

    return _xlsx_report_response('cash-counter-report', [{
        'title': 'Cash Closing',
        'report_title': 'Cash Counter Report',
        'report_subtitle': 'Counter entries and counter collections',
        'metadata': [
            ('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')),
            ('Date', colDate.strftime('%d/%m/%Y')),
            ('Staff', staff_name),
            ('Generated By', _pdf_base_context(request).get('pdf_generated_by')),
        ],
        'tables': [
            {'title': 'Counter Mode Summary', 'headers': ['Mode', 'Amount'], 'rows': [[mode, amount] for mode, amount in sorted(mode_totals.items())]},
            {'title': 'Counter Entry Detail', 'headers': ['Counter ID', 'Mode', 'Party', 'Bank', 'Amount', 'Mix Cash', 'Mix Card', 'Created By', 'Date Time', 'Remark'], 'rows': detail_rows},
            {'title': 'Counter Collection Summary', 'headers': ['Mode', 'Amount'], 'rows': [[mode, amount] for mode, amount in sorted(collection_mode_totals.items())]},
            {'title': 'Counter Collection Detail', 'headers': ['Payment ID', 'Customer', 'Collector', 'Mode', 'Amount', 'Collected On', 'Remark'], 'rows': collection_detail_rows},
        ],
    }])
