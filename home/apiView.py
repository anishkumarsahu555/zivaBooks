import threading
from io import BytesIO
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import requests
from django.contrib.auth import authenticate, login
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Sum, Case, When, Value, F, FloatField, IntegerField, ExpressionWrapper, Window, Max
from django.db.models.functions import Cast, Coalesce
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.utils.html import escape
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.decorators.clickjacking import xframe_options_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from weasyprint import CSS, HTML
from home.models import *
from home.books import ensure_bank_account, ensure_books_for_tenant, sync_book_transactions
from home.cache_keys import (
    inventory_list_cache_key,
    inventory_list_version_cache_key,
    party_list_cache_key,
    party_list_version_cache_key,
)
from home.dashboard_charts import get_dashboard_charts_api
from home.credential_links import DEFAULT_CREDENTIAL_LINK_MINUTES, create_credential_share_token
from home.inventory import (
    get_or_create_default_warehouse,
    rebuild_inventory_stock,
    validate_non_negative_inventory,
)
from home.otp import (
    OTP_TTL_SECONDS,
    clear_collection_otp,
    clear_collection_status,
    clear_order_delivery_status,
    get_collection_otp_payload,
    get_order_delivery_status_payload,
    get_order_delivery_otp_payload,
    clear_order_delivery_otp,
    set_order_delivery_status,
    set_collection_otp,
    set_order_delivery_otp,
    set_collection_status,
)
from home.party_ledger import sync_party_ledger_entries_for_object
from home.services.cash_counter import (
    can_filter_all_cash_counter as _can_filter_all_cash_counter,
    can_manage_cash_counter_records as _can_manage_cash_counter_records,
    cash_counter_collection_list_base_qs as _cash_counter_collection_list_base_qs,
    cash_counter_list_base_qs as _cash_counter_list_base_qs,
)
from home.services.cash_counter_invoicing import (
    cash_counter_billing_modes as _cash_counter_billing_modes,
    cash_counter_invoice_action_buttons as _cash_counter_invoice_action_buttons,
    cash_counter_return_source_modes as _cash_counter_return_source_modes,
    gst_breakup_from_cash_counter as _gst_breakup_from_cash_counter,
    parse_return_source_ref as _parse_return_source_ref,
)
from home.services.inventory_lines import (
    advanced_invoicing_enabled as _advanced_invoicing_enabled,
    assert_advanced_invoicing_enabled as _assert_advanced_invoicing_enabled,
    assert_inventory_enabled as _assert_inventory_enabled,
    assert_non_gst_enabled as _assert_non_gst_enabled,
    available_batch_stock_queryset as _available_batch_stock_queryset,
    earliest_available_batch_for_fefo as _earliest_available_batch_for_fefo,
    inventory_feature_enabled as _inventory_feature_enabled,
    non_gst_shadow_enabled as _non_gst_shadow_enabled,
    normalize_stock_bucket as _normalize_stock_bucket,
    parse_inventory_batch_date as _parse_inventory_batch_date,
    parse_inventory_item as _parse_inventory_item,
    parse_inventory_warehouse as _parse_inventory_warehouse,
    resolve_inventory_batch as _resolve_inventory_batch,
    safe_local_date as _safe_local_date,
    sales_round_off_from_total as _sales_round_off_from_total,
    sales_totals_from_lines as _sales_totals_from_lines,
    validate_inventory_quantity_for_unit as _validate_inventory_quantity_for_unit,
)
from home.services.inventory_mutations import (
    normalize_purchase_lines_for_treatment as _normalize_purchase_lines_for_treatment,
    purchase_totals_from_lines as _purchase_totals_from_lines,
    sync_cash_counter_lines as _sync_cash_counter_lines,
    sync_cash_counter_return_voucher as _sync_cash_counter_return_voucher,
    sync_purchase_lines as _sync_purchase_lines,
)
from home.services.invoices import (
    gst_breakup_from_invoice_subject as _gst_breakup_from_sale,
    gst_state_code_from_number as _gst_state_code_from_number,
    gst_state_code_from_place_of_supply as _gst_state_code_from_place_of_supply,
    is_utgst_state_code as _is_utgst_state_code,
    normalize_gst_number as _normalize_gst_number,
    normalize_invoice_kind as _normalize_invoice_kind,
)
from home.services.inventory_catalog import (
    apply_inventory_category_payload as _apply_inventory_category_payload,
    apply_inventory_item_payload as _apply_inventory_item_payload,
    apply_inventory_tax_profile_payload as _apply_inventory_tax_profile_payload,
    apply_inventory_unit_payload as _apply_inventory_unit_payload,
    parse_inventory_category as _parse_inventory_category,
    parse_inventory_tax_profile as _parse_inventory_tax_profile,
    parse_inventory_unit as _parse_inventory_unit,
)
from home.services.collections import (
    BANK_REQUIRED_COLLECTION_MODES,
    CHEQUE_COLLECTION_MODES,
    can_auto_approve_collections as _can_auto_approve_collections,
    can_filter_all_collections as _can_filter_all_collections,
    can_review_collections as _can_review_collections,
    collection_datetime_with_preserved_time,
    collection_list_base_qs as _collection_list_base_qs,
    collection_payment_validation_error,
    collection_verified_label as _collection_verified_label,
    has_any_permission as _has_any_permission,
    parse_collection_form_date,
    parse_party_id_value as _parse_party_id_value,
)
from home.services.orders import (
    can_add_order as _can_add_order,
    can_approve_orders as _can_approve_orders,
    can_delete_order as _can_delete_order,
    can_delete_order_record as _can_delete_order_record,
    can_edit_order as _can_edit_order,
    can_edit_order_record as _can_edit_order_record,
    can_manage_orders as _can_manage_orders,
    can_set_order_date as _can_set_order_date,
    create_order_status_event as _create_order_status_event,
    format_order_number as _format_order_number,
    link_sale_to_order as _link_sale_to_order,
    normalize_order_status as _normalize_order_status,
    order_allowed_next_statuses as _order_allowed_next_statuses,
    order_public_id as _order_public_id,
    order_status_label as _order_status_label,
    order_status_text as _order_status_text,
    order_status_transition_map as _order_status_transition_map,
    orders_list_base_qs as _orders_list_base_qs,
    set_order_tracking_timestamps as _set_order_tracking_timestamps,
)
from home.services.messages import send_message, send_whatsapp_message
from home.services.reports import (
    formatINR,
    format_money_compact as _format_money_compact,
    pdf_base_context as _pdf_base_context,
    safe_sheet_title as _safe_sheet_title,
    xlsx_report_response as _xlsx_report_response,
)
from home.services.sequences import next_tenant_sequence_value as _next_tenant_sequence_value
from home.tenancy import assign_tenant, ensure_membership, get_current_tenant, get_staff_user_for_request, tenant_object_or_superuser, tenant_qs
from home.audit import log_audit_event, snapshot_instance
from home.navigation import _effective_permission_names
User = get_user_model()
BASE_URL_WHATSAPP = "https://bot.betablaster.in/api/"
DEFAULT_AVATAR_URL = "/static/images/default-avatar.svg"
OPENSTREETMAP_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
OPENSTREETMAP_USER_AGENT = "zivaBooks/attendance-location"
INVOICE_MODULE_MODEL_MAP = {
    InvoiceSeries.Module.SALES: Sales,
    InvoiceSeries.Module.CASH_COUNTER: CashCounter,
    InvoiceSeries.Module.PROFORMA: ProformaInvoice,
}
TENANT_SEQUENCE_DEFAULTS = {
    TenantSequence.Scope.COLLECTION_PAYMENT: {'prefix': 'COL', 'padding': 6},
    TenantSequence.Scope.SALES: {'prefix': 'SAL', 'padding': 6},
    TenantSequence.Scope.SALES_PAYMENT: {'prefix': 'SPY', 'padding': 6},
    TenantSequence.Scope.ORDER: {'prefix': 'ORD', 'padding': 6},
    TenantSequence.Scope.PURCHASE: {'prefix': 'PUR', 'padding': 6},
    TenantSequence.Scope.CASH_COUNTER: {'prefix': 'CC', 'padding': 6},
    TenantSequence.Scope.CASH_COUNTER_COLLECTION: {'prefix': 'CCP', 'padding': 6},
    TenantSequence.Scope.INVENTORY_OPENING: {'prefix': 'OPN', 'padding': 6},
    TenantSequence.Scope.INVENTORY_ADJUSTMENT: {'prefix': 'ADJ', 'padding': 6},
    TenantSequence.Scope.INVENTORY_TRANSFER: {'prefix': 'TRF', 'padding': 6},
    TenantSequence.Scope.PURCHASE_RETURN: {'prefix': 'PRN', 'padding': 6},
    TenantSequence.Scope.SALES_RETURN: {'prefix': 'SRN', 'padding': 6},
    TenantSequence.Scope.PROFORMA_INVOICE: {'prefix': 'PI', 'padding': 6},
}
DEFAULT_GROUP_PERMISSION_MAP = {
    'Owner': '__all__',
    'Admin': '__all__',
    'Moderator': [
        'home.can_manage_parties',
        'home.can_manage_sales',
        'home.can_approve_sales',
        'home.can_approve_collection',
        'home.can_tally_collection',
        'home.can_view_collection_reports',
        'home.can_view_sales_reports',
        'home.can_view_cash_counter_reports',
        'home.can_view_audit_logs',
        'home.can_view_staff_locations',
    ],
    'Collection': [
        'home.add_collection',
        'home.change_collection',
        'home.view_collection',
        'home.view_party',
        'home.view_attendance',
        'home.can_track_staff_location',
    ],
    'SalesAdmin': [
        'home.add_sales',
        'home.change_sales',
        'home.view_sales',
        'home.can_manage_sales',
        'home.can_view_sales_reports',
        'home.view_party',
    ],
    'CashCounter': [
        'home.add_cashcounter',
        'home.change_cashcounter',
        'home.view_cashcounter',
        'home.add_collectioncashcounter',
        'home.change_collectioncashcounter',
        'home.view_collectioncashcounter',
        'home.can_manage_cash_counter',
        'home.can_view_cash_counter_reports',
    ],
    'NormalStaff': [
        'home.view_attendance',
        'home.add_attendance',
        'home.change_attendance',
        'home.can_track_staff_location',
    ],
    'Customer': [
        'home.view_party',
    ],
    'Viewer': [
        'home.view_party',
        'home.view_collection',
        'home.view_sales',
        'home.view_cashcounter',
        'home.view_attendance',
        'home.can_view_collection_reports',
        'home.can_view_sales_reports',
        'home.can_view_cash_counter_reports',
        'home.can_view_audit_logs',
    ],
}
ADMIN_LIKE_PERMS = (
    'home.can_manage_staff',
    'home.can_manage_staff_groups',
    'home.can_manage_parties',
    'home.can_view_party_credentials',
    'home.can_approve_collection',
    'home.can_view_audit_logs',
)
class LocationThread(threading.Thread):
    def __init__(self, lat, lng):
        self.lat = lat
        self.lng = lng
        self._is_running = True
        threading.Thread.__init__(self)
    def run(self):
        while (self._is_running):
            return _reverse_geocode_openstreetmap(self.lat, self.lng)
        self.stop()
    def stop(self):
        self._is_running = False
