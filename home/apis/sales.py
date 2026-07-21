from datetime import datetime

from django.db import transaction
from django.db.models import Case, Q, Value, When
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

import home.apiView as legacy_api
from home.audit import log_audit_event, snapshot_instance
from home.inventory import rebuild_inventory_stock, validate_non_negative_inventory
from home.models import AuditLog, InvoiceSeries, Order, Sales, StaffUser, TenantSequence
from home.services.collections import has_any_permission
from home.services.inventory_lines import (
    inventory_feature_enabled,
    line_items_total,
    request_line_items_payload,
    request_line_items_present,
    safe_sales_float,
)
from home.services.inventory_mutations import sync_sales_lines
from home.services.invoices import apply_sales_invoice_payload
from home.services.invoice_series import reserve_invoice_number
from home.services.messages import send_message
from home.services.orders import link_sale_to_order
from home.services.reports import formatINR
from home.services.sales import (
    can_approve_sales,
    can_delete_sales,
    can_edit_sales,
    can_edit_sales_record,
    can_filter_all_sales,
    can_manage_sales,
    has_sales_access,
    normalize_sales_mode,
    parse_sales_bank_id,
    parse_sales_party,
    sales_list_base_qs,
)
from utils.logger import logger
from home.services.sequences import next_tenant_sequence_value
from home.tenancy import assign_tenant, tenant_qs


def _sales_public_id(obj):
    return getattr(obj, 'salesID', None) or getattr(obj, 'paymentID', None) or f'SAL{str(getattr(obj, "pk", 0)).zfill(6)}'


def _staff_for_request(request):
    return tenant_qs(request, StaffUser).get(user_ID_id=request.user.pk)


def _apply_sales_mode_payload(request, obj: Sales) -> None:
    mode = normalize_sales_mode(
        request.POST.get("paymentMode")
        or request.POST.get("salesMode")
        or request.POST.get("modeOfPayment")
    )
    amount = safe_sales_float(request.POST.get("amount"))
    if amount <= 0 and inventory_feature_enabled(request) and request_line_items_present(request):
        amount = line_items_total(request)
    mix_cash = safe_sales_float(request.POST.get("amountMixCash"))
    mix_online = safe_sales_float(request.POST.get("amountMixOnline"))
    bank_id = parse_sales_bank_id(request, request.POST.get("bank"))

    obj.modeOfPayment = mode
    obj.mixCashAmount = 0.0
    obj.mixOnlineAmount = 0.0
    obj.bankID_id = None

    if mode == Sales.Mode.MIX:
        if mix_cash <= 0 or mix_online <= 0:
            raise ValueError('Mix sales require both cash and online amounts.')
        if not bank_id:
            raise ValueError('Select a bank for online or mix sales.')
        obj.mixCashAmount = mix_cash
        obj.mixOnlineAmount = mix_online
        obj.bankID_id = bank_id
        obj.amount = round(mix_cash + mix_online, 2)
        return

    if amount <= 0:
        raise ValueError('Amount must be greater than zero.')

    obj.amount = amount
    if mode == Sales.Mode.ONLINE:
        if not bank_id:
            raise ValueError('Select a bank for online or mix sales.')
        obj.bankID_id = bank_id


