from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

import home.apiView as legacy_api
from home.inventory import rebuild_inventory_stock, validate_non_negative_inventory
from home.models import InvoiceSeries, ProformaInvoice, Sales, StaffUser, TenantSequence
from home.services.collections import has_any_permission
from home.services.inventory_lines import inventory_feature_enabled, request_line_items_payload, request_line_items_present, safe_sales_float
from home.services.inventory_mutations import copy_proforma_lines_to_sale, sync_proforma_lines
from home.services.invoices import apply_proforma_invoice_payload
from home.services.invoice_series import reserve_invoice_number
from home.services.proforma import can_convert_proforma_invoice, can_edit_proforma_record, can_filter_all_proforma
from home.services.reports import formatINR
from home.services.sales import normalize_sales_mode, parse_sales_bank_id, parse_sales_party
from home.services.sequences import next_tenant_sequence_value
from home.tenancy import assign_tenant, tenant_object_or_superuser, tenant_qs


def _proforma_public_id(obj):
    return getattr(obj, 'proformaID', None) or getattr(obj, 'invoiceNumber', None) or f'PI{str(getattr(obj, "pk", 0)).zfill(6)}'


def _staff_for_request(request):
    return tenant_qs(request, StaffUser).get(user_ID_id=request.user.pk)


@transaction.atomic
def add_proforma_invoice_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not has_any_permission(request.user, 'home.add_proformainvoice', 'home.can_manage_proforma_invoice'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        invoice_series_id = request.POST.get('invoiceSeriesId')
        requested_invoice_number = request.POST.get('requestedInvoiceNumber')
        party = parse_sales_party(request, request.POST.get('party'))
        line_items = request_line_items_payload(request) if (inventory_feature_enabled(request) and request_line_items_present(request)) else []
        obj = ProformaInvoice()
        assign_tenant(obj, request)
        obj.partyID_id = party.pk
        series, sequence_number, invoice_number = reserve_invoice_number(
            request,
            InvoiceSeries.Module.PROFORMA,
            invoice_series_id,
            preferred_number=requested_invoice_number or None,
        )
        obj.invoiceSeries = series
        obj.invoiceSequenceNumber = sequence_number
        obj.invoiceNumber = invoice_number
        obj.remark = (request.POST.get('remark') or '').strip() or None
        obj.terms = (request.POST.get('terms') or '').strip() or None
        obj.buildDate = datetime.strptime(request.POST.get('colDate'), '%d/%m/%Y').date()
        valid_until_raw = (request.POST.get('validUntil') or '').strip()
        obj.validUntil = datetime.strptime(valid_until_raw, '%d/%m/%Y').date() if valid_until_raw else None
        obj.status = ProformaInvoice.Status.DRAFT
        apply_proforma_invoice_payload(request, obj, party, line_items=line_items)
        obj.createdBy_id = _staff_for_request(request).pk
        obj.save()
        obj.proformaID = next_tenant_sequence_value(request, TenantSequence.Scope.PROFORMA_INVOICE)
        obj.save(update_fields=['proformaID', 'lastUpdatedOn'])
        sync_proforma_lines(request, obj)
        return JsonResponse({'message': 'success', 'invoiceNumber': obj.invoiceNumber}, safe=False)
    except Exception as exc:
        transaction.set_rollback(True)
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_proforma_invoice_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        obj = tenant_object_or_superuser(request, ProformaInvoice, pk=int(request.POST.get('ID')), isDeleted=False)
        if obj is None:
            return JsonResponse({'message': 'error', 'error': 'Proforma invoice not found.'}, status=404, safe=False)
        if obj.status == ProformaInvoice.Status.CONVERTED:
            return JsonResponse({'message': 'error', 'error': 'Converted proforma invoices cannot be edited.'}, status=400, safe=False)
        if not can_edit_proforma_record(request.user, obj):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        party = parse_sales_party(request, request.POST.get('party'))
        line_items = request_line_items_payload(request) if (inventory_feature_enabled(request) and request_line_items_present(request)) else []
        obj.partyID_id = party.pk
        obj.remark = (request.POST.get('remark') or '').strip() or None
        obj.terms = (request.POST.get('terms') or '').strip() or None
        obj.buildDate = datetime.strptime(request.POST.get('colDate'), '%d/%m/%Y').date()
        valid_until_raw = (request.POST.get('validUntil') or '').strip()
        obj.validUntil = datetime.strptime(valid_until_raw, '%d/%m/%Y').date() if valid_until_raw else None
        status = (request.POST.get('status') or obj.status or ProformaInvoice.Status.DRAFT).strip()
        if status in {choice[0] for choice in ProformaInvoice.Status.choices} and status != ProformaInvoice.Status.CONVERTED:
            obj.status = status
        apply_proforma_invoice_payload(request, obj, party, line_items=line_items)
        obj.save()
        sync_proforma_lines(request, obj)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        transaction.set_rollback(True)
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_proforma_invoice(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not has_any_permission(request.user, 'home.delete_proformainvoice', 'home.can_manage_proforma_invoice'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        obj = tenant_object_or_superuser(request, ProformaInvoice, pk=int(request.POST.get('userID')))
        if obj is None:
            return JsonResponse({'message': 'error', 'error': 'Proforma invoice not found.'}, status=404, safe=False)
        if obj.status == ProformaInvoice.Status.CONVERTED:
            return JsonResponse({'message': 'error', 'error': 'Converted proforma invoices cannot be deleted.'}, status=400, safe=False)
        obj.isDeleted = True
        obj.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        transaction.set_rollback(True)
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def convert_proforma_to_sales_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not can_convert_proforma_invoice(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        proforma = tenant_object_or_superuser(request, ProformaInvoice.objects.select_for_update(), pk=int(request.POST.get('ID')), isDeleted=False)
        if proforma is None:
            return JsonResponse({'message': 'error', 'error': 'Proforma invoice not found.'}, status=404, safe=False)
        if proforma.status == ProformaInvoice.Status.CONVERTED or proforma.convertedSales_id:
            return JsonResponse({'message': 'error', 'error': 'This proforma invoice is already converted.'}, status=400, safe=False)
        if proforma.status == ProformaInvoice.Status.CANCELLED:
            return JsonResponse({'message': 'error', 'error': 'Cancelled proforma invoices cannot be converted.'}, status=400, safe=False)

        sales_series_id = request.POST.get('salesInvoiceSeriesId')
        if not sales_series_id:
            return JsonResponse({'message': 'error', 'error': 'Sales invoice series is required.'}, status=400, safe=False)
        sale = Sales()
        assign_tenant(sale, request)
        sale.partyID = proforma.partyID
        series, sequence_number, invoice_number = reserve_invoice_number(
            request,
            InvoiceSeries.Module.SALES,
            sales_series_id,
            preferred_number=request.POST.get('requestedInvoiceNumber') or None,
        )
        sale.invoiceSeries = series
        sale.invoiceSequenceNumber = sequence_number
        sale.invoiceNumber = invoice_number
        sale.invoiceKind = proforma.invoiceKind
        sale.buyerLegalName = proforma.buyerLegalName
        sale.buyerGstNumber = proforma.buyerGstNumber
        sale.buyerBillingAddress = proforma.buyerBillingAddress
        sale.placeOfSupply = proforma.placeOfSupply
        sale.taxableAmount = proforma.taxableAmount
        sale.gstAmount = proforma.gstAmount
        sale.roundOffAmount = proforma.roundOffAmount
        sale.amount = proforma.amount
        sale.modeOfPayment = normalize_sales_mode(request.POST.get('paymentMode') or Sales.Mode.CREDIT)
        sale.remark = (request.POST.get('remark') or proforma.remark or f'Converted from {proforma.invoiceNumber}')[:500]
        sale.buildDate = datetime.strptime((request.POST.get('colDate') or proforma.buildDate.strftime('%d/%m/%Y')), '%d/%m/%Y').date()
        sale.createdBy_id = _staff_for_request(request).pk

        if sale.modeOfPayment == Sales.Mode.ONLINE:
            sale.bankID_id = parse_sales_bank_id(request, request.POST.get('bank'))
            if not sale.bankID_id:
                raise ValueError('Select a bank for online sales.')
        elif sale.modeOfPayment == Sales.Mode.MIX:
            sale.mixCashAmount = safe_sales_float(request.POST.get('amountMixCash'))
            sale.mixOnlineAmount = safe_sales_float(request.POST.get('amountMixOnline'))
            sale.bankID_id = parse_sales_bank_id(request, request.POST.get('bank'))
            if sale.mixCashAmount <= 0 or sale.mixOnlineAmount <= 0 or not sale.bankID_id:
                raise ValueError('Cash amount, online amount, and bank are required for mix sales.')
            if abs(round(sale.mixCashAmount + sale.mixOnlineAmount, 2) - round(float(sale.amount or 0.0), 2)) > 0.01:
                raise ValueError('Mix sales total must match proforma amount.')
        sale.save()
        sale.salesID = next_tenant_sequence_value(request, TenantSequence.Scope.SALES)
        sale.paymentID = next_tenant_sequence_value(request, TenantSequence.Scope.SALES_PAYMENT)
        sale.save(update_fields=['salesID', 'paymentID', 'lastUpdatedOn'])

        item_ids = copy_proforma_lines_to_sale(proforma, sale)
        if item_ids:
            rebuild_inventory_stock(sale.tenant, item_ids)
            validate_non_negative_inventory(sale.tenant, item_ids)
        legacy_api.sync_book_transactions(sale)
        legacy_api.sync_party_ledger_entries_for_object(sale)

        proforma.status = ProformaInvoice.Status.CONVERTED
        proforma.convertedSales = sale
        proforma.convertedOn = timezone.now()
        proforma.save(update_fields=['status', 'convertedSales', 'convertedOn', 'lastUpdatedOn'])
        return JsonResponse({'message': 'success', 'salesID': sale.pk, 'invoiceNumber': sale.invoiceNumber}, safe=False)
    except Exception as exc:
        transaction.set_rollback(True)
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


class ProformaInvoiceListJson(BaseDatatableView):
    order_columns = ['proformaID', 'partyID__name', 'invoiceNumber', 'status', 'amount', 'buildDate', 'validUntil', 'createdBy__name', 'datetime', 'remark', 'id']

    def get_order_columns(self):
        if can_filter_all_proforma(self.request.user):
            return ['proformaID', 'partyID__name', 'invoiceNumber', 'status', 'amount', 'id', 'buildDate', 'validUntil', 'createdBy__name', 'convertedSales__invoiceNumber', 'remark']
        return ['proformaID', 'partyID__name', 'invoiceNumber', 'status', 'amount', 'buildDate', 'validUntil', 'createdBy__name', 'convertedSales__invoiceNumber', 'remark', 'id']

    def get_initial_queryset(self):
        queryset = tenant_qs(self.request, ProformaInvoice).filter(isDeleted=False).select_related('partyID', 'createdBy', 'convertedSales')
        if not can_filter_all_proforma(self.request.user):
            queryset = queryset.filter(createdBy__user_ID_id=self.request.user.pk)
        try:
            start_date = datetime.strptime(self.request.GET.get('startDate'), '%d/%m/%Y').date()
            end_date = datetime.strptime(self.request.GET.get('endDate'), '%d/%m/%Y').date()
            queryset = queryset.filter(buildDate__range=[start_date, end_date])
            status = (self.request.GET.get('status') or '').strip()
            if status and status != 'All':
                queryset = queryset.filter(status=status)
            staff_id = self.request.GET.get('staffID')
            if can_filter_all_proforma(self.request.user) and staff_id and staff_id != 'All':
                queryset = queryset.filter(createdBy_id=int(staff_id))
            return queryset
        except Exception:
            return queryset.filter(buildDate=datetime.today().date())

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(proformaID__icontains=search) |
                Q(invoiceNumber__icontains=search) |
                Q(partyID__name__icontains=search) |
                Q(status__icontains=search) |
                Q(amount__icontains=search) |
                Q(remark__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        admin_mode = can_filter_all_proforma(self.request.user)
        for item in qs:
            action_parts = []
            if can_edit_proforma_record(self.request.user, item):
                action_parts.append('''<a href="/edit_proforma_invoice/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button green"><i class="pen icon"></i></a>'''.format(item.pk))
            action_parts.append('''<button data-inverted="" data-tooltip="Download Proforma" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="downloadProformaInvoice('{}')" class="ui circular blue icon button"><i class="file pdf outline icon"></i></button>'''.format(item.pk))
            if can_convert_proforma_invoice(self.request.user) and item.status != ProformaInvoice.Status.CONVERTED and item.status != ProformaInvoice.Status.CANCELLED:
                action_parts.append('''<button data-inverted="" data-tooltip="Convert to Sales" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="showConvertModal('{}')" class="ui circular purple icon button"><i class="exchange alternate icon"></i></button>'''.format(item.pk))
            if has_any_permission(self.request.user, 'home.delete_proformainvoice', 'home.can_manage_proforma_invoice') and item.status != ProformaInvoice.Status.CONVERTED:
                action_parts.append('''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk))
            status_class = {
                ProformaInvoice.Status.DRAFT: 'grey',
                ProformaInvoice.Status.SENT: 'blue',
                ProformaInvoice.Status.ACCEPTED: 'green',
                ProformaInvoice.Status.CONVERTED: 'purple',
                ProformaInvoice.Status.CANCELLED: 'red',
            }.get(item.status, 'grey')
            converted_ref = getattr(getattr(item, 'convertedSales', None), 'invoiceNumber', None) or '-'
            try:
                party_link = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('party', '{item.partyID.pk}')" style="cursor: pointer;">{escape(item.partyID.name)}</span>'''
            except:
                party_link = '-'
            row = [
                escape(_proforma_public_id(item)),
                party_link,
                escape(item.invoiceNumber or '-'),
                f'<div class="ui tiny {status_class} label">{escape(item.get_status_display())}</div>',
                formatINR(format(item.amount or 0.0, '.0f')),
            ]
            if admin_mode:
                row.append(''.join(action_parts) or '<div class="ui tiny grey label">View Only</div>')
            try:
                if item.createdBy:
                    created_by_link = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.createdBy.pk}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.createdBy.name)}</span>'''
                else:
                    created_by_link = '-'
            except:
                created_by_link = '-'
            row.extend([
                escape(item.buildDate.strftime('%d-%m-%Y') if item.buildDate else '-'),
                escape(item.validUntil.strftime('%d-%m-%Y') if item.validUntil else '-'),
                created_by_link,
                escape(converted_ref),
                escape(item.remark or '-'),
            ])
            if not admin_mode:
                row.append(''.join(action_parts) or '<div class="ui tiny grey label">View Only</div>')
            json_data.append(row)
        return json_data