def get_location(lat, lng):
    LocationThread(lat, lng).start()
class MessageThread(threading.Thread):
    def __init__(self, number, message, partyName):
        self.number = number
        self.message = message
        self.partyName = partyName
        self._is_running = True
        threading.Thread.__init__(self)
    def run(self):
        while (self._is_running):
            msg = tenant_qs(get_current_tenant(None), WhatsappMessage).filter(isDeleted__exact=False).last()
            if msg and msg.used < msg.balance:
                try:
                    # r = requests.get(
                    #     msg.name + "send?number=91" + self.number + "&type=text&message=" + self.message + "&instance_id=" + msg.instanceID + "&access_token=" + msg.apiKey,
                    #     verify=False)
                    # data = r.json()
                    obj = WhatsappMessageStatus()
                    assign_tenant(obj, get_current_tenant(None))
                    obj.phone = self.number
                    obj.message = self.message
                    obj.messageTo = self.partyName
                    try:
                        # if data['status'] == 'success':
                        #     obj.status = 'Success'
                        # else:
                        obj.status = 'Fail'
                    except:
                        obj.status = 'Fail'
                    obj.save()
                    msg.used = (msg.used + 1)
                    msg.save()
                except:
                    obj = WhatsappMessageStatus()
                    assign_tenant(obj, get_current_tenant(None))
                    obj.phone = self.number
                    obj.message = self.message
                    obj.messageTo = self.partyName
                    obj.status = 'Fail'
                    obj.save()
            else:
                obj = WhatsappMessageStatus()
                assign_tenant(obj, get_current_tenant(None))
                obj.phone = self.number
                obj.message = self.message
                obj.messageTo = self.partyName
                obj.status = 'Fail'
                obj.save()
            self.stop()
    def stop(self):
        self._is_running = False