def add_sales_by_admin_api(request):
    if request.method == 'POST':
        try:
            with transaction.atomic():
                invoice_series_id = request.POST.get("invoiceSeriesId")
                requested_invoice_number = request.POST.get("requestedInvoiceNumber")
                col_date = request.POST.get("colDate")
                party = request.POST.get("party")
                remark = request.POST.get("remark")
                customer = parse_sales_party(request, party)
                inventory_enabled = inventory_feature_enabled(request)
                line_items = request_line_items_payload(request) if (inventory_enabled and request_line_items_present(request)) else []
                obj = Sales()
                assign_tenant(obj, request)
                obj.partyID_id = customer.pk
                series, sequence_number, invoice_number = reserve_invoice_number(
                    request,
                    InvoiceSeries.Module.SALES,
                    invoice_series_id,
                    preferred_number=requested_invoice_number or None,
                )
                obj.invoiceSeries = series
                obj.invoiceSequenceNumber = sequence_number
                obj.invoiceNumber = invoice_number
                _apply_sales_mode_payload(request, obj)
                obj.remark = remark
                obj.buildDate = datetime.strptime(col_date, '%d/%m/%Y').date()
                apply_sales_invoice_payload(request, obj, customer, line_items=line_items)
                user = _staff_for_request(request)
                obj.createdBy_id = user.pk
                obj.save()
                obj.salesID = next_tenant_sequence_value(request, TenantSequence.Scope.SALES)
                obj.paymentID = next_tenant_sequence_value(request, TenantSequence.Scope.SALES_PAYMENT)
                obj.save(update_fields=['salesID', 'paymentID', 'lastUpdatedOn'])
                if inventory_enabled:
                    sync_sales_lines(request, obj)
                source_order_id = request.POST.get('sourceOrderId')
                if source_order_id:
                    try:
                        source_order = tenant_qs(request, Order).get(pk=int(source_order_id), isDeleted=False)
                        if str(getattr(source_order, 'status', '') or '') in {
                            Order.Status.APPROVED,
                            Order.Status.PACKED,
                            Order.Status.OUT_FOR_DELIVERY,
                            Order.Status.DELIVERED,
                            Order.Status.RETURNED,
                            Order.Status.FULFILLED,
                        }:
                            link_sale_to_order(request, source_order, obj, note='Sale created from order')
                    except Exception:
                        pass
                legacy_api.sync_book_transactions(obj)
                legacy_api.sync_party_ledger_entries_for_object(obj)
            logger.info("[Sales] Added sale %s (Invoice: %s, Amount: %s)", obj.pk, obj.invoiceNumber, obj.amount)
            return JsonResponse({'message': 'success', 'invoiceNumber': obj.invoiceNumber}, safe=False)
        except Exception as exc:
            logger.error("[Sales] Failed to add sale: %s", str(exc))
            return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
    return JsonResponse({'message': 'error'}, status=405, safe=False)