def _coordinate_fallback(lat, lng):
    return f"{lat}, {lng}"


def _tenant_attendance_location_enabled(request_or_user):
    tenant = get_current_tenant(request_or_user)
    return bool(getattr(tenant, 'enableAttendanceLocationMaps', False))


def _reverse_geocode_openstreetmap(lat, lng):
    lat = str(lat).strip()
    lng = str(lng).strip()
    if not lat or not lng:
        return ""
    try:
        response = requests.get(
            OPENSTREETMAP_REVERSE_URL,
            params={
                "format": "jsonv2",
                "lat": lat,
                "lon": lng,
                "zoom": 18,
                "addressdetails": 1,
            },
            headers={
                "User-Agent": OPENSTREETMAP_USER_AGENT,
                "Accept-Language": "en",
            },
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("display_name") or _coordinate_fallback(lat, lng)
    except Exception:
        return _coordinate_fallback(lat, lng)

from home.apis.admin_master import (
    _cash_counter_public_id,
    _invoice_series_payload_for_module,
    _reserve_invoice_number,
    _sync_staff_user_group_membership,
)

# ------------------------------Collection ----------------------------------------
# ----------------------------- Party ---------------------
@transaction.atomic
def change_password_api(request):
    if request.method == 'POST':
        try:
            password = request.POST.get('password')
            data = tenant_qs(request, StaffUser).select_related().get(user_ID_id=request.user.pk)
            data.userPassword = password
            data.save()
            user = User.objects.select_related().get(pk=request.user.pk)
            user.set_password(password)
            user.save()
            log_audit_event(
                data,
                AuditLog.Action.PASSWORD_CHANGE,
                actor=request.user,
                changes={'userPassword': {'from': '***', 'to': '***'}},
                snapshot=snapshot_instance(data),
            )
            user = authenticate(request, username=user.username, password=password)
            if user is not None:
                login(request, user)
                return JsonResponse({'message': 'success'}, safe=False)
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
def generate_collection_report(request):
    cDate = request.GET.get('cDate')
    staffID = request.GET.get('staffID')
    colDate = datetime.strptime(cDate, '%d/%m/%Y')
    a_total_cash = 0.0
    a_total_cheque = 0.0
    a_total_online = 0.0
    a_total_cheque_cc = 0.0
    a_total_party = 0.0
    base_qs = tenant_qs(request, Collection).select_related().filter(
        collectionDateTime__date=colDate.date(),
        isApproved__exact=True,
        isDeleted__exact=False
    )
    if _can_filter_all_collections(request.user):
        if staffID and staffID != 'All':
            col = base_qs.filter(collectedBy_id=int(staffID)).order_by('collectedBy__name')
            user = tenant_qs(request, StaffUser).get(pk=int(staffID))
            staffName = user.name + ' - ' + user.partyGroupID.name
        else:
            col = base_qs.order_by('collectedBy__name')
            staffName = 'All'
    else:
        col = base_qs.filter(collectedBy__user_ID_id=request.user.pk).order_by('collectedBy__name')
        staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk).first()
        staffName = staff.name if staff else 'MyList'
    for a in col:
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
        'date': colDate,
        'col': col,
        'staffName': staffName,
        'a_total_cash': a_total_cash,
        'a_total_cheque': a_total_cheque,
        'a_total_online': a_total_online,
        'a_total_cheque_cc': a_total_cheque_cc,
        'a_total_party': a_total_party,
        'total': a_total_cash + a_total_cheque + a_total_online + a_total_cheque_cc + a_total_party
    }
    context.update(_pdf_base_context(request, [
        f'Date: {colDate.strftime("%d/%m/%Y")}',
        f'Staff: {staffName}',
        'Approved collections only',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = "report.pdf"
    html = render_to_string("home/admin/collectionReportPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A5; margin: .3cm ; }')])
    return response


def generate_collection_report_excel(request):
    cDate = request.GET.get('cDate')
    staffID = request.GET.get('staffID')
    colDate = datetime.strptime(cDate, '%d/%m/%Y')
    base_qs = tenant_qs(request, Collection).select_related('partyID', 'collectedBy', 'bankID').filter(
        collectionDateTime__date=colDate.date(),
        isApproved__exact=True,
        isDeleted__exact=False
    )
    if _can_filter_all_collections(request.user):
        if staffID and staffID != 'All':
            col = base_qs.filter(collectedBy_id=int(staffID)).order_by('collectedBy__name', 'collectionDateTime')
            user = tenant_qs(request, StaffUser).get(pk=int(staffID))
            staffName = user.name + ' - ' + user.partyGroupID.name
        else:
            col = base_qs.order_by('collectedBy__name', 'collectionDateTime')
            staffName = 'All'
    else:
        col = base_qs.filter(collectedBy__user_ID_id=request.user.pk).order_by('collectedBy__name', 'collectionDateTime')
        staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk).first()
        staffName = staff.name if staff else 'MyList'

    mode_totals = defaultdict(float)
    detail_rows = []
    total = 0.0
    for item in col:
        amount = float(item.paidAmount or 0.0)
        total += amount
        mode_totals[item.modeOfPayment or 'Unknown'] += amount
        detail_rows.append([
            item.paymentID or '-',
            item.partyID.name if item.partyID_id else '-',
            item.collectedBy.name if item.collectedBy_id else '-',
            item.modeOfPayment or '-',
            getattr(item.bankID, 'name', '-') if getattr(item, 'bankID', None) else '-',
            amount,
            item.collectionDateTime.strftime('%d/%m/%Y %I:%M %p') if item.collectionDateTime else '-',
            'Yes' if item.otpVerified else 'No',
            'Yes' if item.isTallied else 'No',
            item.remark or '-',
        ])

    return _xlsx_report_response('collection-report', [{
        'title': 'Collection Report',
        'report_title': 'Collection Report',
        'report_subtitle': 'Approved collections only',
        'metadata': [
            ('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')),
            ('Date', colDate.strftime('%d/%m/%Y')),
            ('Staff', staffName),
            ('Generated By', _pdf_base_context(request).get('pdf_generated_by')),
        ],
        'tables': [
            {
                'title': 'Summary',
                'headers': ['Metric', 'Value'],
                'rows': [['Total Collections', total]] + [[mode, amount] for mode, amount in sorted(mode_totals.items())],
            },
            {
                'title': 'Collection Detail',
                'headers': ['Payment ID', 'Customer', 'Collector', 'Mode', 'Bank', 'Amount', 'Collected On', 'OTP Verified', 'Tallied', 'Remark'],
                'rows': detail_rows,
            },
        ],
    }])
# ----------------------Orders-----------------
# ----------------------Sales-----------------
@transaction.atomic
def enable_tally_post_api(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("tallyColID")
            obj = tenant_qs(request, Collection).get(pk=int(id), isApproved__exact=True)
            try:
                obj.isTallied = True
                obj.save()
                return JsonResponse({'message': 'success'}, safe=False)
            except:
                return JsonResponse({'message': 'error'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