@transaction.atomic
def update_sales_by_admin_api(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        try:
            col_date = request.POST.get("colDate")
            party = request.POST.get("party")
            remark = request.POST.get("remark")
            sale_id = request.POST.get("ID")
            customer = parse_sales_party(request, party)
            inventory_enabled = inventory_feature_enabled(request)
            line_items = request_line_items_payload(request) if (inventory_enabled and request_line_items_present(request)) else []
            obj = tenant_qs(request, Sales).get(pk=int(sale_id))
            if not can_edit_sales_record(request.user, obj):
                return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
            obj.partyID_id = customer.pk
            _apply_sales_mode_payload(request, obj)
            obj.remark = remark
            obj.buildDate = datetime.strptime(col_date, '%d/%m/%Y').date()
            apply_sales_invoice_payload(request, obj, customer, line_items=line_items)
            obj.save()
            if inventory_enabled:
                sync_sales_lines(request, obj)
            legacy_api.sync_book_transactions(obj)
            legacy_api.sync_party_ledger_entries_for_object(obj)
            logger.info("[Sales] Updated sale %s (Invoice: %s)", obj.pk, obj.invoiceNumber)
            return JsonResponse({'message': 'success'}, safe=False)
        except Exception as exc:
            logger.error("[Sales] Failed to update sale %s: %s", request.POST.get("ID"), str(exc))
            return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
    return JsonResponse({'message': 'error'}, status=405, safe=False)


@transaction.atomic
def delete_sales(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        if not can_delete_sales(request.user):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        try:
            sale_id = request.POST.get("userID")
            obj = tenant_qs(request, Sales).select_related().get(pk=int(sale_id))
            item_ids = list(obj.lines.filter(isDeleted=False).values_list('item_id', flat=True))
            obj.isDeleted = True
            obj.save()
            rebuild_inventory_stock(obj.tenant, item_ids)
            validate_non_negative_inventory(obj.tenant, item_ids)
            legacy_api.sync_book_transactions(obj)
            legacy_api.sync_party_ledger_entries_for_object(obj)
            logger.info("[Sales] Deleted sale %s", sale_id)
            return JsonResponse({'message': 'success'}, safe=False)
        except Exception as exc:
            logger.error("[Sales] Failed to delete sale %s: %s", request.POST.get("userID"), str(exc))
            return JsonResponse({'message': 'error'}, safe=False)
    return JsonResponse({'message': 'error'}, status=405, safe=False)


@transaction.atomic
def send_message_sales(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'detail': 'Invalid request method.'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized', 'detail': 'Please login to approve sales.'}, status=401, safe=False)
    if not can_approve_sales(request.user):
        return JsonResponse({'message': 'forbidden', 'detail': 'You do not have permission to approve sales.'}, status=403, safe=False)
    try:
        sale_id = request.POST.get("userID")
        obj = tenant_qs(request, Sales).select_related().get(pk=int(sale_id))
        if obj.isApproved:
            return JsonResponse({'message': 'success', 'detail': 'Sale is already approved.'}, safe=False)
        user = tenant_qs(request, StaffUser).get(user_ID__id=request.user.pk)
        obj.isApproved = True
        obj.approvedBy_id = user.pk
        obj.approvedOn = timezone.now()
        obj.save()
        legacy_api.sync_book_transactions(obj)
        log_audit_event(
            obj,
            AuditLog.Action.APPROVE,
            actor=request.user,
            changes={
                'isApproved': {'from': False, 'to': True},
                'approvedBy_id': {'from': None, 'to': user.pk},
                'approvedOn': {'from': None, 'to': obj.approvedOn.isoformat() if obj.approvedOn else None},
            },
            snapshot=snapshot_instance(obj),
        )
        legacy_api.sync_party_ledger_entries_for_object(obj)
        try:
            msg = "Your sale has been approved. Bill No. {} Dt. {} Amt. Rs. {}. Please contact 7005607770 if you have any questions. Thanks, BSS".format(
                obj.invoiceNumber, obj.buildDate.strftime('%d-%m-%Y'), obj.amount)
            try:
                numbers = str(obj.partyID.phone).split('.')
                for number in numbers:
                    send_message(number, msg, obj.partyID.name)
            except Exception:
                send_message(obj.partyID.phone, msg, obj.partyID.name)
        except Exception as msg_exc:
            logger.error("[Sales] Failed to send approval message for sale %s: %s", sale_id, str(msg_exc))
        logger.info("[Sales] Approved sale %s (Invoice: %s)", obj.pk, obj.invoiceNumber)
        return JsonResponse({'message': 'success', 'detail': 'Sale approved successfully.'}, safe=False)
    except Exception as exc:
        logger.error("[Sales] Failed to approve sale %s: %s", request.POST.get("userID"), str(exc))
        return JsonResponse({'message': 'error', 'detail': 'Unable to approve sale.'}, safe=False)


def search_sales_invoice_lookup_api(request):
    if request.method != 'GET':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not has_sales_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)

    query = (request.GET.get('q') or '').strip()
    today_only = str(request.GET.get('today') or '').strip().lower() in {'1', 'true', 'yes'}

    qs = tenant_qs(request, Sales).filter(isDeleted=False).select_related('partyID', 'createdBy', 'bankID')
    if not can_filter_all_sales(request.user):
        qs = qs.filter(createdBy__user_ID_id=request.user.pk)
    if today_only and not query:
        qs = qs.filter(buildDate=datetime.now().date()).order_by('-id')[:20]
    elif query:
        if len(query) < 2:
            return JsonResponse({'message': 'success', 'data': []}, safe=False)
        qs = qs.annotate(
            match_rank=Case(
                When(invoiceNumber__iexact=query, then=Value(0)),
                When(paymentID__iexact=query, then=Value(1)),
                When(invoiceNumber__istartswith=query, then=Value(2)),
                When(paymentID__istartswith=query, then=Value(3)),
                When(partyID__name__istartswith=query, then=Value(4)),
                When(invoiceNumber__icontains=query, then=Value(5)),
                When(paymentID__icontains=query, then=Value(6)),
                When(partyID__name__icontains=query, then=Value(7)),
                default=Value(99),
            )
        ).filter(
            Q(invoiceNumber__icontains=query) |
            Q(paymentID__icontains=query) |
            Q(partyID__name__icontains=query)
        ).order_by('match_rank', '-buildDate', '-id')[:10]
    else:
        return JsonResponse({'message': 'success', 'data': []}, safe=False)

    rows = []
    for sale in qs:
        rows.append({
            'ID': sale.pk,
            'InvoiceNumber': sale.invoiceNumber or '-',
            'PaymentID': sale.paymentID or '-',
            'PartyName': getattr(getattr(sale, 'partyID', None), 'name', None) or '-',
            'Amount': float(sale.amount or 0.0),
            'BuildDate': sale.buildDate.strftime('%d/%m/%Y') if sale.buildDate else '-',
            'Mode': sale.modeOfPayment or '-',
            'CreatedBy': getattr(getattr(sale, 'createdBy', None), 'name', None) or '-',
            'Remark': sale.remark or '',
            'ThermalPrintUrl': reverse('homeApp:print_sales_thermal_challan') + f'?id={sale.pk}',
        })
    return JsonResponse({'message': 'success', 'data': rows}, safe=False)


class SalesByAdminListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID__name', 'invoiceNumber', 'modeOfPayment', 'bankID__name', 'amount', 'id', 'buildDate', 'createdBy__name',
                     'isApproved', 'approvedBy__name', 'approvedOn',
                     'datetime', 'remark'
                     ]

    def get_initial_queryset(self):
        queryset = sales_list_base_qs(self.request).filter(isDeleted__exact=False)
        if not can_filter_all_sales(self.request.user):
            queryset = queryset.filter(createdBy__user_ID_id=self.request.user.pk)
        search_value = self.request.GET.get('search[value]', None)
        if search_value and search_value.strip():
            return queryset
        try:
            start_date_value = self.request.GET.get("startDate")
            end_date_value = self.request.GET.get("endDate")
            staff_id = self.request.GET.get("staffID")
            start_date = datetime.strptime(start_date_value, '%d/%m/%Y')
            end_date = datetime.strptime(end_date_value, '%d/%m/%Y')
            queryset = queryset.filter(buildDate__range=[start_date.date(), end_date.date()])
            if can_filter_all_sales(self.request.user) and staff_id and staff_id != 'All':
                queryset = queryset.filter(createdBy_id=int(staff_id))
            return queryset
        except Exception:
            return queryset.filter(buildDate__exact=datetime.today().date())

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(paymentID__icontains=search) | Q(salesID__icontains=search) | Q(partyID__name__icontains=search) | Q(
                    amount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search) | Q(modeOfPayment__icontains=search) | Q(bankID__name__icontains=search)
                | Q(datetime__icontains=search) | Q(buildDate__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action_parts = []
            if can_approve_sales(self.request.user):
                action_parts.append('''<button data-inverted="" data-tooltip="Make Approval" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="showConfirmationModal('{}')" class="ui circular facebook icon button purple"><i class="check circle icon"></i></button>'''.format(item.pk))
            if can_edit_sales(self.request.user):
                action_parts.append('''<a href="/edit_sales/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button green"><i class="pen icon"></i></a>'''.format(item.pk))
            action_parts.append('''<button data-inverted="" data-tooltip="Download Invoice" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="downloadSalesInvoice('{}')" class="ui circular blue icon button"><i class="file pdf outline icon"></i></button>'''.format(item.pk))
            if can_delete_sales(self.request.user):
                action_parts.append('''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk))
            action = ''.join(action_parts) or '<div class="ui tiny grey label">View Only</div>'
            try:
                party = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('party', '{item.partyID.pk}')" style="cursor: pointer;">{escape(item.partyID.name)}</span>'''
            except:
                party = '-'
            try:
                if item.createdBy:
                    created_by = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.createdBy.pk}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.createdBy.name)}</span>'''
                else:
                    created_by = '-'
            except:
                created_by = '-'
            try:
                if item.approvedBy:
                    approved_by = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.approvedBy.pk}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.approvedBy.name)}</span>'''
                else:
                    approved_by = '-'
            except:
                approved_by = '-'
            try:
                if item.bankID:
                    bank_link = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('bank', '{item.bankID.pk}')" style="cursor: pointer;"><i class="university icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.bankID.name)}</span>'''
                else:
                    bank_link = '-'
            except:
                bank_link = '-'
            payment_mode = item.modeOfPayment or '-'
            if payment_mode == Sales.Mode.MIX:
                payment_detail = 'Cash: {} | Online: {}'.format(
                    formatINR(format(item.mixCashAmount or 0.0, '.0f')),
                    formatINR(format(item.mixOnlineAmount or 0.0, '.0f')),
                )
            elif payment_mode == Sales.Mode.ONLINE:
                payment_detail = f'Online via {bank_link}'
            elif payment_mode == Sales.Mode.CREDIT:
                payment_detail = 'Credit sale'
            else:
                payment_detail = 'Cash sale'
            is_approved = '''<div class="ui tiny green label">
                             Yes
                           </div>''' if item.isApproved else '''<div class="ui tiny red label">
                             No
                           </div>'''
            json_data.append([
                escape(_sales_public_id(item)),
                party,
                escape(item.invoiceNumber),
                escape(payment_mode),
                payment_detail,
                formatINR(format(item.amount, '.0f')),
                action,
                escape(item.buildDate.strftime('%d-%m-%Y')),
                created_by,
                is_approved,
                approved_by,
                escape(item.approvedOn.strftime('%d-%m-%Y %I:%M %p') if item.approvedOn else '-'),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
            ])
        return json_data


class SalesByStaffListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID__name', 'invoiceNumber', 'modeOfPayment', 'bankID__name', 'amount', 'buildDate', 'createdBy__name',
                     'approvedOn', 'datetime', 'remark', 'id'
                     ]

    def get_initial_queryset(self):
        queryset = sales_list_base_qs(self.request).filter(isDeleted__exact=False)
        if not can_filter_all_sales(self.request.user):
            queryset = queryset.filter(createdBy__user_ID_id=self.request.user.pk)
        search_value = self.request.GET.get('search[value]', None)
        if search_value and search_value.strip():
            return queryset
        try:
            start_date_value = self.request.GET.get("startDate")
            end_date_value = self.request.GET.get("endDate")
            staff_id = self.request.GET.get("staffID")
            start_date = datetime.strptime(start_date_value, '%d/%m/%Y')
            end_date = datetime.strptime(end_date_value, '%d/%m/%Y')
            queryset = queryset.filter(buildDate__range=[start_date.date(), end_date.date()])
            if can_filter_all_sales(self.request.user) and staff_id and staff_id != 'All':
                queryset = queryset.filter(createdBy_id=int(staff_id))
            return queryset
        except Exception:
            return queryset.filter(buildDate__exact=datetime.today().date())

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(paymentID__icontains=search) | Q(salesID__icontains=search) | Q(partyID__name__icontains=search) | Q(
                    amount__icontains=search) | Q(
                    invoiceNumber__icontains=search) | Q(createdBy__name__icontains=search) | Q(
                    remark__icontains=search) | Q(modeOfPayment__icontains=search) | Q(bankID__name__icontains=search)
                | Q(datetime__icontains=search) | Q(buildDate__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action_parts = []
            if can_edit_sales(self.request.user):
                if item.isApproved and not (can_manage_sales(self.request.user) or can_approve_sales(self.request.user) or self.request.user.has_perm('home.can_view_party_credentials')):
                    action_parts.append('<div class="ui tiny orange label">Approved</div>')
                else:
                    action_parts.append('''<a href="/edit_sales/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button green"><i class="pen icon"></i></a>'''.format(item.pk))
            action_parts.append('''<button data-inverted="" data-tooltip="Download Invoice" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="downloadSalesInvoice('{}')" class="ui circular blue icon button"><i class="file pdf outline icon"></i></button>'''.format(item.pk))
            if can_delete_sales(self.request.user):
                action_parts.append('''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk))
            action = ''.join(action_parts) or '<div class="ui tiny grey label">View Only</div>'
            try:
                party = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('party', '{item.partyID.pk}')" style="cursor: pointer;">{escape(item.partyID.name)}</span>'''
            except:
                party = '-'
            try:
                if item.createdBy:
                    created_by = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.createdBy.pk}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.createdBy.name)}</span>'''
                else:
                    created_by = '-'
            except:
                created_by = '-'
            try:
                if item.bankID:
                    bank_link = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('bank', '{item.bankID.pk}')" style="cursor: pointer;"><i class="university icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.bankID.name)}</span>'''
                else:
                    bank_link = '-'
            except:
                bank_link = '-'
            payment_mode = item.modeOfPayment or '-'
            if payment_mode == Sales.Mode.MIX:
                payment_detail = 'Cash: {} | Online: {}'.format(
                    formatINR(format(item.mixCashAmount or 0.0, '.0f')),
                    formatINR(format(item.mixOnlineAmount or 0.0, '.0f')),
                )
            elif payment_mode == Sales.Mode.ONLINE:
                payment_detail = f'Online via {bank_link}'
            elif payment_mode == Sales.Mode.CREDIT:
                payment_detail = 'Credit sale'
            else:
                payment_detail = 'Cash sale'
            json_data.append([
                escape(_sales_public_id(item)),
                party,
                escape(item.invoiceNumber),
                escape(payment_mode),
                payment_detail,
                formatINR(format(item.amount, '.0f')),
                escape(item.buildDate.strftime('%d-%m-%Y')),
                created_by,
                escape(item.approvedOn.strftime('%d-%m-%Y %I:%M %p') if item.approvedOn else '-'),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
                action,
            ])
        return json_data
