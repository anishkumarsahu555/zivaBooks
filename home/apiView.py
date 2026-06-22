import threading
import csv
from io import BytesIO
from calendar import month_name, monthrange
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import requests
from django.conf import settings
from django.contrib.auth import authenticate, login
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Sum, Count, Case, When, Value, F, FloatField, IntegerField, ExpressionWrapper, Window, Max
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.utils.html import escape
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
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
from home.credential_links import DEFAULT_CREDENTIAL_LINK_MINUTES, create_credential_share_token
from home.inventory import (
    get_or_create_default_warehouse,
    rebuild_inventory_stock,
    stock_snapshot_for_tenant,
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
def send_message(number, message, party):
    import sys
    if 'test' in sys.argv:
        MessageThread(number, message, party).run()
    else:
        MessageThread(number, message, party).start()
def _parse_party_id_value(raw: str):
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    # Common UI format: "Name - Group @ 123"
    if '@' in value:
        right = value.split('@')[-1].strip()
        if right.isdigit():
            return int(right)
    # Fallback: just an id.
    if value.isdigit():
        return int(value)
    return None


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
def request_collection_otp_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not _has_any_permission(
        request.user,
        'home.can_approve_collection',
        'home.can_view_party_credentials',
        'home.add_collection',
    ):
        return JsonResponse({'message': 'error', 'error': 'Permission denied.'}, status=403)
    try:
        party = request.POST.get('party') or ''
        party_id = _parse_party_id_value(party)
        if not party_id:
            return JsonResponse({'message': 'error', 'error': 'Party is required.'}, status=400)
        cus = tenant_qs(request, Party).filter(isDeleted=False, pk=party_id).only('id', 'name', 'phone', 'tenant_id').first()
        if not cus:
            return JsonResponse({'message': 'error', 'error': 'Party not found.'}, status=404)
        payment_mode = (request.POST.get('paymentMode') or '').strip()
        amount_paid_raw = (request.POST.get('amountPaid') or '').strip()
        collect_date = (request.POST.get('collectDate') or '').strip()
        try:
            amount_paid = float(amount_paid_raw) if amount_paid_raw != '' else None
        except Exception:
            amount_paid = None
        meta = {}
        if payment_mode:
            meta['paymentMode'] = payment_mode
        if amount_paid is not None:
            meta['amountPaid'] = amount_paid
        if collect_date:
            meta['collectDate'] = collect_date
        otp = get_random_string(length=6, allowed_chars='0123456789')
        set_collection_otp(cus.tenant_id, cus.pk, otp, ttl=OTP_TTL_SECONDS, meta=meta)
        return JsonResponse({'message': 'success', 'expires_in': OTP_TTL_SECONDS}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)
def verify_collection_otp_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not _has_any_permission(
        request.user,
        'home.can_approve_collection',
        'home.can_view_party_credentials',
        'home.add_collection',
    ):
        return JsonResponse({'message': 'error', 'error': 'Permission denied.'}, status=403)
    try:
        party = request.POST.get('party') or ''
        code = (request.POST.get('otp') or '').strip()
        if not (code.isdigit() and len(code) == 6):
            return JsonResponse({'message': 'otp_invalid'}, safe=False)
        party_id = _parse_party_id_value(party)
        if not party_id:
            return JsonResponse({'message': 'error', 'error': 'Party is required.'}, status=400)
        party_obj = tenant_qs(request, Party).filter(isDeleted=False, pk=party_id).only('id', 'tenant_id').first()
        if not party_obj:
            return JsonResponse({'message': 'error', 'error': 'Party not found.'}, status=404)
        payload = get_collection_otp_payload(party_obj.tenant_id, party_id) or {}
        if not payload or str(payload.get('otp')) != code:
            return JsonResponse({'message': 'otp_invalid'}, safe=False)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)
def clear_collection_otp_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not _has_any_permission(
        request.user,
        'home.can_approve_collection',
        'home.can_view_party_credentials',
        'home.add_collection',
    ):
        return JsonResponse({'message': 'error', 'error': 'Permission denied.'}, status=403)
    try:
        party = request.POST.get('party') or ''
        party_id = _parse_party_id_value(party)
        if not party_id:
            return JsonResponse({'message': 'error', 'error': 'Party is required.'}, status=400)
        party_obj = tenant_qs(request, Party).filter(isDeleted=False, pk=party_id).only('id', 'tenant_id').first()
        if not party_obj:
            return JsonResponse({'message': 'error', 'error': 'Party not found.'}, status=404)
        clear_collection_otp(party_obj.tenant_id, party_id)
        clear_collection_status(party_obj.tenant_id, party_id)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)


def request_order_delivery_otp_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = int(request.POST.get('orderID') or 0)
        obj = tenant_qs(request, Order).select_related('partyID').filter(pk=order_id, isDeleted=False).first()
        if not obj:
            return JsonResponse({'message': 'error', 'error': 'Order not found.'}, status=404)
        if not (_can_approve_orders(request.user) or _can_edit_order_record(request.user, obj)):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        if not obj.partyID_id:
            return JsonResponse({'message': 'error', 'error': 'Party is required for delivery OTP.'}, status=400)
        otp = get_random_string(length=6, allowed_chars='0123456789')
        meta = {'orderID': obj.pk, 'status': Order.Status.DELIVERED, 'orderNumber': obj.orderNumber or str(obj.pk)}
        clear_order_delivery_status(obj.partyID.tenant_id, obj.partyID_id)
        set_order_delivery_otp(obj.partyID.tenant_id, obj.partyID_id, otp, ttl=OTP_TTL_SECONDS, meta=meta)
        return JsonResponse({'message': 'success', 'expires_in': OTP_TTL_SECONDS}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)


def verify_order_delivery_otp_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = int(request.POST.get('orderID') or 0)
        otp_code = (request.POST.get('otp') or '').strip()
        if not (otp_code.isdigit() and len(otp_code) == 6):
            return JsonResponse({'message': 'otp_invalid'}, safe=False)
        obj = tenant_qs(request, Order).select_related('partyID').filter(pk=order_id, isDeleted=False).first()
        if not obj:
            return JsonResponse({'message': 'error', 'error': 'Order not found.'}, status=404)
        if not (_can_approve_orders(request.user) or _can_edit_order_record(request.user, obj)):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        if not obj.partyID_id:
            return JsonResponse({'message': 'error', 'error': 'Party is required for delivery OTP.'}, status=400)
        payload = get_order_delivery_otp_payload(obj.partyID.tenant_id, obj.partyID_id) or {}
        if not payload or str(payload.get('otp')) != otp_code:
            return JsonResponse({'message': 'otp_invalid'}, safe=False)
        meta = payload.get('meta') or {}
        if str(meta.get('orderID') or '') not in {'', str(obj.pk)}:
            return JsonResponse({'message': 'otp_invalid'}, safe=False)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)


def clear_order_delivery_otp_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = int(request.POST.get('orderID') or 0)
        obj = tenant_qs(request, Order).select_related('partyID').filter(pk=order_id, isDeleted=False).first()
        if not obj:
            return JsonResponse({'message': 'error', 'error': 'Order not found.'}, status=404)
        if not (_can_approve_orders(request.user) or _can_edit_order_record(request.user, obj)):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        if not obj.partyID_id:
            return JsonResponse({'message': 'error', 'error': 'Party is required for delivery OTP.'}, status=400)
        clear_order_delivery_otp(obj.partyID.tenant_id, obj.partyID_id)
        clear_order_delivery_status(obj.partyID.tenant_id, obj.partyID_id)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)
def formatINR(number):
    s, *d = str(number).partition(".")
    r = ",".join([s[x - 2:x] for x in range(-3, -len(s), -2)][::-1] + [s[-3:]])
    return "".join([r] + d)


def _money(value):
    return f'Rs. {formatINR(format(float(value or 0.0), ".0f"))}'


def _report_date(value, default):
    if not value:
        return default
    try:
        return datetime.strptime(value, '%d/%m/%Y').date()
    except Exception:
        return default


def _today_date():
    try:
        now = timezone.now()
        if timezone.is_aware(now):
            return timezone.localtime(now).date()
        return now.date()
    except Exception:
        return datetime.today().date()


def _report_range(request):
    today = _today_date()
    start_date = _report_date(request.GET.get('startDate'), today)
    end_date = _report_date(request.GET.get('endDate'), today)
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date


def _rows_with_money(rows):
    return [
        {
            'label': row.get('label') or 'Unassigned',
            'amount': _money(row.get('amount') or 0.0),
            'count': row.get('count') or 0,
        }
        for row in rows
    ]


def _date_points(start_date, end_date):
    points = []
    current = start_date
    while current <= end_date:
        points.append(current)
        current += timedelta(days=1)
    return points


def _pct_change(current, previous):
    current = float(current or 0.0)
    previous = float(previous or 0.0)
    if previous == 0:
        if current == 0:
            return 0.0
        return 100.0
    return ((current - previous) / previous) * 100.0


def _group_trend_series(points, series_map, group_by='day'):
    group_by = (group_by or 'day').lower()
    grouped = []
    bucket_index = {}
    for point in points:
        if group_by == 'month':
            key = point.replace(day=1)
            label = point.strftime('%b %Y')
        elif group_by == 'week':
            week_start = point - timedelta(days=point.weekday())
            key = week_start
            week_end = min(week_start + timedelta(days=6), points[-1])
            label = f"{week_start.strftime('%d %b')} - {week_end.strftime('%d %b')}"
        else:
            key = point
            label = point.strftime('%d %b')
        if key not in bucket_index:
            bucket_index[key] = len(grouped)
            grouped.append({
                'label': label,
                'sales': 0.0,
                'collections': 0.0,
                'cash_counter': 0.0,
            })
        bucket = grouped[bucket_index[key]]
        bucket['sales'] += float(series_map['sales'].get(point, 0.0) or 0.0)
        bucket['collections'] += float(series_map['collections'].get(point, 0.0) or 0.0)
        bucket['cash_counter'] += float(series_map['cash_counter'].get(point, 0.0) or 0.0)
    return {
        'labels': [item['label'] for item in grouped],
        'sales': [round(item['sales'], 2) for item in grouped],
        'collections': [round(item['collections'], 2) for item in grouped],
        'cashCounter': [round(item['cash_counter'], 2) for item in grouped],
        'groupBy': group_by,
    }


def _invoice_series_queryset(request_or_user, module):
    return tenant_qs(request_or_user, InvoiceSeries).filter(
        isDeleted=False,
        module=module,
        isActive=True,
    ).order_by('-isDefault', 'seriesCode', 'id')
def _invoice_model_for_module(module):
    return INVOICE_MODULE_MODEL_MAP[module]
def _format_invoice_number(series, sequence_number):
    return f'{series.seriesCode}{str(sequence_number).zfill(series.paddingLength)}'
def _next_invoice_number_for_series(series, *, exclude_instance=None):
    return _next_invoice_numbers_for_series(series, limit=1, exclude_instance=exclude_instance)[0]
def _next_invoice_numbers_for_series(series, limit=150, *, exclude_instance=None):
    return _available_invoice_numbers_for_series(series, limit=limit, exclude_instance=exclude_instance, from_next_only=True)
def _available_invoice_numbers_for_series(series, limit=150, *, exclude_instance=None, from_next_only=False):
    model = _invoice_model_for_module(series.module)
    used_numbers = set(
        model.objects.filter(
            tenant=series.tenant,
            isDeleted=False,
            invoiceSeries=series,
        ).exclude(
            pk=getattr(exclude_instance, 'pk', None)
        ).values_list('invoiceSequenceNumber', flat=True)
    )
    numbers = []
    candidate = max(series.nextNumber, series.startNumber) if from_next_only else series.startNumber
    while candidate <= series.endNumber and len(numbers) < limit:
        if candidate not in used_numbers:
            numbers.append(candidate)
        candidate += 1
    if not numbers:
        raise ValueError('Invoice series exhausted.')
    return numbers
def _reserve_invoice_number(request_or_user, module, series_id, *, exclude_instance=None, preferred_number=None):
    series = _invoice_series_queryset(request_or_user, module).select_for_update().get(pk=int(series_id))
    available_numbers = _available_invoice_numbers_for_series(series, limit=150, exclude_instance=exclude_instance)
    if preferred_number is not None:
        preferred_number = int(preferred_number)
        if preferred_number not in available_numbers:
            raise ValueError('Selected invoice number is no longer available.')
        sequence_number = preferred_number
    else:
        sequence_number = available_numbers[0]
    if series.nextNumber <= sequence_number:
        series.nextNumber = sequence_number + 1
        series.save(update_fields=['nextNumber', 'lastUpdatedOn'])
    return series, sequence_number, _format_invoice_number(series, sequence_number)
def _invoice_series_payload_for_module(request, module):
    payload = []
    for series in _invoice_series_queryset(request, module):
        try:
            next_number = _next_invoice_number_for_series(series)
        except ValueError:
            continue
        payload.append({
            'id': series.pk,
            'seriesCode': series.seriesCode,
            'nextNumber': str(next_number).zfill(series.paddingLength),
            'invoiceNumber': _format_invoice_number(series, next_number),
            'upcomingNumbers': [
                {
                    'sequenceNumber': number,
                    'displayNumber': str(number).zfill(series.paddingLength),
                    'invoiceNumber': _format_invoice_number(series, number),
                }
                for number in _available_invoice_numbers_for_series(series, limit=150)
            ],
            'rangeLabel': f'{str(series.startNumber).zfill(series.paddingLength)} - {str(series.endNumber).zfill(series.paddingLength)}',
            'isDefault': series.isDefault,
            'invoiceNature': getattr(series, 'invoiceNature', InvoiceSeries.InvoiceNature.MIXED),
            'invoiceNatureLabel': getattr(series, 'get_invoiceNature_display', lambda: 'Mixed / Manual')(),
            'lockInvoiceNature': bool(getattr(series, 'lockInvoiceNature', False)),
        })
    return payload
def _invoice_series_payload_obj(series):
    try:
        next_sequence = _next_invoice_number_for_series(series)
        next_invoice = _format_invoice_number(series, next_sequence)
    except ValueError:
        next_sequence = None
        next_invoice = 'Exhausted'
    return {
        'ID': series.pk,
        'Name': series.name or '',
        'Module': series.get_module_display(),
        'ModuleValue': series.module,
        'SeriesCode': series.seriesCode,
        'StartNumber': series.startNumber,
        'EndNumber': series.endNumber,
        'NextNumber': series.nextNumber,
        'PaddingLength': series.paddingLength,
        'NextInvoice': next_invoice,
        'IsActive': series.isActive,
        'IsDefault': series.isDefault,
        'InvoiceNature': getattr(series, 'invoiceNature', InvoiceSeries.InvoiceNature.MIXED),
        'InvoiceNatureLabel': getattr(series, 'get_invoiceNature_display', lambda: 'Mixed / Manual')(),
        'LockInvoiceNature': bool(getattr(series, 'lockInvoiceNature', False)),
        'RangeLabel': f'{str(series.startNumber).zfill(series.paddingLength)} - {str(series.endNumber).zfill(series.paddingLength)}',
    }
def _tenant_sequence_queryset(request_or_user, scope):
    tenant = get_current_tenant(request_or_user)
    return TenantSequence.objects.filter(tenant=tenant, scope=scope)
def _next_tenant_sequence_value(request_or_user, scope):
    defaults = TENANT_SEQUENCE_DEFAULTS[scope]
    with transaction.atomic():
        sequence = _tenant_sequence_queryset(request_or_user, scope).select_for_update().first()
        if sequence is None:
            sequence = TenantSequence(
                tenant=get_current_tenant(request_or_user),
                scope=scope,
                prefix=defaults['prefix'],
                paddingLength=defaults['padding'],
                nextNumber=1,
            )
            sequence.save()
        number = sequence.nextNumber
        sequence.nextNumber = number + 1
        sequence.save(update_fields=['nextNumber', 'lastUpdatedOn'])
    return f"{sequence.prefix}{str(number).zfill(sequence.paddingLength)}"
def _sales_public_id(obj):
    return getattr(obj, 'salesID', None) or getattr(obj, 'paymentID', None) or f'SAL{str(getattr(obj, "pk", 0)).zfill(6)}'
def _proforma_public_id(obj):
    return getattr(obj, 'proformaID', None) or getattr(obj, 'invoiceNumber', None) or f'PI{str(getattr(obj, "pk", 0)).zfill(6)}'
def _purchase_public_id(obj):
    return getattr(obj, 'purchaseID', None) or f'PUR{str(getattr(obj, "pk", 0)).zfill(6)}'
def _order_public_id(obj):
    return getattr(obj, 'orderNumber', None) or f'ORD{str(getattr(obj, "pk", 0)).zfill(6)}'
def _cash_counter_public_id(obj):
    return getattr(obj, 'counterID', None) or f'CC{str(getattr(obj, "pk", 0)).zfill(6)}'
def _normalize_bool(raw):
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}
def _validate_invoice_series_form(request, *, instance=None):
    name = (request.POST.get('name') or '').strip()
    module = (request.POST.get('module') or '').strip()
    series_code = (request.POST.get('seriesCode') or '').strip().upper()
    start_number = int(request.POST.get('startNumber') or 0)
    end_number = int(request.POST.get('endNumber') or 0)
    padding_length = int(request.POST.get('paddingLength') or 0)
    is_active = _normalize_bool(request.POST.get('isActive'))
    is_default = _normalize_bool(request.POST.get('isDefault'))
    invoice_nature = (request.POST.get('invoiceNature') or InvoiceSeries.InvoiceNature.MIXED).strip().lower()
    lock_invoice_nature = _normalize_bool(request.POST.get('lockInvoiceNature'))

    if module not in INVOICE_MODULE_MODEL_MAP:
        raise ValueError('Please select a valid module.')
    if not series_code:
        raise ValueError('Series code is required.')
    if start_number <= 0 or end_number <= 0:
        raise ValueError('Start and end numbers must be positive.')
    if start_number > end_number:
        raise ValueError('Start number cannot be greater than end number.')
    if padding_length < len(str(end_number)):
        raise ValueError('Padding length must fit the end number.')
    valid_natures = {choice[0] for choice in InvoiceSeries.InvoiceNature.choices}
    if invoice_nature not in valid_natures:
        invoice_nature = InvoiceSeries.InvoiceNature.MIXED

    qs = tenant_qs(request, InvoiceSeries).filter(
        isDeleted=False,
        module=module,
        seriesCode=series_code,
    )
    if instance is not None:
        qs = qs.exclude(pk=instance.pk)
    if qs.exists():
        raise ValueError('Series code already exists for this module.')

    next_number = start_number if instance is None else instance.nextNumber
    if next_number < start_number:
        next_number = start_number
    if next_number > end_number + 1:
        raise ValueError('Next number is outside the configured range.')
    return {
        'name': name,
        'module': module,
        'seriesCode': series_code,
        'startNumber': start_number,
        'endNumber': end_number,
        'nextNumber': next_number,
        'paddingLength': padding_length,
        'isActive': is_active,
        'isDefault': is_default,
        'invoiceNature': invoice_nature,
        'lockInvoiceNature': lock_invoice_nature,
    }
def _apply_invoice_series_default(series):
    if series.isDefault:
        InvoiceSeries.objects.filter(
            isDeleted=False,
            tenant=series.tenant,
            module=series.module,
            isDefault=True,
        ).exclude(pk=series.pk).update(isDefault=False)
def invoice_series_suggestions_api(request):
    if request.method != 'GET':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    module = (request.GET.get('module') or '').strip()
    if module not in INVOICE_MODULE_MODEL_MAP:
        return JsonResponse({'message': 'error', 'error': 'Invalid module.'}, status=400)
    return JsonResponse({'message': 'success', 'data': _invoice_series_payload_for_module(request, module)}, safe=False)
@transaction.atomic
def add_invoice_series_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _can_add_invoice_series(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        payload = _validate_invoice_series_form(request)
        series = InvoiceSeries()
        assign_tenant(series, request)
        for key, value in payload.items():
            setattr(series, key, value)
        series.save()
        _apply_invoice_series_default(series)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
@transaction.atomic
def edit_invoice_series_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _can_edit_invoice_series(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        edit_id = request.POST.get('EditId')
        series = tenant_qs(request, InvoiceSeries).get(pk=int(edit_id), isDeleted=False)
        payload = _validate_invoice_series_form(request, instance=series)
        for key, value in payload.items():
            setattr(series, key, value)
        series.save()
        _apply_invoice_series_default(series)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
@transaction.atomic
def delete_invoice_series_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _can_delete_invoice_series(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        series_id = request.POST.get('userID')
        series = tenant_qs(request, InvoiceSeries).get(pk=int(series_id), isDeleted=False)
        series.isDeleted = True
        series.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
def _decimal_from_request(value, default='0'):
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        return Decimal(default)
def _tenant_admin_group_name(tenant):
    tenant_slug = getattr(tenant, 'slug', 'tenant')
    return f'{tenant_slug}:Tenant Admin'


LOCATION_ASSIGNABLE_PERMISSION_CODENAMES = {
    'can_track_staff_location',
    'can_view_staff_locations',
}


def _assignable_permissions(request_or_user=None):
    user = getattr(request_or_user, 'user', request_or_user)
    queryset = Permission.objects.select_related('content_type').exclude(
        content_type__app_label__in=['admin', 'auth', 'contenttypes', 'sessions']
    ).order_by('content_type__app_label', 'content_type__model', 'name')
    tenant = get_current_tenant(request_or_user)
    if tenant is not None:
        tenant_admin_group = Group.objects.filter(name=_tenant_admin_group_name(tenant)).first()
        if tenant_admin_group is not None:
            allowed_ids = set(tenant_admin_group.permissions.values_list('id', flat=True))
            if getattr(user, 'is_authenticated', False) and user.has_perm('home.can_manage_staff_groups'):
                allowed_ids.update(
                    Permission.objects.filter(
                        content_type__app_label='home',
                        codename__in=LOCATION_ASSIGNABLE_PERMISSION_CODENAMES,
                    ).values_list('id', flat=True)
                )
            return [permission for permission in queryset if permission.id in allowed_ids]
        return []
    if not getattr(user, 'is_authenticated', False):
        return []
    allowed = set(user.get_all_permissions())
    return [
        permission for permission in queryset
        if f'{permission.content_type.app_label}.{permission.codename}' in allowed
    ]
def _permission_catalog(request_or_user=None):
    permission_groups = defaultdict(list)
    for permission in _assignable_permissions(request_or_user):
        module = permission.content_type.model.replace('_', ' ').title()
        if permission.codename.startswith('can_'):
            module = 'Custom ' + module
        permission_groups[module].append(permission)
    return dict(permission_groups)
def _has_any_permission(user, *perm_names):
    if len(perm_names) == 1 and isinstance(perm_names[0], (list, tuple, set)):
        perm_names = tuple(perm_names[0])
    return any(user.has_perm(perm_name) for perm_name in perm_names)


def _request_has_any_permission(request, *perm_names):
    if len(perm_names) == 1 and isinstance(perm_names[0], (list, tuple, set)):
        perm_names = tuple(perm_names[0])
    effective_permissions = _effective_permission_names(request)
    return any(perm_name in effective_permissions for perm_name in perm_names)
def _is_admin_like(user):
    return _has_any_permission(user, ADMIN_LIKE_PERMS)
def _can_manage_staff(user):
    return user.has_perm('home.can_manage_staff')
def _can_manage_party_records(user):
    return _has_any_permission(user, 'home.can_manage_parties', 'home.can_view_party_credentials')
def _can_view_party_credentials(user):
    return user.has_perm('home.can_view_party_credentials')


def _request_can_manage_inventory_records(request):
    return _request_has_any_permission(
        request,
        'home.can_manage_inventory',
        'home.add_inventoryitem',
        'home.change_inventoryitem',
        'home.can_view_party_credentials',
    )


def _request_can_manage_purchase_records(request):
    return _request_has_any_permission(
        request,
        'home.add_purchaseinvoice',
        'home.change_purchaseinvoice',
        'home.delete_purchaseinvoice',
        'home.can_view_party_credentials',
    )


def _request_can_manage_non_gst_shadow(request):
    return _request_has_any_permission(
        request,
        'home.can_view_party_credentials',
        'home.can_manage_staff',
    )


def _non_gst_shadow_enabled(request_or_user) -> bool:
    tenant = get_current_tenant(request_or_user)
    if tenant is None:
        return True
    return bool(getattr(tenant, 'nonGstShadowEnabled', False))


def _inventory_feature_enabled(request_or_user) -> bool:
    tenant = get_current_tenant(request_or_user)
    if tenant is None:
        return True
    return bool(getattr(tenant, 'inventoryEnabled', True))


def _advanced_invoicing_enabled(request_or_user) -> bool:
    tenant = get_current_tenant(request_or_user)
    if tenant is None:
        return True
    return bool(getattr(tenant, 'advancedInvoicingEnabled', True))


def _assert_inventory_enabled(request, *, label='Inventory feature'):
    if _inventory_feature_enabled(request):
        return
    raise ValueError(f'{label} is disabled for this tenant. Ask an admin to enable Inventory from Billing > Non-GST Control.')


def _assert_advanced_invoicing_enabled(request, *, label='Advanced invoicing'):
    if _advanced_invoicing_enabled(request):
        return
    raise ValueError(f'{label} is disabled for this tenant. Ask an admin to enable Invoicing from Billing > Non-GST Control.')


def _assert_non_gst_enabled(request, *, label='Non-GST mode'):
    if _non_gst_shadow_enabled(request):
        return
    raise ValueError(f'{label} is disabled for this tenant. Ask an admin to enable it from Billing > Non-GST Control.')


def _normalize_purchase_gst_treatment(request, raw_treatment: str | None) -> str:
    treatment = str(raw_treatment or PurchaseInvoice.GstTreatment.GST).strip()
    valid = {choice[0] for choice in PurchaseInvoice.GstTreatment.choices}
    if treatment not in valid:
        return PurchaseInvoice.GstTreatment.GST
    if treatment in {PurchaseInvoice.GstTreatment.NON_GST, PurchaseInvoice.GstTreatment.NON_GST_CLAIMABLE}:
        _assert_non_gst_enabled(request, label='Non-GST purchase flow')
    return treatment


def _normalize_purchase_claim_status(treatment: str, raw_claim_status: str | None) -> str:
    status = str(raw_claim_status or PurchaseInvoice.ClaimStatus.NOT_APPLICABLE).strip()
    valid = {choice[0] for choice in PurchaseInvoice.ClaimStatus.choices}
    if status not in valid:
        status = PurchaseInvoice.ClaimStatus.NOT_APPLICABLE
    if treatment == PurchaseInvoice.GstTreatment.GST:
        return PurchaseInvoice.ClaimStatus.NOT_APPLICABLE
    return status
def _can_add_party(user):
    return user.has_perm('home.add_party') or _can_view_party_credentials(user)
def _can_edit_party(user):
    return user.has_perm('home.change_party') or _can_view_party_credentials(user)
def _can_delete_party(user):
    return user.has_perm('home.delete_party') or _can_view_party_credentials(user)
def _tenant_audit_log_queryset(request):
    tenant = get_current_tenant(request)
    if tenant is None:
        return AuditLog.objects.none()
    tenant_content_type = ContentType.objects.get_for_model(Tenant)
    return AuditLog.objects.select_related('content_type', 'actor').filter(
        Q(snapshot__tenant=tenant.pk) |
        Q(content_type=tenant_content_type, object_id=tenant.pk)
    )
def _apply_audit_log_filters(request, qs):
    search = (request.GET.get('search[value]') or '').strip()
    keyword = (request.GET.get('keyword') or '').strip()
    action = (request.GET.get('action') or '').strip()
    actor_id = (request.GET.get('actor') or '').strip()
    content_type_id = (request.GET.get('content_type') or '').strip()
    date_from = parse_date((request.GET.get('date_from') or '').strip())
    date_to = parse_date((request.GET.get('date_to') or '').strip())

    if action:
        qs = qs.filter(action=action)
    if actor_id.isdigit():
        qs = qs.filter(actor_id=int(actor_id))
    if content_type_id.isdigit():
        qs = qs.filter(content_type_id=int(content_type_id))
    if date_from:
        qs = qs.filter(datetime__date__gte=date_from)
    if date_to:
        qs = qs.filter(datetime__date__lte=date_to)
    if search:
        qs = qs.filter(
            Q(object_repr__icontains=search) |
            Q(action__icontains=search) |
            Q(actor__username__icontains=search) |
            Q(content_type__model__icontains=search) |
            Q(changes__icontains=search) |
            Q(snapshot__icontains=search)
        )
    if keyword:
        qs = qs.filter(
            Q(object_repr__icontains=keyword) |
            Q(action__icontains=keyword) |
            Q(actor__username__icontains=keyword) |
            Q(content_type__model__icontains=keyword) |
            Q(changes__icontains=keyword) |
            Q(snapshot__icontains=keyword)
        )
    return qs
def _audit_log_detail_payload(log):
    dt_display = _format_audit_datetime(log.datetime)
    snapshot = log.snapshot or {}
    changes = log.changes or {}
    return {
        'id': log.pk,
        'datetime': dt_display,
        'action': log.get_action_display(),
        'actor': getattr(log.actor, 'username', 'System'),
        'model': log.content_type.model.replace('_', ' ').title(),
        'object_id': log.object_id,
        'object_repr': log.object_repr,
        'changes': changes,
        'changes_pretty': json.dumps(changes, indent=2, ensure_ascii=True),
        'snapshot': snapshot,
        'snapshot_pretty': json.dumps(snapshot, indent=2, ensure_ascii=True),
    }
def _format_audit_datetime(value):
    if timezone.is_naive(value):
        return value.strftime('%d-%m-%Y %I:%M %p')
    return timezone.localtime(value).strftime('%d-%m-%Y %I:%M %p')
def _can_add_party_group(user):
    return user.has_perm('home.add_partygroup') or _can_view_party_credentials(user)
def _can_edit_party_group(user):
    return user.has_perm('home.change_partygroup') or _can_view_party_credentials(user)
def _can_delete_party_group(user):
    return user.has_perm('home.delete_partygroup') or _can_view_party_credentials(user)
def _can_add_bank(user):
    return user.has_perm('home.add_bank') or _can_view_party_credentials(user)
def _can_edit_bank(user):
    return user.has_perm('home.change_bank') or _can_view_party_credentials(user)
def _can_delete_bank(user):
    return user.has_perm('home.delete_bank') or _can_view_party_credentials(user)
def _can_add_invoice_series(user):
    return user.has_perm('home.add_invoiceseries')
def _can_edit_invoice_series(user):
    return user.has_perm('home.change_invoiceseries')
def _can_delete_invoice_series(user):
    return user.has_perm('home.delete_invoiceseries')
def _can_review_collections(user):
    return user.has_perm('home.can_approve_collection')
def _can_auto_approve_collections(user):
    return user.has_perm('home.can_approve_collection')
def _can_manage_sales(user):
    return user.has_perm('home.can_manage_sales')
def _can_approve_sales(user):
    return user.has_perm('home.can_approve_sales')
def _has_sales_access(user):
    return _has_any_permission(user, 'home.can_manage_sales', 'home.can_approve_sales', 'home.view_sales', 'home.add_sales', 'home.change_sales')
def _can_manage_proforma_invoice(user):
    return user.has_perm('home.can_manage_proforma_invoice')
def _can_convert_proforma_invoice(user):
    return user.has_perm('home.can_convert_proforma_invoice')
def _has_proforma_access(user):
    return _has_any_permission(
        user,
        'home.can_manage_proforma_invoice',
        'home.can_convert_proforma_invoice',
        'home.can_view_proforma_invoice_reports',
        'home.view_proformainvoice',
        'home.add_proformainvoice',
        'home.change_proformainvoice',
    )
def _can_edit_proforma_invoice(user):
    return _has_any_permission(user, 'home.change_proformainvoice', 'home.can_manage_proforma_invoice')
def _can_filter_all_proforma(user):
    return _can_manage_proforma_invoice(user) or user.has_perm('home.can_view_proforma_invoice_reports') or _can_convert_proforma_invoice(user)
def _can_edit_proforma_record(user, proforma_obj):
    if not user.is_authenticated:
        return False
    if not _can_edit_proforma_invoice(user):
        return False
    if proforma_obj is None or proforma_obj.status == ProformaInvoice.Status.CONVERTED:
        return False
    if _can_manage_proforma_invoice(user):
        return True
    staff = tenant_qs(user, StaffUser).filter(user_ID_id=user.pk, isDeleted=False).only('id').first()
    if not staff:
        return False
    return proforma_obj.createdBy_id == staff.pk
def _has_return_source_access(user):
    return _has_sales_access(user) or _has_any_permission(user, 'home.can_manage_cash_counter', 'home.view_cashcounter', 'home.can_view_party_credentials')
def _can_manage_cash_counter_records(user):
    return user.has_perm('home.can_view_party_credentials')
def _can_filter_all_collections(user):
    return _can_review_collections(user)
def _can_filter_all_cash_counter(user):
    return _has_any_permission(
        user,
        'home.can_manage_cash_counter',
        'home.can_view_party_credentials',
        'home.can_approve_collection',
    )
def _can_filter_all_sales(user):
    return _is_admin_like(user) or _can_manage_sales(user) or _can_approve_sales(user)
def _can_edit_sales(user):
    return user.has_perm('home.change_sales') or _can_manage_sales(user) or _can_approve_sales(user) or user.has_perm('home.can_view_party_credentials')
def _can_delete_sales(user):
    return user.has_perm('home.can_view_party_credentials')
def _can_manage_inventory_records(user):
    return _has_any_permission(user, 'home.can_manage_inventory', 'home.add_inventoryitem', 'home.change_inventoryitem', 'home.can_view_party_credentials')
def _can_manage_purchase_records(user):
    return _has_any_permission(user, 'home.add_purchaseinvoice', 'home.change_purchaseinvoice', 'home.delete_purchaseinvoice', 'home.can_view_party_credentials')
def _can_manage_orders(user):
    return _has_any_permission(user, 'home.can_manage_orders', 'home.can_approve_orders')
def _can_filter_all_orders(user):
    return _is_admin_like(user) or _can_manage_orders(user) or user.has_perm('home.can_view_party_credentials')
def _can_add_order(user):
    return user.has_perm('home.add_order')
def _can_edit_order(user):
    return user.has_perm('home.change_order') or _can_manage_orders(user) or user.has_perm('home.can_view_party_credentials')
def _can_delete_order(user):
    return user.has_perm('home.delete_order') or user.has_perm('home.can_view_party_credentials')
def _can_approve_orders(user):
    return user.has_perm('home.can_approve_orders')
def _can_set_order_date(user):
    return user.has_perm('home.can_manage_orders') or user.has_perm('home.can_approve_orders') or user.has_perm('home.can_view_party_credentials')
def _credential_share_payload(request, raw_token, token_obj):
    claim_url = request.build_absolute_uri(reverse('homeApp:claim_access', args=[raw_token]))
    subject_name = token_obj.staffUser.name if token_obj.staffUser_id else (token_obj.party.name if token_obj.party_id else token_obj.user.username)
    share_text = (
        f"Username: {token_obj.user.username}\n"
        f"One-time access link: {claim_url}\n"
        f"This link expires in {DEFAULT_CREDENTIAL_LINK_MINUTES} minutes and can be used only once."
    )
    return {
        'message': 'success',
        'claimUrl': claim_url,
        'shareText': share_text,
        'username': token_obj.user.username,
        'expiresAt': token_obj.expiresAt.strftime('%d-%m-%Y %I:%M %p'),
        'subjectName': subject_name,
        'subjectType': token_obj.get_subjectType_display(),
    }
def _format_order_number(pk):
    return 'O' + str(int(pk)).zfill(8)
def _order_status_text(status):
    status = str(status or '')
    status_map = {
        Order.Status.DRAFT: 'Draft',
        Order.Status.SUBMITTED: 'Submitted',
        Order.Status.APPROVED: 'Confirmed',
        Order.Status.PACKED: 'Packed',
        Order.Status.OUT_FOR_DELIVERY: 'Out for Delivery',
        Order.Status.DELIVERED: 'Delivered',
        Order.Status.CANCELLED: 'Cancelled',
        Order.Status.RETURNED: 'Returned',
        Order.Status.FULFILLED: 'Delivered',
    }
    return status_map.get(status, status.replace('_', ' ').title() or 'Unknown')
def _order_status_label(status):
    status = str(status or '')
    if status == Order.Status.APPROVED:
        return '<div class="ui tiny teal label">Confirmed</div>'
    if status == Order.Status.SUBMITTED:
        return '<div class="ui tiny orange label">Submitted</div>'
    if status == Order.Status.DRAFT:
        return '<div class="ui tiny grey label">Draft</div>'
    if status == Order.Status.PACKED:
        return '<div class="ui tiny violet label">Packed</div>'
    if status == Order.Status.OUT_FOR_DELIVERY:
        return '<div class="ui tiny blue label">Out for Delivery</div>'
    if status == Order.Status.DELIVERED:
        return '<div class="ui tiny green label">Delivered</div>'
    if status == Order.Status.CANCELLED:
        return '<div class="ui tiny red label">Cancelled</div>'
    if status == Order.Status.RETURNED:
        return '<div class="ui tiny pink label">Returned</div>'
    if status == Order.Status.FULFILLED:
        return '<div class="ui tiny green label">Delivered</div>'
    return '<div class="ui tiny label">{}</div>'.format(escape(_order_status_text(status)))
def _order_status_transition_map():
    return {
        Order.Status.DRAFT: [Order.Status.SUBMITTED, Order.Status.CANCELLED],
        Order.Status.SUBMITTED: [Order.Status.APPROVED, Order.Status.CANCELLED],
        Order.Status.APPROVED: [Order.Status.PACKED, Order.Status.CANCELLED],
        Order.Status.PACKED: [Order.Status.OUT_FOR_DELIVERY, Order.Status.CANCELLED],
        Order.Status.OUT_FOR_DELIVERY: [Order.Status.DELIVERED, Order.Status.RETURNED],
        Order.Status.DELIVERED: [Order.Status.RETURNED],
        Order.Status.CANCELLED: [],
        Order.Status.RETURNED: [],
        Order.Status.FULFILLED: [Order.Status.RETURNED],
    }
def _normalize_order_status(status):
    if str(status) == Order.Status.FULFILLED:
        return Order.Status.DELIVERED
    return str(status or '')
def _set_order_tracking_timestamps(order_obj, new_status, actor_staff=None):
    now_value = timezone.now()
    new_status = _normalize_order_status(new_status)
    order_obj.status = new_status
    if new_status == Order.Status.APPROVED:
        order_obj.approvedOn = order_obj.approvedOn or now_value
        if actor_staff and not order_obj.approvedBy_id:
            order_obj.approvedBy = actor_staff
    elif new_status == Order.Status.PACKED:
        order_obj.packedOn = order_obj.packedOn or now_value
    elif new_status == Order.Status.OUT_FOR_DELIVERY:
        order_obj.outForDeliveryOn = order_obj.outForDeliveryOn or now_value
    elif new_status == Order.Status.DELIVERED:
        order_obj.deliveredOn = order_obj.deliveredOn or now_value
    elif new_status == Order.Status.CANCELLED:
        order_obj.cancelledOn = order_obj.cancelledOn or now_value
    elif new_status == Order.Status.RETURNED:
        order_obj.returnedOn = order_obj.returnedOn or now_value
def _create_order_status_event(request, order_obj, from_status, to_status, note=''):
    event = OrderStatusEvent(order=order_obj)
    assign_tenant(event, request)
    event.fromStatus = _normalize_order_status(from_status) or None
    event.toStatus = _normalize_order_status(to_status)
    event.note = note or ''
    staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk, isDeleted=False).only('id').first()
    if staff:
        event.actor_id = staff.pk
    event.save()
def _order_allowed_next_statuses(order_obj):
    current = _normalize_order_status(getattr(order_obj, 'status', None))
    return _order_status_transition_map().get(current, [])
def _link_sale_to_order(request, order_obj, sale_obj, *, note=''):
    if not order_obj or not sale_obj:
        return
    previous_sale_id = order_obj.linkedSales_id
    order_obj.linkedSales = sale_obj
    update_fields = ['linkedSales', 'lastUpdatedOn']
    if _normalize_order_status(order_obj.status) in {Order.Status.DRAFT, Order.Status.SUBMITTED}:
        actor_staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk, isDeleted=False).only('id').first()
        previous_status = _normalize_order_status(order_obj.status)
        _set_order_tracking_timestamps(order_obj, Order.Status.APPROVED, actor_staff)
        update_fields.extend(['status', 'approvedOn', 'approvedBy'])
        order_obj.save(update_fields=update_fields)
        _create_order_status_event(request, order_obj, previous_status, order_obj.status, note or f'Sale {sale_obj.invoiceNumber or sale_obj.salesID or sale_obj.pk} linked')
    else:
        order_obj.save(update_fields=update_fields)
        if previous_sale_id != sale_obj.pk:
            _create_order_status_event(request, order_obj, order_obj.status, order_obj.status, note or f'Sale {sale_obj.invoiceNumber or sale_obj.salesID or sale_obj.pk} linked')
def _collection_verified_label(is_verified):
    if is_verified:
        return '<div class="ui tiny green label">Yes</div>'
    return '<div class="ui tiny grey label">No</div>'
def _can_edit_order_record(user, order_obj):
    if not user.is_authenticated or order_obj is None:
        return False
    if not _can_edit_order(user):
        return False
    admin_like = _can_filter_all_orders(user)
    if str(order_obj.status) not in {Order.Status.DRAFT, Order.Status.SUBMITTED} and not admin_like:
        return False
    if admin_like:
        return True
    staff = tenant_qs(user, StaffUser).filter(user_ID_id=user.pk, isDeleted=False).only('id').first()
    if not staff:
        return False
    return order_obj.createdBy_id == staff.pk
def _can_delete_order_record(user, order_obj):
    if not user.is_authenticated or order_obj is None:
        return False
    if not _can_delete_order(user):
        return False
    admin_like = _can_filter_all_orders(user)
    if str(order_obj.status) not in {Order.Status.DRAFT, Order.Status.SUBMITTED} and not admin_like:
        return False
    if admin_like:
        return True
    staff = tenant_qs(user, StaffUser).filter(user_ID_id=user.pk, isDeleted=False).only('id').first()
    if not staff:
        return False
    return order_obj.createdBy_id == staff.pk
def _can_edit_sales_record(user, sale_obj):
    """
    Staff can edit only their own Sales records and only before approval.
    Admin-like users (manage/approve sales or view party credentials) can edit anytime.
    """
    if not user.is_authenticated:
        return False
    if not _can_edit_sales(user):
        return False
    if sale_obj is None:
        return False
    admin_like = _can_manage_sales(user) or _can_approve_sales(user) or user.has_perm('home.can_view_party_credentials')
    if sale_obj.isApproved and not admin_like:
        return False
    if admin_like:
        return True
    # MyList scope: createdBy must be this staff user.
    staff = tenant_qs(user, StaffUser).filter(user_ID_id=user.pk, isDeleted=False).only('id').first()
    if not staff:
        return False
    return sale_obj.createdBy_id == staff.pk
def _tenant_group_name(request_or_tenant, base_name):
    tenant = get_current_tenant(request_or_tenant) if not hasattr(request_or_tenant, 'slug') else request_or_tenant
    tenant_slug = getattr(tenant, 'slug', 'tenant')
    return f'{tenant_slug}:{base_name}'
def _get_or_create_tenant_django_group(request_or_tenant, base_name, existing_group=None):
    django_group_name = _tenant_group_name(request_or_tenant, base_name)
    if existing_group is not None:
        existing_group.name = django_group_name
        existing_group.save(update_fields=['name'])
        return existing_group
    django_group, _ = Group.objects.get_or_create(name=django_group_name)
    return django_group
def _sync_staff_group_permissions(staff_group, permission_ids):
    django_group = staff_group.djangoGroup
    if django_group is None:
        django_group, _ = Group.objects.get_or_create(name=staff_group.name)
        staff_group.djangoGroup = django_group
        staff_group.save(update_fields=['djangoGroup', 'lastUpdatedOn'])
    permissions = Permission.objects.filter(id__in=permission_ids)
    django_group.permissions.set(permissions)
    return django_group
def _sync_staff_user_group_membership(staff_user):
    if not staff_user.user_ID_id:
        return
    user = staff_user.user_ID
    tenant_id = getattr(staff_user, 'tenant_id', None)
    if tenant_id:
        tenant_group_ids = list(
            StaffGroup.objects.filter(
                tenant_id=tenant_id,
                djangoGroup__isnull=False,
            ).values_list('djangoGroup_id', flat=True)
        )
        if tenant_group_ids:
            user.groups.remove(*Group.objects.filter(id__in=tenant_group_ids))

    if staff_user.staffGroup and staff_user.staffGroup.djangoGroup_id:
        user.groups.add(staff_user.staffGroup.djangoGroup)
def seed_default_groups():
    all_permissions = Permission.objects.exclude(content_type__app_label__in=['admin', 'contenttypes', 'sessions'])
    for group_name, permission_codenames in DEFAULT_GROUP_PERMISSION_MAP.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        if permission_codenames == '__all__':
            group.permissions.set(all_permissions)
        else:
            perms = []
            for perm_name in permission_codenames:
                app_label, codename = perm_name.split('.', 1)
                perm = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
                if perm:
                    perms.append(perm)
            group.permissions.set(perms)
def _image_url(image_field, variation=None):
    if not image_field:
        return DEFAULT_AVATAR_URL
    if variation:
        candidate = getattr(image_field, variation, None)
        if candidate and getattr(candidate, 'url', None):
            return candidate.url
    return getattr(image_field, 'url', '') or DEFAULT_AVATAR_URL
def _collection_list_base_qs(request_or_user):
    return tenant_qs(request_or_user, Collection).select_related(
        'partyID', 'transferredPartyID', 'bankID', 'collectedBy', 'approvedBy'
    ).only(
        'id', 'paymentID', 'paidAmount', 'modeOfPayment', 'detail', 'remark', 'collectionAddress',
        'collectionDateTime', 'datetime', 'isApproved', 'isTallied', 'chequeDate', 'otpVerified', 'otpVerifiedOn',
        'partyID__name', 'transferredPartyID__name', 'bankID__name',
        'collectedBy__name', 'approvedBy__name'
    )
def _sales_list_base_qs(request_or_user):
    return tenant_qs(request_or_user, Sales).select_related('partyID', 'createdBy', 'approvedBy').only(
        'id', 'paymentID', 'invoiceNumber', 'amount', 'buildDate', 'datetime', 'remark', 'isApproved',
        'partyID__name', 'createdBy__name', 'approvedBy__name'
    )
def _orders_list_base_qs(request_or_user):
    return tenant_qs(request_or_user, Order).select_related('partyID', 'createdBy', 'approvedBy').only(
        'id', 'orderNumber', 'buildDate', 'datetime', 'lastUpdatedOn', 'remark', 'approvedOn',
        'status', 'latitude', 'longitude', 'location',
        'partyID__name', 'createdBy__name', 'approvedBy__name'
    )
def _attendance_list_base_qs(request_or_user):
    return tenant_qs(request_or_user, Attendance).select_related('staffID').only(
        'id', 'datetime', 'isLogIn', 'isLogOut', 'loginDateTime', 'logoutDateTime',
        'login_remark', 'logout_remark', 'login_location', 'logout_location', 'loginPhoto', 'logoutPhoto', 'staffID__name'
    )
def _cash_counter_list_base_qs(request_or_user):
    return tenant_qs(request_or_user, CashCounter).select_related('partyID', 'bankID', 'createdBy').only(
        'id', 'counterID', 'invoiceNumber', 'amount', 'mixCashAmount', 'mixCardAmount', 'mode',
        'expenseType', 'remark', 'datetime', 'partyID__name', 'bankID__name',
        'bankID__accountNumber', 'createdBy__name'
    )
def _cash_counter_collection_list_base_qs(request_or_user):
    return tenant_qs(request_or_user, CollectionCashCounter).select_related(
        'partyID', 'transferredPartyID', 'bankID', 'collectedBy', 'approvedBy'
    ).only(
        'id', 'paymentID', 'paidAmount', 'modeOfPayment', 'detail', 'remark', 'collectionAddress',
        'collectionDateTime', 'datetime', 'isApproved', 'chequeDate',
        'partyID__name', 'transferredPartyID__name', 'bankID__name', 'collectedBy__name', 'approvedBy__name'
    )
def _get_party_cache_version(request_or_user):
    version_key = party_list_version_cache_key(request_or_user)
    version = cache.get(version_key)
    if version is None:
        version = 1
        cache.set(version_key, version, timeout=None)
    return version
def _bump_party_cache_version(request_or_user):
    version_key = party_list_version_cache_key(request_or_user)
    version = cache.get(version_key) or 1
    cache.set(version_key, version + 1, timeout=None)
def _get_inventory_list_cache_version(resource, request_or_user=None, *, tenant=None):
    version_key = inventory_list_version_cache_key(resource, request_or_user=request_or_user, tenant=tenant)
    version = cache.get(version_key)
    if version is None:
        version = 1
        cache.set(version_key, version, timeout=None)
    return version
def _bump_inventory_list_cache_version(resource, request_or_user=None, *, tenant=None):
    version_key = inventory_list_version_cache_key(resource, request_or_user=request_or_user, tenant=tenant)
    version = cache.get(version_key) or 1
    cache.set(version_key, version + 1, timeout=None)
def _inventory_list_cache_key(resource, *parts, request_or_user=None, tenant=None):
    version = _get_inventory_list_cache_version(resource, request_or_user=request_or_user, tenant=tenant)
    return inventory_list_cache_key(resource, f'v{version}', *parts, request_or_user=request_or_user, tenant=tenant)
def deduct_location_balance(lat, lng):
    return _reverse_geocode_openstreetmap(lat, lng)
def send_whatsapp_message(number, message):
    msg = tenant_qs(get_current_tenant(None), WhatsappMessage).filter(isDeleted__exact=False).last()
    if msg and msg.used < msg.balance:
        # r = requests.get(
        #     msg.name + "send?number=91" + number + "&type=text&message=" + message + "&instance_id=" + msg.instanceID + "&access_token=" + msg.apiKey,
        #     verify=False)
        # data = r.json()
        msg.used = (msg.used + 1)
        msg.save()
@transaction.atomic
def add_staff_group_api(request):
    if request.method == 'POST':
        try:
            name = request.POST.get("groupName")
            description = request.POST.get("description")
            permission_ids = request.POST.getlist("permission_ids[]") or request.POST.getlist("permission_ids")
            if not name:
                return JsonResponse({'message': 'error', 'error': 'Group name is required.'}, safe=False)
            allowed_permission_ids = {str(permission.id) for permission in _assignable_permissions(request)}
            invalid_permission_ids = [permission_id for permission_id in permission_ids if permission_id not in allowed_permission_ids]
            if invalid_permission_ids:
                return JsonResponse({'message': 'error', 'error': 'You can only assign permissions allowed for your tenant admin access.'}, safe=False)
            obj = StaffGroup()
            assign_tenant(obj, request)
            obj.name = name
            obj.description = description
            obj.save()
            django_group = _get_or_create_tenant_django_group(request, name)
            obj.djangoGroup = django_group
            obj.save(update_fields=['djangoGroup'])
            _sync_staff_group_permissions(obj, permission_ids)
            return JsonResponse({'message': 'success', 'group_id': obj.pk}, safe=False)
        except Exception as exc:
            transaction.set_rollback(True)
            return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
class StaffGroupListJson(BaseDatatableView):
    order_columns = ['name', 'description', 'name', 'datetime', 'name']
    def get_initial_queryset(self):
        qs = tenant_qs(self.request, StaffGroup).filter(isDeleted__exact=False)
        start_date = self.request.GET.get('startDate')
        end_date = self.request.GET.get('endDate')
        if start_date and end_date:
            try:
                s_date = datetime.strptime(start_date, '%Y-%m-%d').date()
                e_date = datetime.strptime(end_date, '%Y-%m-%d').date()
                qs = qs.filter(datetime__date__range=[s_date, e_date])
            except ValueError:
                pass
        return qs.only('id', 'name', 'description', 'datetime')
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            perm_count = item.djangoGroup.permissions.count() if item.djangoGroup_id else 0
            detail_url = reverse('homeApp:staff_group_detail', args=[item.pk])
            edit_url = reverse('homeApp:staff_group_edit', args=[item.pk])
            action = '''<a data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" href="{}" class="ui circular teal icon button">
                    <i class="eye icon"></i>
                  </a>
                  <a data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" href="{}" class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </a>
                  <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button">
                    <i class="trash alternate icon"></i>
                  </button>'''.format(detail_url, edit_url, item.pk)
            json_data.append([
                escape(item.name),
                escape(item.description or '-'),
                perm_count,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,
            ])
        return json_data
def get_staff_group_detail(request):
    group_id = request.GET.get('id')
    instance = get_object_or_404(tenant_qs(request, StaffGroup), id=group_id)
    visible_permission_ids = {permission.id for permission in _assignable_permissions(request)}
    permissions = list(
        instance.djangoGroup.permissions.select_related('content_type').order_by('content_type__model', 'name')
    ) if instance.djangoGroup_id else []
    permissions = [permission for permission in permissions if permission.id in visible_permission_ids]
    permission_ids = [permission.id for permission in permissions]
    permission_details = [
        {
            'id': permission.id,
            'name': permission.name,
            'codename': permission.codename,
            'module': permission.content_type.model.replace('_', ' ').title(),
        }
        for permission in permissions
    ]
    members = list(
        tenant_qs(request, StaffUser)
        .filter(staffGroup=instance, isDeleted=False)
        .order_by('name')
        .values('id', 'name', 'phone', 'email')
    )
    data = {
        'ID': instance.pk,
        'Name': instance.name,
        'Description': instance.description,
        'DjangoGroup': instance.djangoGroup.name if instance.djangoGroup_id else '',
        'PermissionIDs': permission_ids,
        'Permissions': permission_details,
        'PermissionCount': len(permission_details),
        'Members': members,
        'MemberCount': len(members),
        'CreatedOn': instance.datetime.strftime('%d-%m-%Y %I:%M %p'),
        'UpdatedOn': instance.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p'),
    }
    return JsonResponse({'data': data, 'permission_catalog': {
        module: [{'id': perm.id, 'name': perm.name, 'codename': perm.codename} for perm in perms]
        for module, perms in _permission_catalog(request).items()
    }}, safe=False)
@transaction.atomic
def edit_staff_group_api(request):
    if request.method == 'POST':
        try:
            group_id = request.POST.get("EditId")
            name = request.POST.get("groupName")
            description = request.POST.get("description")
            permission_ids = request.POST.getlist("permission_ids[]") or request.POST.getlist("permission_ids")
            if not name:
                return JsonResponse({'message': 'error', 'error': 'Group name is required.'}, safe=False)
            allowed_permission_ids = {str(permission.id) for permission in _assignable_permissions(request)}
            invalid_permission_ids = [permission_id for permission_id in permission_ids if permission_id not in allowed_permission_ids]
            if invalid_permission_ids:
                return JsonResponse({'message': 'error', 'error': 'You can only assign permissions allowed for your tenant admin access.'}, safe=False)
            obj = tenant_qs(request, StaffGroup).get(id=int(group_id))
            obj.name = name
            obj.description = description
            obj.djangoGroup = _get_or_create_tenant_django_group(request, name, existing_group=obj.djangoGroup)
            obj.save()
            _sync_staff_group_permissions(obj, permission_ids)
            StaffUser.objects.filter(staffGroup=obj).update(group=obj.name)
            for staff in StaffUser.objects.filter(staffGroup=obj, user_ID__isnull=False).select_related('user_ID', 'staffGroup__djangoGroup'):
                _sync_staff_user_group_membership(staff)
            return JsonResponse({'message': 'success', 'group_id': obj.pk}, safe=False)
        except Exception as exc:
            transaction.set_rollback(True)
            return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
@transaction.atomic
def delete_staff_group_api(request):
    if request.method == 'POST':
        try:
            group_id = request.POST.get("userID")
            obj = tenant_qs(request, StaffGroup).get(pk=int(group_id))
            obj.isDeleted = True
            obj.save(update_fields=['isDeleted', 'lastUpdatedOn'])
            if obj.djangoGroup_id:
                obj.djangoGroup.user_set.clear()
            StaffUser.objects.filter(staffGroup=obj).update(staffGroup=None, group='')
            return JsonResponse({'message': 'success'}, safe=False)
        except Exception:
            return JsonResponse({'message': 'error'}, safe=False)
#
@transaction.atomic
def add_staff_api(request):
    if request.method == 'POST':
        try:
            CompanyUserName = request.POST.get("CompanyUserName")
            UserPhoneNo = request.POST.get("UserPhoneNo")
            UserEmail = request.POST.get("UserEmail")
            UserAddress = request.POST.get("UserAddress")
            UserGroup = request.POST.get("UserGroup")
            UserStatus = request.POST.get("UserStatus")
            UserPwd = request.POST.get("UserPwd")
            # PartyGroup = request.POST.get("PartyGroup")
            imageUpload = request.FILES.get("imageUpload")
            imageUploadID = request.FILES.get("imageUploadID")
            staff = StaffUser()
            assign_tenant(staff, request)
            if imageUpload:
                staff.photo = imageUpload
            if imageUploadID:
                staff.idProof = imageUploadID
            staff.name = CompanyUserName
            staff.phone = UserPhoneNo
            staff.email = UserEmail
            staff.address = UserAddress
            selected_group = tenant_qs(request, StaffGroup).get(pk=int(UserGroup))
            staff.staffGroup = selected_group
            staff.group = selected_group.name
            staff.isActive = UserStatus
            staff.userPassword = UserPwd
            # try:
            #     staff.partyGroupID_id = int(PartyGroup)
            # except:
            #     pass
            staff.save()
            username = 'u' + get_random_string(length=7, allowed_chars='0123456789')
            while User.objects.filter(username__exact=username).exists():
                username = 'u' + get_random_string(length=7, allowed_chars='0123456789')
            else:
                new_user = User()
                new_user.username = username
                new_user.set_password(UserPwd)
                new_user.save()
                staff.username = username
                staff.user_ID_id = new_user.pk
                staff.save()
                ensure_membership(new_user, staff.tenant, TenantMembership.Role.STAFF)
                _sync_staff_user_group_membership(staff)
            return JsonResponse({'message': 'success'}, safe=False)
        except Exception:
            return JsonResponse({'message': 'error'}, safe=False)


@transaction.atomic
def generate_staff_access_link_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_staff(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    staff_id = request.POST.get('userID')
    if not staff_id or not str(staff_id).isdigit():
        return JsonResponse({'message': 'error', 'error': 'Invalid staff id.'}, status=400)
    staff = tenant_qs(request, StaffUser).filter(isDeleted=False, pk=int(staff_id)).select_related('user_ID').first()
    if staff is None or staff.user_ID_id is None:
        return JsonResponse({'message': 'error', 'error': 'Staff login account is not available.'}, status=404)
    raw_token, token_obj = create_credential_share_token(
        request,
        user=staff.user_ID,
        subject_type=CredentialShareToken.SubjectType.STAFF,
        staff_user=staff,
        created_by=request.user,
    )
    return JsonResponse(_credential_share_payload(request, raw_token, token_obj), safe=False)
#
class StaffUserListJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'username', 'userPassword', 'group', 'phone', 'address',
                     'isActive', 'datetime']
    def get_initial_queryset(self):
        return tenant_qs(self.request, StaffUser).select_related('staffGroup').filter(isDeleted__exact=False).only(
            'id', 'photo', 'name', 'username', 'userPassword', 'group', 'phone',
            'address', 'isActive', 'datetime', 'staffGroup__name'
        )
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(username__icontains=search)
                | Q(group__icontains=search) | Q(staffGroup__name__icontains=search) | Q(phone__icontains=search)
                | Q(address__icontains=search) | Q(isActive__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            photo_url = _image_url(item.photo, 'thumbnail')
            images = '<img class="ui avatar image" src="{}">'.format(photo_url)
            if _can_manage_staff(self.request.user):
                action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetUserDetails('{}')" class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </button>
                  <button data-inverted="" data-tooltip="Share Access Link" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="showAccessLink('staff','{}')" class="ui circular teal icon button" style="margin-left: 3px">
                    <i class="linkify icon"></i>
                  </button>
                  <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button></td>'''.format(item.pk, item.pk, item.pk),
                username = item.username
                password = item.userPassword
            else:
                action = '''<div class="ui tiny label">
                  Denied
                </div>'''
                username = "*********"
                password = "*********"
            group_name = item.staffGroup.name if item.staffGroup_id else item.group
            json_data.append([
                images,  # escape HTML for security reasons
                escape(item.name),
                username,
                password,
                escape(group_name),
                escape(item.phone),
                escape(item.address),
                escape(item.isActive),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,
            ])
        return json_data
@transaction.atomic
def delete_staff_user(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            staff = tenant_qs(request, StaffUser).select_related().get(pk=int(id))
            staff.isDeleted = True
            staff.save()
            new_user = User.objects.select_related().get(pk=staff.user_ID_id)
            new_user.is_active = False
            new_user.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
def get_staff_user_detail(request):
    id = request.GET.get('id')
    C_User = get_object_or_404(tenant_qs(request, StaffUser), id=id)
    # instance = BankDetails.objects.get(companyID_id=company.pk)
    try:
        party = C_User.partyGroupID.id
    except:
        party = 'N/A'
    data = {
        'ID': C_User.pk,
        'UserName': C_User.name,
        'UserPhone': C_User.phone,
        'UserAddress': C_User.address,
        'UserEmail': C_User.email,
        'UserPassword': C_User.userPassword,
        'UserGroup': C_User.staffGroup_id,
        'PartyGroup': party,
        'IsActive': C_User.isActive,
        'ImgUrl': _image_url(C_User.photo, 'medium'),
        'IDUrl': _image_url(C_User.idProof, 'medium')
    }
    return JsonResponse({'data': data}, safe=False)
@transaction.atomic
def edit_staff_api(request):
    if request.method == 'POST':
        try:
            EditUserId = request.POST.get("EditUserId")
            CompanyUserName = request.POST.get("CompanyUserName")
            UserPhoneNo = request.POST.get("UserPhoneNo")
            UserEmail = request.POST.get("UserEmail")
            UserAddress = request.POST.get("UserAddress")
            UserGroup = request.POST.get("UserGroup")
            UserStatus = request.POST.get("UserStatus")
            UserPwd = request.POST.get("UserPwd")
            # PartyGroup = request.POST.get("PartyGroup")
            staff = tenant_qs(request, StaffUser).select_related().get(pk=int(EditUserId))
            old_password = staff.userPassword
            try:
                imageUpload = request.FILES["imageUpload"]
                staff.photo = imageUpload
            except:
                pass
            try:
                imageUploadID = request.FILES["imageUploadID"]
                staff.idProof = imageUploadID
            except:
                pass
            staff.name = CompanyUserName
            staff.phone = UserPhoneNo
            staff.email = UserEmail
            staff.address = UserAddress
            selected_group = tenant_qs(request, StaffGroup).get(pk=int(UserGroup))
            staff.staffGroup = selected_group
            staff.group = selected_group.name
            # try:
            #     staff.partyGroupID_id = int
            #     (PartyGroup)
            # except:
            #     pass
            staff.isActive = UserStatus
            staff.userPassword = UserPwd
            staff.save()
            new_user = User.objects.select_related().get(pk=staff.user_ID_id)
            new_user.set_password(UserPwd)
            if UserStatus == 'Active':
                new_user.is_active = True
            else:
                new_user.is_active = False
            new_user.save()
            if UserPwd and UserPwd != old_password:
                log_audit_event(
                    staff,
                    AuditLog.Action.PASSWORD_CHANGE,
                    actor=request.user,
                    changes={'userPassword': {'from': '***', 'to': '***'}},
                    snapshot=snapshot_instance(staff),
                )
            ensure_membership(new_user, staff.tenant, TenantMembership.Role.STAFF)
            _sync_staff_user_group_membership(staff)
            return JsonResponse({'message': 'success'}, safe=False)
        except Exception:
            return JsonResponse({'message': 'error'}, safe=False)
#
# # ---------------------------------Party Groups ----------------------------------------------------
#
@transaction.atomic
def add_party_group_api(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        if not _can_add_party_group(request.user):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        try:
            groupName = request.POST.get("groupName")
            description = request.POST.get("description")
            obj = PartyGroup()
            assign_tenant(obj, request)
            obj.name = groupName
            obj.description = description
            obj.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
#
@transaction.atomic
def delete_part_group(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        if not _can_delete_party_group(request.user):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        try:
            id = request.POST.get("userID")
            obj = tenant_qs(request, PartyGroup).select_related().get(pk=int(id))
            obj.isDeleted = True
            obj.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
class PartyGroupListJson(BaseDatatableView):
    order_columns = ['name', 'description', 'datetime']
    def get_initial_queryset(self):
        # if 'Admin' in group_names:
        if not getattr(self.request, 'user', None) or not self.request.user.is_authenticated:
            return PartyGroup.objects.none()
        if not _has_any_permission(self.request.user, 'home.can_manage_parties', 'home.can_view_party_credentials', 'home.view_partygroup'):
            return PartyGroup.objects.none()
        return tenant_qs(self.request, PartyGroup).filter(isDeleted__exact=False).only(
            'id', 'name', 'description', 'datetime'
        )
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            can_edit = _can_edit_party_group(self.request.user)
            can_delete = _can_delete_party_group(self.request.user)
            if can_edit:
                action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="GetUserDetails('{}','edit')" class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </button>'''.format(item.pk)
            else:
                action = '''<button data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="GetUserDetails('{}','view')" class="ui circular teal icon button">
                    <i class="eye icon"></i>
                  </button>'''.format(item.pk)
            if can_delete:
                action = action + ''' <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button>'''.format(item.pk)
            json_data.append([
                escape(item.name),
                escape(item.description),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,
            ])
        return json_data
def get_party_group_detail(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.can_manage_parties', 'home.can_view_party_credentials', 'home.view_partygroup'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    id = request.GET.get('id')
    instance = get_object_or_404(tenant_qs(request, PartyGroup), id=id)
    # instance = BankDetails.objects.get(companyID_id=company.pk)
    data = {
        'ID': instance.pk,
        'Name': instance.name,
        'Description': instance.description,
    }
    return JsonResponse({'data': data}, safe=False)
#
@transaction.atomic
def edit_part_group_api(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        if not _can_edit_party_group(request.user):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        try:
            Id = request.POST.get("EditId")
            name = request.POST.get("groupName")
            description = request.POST.get("description")
            obj = tenant_qs(request, PartyGroup).select_related().get(id=int(Id))
            obj.name = name
            obj.description = description
            obj.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
# # ----------------------------------Banks------------------------
@transaction.atomic
def add_bank_api(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        if not _can_add_bank(request.user):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        try:
            bankName = request.POST.get("bankName")
            accountNumber = request.POST.get("accountNumber")
            description = request.POST.get("description")
            openingBalance = request.POST.get("openingBalance")
            obj = Bank()
            assign_tenant(obj, request)
            obj.name = bankName
            obj.accountNumber = accountNumber
            obj.description = description
            obj.save()
            if obj.tenant_id:
                bank_account = ensure_bank_account(obj.tenant, obj)
                bank_account.openingBalance = float(_decimal_from_request(openingBalance))
                display_name = obj.name or 'Bank'
                if obj.accountNumber:
                    display_name = f'{display_name} - {obj.accountNumber}'
                bank_account.name = display_name
                bank_account.save(update_fields=['openingBalance', 'name', 'lastUpdatedOn'])
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
class BankListJson(BaseDatatableView):
    order_columns = ['name', 'accountNumber', 'description', 'datetime']
    def get_initial_queryset(self):
        # if 'Admin' in group_names:
        if not getattr(self.request, 'user', None) or not self.request.user.is_authenticated:
            return Bank.objects.none()
        if not _has_any_permission(self.request.user, 'home.can_manage_parties', 'home.can_view_party_credentials', 'home.view_bank'):
            return Bank.objects.none()
        return tenant_qs(self.request, Bank).filter(isDeleted__exact=False).only(
            'id', 'name', 'accountNumber', 'description', 'datetime'
        )
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(accountNumber__icontains=search) | Q(description__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            can_edit = _can_edit_bank(self.request.user)
            can_delete = _can_delete_bank(self.request.user)
            if can_edit:
                action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="GetUserDetails('{}','edit')" class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </button>'''.format(item.pk)
            else:
                action = '''<button data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="GetUserDetails('{}','view')" class="ui circular teal icon button">
                    <i class="eye icon"></i>
                  </button>'''.format(item.pk)
            if can_delete:
                action = action + ''' <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button>'''.format(item.pk)
            json_data.append([
                escape(item.name),
                escape(item.accountNumber),
                escape(item.description),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,
            ])
        return json_data
def get_bank_detail(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.can_manage_parties', 'home.can_view_party_credentials', 'home.view_bank'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    id = request.GET.get('id')
    instance = get_object_or_404(tenant_qs(request, Bank), id=id)
    bank_account = tenant_qs(request, BookAccount).filter(
        isDeleted=False,
        type=BookAccount.Type.BANK,
        bank=instance,
    ).only('openingBalance').first()
    # instance = BankDetails.objects.get(companyID_id=company.pk)
    data = {
        'ID': instance.pk,
        'Name': instance.name,
        'AccountNumber': instance.accountNumber,
        'Description': instance.description,
        'OpeningBalance': str(getattr(bank_account, 'openingBalance', 0.0) or 0.0),
    }
    return JsonResponse({'data': data}, safe=False)
class InvoiceSeriesListJson(BaseDatatableView):
    order_columns = ['module', 'seriesCode', 'invoiceNature', 'startNumber', 'nextNumber', 'paddingLength', 'isActive', 'isDefault', 'datetime']
    def get_initial_queryset(self):
        if not getattr(self.request, 'user', None) or not self.request.user.is_authenticated:
            return InvoiceSeries.objects.none()
        if not _has_any_permission(self.request.user, 'home.view_invoiceseries', 'home.add_invoiceseries', 'home.change_invoiceseries', 'home.delete_invoiceseries'):
            return InvoiceSeries.objects.none()
        return tenant_qs(self.request, InvoiceSeries).filter(isDeleted=False).only(
            'id', 'name', 'module', 'seriesCode', 'startNumber', 'endNumber', 'nextNumber', 'paddingLength', 'isActive', 'isDefault', 'invoiceNature', 'lockInvoiceNature', 'datetime'
        )
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(module__icontains=search) | Q(seriesCode__icontains=search))
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            payload = _invoice_series_payload_obj(item)
            can_edit = _can_edit_invoice_series(self.request.user)
            can_delete = _can_delete_invoice_series(self.request.user)
            if can_edit:
                action = '''<button data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="GetUserDetails('{}','edit')" class="ui circular facebook icon button green"><i class="pen icon"></i></button>'''.format(item.pk)
            else:
                action = '''<button data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="GetUserDetails('{}','view')" class="ui circular teal icon button"><i class="eye icon"></i></button>'''.format(item.pk)
            if can_delete:
                action += ''' <button data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk)
            status = '''<div class="ui tiny green label">Active</div>''' if item.isActive else '''<div class="ui tiny grey label">Inactive</div>'''
            default = '''<div class="ui tiny blue label">Default</div>''' if item.isDefault else '-'
            nature = escape(payload['InvoiceNatureLabel'])
            if payload['LockInvoiceNature'] and payload['InvoiceNature'] != InvoiceSeries.InvoiceNature.MIXED:
                nature = nature + ' <div class="ui tiny teal label">Locked</div>'
            json_data.append([
                escape(payload['Module']),
                escape(payload['SeriesCode']),
                nature,
                escape(payload['RangeLabel']),
                escape(payload['NextInvoice']),
                escape(str(payload['PaddingLength'])),
                status,
                default,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,
            ])
        return json_data
def get_invoice_series_detail(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.view_invoiceseries', 'home.add_invoiceseries', 'home.change_invoiceseries', 'home.delete_invoiceseries'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        series_id = request.GET.get('id')
        series = tenant_qs(request, InvoiceSeries).get(pk=int(series_id), isDeleted=False)
        return JsonResponse({'data': _invoice_series_payload_obj(series)}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def edit_bank_api(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        if not _can_edit_bank(request.user):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        try:
            Id = request.POST.get("EditId")
            name = request.POST.get("bankName")
            accountNumber = request.POST.get("accountNumber")
            description = request.POST.get("description")
            openingBalance = request.POST.get("openingBalance")
            obj = tenant_qs(request, Bank).select_related().get(id=int(Id))
            obj.name = name
            obj.accountNumber = accountNumber
            obj.description = description
            obj.save()
            if obj.tenant_id:
                bank_account = ensure_bank_account(obj.tenant, obj)
                bank_account.openingBalance = float(_decimal_from_request(openingBalance))
                display_name = obj.name or 'Bank'
                if obj.accountNumber:
                    display_name = f'{display_name} - {obj.accountNumber}'
                bank_account.name = display_name
                bank_account.save(update_fields=['openingBalance', 'name', 'lastUpdatedOn'])
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def delete_bank(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        if not _can_delete_bank(request.user):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        try:
            id = request.POST.get("userID")
            obj = tenant_qs(request, Bank).select_related().get(pk=int(id))
            obj.isDeleted = True
            obj.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
# ----------------------------- Party ---------------------
@transaction.atomic
def add_party_api(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        if not _can_add_party(request.user):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        try:
            partyName = request.POST.get("partyName")
            phoneNumber = request.POST.get("phoneNumber")
            openingBalance = request.POST.get("openingBalance")
            partyGroup = request.POST.get("partyGroup")
            address = request.POST.get("address")
            legalName = request.POST.get("legalName")
            gstNumber = request.POST.get("gstNumber")
            isB2B = str(request.POST.get("isB2B") or '').lower() in {'1', 'true', 'yes', 'on'}
            billingAddress = request.POST.get("billingAddress")
            staffID = request.POST.get("staffID")
            obj = Party()
            assign_tenant(obj, request)
            obj.name = partyName
            obj.legalName = (legalName or '').strip() or None
            obj.phone = phoneNumber
            obj.gstNumber = _normalize_gst_number(gstNumber) or None
            obj.isB2B = isB2B
            obj.openingBalance = _decimal_from_request(openingBalance)
            try:
                obj.assignTo = tenant_qs(request, StaffUser).get(pk=int(staffID))
            except:
                pass
            obj.address = (address or '').strip() or None
            obj.billingAddress = (billingAddress or '').strip() or None
            try:
                obj.partyGroupID_id = int(partyGroup)
            except:
                pass
            username = get_random_string(length=7, allowed_chars='0123456789')
            while User.objects.filter(username__exact=username).exists():
                username = get_random_string(length=7, allowed_chars='0123456789')
            password = get_random_string(length=6, allowed_chars='0123456789')
            new_user = User()
            new_user.username = username
            new_user.set_password(password)
            new_user.save()
            group, _ = Group.objects.get_or_create(name='Customer')
            group.user_set.add(new_user.pk)
            obj.username = username
            obj.password = password
            obj.userID_id = new_user.pk
            obj.save()
            ensure_membership(new_user, obj.tenant, TenantMembership.Role.PARTY)
            _bump_party_cache_version(request)
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def generate_party_users_api(request):
    if request.method == 'GET':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        if not _can_manage_party_records(request.user):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        try:
            customer_group, _ = Group.objects.get_or_create(name='Customer')
            created = 0
            skipped = 0
            parties = tenant_qs(request, Party).filter(isDeleted__exact=False).order_by('id')
            for party in parties:
                if party.userID_id:
                    skipped = skipped + 1
                    continue
                username = get_random_string(length=7, allowed_chars='0123456789')
                while User.objects.filter(username__exact=username).exists():
                    username = get_random_string(length=7, allowed_chars='0123456789')
                password = get_random_string(length=6, allowed_chars='0123456789')
                new_user = User()
                new_user.username = username
                new_user.set_password(password)
                new_user.save()
                customer_group.user_set.add(new_user.pk)
                party.username = username
                party.password = password
                party.userID_id = new_user.pk
                party.save(update_fields=['username', 'password', 'userID'])
                ensure_membership(new_user, party.tenant, TenantMembership.Role.PARTY)
                created = created + 1
            if created > 0:
                _bump_party_cache_version(request)
            return JsonResponse({
                'message': 'success',
                'created': created,
                'skipped': skipped,
            }, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


@transaction.atomic
def generate_party_access_link_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_party_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    party_id = request.POST.get('userID')
    if not party_id or not str(party_id).isdigit():
        return JsonResponse({'message': 'error', 'error': 'Invalid customer id.'}, status=400)
    party = tenant_qs(request, Party).filter(isDeleted=False, pk=int(party_id)).select_related('userID').first()
    if party is None or party.userID_id is None:
        return JsonResponse({'message': 'error', 'error': 'Customer login account is not available.'}, status=404)
    raw_token, token_obj = create_credential_share_token(
        request,
        user=party.userID,
        subject_type=CredentialShareToken.SubjectType.PARTY,
        party=party,
        created_by=request.user,
    )
    return JsonResponse(_credential_share_payload(request, raw_token, token_obj), safe=False)


class PartyListJson(BaseDatatableView):
    order_columns = ['name', 'phone', 'gstNumber', 'openingBalance', 'username', 'password', 'partyGroupID.name', 'assignTo.name', 'datetime']
    def get_initial_queryset(self):
        return tenant_qs(self.request, Party).select_related('partyGroupID', 'assignTo').filter(isDeleted__exact=False).only(
            'id', 'name', 'legalName', 'phone', 'gstNumber', 'isB2B', 'openingBalance', 'username', 'password', 'address', 'billingAddress', 'datetime',
            'partyGroupID__name', 'assignTo__name'
        )
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(legalName__icontains=search) | Q(phone__icontains=search) | Q(gstNumber__icontains=search) | Q(username__icontains=search)
                | Q(password__icontains=search) | Q(address__icontains=search) | Q(openingBalance__icontains=search)
                | Q(partyGroupID__name__icontains=search) | Q(assignTo__name__icontains=search) | Q(
                    datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            can_view_credentials = _can_view_party_credentials(self.request.user)
            can_edit = _can_edit_party(self.request.user)
            can_delete = _can_delete_party(self.request.user)
            if can_edit:
                action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="GetUserDetails('{}','edit')" class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </button>'''.format(item.pk)
            else:
                action = '''<button data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="GetUserDetails('{}','view')" class="ui circular teal icon button">
                    <i class="eye icon"></i>
                  </button>'''.format(item.pk)
            ledger_url = reverse('homeApp:party_ledger', args=[item.pk])
            action = action + ''' <a data-inverted="" data-tooltip="Ledger" data-position="left center" data-variation="mini" style="font-size:10px;" href="{}" class="ui circular blue icon button">
                    <i class="book icon"></i>
                  </a>'''.format(ledger_url)
            if can_view_credentials:
                action = action + ''' <button data-inverted="" data-tooltip="Share Access Link" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="showAccessLink('party','{}')" class="ui circular teal icon button">
                    <i class="linkify icon"></i>
                  </button>'''.format(item.pk)
            if can_delete:
                action = action + ''' <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button>'''.format(item.pk)
            try:
                party = item.partyGroupID.name
            except:
                party = 'N/A'
            try:
                usr = item.assignTo.name
            except:
                usr = 'N/A'
            if can_view_credentials and item.username:
                username = '''{} <button data-copy="{}" class="ui mini icon button copy-btn" type="button" title="Copy Username"><i class="copy outline icon"></i></button>'''.format(
                    escape(item.username),
                    escape(item.username)
                )
            elif can_view_credentials:
                username = 'N/A'
            else:
                username = '<div class="ui tiny label">Hidden</div>'
            if can_view_credentials and item.password:
                password = '''{} <button data-copy="{}" class="ui mini icon button copy-btn" type="button" title="Copy Password"><i class="copy outline icon"></i></button>'''.format(
                    escape(item.password),
                    escape(item.password)
                )
            elif can_view_credentials:
                password = 'N/A'
            else:
                password = '<div class="ui tiny label">Hidden</div>'
            json_data.append([
                escape(item.name),
                escape(item.phone),
                escape(item.gstNumber or ('B2B' if item.isB2B else '-')),
                formatINR(format(item.openingBalance, '.2f')),
                username,
                password,
                party,
                usr,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,
            ])
        return json_data
def get_party_detail(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized', 'error': 'Login required.'}, status=401)
    if not _can_manage_party_records(request.user):
        return JsonResponse({'message': 'forbidden', 'error': 'You do not have permission to view party details.'}, status=403)
    id = request.GET.get('id')
    instance = get_object_or_404(tenant_qs(request, Party), id=id)
    # instance = BankDetails.objects.get(companyID_id=company.pk)
    try:
        party = instance.partyGroupID.pk
    except:
        party = 'N/A'
    try:
        usr = instance.assignTo.pk
    except:
        usr = 'N/A'
    data = {
        'ID': instance.pk,
        'Name': instance.name,
        'LegalName': instance.legalName,
        'PhoneNumber': instance.phone,
        'GSTNumber': instance.gstNumber,
        'IsB2B': bool(instance.isB2B),
        'OpeningBalance': str(instance.openingBalance),
        'Address': instance.address,
        'BillingAddress': instance.billingAddress,
        'PartyGroup': party,
        'AssignTo': usr,
    }
    return JsonResponse({'data': data}, safe=False)
@transaction.atomic
def edit_party_api(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        if not _can_edit_party(request.user):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        try:
            Id = request.POST.get("EditId")
            name = request.POST.get("partyName")
            phoneNumber = request.POST.get("phoneNumber")
            openingBalance = request.POST.get("openingBalance")
            address = request.POST.get("address")
            legalName = request.POST.get("legalName")
            gstNumber = request.POST.get("gstNumber")
            isB2B = str(request.POST.get("isB2B") or '').lower() in {'1', 'true', 'yes', 'on'}
            billingAddress = request.POST.get("billingAddress")
            partyGroup = request.POST.get("partyGroup")
            staffID = request.POST.get("staffID")
            obj = tenant_qs(request, Party).select_related().get(id=int(Id))
            obj.name = name
            obj.legalName = (legalName or '').strip() or None
            obj.phone = phoneNumber
            obj.gstNumber = _normalize_gst_number(gstNumber) or None
            obj.isB2B = isB2B
            obj.openingBalance = _decimal_from_request(openingBalance)
            obj.address = (address or '').strip() or None
            obj.billingAddress = (billingAddress or '').strip() or None
            try:
                obj.partyGroupID_id = int(partyGroup)
            except:
                pass
            try:
                obj.assignTo_id = int(staffID)
            except:
                pass
            obj.save()
            _bump_party_cache_version(request)
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def delete_party(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        if not _can_delete_party(request.user):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        try:
            id = request.POST.get("userID")
            obj = tenant_qs(request, Party).select_related().get(pk=int(id))
            obj.isDeleted = True
            obj.save()
            _bump_party_cache_version(request)
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
def list_party_api(request):
    try:
        version = _get_party_cache_version(request)
        cache_key = party_list_cache_key(f'v{version}', 'all', request_or_user=request)
        o_list = cache.get(cache_key)
        if o_list is None:
            o_list = []
            obj_list = tenant_qs(request, Party).select_related('partyGroupID').filter(isDeleted__exact=False).only(
                'id', 'name', 'legalName', 'phone', 'gstNumber', 'isB2B', 'address', 'billingAddress', 'partyGroupID__name'
            ).order_by('name')
            for obj in obj_list:
                party = obj.partyGroupID.name if obj.partyGroupID else 'N/A'
                obj_dic = {
                    'ID': obj.pk,
                    'Name': obj.name,
                    'LegalName': obj.legalName or obj.name,
                    'PartyGroup': party,
                    'Phone': obj.phone,
                    'GSTNumber': obj.gstNumber or '',
                    'IsB2B': bool(obj.isB2B),
                    'Address': obj.address or '',
                    'BillingAddress': obj.billingAddress or obj.address or '',
                    'Detail': obj.name + ' - ' + party + ' @ ' + str(obj.pk),
                    'DisplayDetail': obj.name + ' - ' + party + ((' | GSTIN: ' + obj.gstNumber) if obj.gstNumber else '')
                }
                o_list.append(obj_dic)
            cache.set(cache_key, o_list, timeout=60 * 30)
        return JsonResponse({'message': 'success', 'data': o_list}, safe=False)
    except:
        return JsonResponse({'message': 'error'}, safe=False)
def list_party_by_executive_or_station_api(request):
    try:
        executive = request.GET.get('executive')
        station = request.GET.get('station')
        o_list = []
        version = _get_party_cache_version(request)
        cache_key = party_list_cache_key(f'v{version}', 'exec', executive, 'station', station, request_or_user=request)
        cached = cache.get(cache_key)
        if cached is not None:
            return JsonResponse({'message': 'success', 'data': cached}, safe=False)
        obj_list = tenant_qs(request, Party).select_related('partyGroupID').filter(
            isDeleted__exact=False
        ).only(
            'id', 'name', 'phone', 'partyGroupID__name'
        )
        if executive != 'All' and station == 'All':
            obj_list = obj_list.filter(assignTo__id=int(executive))
        elif executive == 'All' and station != 'All':
            obj_list = obj_list.filter(partyGroupID__id=int(station))
        elif executive != 'All' and station != 'All':
            obj_list = obj_list.filter(assignTo__id=int(executive), partyGroupID__id=int(station))
        obj_list = obj_list.order_by('name')
        for obj in obj_list:
            try:
                party = obj.partyGroupID.name
            except:
                party = 'N/A'
            obj_dic = {
                'ID': obj.pk,
                'Name': obj.name,
                'PartyGroup': party,
                'Phone': obj.phone,
                'Detail': obj.name + ' - ' + party + ' @ ' + str(obj.pk),
                'DisplayDetail': obj.name + ' - ' + party
            }
            o_list.append(obj_dic)
        cache.set(cache_key, o_list, timeout=60 * 15)
        return JsonResponse({'message': 'success', 'data': o_list}, safe=False)
    except:
        return JsonResponse({'message': 'error'}, safe=False)
# ------------------------------Collection ----------------------------------------
# ----------------------------- Party ---------------------
@transaction.atomic
def add_collection_by_staff_api(request):
    if request.method == 'POST':
        try:
            transferredParty = request.POST.get("TransferredParty")
            party = request.POST.get("party")
            paymentMode = request.POST.get("paymentMode")
            amountPaid = request.POST.get("amountPaid")
            bank = request.POST.get("bank")
            detail = request.POST.get("detail")
            remark = request.POST.get("remark")
            otp_required = str(request.POST.get("otpRequired") or "").lower() in {"1", "true", "yes", "on"}
            otp_code = (request.POST.get("otpCode") or "").strip()
            lat = request.POST.get("lat")
            lng = request.POST.get("lng")
            chequeDate = request.POST.get("chequeDate")
            collectDate = request.POST.get("collectDate")
            collect_date_obj = None
            if collectDate:
                try:
                    collect_date_obj = datetime.strptime(collectDate, '%d/%m/%Y').date()
                except Exception:
                    collect_date_obj = None
            bank_required_modes = {'Online', 'Cheque', 'Cheque CC'}
            if paymentMode in bank_required_modes and not bank:
                return JsonResponse({'message': 'bank_required'}, safe=False)
            if paymentMode in {'Cheque', 'Cheque CC'} and not chequeDate:
                return JsonResponse({'message': 'cheque_date_required'}, safe=False)
            party_id = _parse_party_id_value(party)
            if not party_id:
                return JsonResponse({'message': 'error'}, safe=False)
            cus = tenant_qs(request, Party).select_related().get(pk=party_id)
            if otp_required:
                if not (otp_code.isdigit() and len(otp_code) == 6):
                    return JsonResponse({'message': 'otp_required'}, safe=False)
                payload = get_collection_otp_payload(cus.tenant_id, cus.pk) or {}
                if not payload or str(payload.get('otp')) != otp_code:
                    return JsonResponse({'message': 'otp_invalid'}, safe=False)
                meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
                try:
                    meta_mode = (meta.get('paymentMode') or '').strip()
                    if meta_mode and meta_mode != paymentMode:
                        return JsonResponse({'message': 'otp_mismatch'}, safe=False)
                except Exception:
                    pass
                try:
                    meta_amt = meta.get('amountPaid', None)
                    if meta_amt is not None:
                        if abs(float(meta_amt) - float(amountPaid)) > 0.01:
                            return JsonResponse({'message': 'otp_mismatch'}, safe=False)
                except Exception:
                    pass
                try:
                    meta_date = (meta.get('collectDate') or '').strip()
                    if meta_date and meta_date != (collectDate or ''):
                        return JsonResponse({'message': 'otp_mismatch'}, safe=False)
                except Exception:
                    pass
            obj = Collection()
            assign_tenant(obj, request)
            obj.isTallied = False
            obj.partyID_id = cus.pk
            obj.modeOfPayment = paymentMode
            obj.paidAmount = float(amountPaid)
            if paymentMode in bank_required_modes:
                obj.bankID_id = int(bank)
            else:
                obj.bankID = None
            if paymentMode in {'Cheque', 'Cheque CC'}:
                obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
            if paymentMode == 'Party':
                try:
                    p = str(transferredParty).split('@')
                    tParty = tenant_qs(request, Party).select_related().get(pk=int(p[1]))
                    obj.transferredPartyID_id = int(tParty.pk)
                except:
                    pass
            obj.detail = detail
            obj.remark = remark
            obj.latitude = lat
            obj.longitude = lng
            user = tenant_qs(request, StaffUser).get(user_ID_id=request.user.pk)
            obj.collectedBy_id = user.pk
            if otp_required:
                obj.otpVerified = True
                obj.otpVerifiedOn = timezone.now()
            try:
                # obj.collectionAddress = deduct_location_balance(lat, lng)
                obj.collectionAddress = "From Field"
            except:
                pass
            obj.save()
            obj.paymentID = _next_tenant_sequence_value(request, TenantSequence.Scope.COLLECTION_PAYMENT)
            if collect_date_obj:
                try:
                    base_dt = obj.datetime or timezone.now()
                    try:
                        base_dt = timezone.localtime(base_dt)
                    except Exception:
                        pass
                    obj.collectionDateTime = datetime.combine(collect_date_obj, base_dt.timetz().replace(microsecond=0))
                except Exception:
                    obj.collectionDateTime = obj.datetime
            else:
                obj.collectionDateTime = obj.datetime
            obj.save(update_fields=['paymentID', 'collectionDateTime', 'otpVerified', 'otpVerifiedOn', 'lastUpdatedOn'])
            sync_book_transactions(obj)
            sync_party_ledger_entries_for_object(obj)
            if otp_required:
                try:
                    clear_collection_otp(cus.tenant_id, cus.pk)
                except Exception:
                    pass
                try:
                    set_collection_status(
                        cus.tenant_id,
                        cus.pk,
                        status='received',
                        meta={
                            'paymentMode': paymentMode,
                            'amountPaid': float(amountPaid),
                            'collectDate': collectDate,
                            'paymentID': obj.paymentID,
                        },
                    )
                except Exception:
                    pass
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def add_collection_by_admin_api(request):
    if request.method == 'POST':
        try:
            transferredParty = request.POST.get("TransferredParty")
            party = request.POST.get("party")
            paymentMode = request.POST.get("paymentMode")
            amountPaid = request.POST.get("amountPaid")
            bank = request.POST.get("bank")
            detail = request.POST.get("detail")
            remark = request.POST.get("remark")
            otp_required = str(request.POST.get("otpRequired") or "").lower() in {"1", "true", "yes", "on"}
            otp_code = (request.POST.get("otpCode") or "").strip()
            lat = request.POST.get("lat")
            lng = request.POST.get("lng")
            chequeDate = request.POST.get("chequeDate")
            collectDate = request.POST.get("collectDate")
            collect_date_obj = None
            if collectDate:
                try:
                    collect_date_obj = datetime.strptime(collectDate, '%d/%m/%Y').date()
                except Exception:
                    collect_date_obj = None
            bank_required_modes = {'Online', 'Cheque', 'Cheque CC'}
            if paymentMode in bank_required_modes and not bank:
                return JsonResponse({'message': 'bank_required'}, safe=False)
            if paymentMode in {'Cheque', 'Cheque CC'} and not chequeDate:
                return JsonResponse({'message': 'cheque_date_required'}, safe=False)
            c = str(party).split('@')
            cus = tenant_qs(request, Party).select_related().get(pk=int(c[1]))
            if otp_required:
                if not (otp_code.isdigit() and len(otp_code) == 6):
                    return JsonResponse({'message': 'otp_required'}, safe=False)
                payload = get_collection_otp_payload(cus.tenant_id, cus.pk) or {}
                if not payload or str(payload.get('otp')) != otp_code:
                    return JsonResponse({'message': 'otp_invalid'}, safe=False)
                meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
                # If OTP was generated with details, enforce that the submitted values match.
                try:
                    meta_mode = (meta.get('paymentMode') or '').strip()
                    if meta_mode and meta_mode != paymentMode:
                        return JsonResponse({'message': 'otp_mismatch'}, safe=False)
                except Exception:
                    pass
                try:
                    meta_amt = meta.get('amountPaid', None)
                    if meta_amt is not None:
                        if abs(float(meta_amt) - float(amountPaid)) > 0.01:
                            return JsonResponse({'message': 'otp_mismatch'}, safe=False)
                except Exception:
                    pass
                try:
                    meta_date = (meta.get('collectDate') or '').strip()
                    if meta_date and meta_date != collectDate:
                        return JsonResponse({'message': 'otp_mismatch'}, safe=False)
                except Exception:
                    pass
            obj = Collection()
            assign_tenant(obj, request)
            obj.partyID_id = cus.pk
            obj.isTallied = False
            obj.modeOfPayment = paymentMode
            obj.paidAmount = float(amountPaid)
            if paymentMode in bank_required_modes:
                obj.bankID_id = int(bank)
            else:
                obj.bankID = None
            if paymentMode in {'Cheque', 'Cheque CC'}:
                obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
            if paymentMode == 'Party':
                try:
                    p = str(transferredParty).split('@')
                    tParty = tenant_qs(request, Party).select_related().get(pk=int(p[1]))
                    obj.transferredPartyID_id = int(tParty.pk)
                except:
                    pass
            obj.detail = detail
            obj.remark = remark
            obj.latitude = lat
            obj.longitude = lng
            user = tenant_qs(request, StaffUser).get(user_ID_id=request.user.pk)
            obj.collectedBy_id = user.pk
            if otp_required:
                obj.otpVerified = True
                obj.otpVerifiedOn = timezone.now()
            obj.collectionAddress = "From Shop."
            obj.save()
            obj.paymentID = _next_tenant_sequence_value(request, TenantSequence.Scope.COLLECTION_PAYMENT)
            # If user selected a date-only value, keep the entry time (not 12:00 AM).
            if collect_date_obj:
                try:
                    base_dt = obj.datetime or timezone.now()
                    try:
                        base_dt = timezone.localtime(base_dt)
                    except Exception:
                        pass
                    obj.collectionDateTime = datetime.combine(collect_date_obj, base_dt.timetz().replace(microsecond=0))
                except Exception:
                    obj.collectionDateTime = obj.datetime
            else:
                obj.collectionDateTime = obj.datetime
            can_auto_approve = _can_auto_approve_collections(request.user)
            if can_auto_approve:
                try:
                    obj.isApproved = True
                    obj.approvedBy_id = user.pk
                    obj.approvedOn = timezone.now()
                except Exception:
                    pass
            obj.save()
            sync_book_transactions(obj)
            sync_party_ledger_entries_for_object(obj)
            if otp_required:
                try:
                    clear_collection_otp(cus.tenant_id, cus.pk)
                except Exception:
                    pass
                try:
                    set_collection_status(
                        cus.tenant_id,
                        cus.pk,
                        status='received',
                        meta={
                            'paymentMode': paymentMode,
                            'amountPaid': float(amountPaid),
                            'collectDate': collectDate,
                            'paymentID': obj.paymentID,
                        },
                    )
                except Exception:
                    pass
            if can_auto_approve:
                try:
                    msg = "Sir, Your Payment has been received by {} Rs.{}/- on Dt.{} ag. Ref. No. {}, Kindly confirm the same. If you have any query Please feel free to contact on this no. 7005607770. Thanks, BSS".format(
                        obj.modeOfPayment, obj.paidAmount, str(obj.collectionDateTime.strftime('%d-%m-%Y')),
                        obj.paymentID)
                    try:
                        numbers = str(obj.partyID.phone).split('.')
                        for n in numbers:
                            send_message(n, msg, obj.partyID.name)
                    except:
                        send_message(obj.partyID.phone, msg, obj.partyID.name)
                except:
                    pass
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def edit_collection_by_admin_api(request):
    if request.method == 'POST':
        try:
            ID = request.POST.get("ID")
            cDate = request.POST.get("cDate")
            party = request.POST.get("party")
            transferredParty = request.POST.get("TransferredParty")
            paymentMode = request.POST.get("paymentMode")
            amountPaid = request.POST.get("amountPaid")
            bank = request.POST.get("bank")
            detail = request.POST.get("detail")
            remark = request.POST.get("remark")
            # c = str(party).split('@')
            chequeDate = request.POST.get("chequeDate")
            bank_required_modes = {'Online', 'Cheque', 'Cheque CC'}
            if paymentMode in bank_required_modes and not bank:
                return JsonResponse({'message': 'bank_required'}, safe=False)
            if paymentMode in {'Cheque', 'Cheque CC'} and not chequeDate:
                return JsonResponse({'message': 'cheque_date_required'}, safe=False)
            # cus = Party.objects.select_related().get(pk=int(c[1]))
            cus = tenant_qs(request, Party).select_related().get(pk=int(party))
            obj = tenant_qs(request, Collection).get(pk=int(ID))
            if _can_review_collections(request.user):
                obj.partyID_id = cus.pk
                obj.modeOfPayment = paymentMode
                obj.paidAmount = float(amountPaid)
                obj.detail = detail
                obj.remark = remark
                obj.transferredPartyID = None
                # Preserve time when changing date (avoid 12:00 AM).
                try:
                    c_date_obj = datetime.strptime(cDate, '%d/%m/%Y').date()
                    base_dt = obj.collectionDateTime or obj.datetime or timezone.now()
                    try:
                        base_dt = timezone.localtime(base_dt)
                    except Exception:
                        pass
                    obj.collectionDateTime = datetime.combine(c_date_obj, base_dt.timetz().replace(microsecond=0))
                except Exception:
                    obj.collectionDateTime = datetime.strptime(cDate, '%d/%m/%Y')
                if paymentMode in bank_required_modes:
                    obj.bankID_id = int(bank)
                else:
                    obj.bankID = None
                if paymentMode in {'Cheque', 'Cheque CC'}:
                    obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
                else:
                    obj.chequeDate = None
                if paymentMode == 'Party':
                    try:
                        tparty = tenant_qs(request, Party).select_related().get(pk=int(transferredParty))
                        obj.transferredPartyID_id = tparty.pk
                    except:
                        pass
                obj.save()
                sync_book_transactions(obj)
                sync_party_ledger_entries_for_object(obj)
            # if 'Moderator' in request.user.groups.values_list('name', flat=True):
            #     if paymentMode == 'Cheque':
            #         obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
            #
            #     obj.save()
            # try:
            #     msg = "Sir, Our Executive has collected the payment of {} Rs.{}/- from you, Kindly confirm the same. If you have any query Please feel free contact on this no. 7005607770. Thanks, BSS".format(
            #         obj.modeOfPayment, obj.paidAmount)
            #     send_whatsapp_message(obj.partyID.phone, msg)
            # except:
            #     pass
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
class CollectionByStaffListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID.name', 'paidAmount', 'otpVerified', 'modeOfPayment', 'collectionDateTime', 'datetime',
                     'bankID.name', 'detail', 'transferredPartyID',
                     'remark',
                     ]
    def get_initial_queryset(self):
        # user = StaffUser.objects.get(user_ID__id = self.request.pk)
        # if 'Admin' in group_names:
        return _collection_list_base_qs(self.request).filter(
            isDeleted__exact=False,
            collectionDateTime__date=datetime.today().date(),
            collectedBy__user_ID_id=self.request.user.pk
        )
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(paymentID__icontains=search) | Q(partyID__name__icontains=search) | Q(
                    transferredPartyID__name__icontains=search) | Q(
                    paidAmount__icontains=search) | Q(
                    modeOfPayment__icontains=search)
                | Q(bankID__name__icontains=search) | Q(detail__icontains=search) | Q(detail__icontains=search) | Q(
                    remark__icontains=search)
                | Q(collectionDateTime__icontains=search) | Q(datetime__icontains=search) | Q(
                    collectionAddress__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            # action = '''<button  data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "GetUserDetails('{}')" class="ui circular facebook icon button green">
            #         <i class="pen icon"></i>
            #       </button>
            #       <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
            #         <i class="trash alternate icon"></i>
            #       </button>'''.format(item.pk, item.pk),
            action = '''<div class="ui tiny label">
  Denied
</div>''',
            try:
                bank = item.bankID.name
            except:
                bank = '-'
            try:
                tparty = item.transferredPartyID.name
            except:
                tparty = '-'
            json_data.append([
                escape(item.paymentID),
                escape(item.partyID.name),
                formatINR(format(item.paidAmount, '.0f')),
                _collection_verified_label(item.otpVerified),
                escape(item.modeOfPayment),
                escape(item.collectionDateTime.strftime('%d-%m-%Y')),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(bank),
                escape(item.detail),
                escape(tparty),
                escape(item.remark),
                action,
            ])
        return json_data
class CollectionByAdminListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID.name', 'paidAmount', 'action', 'otpVerified', 'modeOfPayment', 'collectedBy.name',
                     'collectionDateTime', 'datetime',
                     'isApproved', 'approvedBy.name', 'bankID.name', 'isTallied', 'detail', 'chequeDate',
                     'transferredPartyID',
                     'collectionAddress', 'remark'
                     ]
    def get_initial_queryset(self):
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            staffID = self.request.GET.get("staffID")
            sDate = datetime.strptime(startDateV, '%d/%m/%Y')
            eDate = datetime.strptime(endDateV, '%d/%m/%Y')
            queryset = _collection_list_base_qs(self.request).filter(
                isDeleted__exact=False,
                collectionDateTime__date__range=[sDate.date(), eDate.date()]
            )
            if not _can_filter_all_collections(self.request.user):
                return queryset.filter(collectedBy__user_ID_id=self.request.user.pk)
            if staffID and staffID != 'All':
                return queryset.filter(collectedBy_id=int(staffID))
            return queryset
        except:
            queryset = _collection_list_base_qs(self.request).filter(
                isDeleted__exact=False,
                collectionDateTime__date=datetime.today().date()
            )
            if not _can_filter_all_collections(self.request.user):
                return queryset.filter(collectedBy__user_ID_id=self.request.user.pk)
            return queryset
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(paymentID__icontains=search) | Q(partyID__name__icontains=search) | Q(
                    transferredPartyID__name__icontains=search) | Q(
                    paidAmount__icontains=search) | Q(
                    modeOfPayment__icontains=search)
                | Q(bankID__name__icontains=search) | Q(chequeDate__icontains=search) | Q(detail__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search) | Q(collectionDateTime__icontains=search) | Q(
                    collectionAddress__icontains=search) | Q(
                    collectedBy__name__icontains=search)
                | Q(approvedBy__name__icontains=search) | Q(isApproved__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if _can_auto_approve_collections(self.request.user):
                action = '''<button  data-inverted="" data-tooltip="Make Approval" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "showConfirmationModal('{}')" class="ui circular facebook icon button purple">
                   <i class="check circle icon"></i>
                  </button>
                  <a href="/edit_collection/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </a>
                  <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button>'''.format(item.pk, item.pk, item.pk),
            elif _can_review_collections(self.request.user):
                action = ''' <a href="/edit_collection/{}/" data-inverted="" data-tooltip="Edit Cheque Date Only" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </a>'''.format(item.pk)
            else:
                action = '''<div class="ui tiny label">
  Denied
</div>'''
            if item.modeOfPayment == 'Cheque':
                try:
                    chequeDate = item.chequeDate.strftime('%d-%m-%Y')
                except:
                    chequeDate = '-'
            else:
                chequeDate = '-'
            try:
                bank = item.bankID.name
            except:
                bank = '-'
            try:
                tparty = item.transferredPartyID.name
            except:
                tparty = '-'
            try:
                collectedBy = item.collectedBy.name
            except:
                collectedBy = '-'
            try:
                approvedBy = item.approvedBy.name
            except:
                approvedBy = '-'
            if item.isApproved == True:
                isApproved = '''<div class="ui tiny green label">
                  Yes
                </div>'''
            else:
                isApproved = '''<div class="ui tiny red label">
                  No
                </div>'''
            if item.isTallied == True:
                isTallied = '''<div class="ui tiny green label">
                            Yes
                          </div>'''
            else:
                isTallied = '''<div class="ui tiny red label" data-tooltip="Enable Tally" data-position="left center" data-variation="mini" style="cursor:pointer"  onclick = "confirmTallyModel('{}')">
                            No
                          </div>'''.format(item.pk)
            json_data.append([
                escape(item.paymentID),
                escape(item.partyID.name),
                formatINR(format(item.paidAmount, '.0f')),
                action,
                _collection_verified_label(item.otpVerified),
                escape(item.modeOfPayment),
                collectedBy,
                escape(item.collectionDateTime.strftime('%d-%m-%Y')),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                isApproved,
                approvedBy,
                escape(bank),
                isTallied,
                escape(item.detail),
                escape(chequeDate),
                escape(tparty),
                escape(item.collectionAddress),
                escape(item.remark),
            ])
        return json_data


def export_collection_list_excel(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    qs = _collection_list_base_qs(request).filter(isDeleted=False)
    try:
        start_date = datetime.strptime(request.GET.get('startDate'), '%d/%m/%Y').date()
        end_date = datetime.strptime(request.GET.get('endDate'), '%d/%m/%Y').date()
    except Exception:
        start_date = end_date = datetime.today().date()
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    qs = qs.filter(collectionDateTime__date__range=[start_date, end_date])
    if not _can_filter_all_collections(request.user):
        qs = qs.filter(collectedBy__user_ID_id=request.user.pk)
        staff_name = 'MyList'
    else:
        staff_id = request.GET.get('staffID')
        if staff_id and staff_id != 'All':
            qs = qs.filter(collectedBy_id=int(staff_id))
            staff = tenant_qs(request, StaffUser).filter(pk=int(staff_id)).first()
            staff_name = staff.name if staff else 'Selected Staff'
        else:
            staff_name = 'All'
    search = (request.GET.get('search') or '').strip()
    if search:
        qs = qs.filter(
            Q(paymentID__icontains=search) | Q(partyID__name__icontains=search) |
            Q(transferredPartyID__name__icontains=search) | Q(paidAmount__icontains=search) |
            Q(modeOfPayment__icontains=search) | Q(bankID__name__icontains=search) |
            Q(chequeDate__icontains=search) | Q(detail__icontains=search) |
            Q(remark__icontains=search) | Q(collectionAddress__icontains=search) |
            Q(collectedBy__name__icontains=search) | Q(approvedBy__name__icontains=search)
        )
    rows = []
    for item in qs.order_by('-collectionDateTime', '-datetime'):
        rows.append([
            item.paymentID or '-',
            item.partyID.name if item.partyID_id else '-',
            float(item.paidAmount or 0.0),
            'Yes' if item.otpVerified else 'No',
            item.modeOfPayment or '-',
            item.collectedBy.name if item.collectedBy_id else '-',
            item.collectionDateTime.strftime('%d/%m/%Y') if item.collectionDateTime else '-',
            item.datetime.strftime('%d/%m/%Y %I:%M %p') if item.datetime else '-',
            'Yes' if item.isApproved else 'No',
            item.approvedBy.name if item.approvedBy_id else '-',
            item.bankID.name if item.bankID_id else '-',
            'Yes' if item.isTallied else 'No',
            item.detail or '-',
            item.chequeDate.strftime('%d/%m/%Y') if item.chequeDate else '-',
            item.transferredPartyID.name if item.transferredPartyID_id else '-',
            item.collectionAddress or '-',
            item.remark or '-',
        ])
    return _xlsx_report_response('collection-list', [{
        'title': 'Collections',
        'report_title': 'Collection List Export',
        'report_subtitle': 'Detailed filtered collection rows',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('From', start_date.strftime('%d/%m/%Y')), ('To', end_date.strftime('%d/%m/%Y')), ('Staff', staff_name), ('Search', search or '-')],
        'tables': [{'title': 'Collections', 'headers': ['Payment ID', 'Party', 'Amount', 'Verified', 'Mode', 'Collected By', 'Collection Date', 'Added On', 'Approved', 'Approved By', 'Bank', 'Tallied', 'Detail', 'Cheque Date', 'Transferred Party', 'Location', 'Remark'], 'rows': rows}],
    }])
class ChequeReminderCollectionListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID.name', 'paidAmount', 'modeOfPayment', 'collectedBy.name',
                     'collectionDateTime', 'datetime',
                     'isApproved', 'approvedBy.name', 'bankID.name', 'detail', 'chequeDate', 'collectionAddress',
                     'remark'
                     ]
    def get_initial_queryset(self):
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            staffID = self.request.GET.get("staffID")
            sDate = datetime.strptime(startDateV, '%d/%m/%Y')
            eDate = datetime.strptime(endDateV, '%d/%m/%Y')
            queryset = _collection_list_base_qs(self.request).filter(
                isDeleted__exact=False,
                chequeDate__range=[sDate.date(), eDate.date()],
                modeOfPayment__exact='Cheque'
            )
            if not _can_filter_all_collections(self.request.user):
                return queryset.filter(collectedBy__user_ID_id=self.request.user.pk)
            if staffID and staffID != 'All':
                return queryset.filter(collectedBy_id=int(staffID))
            return queryset
        except:
            queryset = _collection_list_base_qs(self.request).filter(
                isDeleted__exact=False,
                chequeDate__exact=datetime.today().date(),
                modeOfPayment__exact='Cheque'
            )
            if not _can_filter_all_collections(self.request.user):
                return queryset.filter(collectedBy__user_ID_id=self.request.user.pk)
            return queryset
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(paymentID__icontains=search) | Q(partyID__name__icontains=search) | Q(
                    paidAmount__icontains=search) | Q(
                    modeOfPayment__icontains=search)
                | Q(bankID__name__icontains=search) | Q(chequeDate__icontains=search) | Q(detail__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search) | Q(collectionDateTime__icontains=search) | Q(
                    collectionAddress__icontains=search) | Q(
                    collectedBy__name__icontains=search)
                | Q(approvedBy__name__icontains=search) | Q(isApproved__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if _can_auto_approve_collections(self.request.user):
                action = '''<button  data-inverted="" data-tooltip="Make Approval" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick = "showConfirmationModal('{}')" class="ui circular facebook icon button purple">
                   <i class="check circle icon"></i>
                  </button>
                  <a href="/edit_collection/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </a>
                  <button  data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"  style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                    <i class="trash alternate icon"></i>
                  </button>'''.format(item.pk, item.pk, item.pk),
            elif _can_review_collections(self.request.user):
                action = ''' <a href="/edit_collection/{}/" data-inverted="" data-tooltip="Edit Cheque Date Only" data-position="left center" data-variation="mini" style="font-size:10px;"  class="ui circular facebook icon button green">
                        <i class="pen icon"></i>
                      </a>'''.format(item.pk)
            else:
                action = '''<div class="ui tiny label">
  Denied
</div>'''
            if item.modeOfPayment == 'Cheque':
                try:
                    chequeDate = item.chequeDate.strftime('%d-%m-%Y')
                except:
                    chequeDate = '-'
            else:
                chequeDate = '-'
            try:
                bank = item.bankID.name
            except:
                bank = '-'
            try:
                collectedBy = item.collectedBy.name
            except:
                collectedBy = '-'
            try:
                approvedBy = item.approvedBy.name
            except:
                approvedBy = '-'
            if item.isApproved == True:
                isApproved = '''<div class="ui tiny green label">
                  Yes
                </div>'''
            else:
                isApproved = '''<div class="ui tiny red label">
                  No
                </div>'''
            json_data.append([
                escape(item.paymentID),
                escape(item.partyID.name),
                formatINR(format(item.paidAmount, '.0f')),
                escape(item.modeOfPayment),
                collectedBy,
                escape(item.collectionDateTime.strftime('%d-%m-%Y')),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                isApproved,
                approvedBy,
                escape(bank),
                escape(item.detail),
                escape(chequeDate),
                escape(item.collectionAddress),
                escape(item.remark),
                action,
            ])
        return json_data
@transaction.atomic
def delete_collection(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            obj = tenant_qs(request, Collection).select_related().get(pk=int(id))
            obj.isDeleted = True
            obj.save()
            sync_book_transactions(obj)
            sync_party_ledger_entries_for_object(obj)
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def approve_collection(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _can_review_collections(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        id = request.POST.get("userID")
        user = tenant_qs(request, StaffUser).get(user_ID__id=request.user.pk)
        is_counter_collection = isinstance(id, str) and id.startswith('c')
        if is_counter_collection:
            obj = tenant_qs(request, CollectionCashCounter).select_related().get(pk=int(id[1:]))
        else:
            obj = tenant_qs(request, Collection).select_related().get(pk=int(id))
        obj.isApproved = True
        obj.approvedBy_id = user.pk
        obj.approvedOn = timezone.now()
        obj.save()
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
        try:
            sync_party_ledger_entries_for_object(obj)
        except Exception:
            pass
        try:
            msg = "Sir, Your Payment has been received by {} Rs.{}/- on Dt.{} ag. Ref. No. {}, Kindly confirm the same. If you have any query Please feel free to contact on this no. 7005607770. Thanks, BSS".format(
                obj.modeOfPayment, obj.paidAmount, str(obj.collectionDateTime.strftime('%d-%m-%Y')), obj.paymentID)
            try:
                numbers = str(obj.partyID.phone).split('.')
                for n in numbers:
                    send_message(n, msg, obj.partyID.name)
            except:
                send_message(obj.partyID.phone, msg, obj.partyID.name)
        except:
            pass
        return JsonResponse({'message': 'success'}, safe=False)
    except:
        return JsonResponse({'message': 'error'}, safe=False)
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
@cache_page(60 * 5)
def get_admin_dashboard_report_api(request):
    today = datetime.today().date()
    party_count = tenant_qs(request, Party).filter(isDeleted=False).count()
    staff_count = tenant_qs(request, StaffUser).filter(isDeleted=False).count()
    location = tenant_qs(request, GeolocationPackage).filter(isDeleted=False).only('used', 'balance').order_by('-id').first()
    collection_total = tenant_qs(request, Collection).filter(
        isDeleted=False,
        collectionDateTime__date=today
    ).aggregate(total=Sum('paidAmount'))['total'] or 0.0
    sales_total = tenant_qs(request, Sales).filter(
        isDeleted=False,
        buildDate__exact=today
    ).aggregate(total=Sum('amount'))['total'] or 0.0
    data = {
        'partyCount': formatINR(party_count),
        'staffCount': staff_count,
        'locationCount': str(int(location.used)) + '/' + str(int(location.balance)) if location else '0/0',
        'collection': formatINR(collection_total),
        'Sale': formatINR(sales_total),
    }
    return JsonResponse({'data': data}, safe=False)
@cache_page(60 * 5)
def get_staff_dashboard_report_api(request):
    today = datetime.today().date()
    user_party_group_id = tenant_qs(request, StaffUser).filter(
        user_ID_id=request.user.pk,
        isDeleted=False
    ).values_list('partyGroupID_id', flat=True).first()
    party_count = 0
    if user_party_group_id:
        party_count = tenant_qs(request, Party).filter(
            isDeleted=False,
            partyGroupID_id=user_party_group_id
        ).count()
    collection_stats = tenant_qs(request, Collection).filter(
        isDeleted=False,
        collectedBy__user_ID_id=request.user.pk
    ).aggregate(
        total_amount=Sum('paidAmount'),
        today_amount=Sum('paidAmount', filter=Q(collectionDateTime__date=today)),
        today_count=Count('id', filter=Q(collectionDateTime__date=today)),
    )
    data = {
        'partyCount': formatINR(party_count),
        'collection_totalCount': collection_stats['today_count'] or 0,
        'collection_total': formatINR(collection_stats['total_amount'] or 0.0),
        'collection': formatINR(collection_stats['today_amount'] or 0.0)
    }
    return JsonResponse({'data': data}, safe=False)
@cache_page(60 * 5)
def get_party_dashboard_report_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'forbidden'}, status=403)
    if not _has_any_permission(request.user, 'home.can_manage_parties', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403)
    today = datetime.today().date()
    thirty_days_ago = today - timedelta(days=30)
    parties = tenant_qs(request, Party).filter(isDeleted=False)
    party_groups = tenant_qs(request, PartyGroup).filter(isDeleted=False)
    staff_qs = tenant_qs(request, StaffUser).filter(isDeleted=False, user_ID_id=request.user.pk)
    staff = staff_qs.first()
    total_parties = parties.count()
    assigned_parties = parties.filter(assignTo__isnull=False).count()
    unassigned_parties = parties.filter(assignTo__isnull=True).count()
    recent_parties = parties.filter(datetime__date__gte=thirty_days_ago).count()
    my_assigned_parties = parties.filter(assignTo=staff).count() if staff else 0
    data = {
        'party_total_count': total_parties,
        'party_group_count': party_groups.count(),
        'party_assigned_count': assigned_parties,
        'party_unassigned_count': unassigned_parties,
        'party_recent_count': recent_parties,
        'party_my_assigned_count': my_assigned_parties,
    }
    return JsonResponse({'data': data}, safe=False)
@cache_page(60 * 5)
def get_bank_dashboard_report_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'forbidden'}, status=403)
    if not _has_any_permission(request.user, 'home.can_manage_parties', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403)
    banks = tenant_qs(request, Bank).filter(isDeleted=False)
    total_banks = banks.count()
    banks_with_account = banks.exclude(accountNumber__isnull=True).exclude(accountNumber__exact='').count()
    banks_missing_account = total_banks - banks_with_account
    banks_used = tenant_qs(request, Collection).filter(isDeleted=False, bankID__isnull=False).values('bankID').distinct().count()
    data = {
        'bank_total_count': total_banks,
        'bank_used_count': banks_used,
        'bank_with_account_count': banks_with_account,
        'bank_missing_account_count': banks_missing_account,
    }
    return JsonResponse({'data': data}, safe=False)
def get_bank_balances_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'forbidden'}, status=403)
    # Bank balances are sensitive; restrict to admin/owner-style access.
    if not request.user.has_perm('home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403)
    tenant = get_current_tenant(request)
    if tenant is not None:
        ensure_books_for_tenant(tenant)
    banks = list(
        tenant_qs(request, Bank)
        .filter(isDeleted=False)
        .values('id', 'name', 'accountNumber')
        .order_by('name')
    )
    totals_by_bank = {bank['id']: 0.0 for bank in banks}
    account_rows = list(
        tenant_qs(request, BookAccount)
        .filter(isDeleted=False, type=BookAccount.Type.BANK, bank__isnull=False)
        .values('id', 'bank_id', 'openingBalance')
    )
    tx_totals = {
        row['account']: row
        for row in (
            tenant_qs(request, BookTransaction)
            .filter(
                isDeleted=False,
                account_id__in=[row['id'] for row in account_rows],
            )
            .values('account')
            .annotate(
                in_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.IN)),
                out_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.OUT)),
            )
        )
    }
    for row in account_rows:
        account_total = float(row['openingBalance'] or 0.0)
        tx_row = tx_totals.get(row['id'])
        if tx_row:
            account_total += float(tx_row.get('in_total') or 0.0) - float(tx_row.get('out_total') or 0.0)
        totals_by_bank[row['bank_id']] = totals_by_bank.get(row['bank_id'], 0.0) + account_total
    data = []
    for bank in banks:
        data.append({
            'id': bank['id'],
            'name': bank['name'],
            'accountNumber': bank['accountNumber'] or '',
            'balance': totals_by_bank.get(bank['id'], 0.0),
        })
    return JsonResponse({'message': 'success', 'data': data}, safe=False)
def _parse_books_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%d/%m/%Y').date()
    except Exception:
        return None
def _cashbook_export_account(request):
    account_id = request.GET.get('account') or request.GET.get('accountId')
    if account_id and str(account_id).isdigit():
        return tenant_qs(request, BookAccount).filter(
            isDeleted=False,
            pk=int(account_id),
            type=BookAccount.Type.CASH,
        ).first()
    tenant = get_current_tenant(request)
    if tenant:
        ensure_books_for_tenant(tenant)
    return tenant_qs(request, BookAccount).filter(
        isDeleted=False,
        type=BookAccount.Type.CASH,
    ).order_by('-isDefault', 'name').first()
def _bankbook_export_account(request):
    bank_id = request.GET.get('bank')
    tenant = get_current_tenant(request)
    if tenant:
        ensure_books_for_tenant(tenant)
    accounts = tenant_qs(request, BookAccount).filter(
        isDeleted=False,
        type=BookAccount.Type.BANK,
    ).select_related('bank').order_by('name')
    if bank_id and str(bank_id).isdigit():
        selected_bank = tenant_qs(request, Bank).filter(isDeleted=False, pk=int(bank_id)).first()
        if selected_bank:
            return accounts.filter(bank=selected_bank).first()
    return accounts.first()
def _can_access_cashbook(user):
    return _has_any_permission(user, 'home.can_manage_cash_counter', 'home.can_view_party_credentials', 'home.can_manage_books')
def _can_access_bankbook(user):
    return user.has_perm('home.can_view_party_credentials') or user.has_perm('home.can_manage_books')
def _cashbook_statement_rows(request, account, start_date, end_date):
    base_qs = tenant_qs(request, BookTransaction).filter(isDeleted=False, account=account)
    prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
        in_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.IN)),
        out_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.OUT)),
    )
    opening = float(account.openingBalance or 0.0) + float(prior['in_total'] or 0.0) - float(prior['out_total'] or 0.0)
    qs = (
        base_qs
        .filter(txDateTime__date__range=[start_date, end_date])
        .select_related('party', 'account')
        .order_by('txDateTime', 'id')
    )
    balance = opening
    total_in = 0.0
    total_out = 0.0
    rows = []
    for item in qs:
        in_amt = float(item.amount or 0.0) if item.direction == BookTransaction.Direction.IN else 0.0
        out_amt = float(item.amount or 0.0) if item.direction == BookTransaction.Direction.OUT else 0.0
        balance += in_amt - out_amt
        total_in += in_amt
        total_out += out_amt
        rows.append({
            'date': item.txDateTime,
            'particulars': (item.narration or '-').strip() or '-',
            'party': item.party.name if item.party_id else '-',
            'receipt': in_amt,
            'payment': out_amt,
            'balance': balance,
            'source': item.get_source_display(),
        })
    return {
        'opening': opening,
        'rows': rows,
        'total_in': total_in,
        'total_out': total_out,
        'closing': balance,
    }
def _bankbook_statement_rows(request, account, start_date, end_date):
    base_qs = tenant_qs(request, BookTransaction).filter(isDeleted=False, account=account)
    prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
        in_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.IN)),
        out_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.OUT)),
    )
    opening = float(account.openingBalance or 0.0) + float(prior['in_total'] or 0.0) - float(prior['out_total'] or 0.0)
    qs = (
        base_qs
        .filter(txDateTime__date__range=[start_date, end_date])
        .select_related('party', 'account', 'account__bank')
        .order_by('txDateTime', 'id')
    )
    balance = opening
    total_in = 0.0
    total_out = 0.0
    rows = []
    for item in qs:
        in_amt = float(item.amount or 0.0) if item.direction == BookTransaction.Direction.IN else 0.0
        out_amt = float(item.amount or 0.0) if item.direction == BookTransaction.Direction.OUT else 0.0
        balance += in_amt - out_amt
        total_in += in_amt
        total_out += out_amt
        rows.append({
            'date': item.txDateTime,
            'particulars': (item.narration or '-').strip() or '-',
            'party': item.party.name if item.party_id else '-',
            'receipt': in_amt,
            'payment': out_amt,
            'balance': balance,
            'source': item.get_source_display(),
        })
    return {
        'opening': opening,
        'rows': rows,
        'total_in': total_in,
        'total_out': total_out,
        'closing': balance,
    }
@transaction.atomic
def add_book_manual_tx_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    account_id = request.POST.get('accountId')
    direction = request.POST.get('direction')
    amount = request.POST.get('amount')
    date = request.POST.get('date')
    narration = request.POST.get('narration', '')
    party_id = request.POST.get('partyId')
    if not account_id or not direction or not amount:
        return JsonResponse({'message': 'invalid'}, safe=False)
    try:
        account = tenant_qs(request, BookAccount).select_related('bank').get(pk=int(account_id), isDeleted=False)
    except Exception:
        return JsonResponse({'message': 'invalid_account'}, safe=False)
    # Permissions
    if account.type == BookAccount.Type.BANK:
        if not request.user.has_perm('home.can_view_party_credentials'):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    else:
        if not _has_any_permission(request.user, 'home.can_manage_cash_counter', 'home.can_view_party_credentials', 'home.can_manage_books'):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        amount_value = float(amount)
    except Exception:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    if amount_value <= 0:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    tx_date = _parse_books_date(date) or datetime.today().date()
    tx_datetime = datetime.combine(tx_date, datetime.now().time())
    party = None
    if party_id and party_id.isdigit():
        party = tenant_qs(request, Party).filter(isDeleted=False, pk=int(party_id)).first()
    staff_user = tenant_qs(request, StaffUser).filter(isDeleted=False, user_ID_id=request.user.pk).first()
    tx = BookTransaction()
    assign_tenant(tx, request)
    tx.account = account
    tx.direction = direction
    tx.amount = amount_value
    tx.txDateTime = tx_datetime
    tx.narration = narration
    tx.party = party
    tx.createdBy = staff_user
    tx.source = BookTransaction.Source.MANUAL
    tx.save()
    return JsonResponse({'message': 'success'}, safe=False)
@transaction.atomic
def transfer_books_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not request.user.has_perm('home.can_view_party_credentials') and not request.user.has_perm('home.can_manage_books'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    from_account_id = request.POST.get('fromAccountId')
    to_account_id = request.POST.get('toAccountId')
    amount = request.POST.get('amount')
    date = request.POST.get('date')
    note = request.POST.get('note', '')
    if not from_account_id or not to_account_id or not amount:
        return JsonResponse({'message': 'invalid'}, safe=False)
    try:
        from_account = tenant_qs(request, BookAccount).get(pk=int(from_account_id), isDeleted=False)
        to_account = tenant_qs(request, BookAccount).get(pk=int(to_account_id), isDeleted=False)
    except Exception:
        return JsonResponse({'message': 'invalid_account'}, safe=False)
    if from_account.pk == to_account.pk:
        return JsonResponse({'message': 'same_account'}, safe=False)
    try:
        amount_value = float(amount)
    except Exception:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    if amount_value <= 0:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    tx_date = _parse_books_date(date) or datetime.today().date()
    tx_datetime = datetime.combine(tx_date, datetime.now().time())
    staff_user = tenant_qs(request, StaffUser).filter(isDeleted=False, user_ID_id=request.user.pk).first()
    transfer_id = get_random_string(20)
    out_tx = BookTransaction()
    assign_tenant(out_tx, request)
    out_tx.account = from_account
    out_tx.direction = BookTransaction.Direction.OUT
    out_tx.amount = amount_value
    out_tx.txDateTime = tx_datetime
    out_tx.narration = note or 'Transfer'
    out_tx.createdBy = staff_user
    out_tx.source = BookTransaction.Source.TRANSFER
    out_tx.transferId = transfer_id
    out_tx.save()
    in_tx = BookTransaction()
    assign_tenant(in_tx, request)
    in_tx.account = to_account
    in_tx.direction = BookTransaction.Direction.IN
    in_tx.amount = amount_value
    in_tx.txDateTime = tx_datetime
    in_tx.narration = note or 'Transfer'
    in_tx.createdBy = staff_user
    in_tx.source = BookTransaction.Source.TRANSFER
    in_tx.transferId = transfer_id
    in_tx.save()
    log_audit_event(
        out_tx,
        AuditLog.Action.TRANSFER,
        actor=request.user,
        changes={
            'transferId': transfer_id,
            'fromAccount': str(from_account),
            'toAccount': str(to_account),
            'amount': amount_value,
            'note': note or 'Transfer',
        },
        snapshot=snapshot_instance(out_tx),
    )
    return JsonResponse({'message': 'success'}, safe=False)
@transaction.atomic
def transfer_cash_to_bank_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not request.user.has_perm('home.can_view_party_credentials') and not request.user.has_perm('home.can_manage_books'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    from_account_id = request.POST.get('fromAccountId')
    to_bank_id = request.POST.get('toBankId')
    if not to_bank_id:
        return JsonResponse({'message': 'invalid_bank'}, safe=False)
    try:
        bank = tenant_qs(request, Bank).get(pk=int(to_bank_id), isDeleted=False)
    except Exception:
        return JsonResponse({'message': 'invalid_bank'}, safe=False)
    tenant = get_current_tenant(request)
    to_account = (
        BookAccount.objects
        .filter(tenant=tenant, isDeleted=False, type=BookAccount.Type.BANK, bank=bank)
        .first()
    )
    if to_account is None:
        to_account = BookAccount.objects.create(
            tenant=tenant,
            type=BookAccount.Type.BANK,
            bank=bank,
            name=f'{bank.name} - {bank.accountNumber}' if bank.accountNumber else bank.name,
        )
    from_account_id = request.POST.get('fromAccountId')
    amount = request.POST.get('amount')
    date = request.POST.get('date')
    note = request.POST.get('note', '')
    if not from_account_id or not amount:
        return JsonResponse({'message': 'invalid'}, safe=False)
    try:
        from_account = tenant_qs(request, BookAccount).get(pk=int(from_account_id), isDeleted=False)
    except Exception:
        return JsonResponse({'message': 'invalid_account'}, safe=False)
    try:
        amount_value = float(amount)
    except Exception:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    if amount_value <= 0:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    tx_date = _parse_books_date(date) or datetime.today().date()
    tx_datetime = datetime.combine(tx_date, datetime.now().time())
    staff_user = tenant_qs(request, StaffUser).filter(isDeleted=False, user_ID_id=request.user.pk).first()
    transfer_id = get_random_string(20)
    out_tx = BookTransaction()
    assign_tenant(out_tx, request)
    out_tx.account = from_account
    out_tx.direction = BookTransaction.Direction.OUT
    out_tx.amount = amount_value
    out_tx.txDateTime = tx_datetime
    out_tx.narration = note or 'Transfer'
    out_tx.createdBy = staff_user
    out_tx.source = BookTransaction.Source.TRANSFER
    out_tx.transferId = transfer_id
    out_tx.save()
    in_tx = BookTransaction()
    assign_tenant(in_tx, request)
    in_tx.account = to_account
    in_tx.direction = BookTransaction.Direction.IN
    in_tx.amount = amount_value
    in_tx.txDateTime = tx_datetime
    in_tx.narration = note or 'Transfer'
    in_tx.createdBy = staff_user
    in_tx.source = BookTransaction.Source.TRANSFER
    in_tx.transferId = transfer_id
    in_tx.save()
    log_audit_event(
        in_tx,
        AuditLog.Action.TRANSFER,
        actor=request.user,
        changes={
            'transferId': transfer_id,
            'fromAccount': str(from_account),
            'toAccount': str(to_account),
            'bank': bank.name,
            'amount': amount_value,
            'note': note or 'Transfer',
        },
        snapshot=snapshot_instance(in_tx),
    )
    return JsonResponse({'message': 'success'}, safe=False)
def get_book_tx_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    tx_id = request.GET.get('id')
    if not tx_id or not tx_id.isdigit():
        return JsonResponse({'message': 'invalid'}, safe=False)
    tx = tenant_qs(request, BookTransaction).select_related('account', 'account__bank').filter(isDeleted=False, pk=int(tx_id)).first()
    if tx is None:
        return JsonResponse({'message': 'not_found'}, safe=False)
    # Bankbook transactions are admin-only.
    if tx.account.type == BookAccount.Type.BANK and not request.user.has_perm('home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    return JsonResponse({
        'message': 'success',
        'data': {
            'id': tx.id,
            'accountId': tx.account_id,
            'accountType': tx.account.type,
            'direction': tx.direction,
            'amount': tx.amount,
            'date': tx.txDateTime.strftime('%d/%m/%Y'),
            'narration': tx.narration or '',
            'source': tx.source,
            'transferId': tx.transferId or '',
        },
    }, safe=False)
@transaction.atomic
def edit_book_tx_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    tx_id = request.POST.get('id')
    if not tx_id or not tx_id.isdigit():
        return JsonResponse({'message': 'invalid'}, safe=False)
    tx = tenant_qs(request, BookTransaction).select_related('account').filter(isDeleted=False, pk=int(tx_id)).first()
    if tx is None:
        return JsonResponse({'message': 'not_found'}, safe=False)
    # Only manual entries are editable. Auto-synced rows must be edited at the source record.
    if tx.source != BookTransaction.Source.MANUAL:
        return JsonResponse({'message': 'edit_source'}, safe=False)
    if tx.account.type == BookAccount.Type.BANK and not request.user.has_perm('home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    if tx.account.type == BookAccount.Type.CASH and not _has_any_permission(request.user, 'home.can_manage_cash_counter', 'home.can_view_party_credentials', 'home.can_manage_books'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    direction = request.POST.get('direction')
    amount = request.POST.get('amount')
    date = request.POST.get('date')
    narration = request.POST.get('narration', '')
    if direction not in (BookTransaction.Direction.IN, BookTransaction.Direction.OUT):
        return JsonResponse({'message': 'invalid_direction'}, safe=False)
    try:
        amount_value = float(amount)
    except Exception:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    if amount_value <= 0:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    tx_date = _parse_books_date(date) or tx.txDateTime.date()
    tx.txDateTime = datetime.combine(tx_date, tx.txDateTime.time())
    tx.direction = direction
    tx.amount = amount_value
    tx.narration = narration
    tx.save()
    return JsonResponse({'message': 'success'}, safe=False)
@transaction.atomic
def delete_book_tx_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    tx_id = request.POST.get('id')
    if not tx_id or not tx_id.isdigit():
        return JsonResponse({'message': 'invalid'}, safe=False)
    tx = tenant_qs(request, BookTransaction).select_related('account').filter(isDeleted=False, pk=int(tx_id)).first()
    if tx is None:
        return JsonResponse({'message': 'not_found'}, safe=False)
    if tx.account.type == BookAccount.Type.BANK and not request.user.has_perm('home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    if tx.account.type == BookAccount.Type.CASH and not _has_any_permission(request.user, 'home.can_manage_cash_counter', 'home.can_view_party_credentials', 'home.can_manage_books'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    # Only manual/transfer rows are deletable directly.
    if tx.source not in (BookTransaction.Source.MANUAL, BookTransaction.Source.TRANSFER):
        return JsonResponse({'message': 'delete_source'}, safe=False)
    if tx.source == BookTransaction.Source.TRANSFER and tx.transferId:
        tenant = get_current_tenant(request)
        BookTransaction.objects.filter(tenant=tenant, isDeleted=False, transferId=tx.transferId).update(isDeleted=True)
        return JsonResponse({'message': 'success'}, safe=False)
    tx.isDeleted = True
    tx.save()
    return JsonResponse({'message': 'success'}, safe=False)
def _format_money_compact(value):
    try:
        val = float(value or 0.0)
    except Exception:
        return '0'
    if abs(val - int(val)) < 1e-9:
        return formatINR(format(int(val), '.0f'))
    return formatINR(format(val, '.2f'))


def _pdf_base_context(request, applied_filters=None):
    tenant = get_current_tenant(request)
    generated_by = 'System'
    try:
        if request.user.is_authenticated:
            staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk).first()
            generated_by = staff.name if staff and staff.name else request.user.get_username()
    except Exception:
        generated_by = getattr(request.user, 'username', 'System') or 'System'
    return {
        'tenant': tenant,
        'pdf_brand_name': 'zivabooks.in',
        'pdf_tenant_name': getattr(tenant, 'name', None) or 'zivabooks.in',
        'pdf_generated_by': generated_by,
        'pdf_generated_on': timezone.now(),
        'pdf_applied_filters': applied_filters or [],
    }


def _analytics_excel_workbook(context):
    generated_on = context.get('pdf_generated_on')
    if generated_on:
        if timezone.is_naive(generated_on):
            generated_on_display = generated_on.strftime('%d %b %Y %I:%M %p')
        else:
            generated_on_display = timezone.localtime(generated_on).strftime('%d %b %Y %I:%M %p')
    else:
        generated_on_display = ''

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Report'

    thin_border = Border(
        left=Side(style='thin', color='D7E0EA'),
        right=Side(style='thin', color='D7E0EA'),
        top=Side(style='thin', color='D7E0EA'),
        bottom=Side(style='thin', color='D7E0EA'),
    )
    title_fill = PatternFill('solid', fgColor='FFFFFF')
    section_fill = PatternFill('solid', fgColor='E8F1FB')
    header_fill = PatternFill('solid', fgColor='D7E8FB')
    wrap_alignment = Alignment(vertical='center', wrap_text=True)

    def add_row(values, font=None, fill=None):
        row_index = sheet.max_row + 1
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row_index, column=column, value='' if value is None else str(value))
            cell.alignment = wrap_alignment
            cell.border = thin_border
            if font:
                cell.font = font
            if fill:
                cell.fill = fill
        return row_index

    add_row([context.get('pdf_tenant_name') or 'zivabooks.in'], font=Font(name='Calibri', size=16, bold=True), fill=title_fill)
    add_row([context.get('report_title') or '', context.get('report_subtitle') or '', context.get('pdf_brand_name') or 'zivabooks.in'], font=Font(name='Calibri', size=11, color='475569'))
    add_row([])

    summary = context.get('summary') or []
    if summary:
        add_row(['Summary'], font=Font(name='Calibri', size=12, bold=True), fill=section_fill)
        add_row(['Metric', 'Value', 'Delta'], font=Font(name='Calibri', size=11, bold=True), fill=header_fill)
        for item in summary:
            add_row([
                item.get('label', ''),
                item.get('value', ''),
                item.get('delta', '-') if item.get('delta') is not None else '-',
            ])
        add_row([])

    health = context.get('health') or []
    if health:
        add_row(['Health'], font=Font(name='Calibri', size=12, bold=True), fill=section_fill)
        add_row(['Metric', 'Value', 'Note'], font=Font(name='Calibri', size=11, bold=True), fill=header_fill)
        for item in health:
            add_row([
                item.get('label', ''),
                item.get('value', ''),
                item.get('subtext', '-') or '-',
            ])
        add_row([])

    for section in context.get('section_tables') or []:
        add_row([section.get('title', 'Section')], font=Font(name='Calibri', size=12, bold=True), fill=section_fill)
        add_row(section.get('headers') or [], font=Font(name='Calibri', size=11, bold=True), fill=header_fill)
        for row in section.get('rows') or []:
            add_row(row)
        add_row([])

    insights = context.get('insights') or []
    if insights:
        add_row(['Insights'], font=Font(name='Calibri', size=12, bold=True), fill=section_fill)
        add_row(['Topic', 'Observation'], font=Font(name='Calibri', size=11, bold=True), fill=header_fill)
        for item in insights:
            add_row([
                item.get('title', ''),
                item.get('body', ''),
            ])
        add_row([])

    meta_font = Font(name='Calibri', size=11, color='334155')
    add_row(['Generated By', context.get('pdf_generated_by', '')], font=meta_font)
    add_row(['Generated On', generated_on_display], font=meta_font)
    add_row(['Applied Filters', ' | '.join(context.get('pdf_applied_filters') or ['All records'])], font=meta_font)

    widths = {
        'A': 28,
        'B': 24,
        'C': 34,
        'D': 20,
        'E': 20,
        'F': 20,
        'G': 20,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream.getvalue()


def _safe_sheet_title(title):
    text = (title or 'Report').strip() or 'Report'
    cleaned = ''.join(ch for ch in text if ch not in '[]:*?/\\')
    return cleaned[:31] or 'Report'


def _xlsx_report_response(filename_prefix, sheets):
    workbook = Workbook()
    workbook.remove(workbook.active)
    thin_border = Border(
        left=Side(style='thin', color='D7E0EA'),
        right=Side(style='thin', color='D7E0EA'),
        top=Side(style='thin', color='D7E0EA'),
        bottom=Side(style='thin', color='D7E0EA'),
    )
    wrap_alignment = Alignment(vertical='center', wrap_text=True)
    title_font = Font(name='Calibri', size=15, bold=True)
    subtitle_font = Font(name='Calibri', size=11, color='475569')
    section_font = Font(name='Calibri', size=12, bold=True)
    header_font = Font(name='Calibri', size=11, bold=True)
    meta_font = Font(name='Calibri', size=11, color='334155')
    section_fill = PatternFill('solid', fgColor='E8F1FB')
    header_fill = PatternFill('solid', fgColor='D7E8FB')

    def write_cell(cell, value, font=None, fill=None):
        cell.value = value
        cell.alignment = wrap_alignment
        cell.border = thin_border
        if font:
            cell.font = font
        if fill:
            cell.fill = fill
        if isinstance(value, (float, Decimal)):
            cell.number_format = '#,##0.00'
        elif isinstance(value, datetime):
            cell.number_format = 'DD-MM-YYYY HH:MM'
        elif hasattr(value, 'strftime') and not isinstance(value, str):
            cell.number_format = 'DD-MM-YYYY'

    for index, spec in enumerate(sheets):
        ws = workbook.create_sheet(title=_safe_sheet_title(spec.get('title') or f'Report {index + 1}'))
        row_idx = 1

        def append_row(values, font=None, fill=None):
            nonlocal row_idx
            for col_idx, value in enumerate(values, start=1):
                write_cell(ws.cell(row=row_idx, column=col_idx), value, font=font, fill=fill)
            row_idx += 1

        if spec.get('report_title'):
            append_row([spec['report_title']], font=title_font)
        if spec.get('report_subtitle'):
            append_row([spec['report_subtitle']], font=subtitle_font)
        if spec.get('report_title') or spec.get('report_subtitle'):
            row_idx += 1

        metadata = spec.get('metadata') or []
        if metadata:
            append_row(['Report Metadata'], font=section_font, fill=section_fill)
            for label, value in metadata:
                append_row([label, value], font=meta_font)
            row_idx += 1

        for table in spec.get('tables') or []:
            if table.get('title'):
                append_row([table['title']], font=section_font, fill=section_fill)
            headers = table.get('headers') or []
            if headers:
                append_row(headers, font=header_font, fill=header_fill)
            for row in table.get('rows') or []:
                append_row(row)
            row_idx += 1

        for column_cells in ws.columns:
            length = 0
            letter = get_column_letter(column_cells[0].column)
            for cell in column_cells:
                value = '' if cell.value is None else str(cell.value)
                length = max(length, len(value))
            ws.column_dimensions[letter].width = min(max(length + 2, 14), 40)

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename={filename_prefix}-{datetime.today().strftime("%Y-%m-%d")}.xlsx'
    response.write(stream.getvalue())
    return response


def _analytics_export_response(request, report_title, report_subtitle, summary, health, insights, section_tables, applied_filters, filename_prefix, file_format='pdf'):
    context = {
        'report_title': report_title,
        'report_subtitle': report_subtitle,
        'summary': summary or [],
        'health': health or [],
        'insights': insights or [],
        'section_tables': section_tables or [],
    }
    context.update(_pdf_base_context(request, applied_filters))
    safe_name = f"{filename_prefix}-{datetime.today().strftime('%Y-%m-%d')}"
    if file_format == 'excel':
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename={safe_name}.xlsx'
        response.write(_analytics_excel_workbook(context))
        return response
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename={safe_name}.pdf'
    html = render_to_string("home/report/analyticsSummaryPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A4 portrait; margin: .7cm; }')])
    return response
def generate_cashbook_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _can_access_cashbook(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    account = _cashbook_export_account(request)
    if account is None:
        return JsonResponse({'message': 'not_found'}, status=404, safe=False)
    start_date = _parse_books_date(request.GET.get('startDate')) or datetime.today().date()
    end_date = _parse_books_date(request.GET.get('endDate')) or start_date
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    statement = _cashbook_statement_rows(request, account, start_date, end_date)
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
    if not _can_access_cashbook(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    account = _cashbook_export_account(request)
    if account is None:
        return JsonResponse({'message': 'not_found'}, status=404, safe=False)
    start_date = _parse_books_date(request.GET.get('startDate')) or datetime.today().date()
    end_date = _parse_books_date(request.GET.get('endDate')) or start_date
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    statement = _cashbook_statement_rows(request, account, start_date, end_date)
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
    if not _can_access_bankbook(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    account = _bankbook_export_account(request)
    if account is None:
        return JsonResponse({'message': 'not_found'}, status=404, safe=False)
    start_date = _parse_books_date(request.GET.get('startDate')) or datetime.today().date()
    end_date = _parse_books_date(request.GET.get('endDate')) or start_date
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    statement = _bankbook_statement_rows(request, account, start_date, end_date)
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
    if not _can_access_bankbook(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    account = _bankbook_export_account(request)
    if account is None:
        return JsonResponse({'message': 'not_found'}, status=404, safe=False)
    start_date = _parse_books_date(request.GET.get('startDate')) or datetime.today().date()
    end_date = _parse_books_date(request.GET.get('endDate')) or start_date
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    statement = _bankbook_statement_rows(request, account, start_date, end_date)
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
class BookTransactionListJson(BaseDatatableView):
    """
    Server-side DataTables list for BookTransaction.
    Uses a fixed chronological ordering so the running balance remains meaningful.
    """
    order_columns = ['txDateTime', 'narration', 'party__name', 'amount', 'amount', 'balance', 'source', 'id']
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        return super().dispatch(request, *args, **kwargs)
    def _parse_date(self, value):
        return _parse_books_date(value)
    def _get_account(self):
        account_id = self.request.GET.get('accountId') or self.request.GET.get('account')
        if not account_id or not str(account_id).isdigit():
            return None
        return tenant_qs(self.request, BookAccount).filter(isDeleted=False, pk=int(account_id)).first()
    def _ensure_permitted(self, account):
        if account is None:
            return False
        if account.type == BookAccount.Type.BANK:
            return self.request.user.has_perm('home.can_view_party_credentials')
        return _has_any_permission(self.request.user, 'home.can_manage_cash_counter', 'home.can_view_party_credentials', 'home.can_manage_books')
    def get_initial_queryset(self):
        account = self._get_account()
        if not self._ensure_permitted(account):
            return BookTransaction.objects.none()
        start_date = self._parse_date(self.request.GET.get('startDate')) or datetime.today().date()
        end_date = self._parse_date(self.request.GET.get('endDate')) or start_date
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        base_qs = tenant_qs(self.request, BookTransaction).filter(isDeleted=False, account=account)
        prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
            in_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.IN)),
            out_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.OUT)),
        )
        opening = float(account.openingBalance or 0.0) + float(prior['in_total'] or 0.0) - float(prior['out_total'] or 0.0)
        self._opening = opening
        qs = (
            base_qs
            .filter(txDateTime__date__range=[start_date, end_date])
            .select_related('party', 'account')
        )
        signed_amount = Case(
            When(direction=BookTransaction.Direction.IN, then=F('amount')),
            default=ExpressionWrapper(-F('amount'), output_field=FloatField()),
            output_field=FloatField(),
        )
        running_delta = Window(expression=Sum(signed_amount), order_by=[F('txDateTime').asc(), F('id').asc()])
        balance_expr = ExpressionWrapper(Value(opening, output_field=FloatField()) + running_delta, output_field=FloatField())
        qs = qs.annotate(
            in_amount=Case(
                When(direction=BookTransaction.Direction.IN, then=F('amount')),
                default=Value(0.0),
                output_field=FloatField(),
            ),
            out_amount=Case(
                When(direction=BookTransaction.Direction.OUT, then=F('amount')),
                default=Value(0.0),
                output_field=FloatField(),
            ),
            balance=balance_expr,
        )
        return qs
    def ordering(self, qs):
        return qs.order_by('txDateTime', 'id')
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(narration__icontains=search)
                | Q(party__name__icontains=search)
                | Q(source__icontains=search)
                | Q(direction__icontains=search)
                | Q(amount__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        can_manage = _has_any_permission(self.request.user, 'home.can_manage_cash_counter', 'home.can_view_party_credentials', 'home.can_manage_books')
        for item in qs:
            if item.source == BookTransaction.Source.MANUAL and can_manage:
                action = f'''
                  <a href="javascript:void(0)" data-inverted="" data-tooltip="Edit" data-position="left center" data-variation="mini"
                     style="font-size:10px;" onclick="openEditTx({item.pk})" class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </a>
                  <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"
                          style="font-size:10px;" onclick="deleteTx({item.pk})" class="ui circular youtube icon button">
                    <i class="trash alternate icon"></i>
                  </button>'''
            elif item.source == BookTransaction.Source.TRANSFER and can_manage:
                action = f'''
                  <button data-inverted="" data-tooltip="Delete Transfer" data-position="left center" data-variation="mini"
                          style="font-size:10px;" onclick="deleteTx({item.pk})" class="ui circular youtube icon button">
                    <i class="trash alternate icon"></i>
                  </button>'''
            else:
                action = '''<div class="ui tiny label">Auto</div>'''
            party_name = item.party.name if item.party_id else '-'
            json_data.append([
                escape(item.txDateTime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.narration or '-'),
                escape(party_name),
                f'₹ {formatINR(format(float(item.in_amount or 0.0), ".0f"))}' if float(item.in_amount or 0.0) else '-',
                f'₹ {formatINR(format(float(item.out_amount or 0.0), ".0f"))}' if float(item.out_amount or 0.0) else '-',
                f'₹ {_format_money_compact(item.balance)}',
                escape(item.get_source_display()),
                action,
            ])
        return json_data
def _parse_ddmmyyyy_api(value, default=None):
    if not value:
        return default
    try:
        return datetime.strptime(value, '%d/%m/%Y').date()
    except Exception:
        pass
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except Exception:
        return default
def _customer_visible_party_ledger_qs_for_request(request, qs):
    approved_collection_ids = tenant_qs(request, Collection).filter(
        isDeleted=False,
        isApproved=True,
    ).values('id')
    approved_counter_collection_ids = tenant_qs(request, CollectionCashCounter).filter(
        isDeleted=False,
        isApproved=True,
    ).values('id')
    return qs.filter(
        Q(source=PartyLedgerEntry.Source.SALES) |
        Q(source=PartyLedgerEntry.Source.CASH_COUNTER) |
        (Q(source=PartyLedgerEntry.Source.COLLECTION) & Q(source_object_id__in=approved_collection_ids)) |
        (Q(source=PartyLedgerEntry.Source.COUNTER_COLLECTION) & Q(source_object_id__in=approved_counter_collection_ids))
    )
def _party_ledger_export_party(request, party_id):
    if request.user.has_perm('home.can_manage_parties') or request.user.has_perm('home.can_view_party_credentials'):
        return tenant_qs(request, Party).filter(isDeleted=False, pk=int(party_id)).only(
            'id', 'name', 'address', 'openingBalance', 'tenant_id'
        ).first()
    return tenant_qs(request, Party).filter(isDeleted=False, userID_id=request.user.pk).only(
        'id', 'name', 'address', 'openingBalance', 'tenant_id'
    ).first()
def _party_ledger_voucher_type(item, is_customer_view=False):
    if item.source == PartyLedgerEntry.Source.SALES:
        return 'Purchase' if is_customer_view else 'Sales'
    if item.source in (PartyLedgerEntry.Source.COLLECTION, PartyLedgerEntry.Source.COUNTER_COLLECTION):
        return 'Payment'
    if item.source == PartyLedgerEntry.Source.CASH_COUNTER:
        return 'Cash Counter'
    if item.source == PartyLedgerEntry.Source.TRANSFER:
        return 'Transfer'
    return 'Manual'
def _party_ledger_voucher_number(item):
    if item.reference:
        return str(item.reference)
    if item.transferId:
        return str(item.transferId)
    if item.source_object_id:
        return str(item.source_object_id)
    return '-'
def _party_ledger_statement_rows(request, party, start_date, end_date, is_customer_view=False):
    base_qs = tenant_qs(request, PartyLedgerEntry).filter(isDeleted=False, party=party)
    if is_customer_view:
        base_qs = _customer_visible_party_ledger_qs_for_request(request, base_qs)
    prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
        debit_total=Sum('debit'),
        credit_total=Sum('credit'),
    )
    opening = float(party.openingBalance or 0.0) + float(prior['debit_total'] or 0.0) - float(prior['credit_total'] or 0.0)
    qs = base_qs.filter(txDateTime__date__range=[start_date, end_date]).order_by('txDateTime', 'id')
    balance = opening
    total_debit = 0.0
    total_credit = 0.0
    rows = []
    for item in qs:
        debit = float(item.debit or 0.0)
        credit = float(item.credit or 0.0)
        balance += debit - credit
        total_debit += debit
        total_credit += credit
        particulars = (item.particulars or '').strip()
        if ' - ' in particulars:
            parts = [p for p in particulars.split(' - ') if p]
            particulars = parts[0] if parts else particulars
        if is_customer_view:
            if particulars.startswith('Sale'):
                particulars = 'Purchase' + particulars[len('Sale'):]
            if particulars.startswith('Counter Collection'):
                particulars = 'Payment' + particulars[len('Counter Collection'):]
            if particulars.startswith('Collection'):
                particulars = 'Payment' + particulars[len('Collection'):]
            particulars = particulars.replace('(Collection)', '(Payment)')
        rows.append({
            'date': item.txDateTime.date(),
            'particulars': particulars or '-',
            'voucher_type': _party_ledger_voucher_type(item, is_customer_view=is_customer_view),
            'voucher_no': _party_ledger_voucher_number(item),
            'debit': debit,
            'credit': credit,
            'balance': balance,
        })
    return {
        'opening': opening,
        'rows': rows,
        'total_debit': total_debit,
        'total_credit': total_credit,
        'closing': balance,
    }
class PartyLedgerListJson(BaseDatatableView):
    order_columns = ['txDateTime', 'particulars', 'txDateTime', 'txDateTime']
    def _customer_visible_party_ledger_qs(self, qs):
        return _customer_visible_party_ledger_qs_for_request(self.request, qs)
    def _resolve_party(self):
        party_id = self.request.GET.get('partyId')
        if not party_id or not str(party_id).isdigit():
            return None
        if self.request.user.has_perm('home.can_manage_parties') or self.request.user.has_perm('home.can_view_party_credentials'):
            return tenant_qs(self.request, Party).filter(isDeleted=False, pk=int(party_id)).only('id', 'openingBalance', 'name').first()
        # Party portal user can only view own ledger.
        return tenant_qs(self.request, Party).filter(isDeleted=False, userID_id=self.request.user.pk).only('id', 'openingBalance', 'name').first()
    def get_initial_queryset(self):
        party = self._resolve_party()
        if not party:
            return tenant_qs(self.request, PartyLedgerEntry).none()
        is_customer_view = TenantMembership.objects.filter(
            user=self.request.user,
            role=TenantMembership.Role.PARTY,
        ).exists()
        today = datetime.today().date()
        start_date = _parse_ddmmyyyy_api(self.request.GET.get('startDate'), today)
        end_date = _parse_ddmmyyyy_api(self.request.GET.get('endDate'), today)
        if end_date and start_date and end_date < start_date:
            start_date, end_date = end_date, start_date
        base_qs = tenant_qs(self.request, PartyLedgerEntry).filter(isDeleted=False, party=party)
        if is_customer_view:
            base_qs = self._customer_visible_party_ledger_qs(base_qs)
        prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
            debit_total=Sum('debit'),
            credit_total=Sum('credit'),
        )
        opening = float(party.openingBalance or 0.0) + float(prior['debit_total'] or 0.0) - float(prior['credit_total'] or 0.0)
        self._opening_balance = opening
        qs = base_qs.filter(txDateTime__date__range=[start_date, end_date])
        signed = ExpressionWrapper(F('debit') - F('credit'), output_field=FloatField())
        # Compute running totals in chronological order, but display latest-first.
        return qs.annotate(
            signed_amount=signed,
            running_total=Window(
                expression=Sum('signed_amount'),
                order_by=[F('txDateTime').asc(), F('id').asc()],
            ),
        ).order_by('-txDateTime', '-id')
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(particulars__icontains=search) |
                Q(reference__icontains=search) |
                Q(source__icontains=search)
            )
        return qs
    def ordering(self, qs):
        return qs
    def prepare_results(self, qs):
        json_data = []
        opening = float(getattr(self, '_opening_balance', 0.0) or 0.0)
        is_customer_view = TenantMembership.objects.filter(
            user=self.request.user,
            role=TenantMembership.Role.PARTY,
        ).exists()
        sales_id_map = {}
        collection_id_map = {}
        counter_collection_id_map = {}
        if is_customer_view:
            sales_source_ids = [item.source_object_id for item in qs if item.source == PartyLedgerEntry.Source.SALES and item.source_object_id]
            collection_source_ids = [item.source_object_id for item in qs if item.source == PartyLedgerEntry.Source.COLLECTION and item.source_object_id]
            counter_collection_source_ids = [item.source_object_id for item in qs if item.source == PartyLedgerEntry.Source.COUNTER_COLLECTION and item.source_object_id]
            if sales_source_ids:
                sales_id_map = {
                    row['id']: (row.get('salesID') or row.get('invoiceNumber') or '')
                    for row in tenant_qs(self.request, Sales).filter(id__in=sales_source_ids).values('id', 'salesID', 'invoiceNumber')
                }
            if collection_source_ids:
                collection_id_map = {
                    row['id']: (row.get('paymentID') or '')
                    for row in tenant_qs(self.request, Collection).filter(id__in=collection_source_ids).values('id', 'paymentID')
                }
            if counter_collection_source_ids:
                counter_collection_id_map = {
                    row['id']: (row.get('paymentID') or '')
                    for row in tenant_qs(self.request, CollectionCashCounter).filter(id__in=counter_collection_source_ids).values('id', 'paymentID')
                }
        for item in qs:
            running = float(item.running_total or 0.0)
            balance = opening + running
            debit = float(item.debit or 0.0)
            credit = float(item.credit or 0.0)
            signed = debit - credit
            # Many legacy/admin flows save collection dates as date-only (00:00 time).
            # For display, prefer the record creation time in that case.
            time_dt = item.txDateTime
            try:
                if (
                    item.source in (PartyLedgerEntry.Source.COLLECTION, PartyLedgerEntry.Source.COUNTER_COLLECTION)
                    and item.txDateTime
                    and item.txDateTime.time().strftime('%H:%M:%S') == '00:00:00'
                    and getattr(item, 'datetime', None)
                ):
                    time_dt = item.datetime
            except Exception:
                time_dt = item.txDateTime
            date_html = '<div class="pl-date">{}</div><div class="pl-time">{}</div>'.format(
                escape(item.txDateTime.strftime('%d-%m-%Y')),
                escape(time_dt.strftime('%I:%M %p')),
            )
            particulars = (item.particulars or '').strip()
            if is_customer_view:
                # Rename: Sale -> Purchase, Collection -> Payment, remove remarks.
                if particulars.startswith('Sale'):
                    particulars = 'Purchase' + particulars[len('Sale'):]
                if particulars.startswith('Counter Collection'):
                    particulars = 'Payment' + particulars[len('Counter Collection'):]
                if particulars.startswith('Collection'):
                    particulars = 'Payment' + particulars[len('Collection'):]
                particulars = particulars.replace('(Collection)', '(Payment)')
                # Drop trailing remark chunks; reference is shown separately below.
                if ' - ' in particulars:
                    parts = [p for p in particulars.split(' - ') if p]
                    particulars = parts[0] if parts else particulars
            ref = (item.reference or '').strip()
            ref_html = ''
            if is_customer_view:
                ref_label = ''
                ref_value = ''
                if item.source == PartyLedgerEntry.Source.SALES:
                    if getattr(item, 'source_object_id', None):
                        ref_label = 'Sale ID'
                        ref_value = sales_id_map.get(item.source_object_id) or ref
                elif item.source in (PartyLedgerEntry.Source.COLLECTION, PartyLedgerEntry.Source.COUNTER_COLLECTION):
                    if item.source == PartyLedgerEntry.Source.COLLECTION:
                        ref_value = collection_id_map.get(item.source_object_id) or ref
                    else:
                        ref_value = counter_collection_id_map.get(item.source_object_id) or ref
                    if ref_value:
                        ref_label = 'Payment ID'
                if ref_label and ref_value:
                    ref_html = '<div class="pl-ref"><span class="pl-ref-chip">{}</span><span class="pl-ref-val">{}</span></div>'.format(
                        escape(ref_label),
                        escape(ref_value),
                    )
            part_html = '<div class="pl-part">{}</div>{}'.format(escape(particulars), ref_html)
            if abs(signed) < 1e-9:
                amt_html = '-'
            elif signed > 0:
                amt_html = '<span class="pl-amt pl-amt-in"><span class="pl-chip pl-chip-dr">DR</span> ₹ {}</span>'.format(
                    formatINR(format(abs(signed), '.2f'))
                )
            else:
                amt_html = '<span class="pl-amt pl-amt-out"><span class="pl-chip pl-chip-cr">CR</span> ₹ {}</span>'.format(
                    formatINR(format(abs(signed), '.2f'))
                )
            json_data.append([
                date_html,
                part_html,
                amt_html,
                formatINR(format(balance, '.2f')),
            ])
        return json_data
def generate_party_ledger_pdf(request, party_id):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.view_party', 'home.can_manage_parties', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    party = _party_ledger_export_party(request, party_id)
    if not party:
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    is_customer_view = TenantMembership.objects.filter(
        user=request.user,
        role=TenantMembership.Role.PARTY,
    ).exists()
    today = datetime.today().date()
    start_date = _parse_ddmmyyyy_api(request.GET.get('startDate'), today)
    end_date = _parse_ddmmyyyy_api(request.GET.get('endDate'), today)
    if end_date and start_date and end_date < start_date:
        start_date, end_date = end_date, start_date
    statement = _party_ledger_statement_rows(
        request,
        party,
        start_date,
        end_date,
        is_customer_view=is_customer_view,
    )
    context = {
        'party': party,
        'start_date': start_date,
        'end_date': end_date,
        'opening': statement['opening'],
        'rows': statement['rows'],
        'total_debit': statement['total_debit'],
        'total_credit': statement['total_credit'],
        'closing': statement['closing'],
        'generated_on': timezone.now(),
    }
    context.update(_pdf_base_context(request, [
        f'Party: {party.name}',
        f'From: {start_date.strftime("%d/%m/%Y") if start_date else "-"}',
        f'To: {end_date.strftime("%d/%m/%Y") if end_date else "-"}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    safe_party_name = (party.name or f'party-{party.pk}').replace('/', '-')
    response['Content-Disposition'] = f'attachment; filename=party-ledger-{safe_party_name}.pdf'
    html = render_to_string("home/party/partyLedgerPDF.html", context)
    base_url = request.build_absolute_uri('/')
    HTML(string=html, base_url=base_url).write_pdf(
        response,
        stylesheets=[CSS(string='@page { size: A4 portrait; margin: 1cm 0.75cm 1.1cm; }')]
    )
    return response


def generate_party_ledger_excel(request, party_id):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.view_party', 'home.can_manage_parties', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    party = _party_ledger_export_party(request, party_id)
    if not party:
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    is_customer_view = TenantMembership.objects.filter(user=request.user, role=TenantMembership.Role.PARTY).exists()
    today = datetime.today().date()
    start_date = _parse_ddmmyyyy_api(request.GET.get('startDate'), today)
    end_date = _parse_ddmmyyyy_api(request.GET.get('endDate'), today)
    if end_date and start_date and end_date < start_date:
        start_date, end_date = end_date, start_date
    statement = _party_ledger_statement_rows(request, party, start_date, end_date, is_customer_view=is_customer_view)
    safe_party_name = (party.name or f'party-{party.pk}').replace('/', '-')
    return _xlsx_report_response(f'party-ledger-{safe_party_name}', [{
        'title': 'Party Ledger',
        'report_title': 'Customer Ledger Statement',
        'report_subtitle': party.name,
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('Party', party.name), ('From', start_date.strftime('%d/%m/%Y')), ('To', end_date.strftime('%d/%m/%Y'))],
        'tables': [
            {'title': 'Summary', 'headers': ['Metric', 'Value'], 'rows': [['Opening', statement['opening']], ['Debit', statement['total_debit']], ['Credit', statement['total_credit']], ['Closing', statement['closing']]]},
            {'title': 'Ledger Detail', 'headers': ['Date', 'Particulars', 'Voucher Type', 'Voucher No', 'Debit', 'Credit', 'Balance'], 'rows': [[row['date'].strftime('%d/%m/%Y') if row['date'] else '-', row['particulars'], row['voucher_type'], row['voucher_no'], row['debit'], row['credit'], row['balance']] for row in statement['rows']]},
        ],
    }])
@cache_page(60 * 5)
def get_sales_dashboard_report_api(request):
    today = datetime.today().date()
    queryset = tenant_qs(request, Sales).filter(isDeleted=False)
    if not _can_filter_all_sales(request.user):
        queryset = queryset.filter(createdBy__user_ID_id=request.user.pk)
    today_qs = queryset.filter(buildDate=today)
    data = {
        'sales_today_total': formatINR(today_qs.aggregate(total=Sum('amount'))['total'] or 0.0),
        'sales_today_count': today_qs.count(),
        'sales_lifetime_total': formatINR(queryset.aggregate(total=Sum('amount'))['total'] or 0.0),
        'sales_record_count': queryset.count(),
    }
    return JsonResponse({'data': data}, safe=False)
def get_orders_dashboard_report_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'forbidden'}, status=403)
    today = datetime.today().date()
    queryset = tenant_qs(request, Order).filter(isDeleted=False)
    if not _can_filter_all_orders(request.user):
        queryset = queryset.filter(createdBy__user_ID_id=request.user.pk)
    today_qs = queryset.filter(buildDate=today)
    data = {
        'orders_today_count': today_qs.count(),
        'orders_pending_count': queryset.filter(status=Order.Status.SUBMITTED).count(),
        'orders_approved_today_count': queryset.filter(status=Order.Status.APPROVED, approvedOn__date=today).count(),
        'orders_record_count': queryset.count(),
    }
    return JsonResponse({'data': data}, safe=False)
@cache_page(60 * 5)
def get_attendance_dashboard_report_api(request):
    today = datetime.today().date()
    monthly_start = today.replace(day=1)
    today_record = tenant_qs(request, Attendance).filter(
        isDeleted=False,
        staffID__user_ID_id=request.user.pk,
        datetime__date=today,
    ).order_by('-datetime').first()
    monthly_qs = tenant_qs(request, Attendance).filter(
        isDeleted=False,
        staffID__user_ID_id=request.user.pk,
        datetime__date__range=[monthly_start, today],
    )
    if today_record:
        if getattr(today_record, 'isLogOut', False):
            today_status = 'Logged Out'
        elif getattr(today_record, 'isLogIn', False):
            today_status = 'Logged In'
        else:
            today_status = 'Pending'
        login_time = today_record.loginDateTime.strftime('%I:%M %p') if today_record.loginDateTime else '--'
        logout_time = today_record.logoutDateTime.strftime('%I:%M %p') if today_record.logoutDateTime else '--'
    else:
        today_status = 'Not Marked'
        login_time = '--'
        logout_time = '--'
    data = {
        'attendance_today_status': today_status,
        'attendance_login_time': login_time,
        'attendance_logout_time': logout_time,
        'attendance_monthly_count': monthly_qs.count(),
    }
    return JsonResponse({'data': data}, safe=False)
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
# ----------- attendance API----------------
@transaction.atomic
def add_attendance_api(request):
    if request.method == 'POST':
        try:
            remark = request.POST.get("remark")
            stat = request.POST.get("stat")
            lat = (request.POST.get("lat") or "").strip()
            lng = (request.POST.get("lng") or "").strip()
            photo = request.FILES.get("photo")
            if not lat or not lng or lat == '0.0' or lng == '0.0':
                return JsonResponse({
                    'message': 'error',
                    'error': 'Live location is required to submit attendance.'
                }, safe=False)
            obj = tenant_qs(request, Attendance).get(datetime__date=datetime.today().date(),
                                         staffID__user_ID__id=request.user.pk)
            if stat == 'Login':
                obj.isLogIn = True
                obj.login_remark = remark
                obj.login_latitude = lat
                obj.login_longitude = lng
                obj.loginDateTime = timezone.now()
                if photo:
                    obj.loginPhoto = photo
                if _tenant_attendance_location_enabled(request):
                    try:
                        obj.login_location = deduct_location_balance(lat, lng)
                    except:
                        pass
                else:
                    obj.login_location = None
            if stat == 'Logout':
                obj.isLogOut = True
                obj.logout_remark = remark
                obj.logout_latitude = lat
                obj.logout_longitude = lng
                obj.logoutDateTime = timezone.now()
                if photo:
                    obj.logoutPhoto = photo
                if _tenant_attendance_location_enabled(request):
                    try:
                        obj.logout_location = deduct_location_balance(lat, lng)
                    except:
                        pass
                else:
                    obj.logout_location = None
            obj.save()
            return JsonResponse({'message': 'success'}, safe=False)
        except Exception as exc:
            return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def _staff_location_can_view_all(request):
    return (
        request.user.has_perm('home.can_view_staff_locations') or
        request.user.has_perm('home.can_view_all_attendance') or
        request.user.has_perm('home.can_view_attendance_reports') or
        request.user.has_perm('home.can_manage_staff') or
        request.user.has_perm('home.can_view_party_credentials')
    )


def _staff_location_staff_for_request(request):
    staff = get_staff_user_for_request(request)
    if staff is None:
        raise PermissionError('Staff profile is required for location tracking.')
    return staff


def _staff_location_decimal(value, label, min_value, max_value):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{label} is invalid.')
    if parsed < Decimal(str(min_value)) or parsed > Decimal(str(max_value)):
        raise ValueError(f'{label} is out of range.')
    return parsed


def _staff_location_float(value):
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _staff_location_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _staff_location_recorded_at(value):
    parsed = parse_datetime(str(value)) if value else None
    if parsed is None:
        return timezone.now()
    if settings.USE_TZ and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    if not settings.USE_TZ and timezone.is_aware(parsed):
        parsed = timezone.make_naive(parsed, timezone.get_current_timezone())
    return parsed


def _staff_location_payload(request):
    if request.content_type and 'application/json' in request.content_type:
        try:
            return json.loads(request.body.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            return {}
    return request.POST


def _save_staff_location_sample(request, staff, sample):
    lat = _staff_location_decimal(sample.get('latitude') or sample.get('lat'), 'Latitude', -90, 90)
    lng = _staff_location_decimal(sample.get('longitude') or sample.get('lng'), 'Longitude', -180, 180)
    client_sample_id = (sample.get('client_sample_id') or sample.get('clientSampleId') or '').strip()[:80] or None
    if client_sample_id:
        existing = tenant_qs(request, StaffLocationLog).filter(
            staff=staff,
            client_sample_id=client_sample_id,
            isDeleted=False,
        ).first()
        if existing:
            return existing, False
    obj = StaffLocationLog(
        staff=staff,
        latitude=lat,
        longitude=lng,
        accuracy=_staff_location_float(sample.get('accuracy')),
        speed=_staff_location_float(sample.get('speed')),
        heading=_staff_location_float(sample.get('heading')),
        battery_percent=_staff_location_float(sample.get('battery_percent') or sample.get('batteryPercent')),
        is_mock_location=_staff_location_bool(sample.get('is_mock_location') or sample.get('isMockLocation')),
        source=(sample.get('source') or 'pwa')[:30],
        client_sample_id=client_sample_id,
        recorded_at=_staff_location_recorded_at(sample.get('recorded_at') or sample.get('recordedAt')),
    )
    assign_tenant(obj, request)
    obj.save()
    return obj, True


@transaction.atomic
def add_staff_location_ping_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'POST is required.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    try:
        staff = _staff_location_staff_for_request(request)
        obj, created = _save_staff_location_sample(request, staff, _staff_location_payload(request))
        return JsonResponse({
            'message': 'success',
            'created': created,
            'id': obj.pk,
            'recorded_at': obj.recorded_at.isoformat(),
        })
    except PermissionError as exc:
        return JsonResponse({'message': 'forbidden', 'error': str(exc)}, status=403)
    except ValueError as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)


@transaction.atomic
def bulk_staff_location_sync_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'POST is required.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    try:
        staff = _staff_location_staff_for_request(request)
        payload = _staff_location_payload(request)
        samples = payload.get('locations') or payload.get('samples') or []
        if not isinstance(samples, list):
            return JsonResponse({'message': 'error', 'error': 'locations must be a list.'}, status=400)
        created = 0
        skipped = 0
        errors = []
        for index, sample in enumerate(samples[:500]):
            try:
                _, was_created = _save_staff_location_sample(request, staff, sample or {})
                created += 1 if was_created else 0
                skipped += 0 if was_created else 1
            except ValueError as exc:
                errors.append({'index': index, 'error': str(exc)})
        return JsonResponse({'message': 'success', 'created': created, 'skipped': skipped, 'errors': errors})
    except PermissionError as exc:
        return JsonResponse({'message': 'forbidden', 'error': str(exc)}, status=403)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)


def staff_location_daily_route_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    staff = get_staff_user_for_request(request)
    can_view_all = _staff_location_can_view_all(request)
    staff_id = request.GET.get('staff_id') or request.GET.get('staffID')
    if staff_id and can_view_all:
        selected_staff = tenant_qs(request, StaffUser).filter(pk=staff_id, isDeleted=False).first()
    else:
        selected_staff = staff
    if selected_staff is None:
        return JsonResponse({'message': 'forbidden', 'error': 'Staff profile is required.'}, status=403)
    route_date = parse_date(request.GET.get('date') or '') or datetime.today().date()
    start_at = datetime.combine(route_date, datetime.min.time())
    if settings.USE_TZ:
        start_at = timezone.make_aware(start_at, timezone.get_current_timezone())
    end_at = start_at + timedelta(days=1)
    logs = tenant_qs(request, StaffLocationLog).filter(
        staff=selected_staff,
        isDeleted=False,
        recorded_at__gte=start_at,
        recorded_at__lt=end_at,
    ).order_by('recorded_at')
    points = [
        {
            'id': log.pk,
            'latitude': float(log.latitude),
            'longitude': float(log.longitude),
            'accuracy': log.accuracy,
            'speed': log.speed,
            'heading': log.heading,
            'battery_percent': log.battery_percent,
            'is_mock_location': log.is_mock_location,
            'recorded_at': (
                timezone.localtime(log.recorded_at).isoformat()
                if settings.USE_TZ else log.recorded_at.isoformat()
            ),
        }
        for log in logs[:2000]
    ]
    return JsonResponse({
        'message': 'success',
        'staff': {'id': selected_staff.pk, 'name': selected_staff.name or '-'},
        'date': route_date.isoformat(),
        'count': len(points),
        'points': points,
    })


def staff_location_staff_options_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _staff_location_can_view_all(request):
        staff = get_staff_user_for_request(request)
        rows = [{'id': staff.pk, 'name': staff.name or '-'}] if staff else []
    else:
        rows = [
            {'id': item.pk, 'name': item.name or '-'}
            for item in tenant_qs(request, StaffUser).filter(isDeleted=False).order_by('name')
        ]
    return JsonResponse({'message': 'success', 'staff': rows})
class StaffAttendanceListJson(BaseDatatableView):
    order_columns = ['datetime', 'loginDateTime', 'loginPhoto', 'login_location', 'login_remark', 'logoutDateTime', 'logoutPhoto', 'logout_location',
                     'logout_remark',
                     ]
    def get_initial_queryset(self):
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            sDate = datetime.strptime(startDateV, '%d/%m/%Y')
            eDate = datetime.strptime(endDateV, '%d/%m/%Y')
            return _attendance_list_base_qs(self.request).filter(
                isDeleted__exact=False,
                datetime__range=(sDate.date(), eDate.date() + timedelta(days=1)),
                staffID__user_ID__id=self.request.user.pk
            )
        except:
            return _attendance_list_base_qs(self.request).filter(
                isDeleted__exact=False,
                datetime__date=datetime.today().date(),
                staffID__user_ID__id=self.request.user.pk
            )
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(login_remark__icontains=search) | Q(login_location__icontains=search) | Q(
                    logout_remark__icontains=search) | Q(
                    logout_location__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if item.isLogIn == True:
                login_time = item.loginDateTime.strftime('%I:%M %p')
                login_remark = item.login_remark
                login_location = item.login_location
                login_photo = '<img src="{}" alt="Login photo" style="width:40px;height:40px;border-radius:10px;object-fit:cover;border:1px solid rgba(15,23,42,0.08);">'.format(escape(_image_url(item.loginPhoto, 'thumbnail'))) if getattr(item, 'loginPhoto', None) else '-'
            else:
                login_time = '-'
                login_remark = '-'
                login_location = '-'
                login_photo = '-'
            if item.isLogOut == True:
                logout_time = item.logoutDateTime.strftime('%I:%M %p')
                logout_remark = item.logout_remark
                logout_location = item.logout_location
                logout_photo = '<img src="{}" alt="Logout photo" style="width:40px;height:40px;border-radius:10px;object-fit:cover;border:1px solid rgba(15,23,42,0.08);">'.format(escape(_image_url(item.logoutPhoto, 'thumbnail'))) if getattr(item, 'logoutPhoto', None) else '-'
            else:
                logout_time = '-'
                logout_remark = '-'
                logout_location = '-'
                logout_photo = '-'
            json_data.append([
                escape(item.datetime.strftime('%d-%m-%Y')),
                login_time,
                login_photo,
                login_location,
                login_remark,
                logout_time,
                logout_photo,
                logout_location,
                logout_remark
            ])
        return json_data
class AdminAttendanceListJson(BaseDatatableView):
    order_columns = ['datetime', 'staffID.name', 'loginDateTime', 'loginPhoto', 'logoutDateTime', 'logoutPhoto', 'login_location', 'login_remark',
                     'logout_location', 'logout_remark',
                     ]
    def get_initial_queryset(self):
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            staffID = self.request.GET.get("staffID")
            sDate = datetime.strptime(startDateV, '%d/%m/%Y')
            eDate = datetime.strptime(endDateV, '%d/%m/%Y')
            if staffID == 'All':
                return _attendance_list_base_qs(self.request).filter(
                    isDeleted__exact=False,
                    datetime__range=(sDate.date(), eDate.date() + timedelta(days=1))
                )
            else:
                return _attendance_list_base_qs(self.request).filter(
                    isDeleted__exact=False,
                    datetime__range=(sDate.date(), eDate.date() + timedelta(days=1)),
                    staffID_id=int(staffID)
                )
        except:
            return _attendance_list_base_qs(self.request).filter(
                isDeleted__exact=False,
                datetime__date=datetime.today().date()
            )
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(login_remark__icontains=search) | Q(login_location__icontains=search) | Q(
                    logout_remark__icontains=search) | Q(
                    logout_location__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if item.isLogIn == True:
                login_time = item.loginDateTime.strftime('%I:%M %p')
                login_remark = item.login_remark
                login_location = item.login_location
                login_photo = '<img src="{}" alt="Login photo" style="width:40px;height:40px;border-radius:10px;object-fit:cover;border:1px solid rgba(15,23,42,0.08);">'.format(escape(_image_url(item.loginPhoto, 'thumbnail'))) if getattr(item, 'loginPhoto', None) else '-'
            else:
                login_time = '-'
                login_remark = '-'
                login_location = '-'
                login_photo = '-'
            if item.isLogOut == True:
                logout_time = item.logoutDateTime.strftime('%I:%M %p')
                logout_remark = item.logout_remark
                logout_location = item.logout_location
                logout_photo = '<img src="{}" alt="Logout photo" style="width:40px;height:40px;border-radius:10px;object-fit:cover;border:1px solid rgba(15,23,42,0.08);">'.format(escape(_image_url(item.logoutPhoto, 'thumbnail'))) if getattr(item, 'logoutPhoto', None) else '-'
            else:
                logout_time = '-'
                logout_remark = '-'
                logout_location = '-'
                logout_photo = '-'
            json_data.append([
                escape(item.datetime.strftime('%d-%m-%Y')),
                escape(item.staffID.name),
                login_time,
                login_photo,
                logout_time,
                logout_photo,
                login_location,
                login_remark,
                logout_location,
                logout_remark
            ])
        return json_data


def export_attendance_list_excel(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        start_date = datetime.strptime(request.GET.get('startDate'), '%d/%m/%Y').date()
        end_date = datetime.strptime(request.GET.get('endDate'), '%d/%m/%Y').date()
    except Exception:
        start_date = end_date = datetime.today().date()
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    qs = _attendance_list_base_qs(request).filter(isDeleted=False, datetime__range=(start_date, end_date + timedelta(days=1)))
    if request.user.has_perm('home.can_view_all_attendance') or request.user.has_perm('home.can_view_attendance_reports') or request.user.has_perm('home.can_view_party_credentials'):
        staff_id = request.GET.get('staffID')
        if staff_id and staff_id != 'All':
            qs = qs.filter(staffID_id=int(staff_id))
            staff = tenant_qs(request, StaffUser).filter(pk=int(staff_id)).first()
            staff_name = staff.name if staff else 'Selected Staff'
        else:
            staff_name = 'All'
    else:
        qs = qs.filter(staffID__user_ID__id=request.user.pk)
        staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk).first()
        staff_name = staff.name if staff else 'My Attendance'
    search = (request.GET.get('search') or '').strip()
    if search:
        qs = qs.filter(
            Q(login_remark__icontains=search) | Q(login_location__icontains=search) |
            Q(logout_remark__icontains=search) | Q(logout_location__icontains=search)
        )
    rows = [[
        item.datetime.strftime('%d/%m/%Y') if item.datetime else '-',
        item.staffID.name if item.staffID_id else '-',
        item.loginDateTime.strftime('%I:%M %p') if item.isLogIn and item.loginDateTime else '-',
        item.logoutDateTime.strftime('%I:%M %p') if item.isLogOut and item.logoutDateTime else '-',
        item.login_location or '-',
        item.login_remark or '-',
        item.logout_location or '-',
        item.logout_remark or '-',
    ] for item in qs.order_by('-datetime')]
    return _xlsx_report_response('attendance-list', [{
        'title': 'Attendance',
        'report_title': 'Attendance List Export',
        'report_subtitle': 'Detailed filtered attendance rows',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('From', start_date.strftime('%d/%m/%Y')), ('To', end_date.strftime('%d/%m/%Y')), ('Staff', staff_name), ('Search', search or '-')],
        'tables': [{'title': 'Attendance', 'headers': ['Date', 'Staff', 'Login Time', 'Logout Time', 'Login Location', 'Login Remark', 'Logout Location', 'Logout Remark'], 'rows': rows}],
    }])
def generate_attendance_pdf_staff_report(request):
    cDate = request.GET.get('cDate')
    eDate = request.GET.get('eDate')
    startDate = datetime.strptime(cDate, '%d/%m/%Y')
    endDate = datetime.strptime(eDate, '%d/%m/%Y')
    col = tenant_qs(request, Attendance).select_related().filter(datetime__range=(
        startDate.date(), endDate.date() + timedelta(days=1)),
        isDeleted__exact=False, staffID__user_ID_id__exact=request.user.pk).order_by(
        'datetime')
    user = tenant_qs(request, StaffUser).get(user_ID__id=request.user.pk)
    context = {
        'startDate': startDate,
        'endDate': endDate,
        'col': col,
        'staffName': user.name,
    }
    context.update(_pdf_base_context(request, [
        f'From: {startDate.strftime("%d/%m/%Y")}',
        f'To: {endDate.strftime("%d/%m/%Y")}',
        f'Staff: {user.name}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = "report.pdf"
    html = render_to_string("home/report/staffAttendanceReportPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A5; margin: .3cm ; }')])
    return response


def generate_attendance_excel_staff_report(request):
    cDate = request.GET.get('cDate')
    eDate = request.GET.get('eDate')
    startDate = datetime.strptime(cDate, '%d/%m/%Y')
    endDate = datetime.strptime(eDate, '%d/%m/%Y')
    col = tenant_qs(request, Attendance).select_related('staffID').filter(
        datetime__range=(startDate.date(), endDate.date() + timedelta(days=1)),
        isDeleted__exact=False,
        staffID__user_ID_id__exact=request.user.pk
    ).order_by('datetime')
    user = tenant_qs(request, StaffUser).get(user_ID__id=request.user.pk)
    rows = [[
        item.datetime.strftime('%d/%m/%Y') if item.datetime else '-',
        item.staffID.name if item.staffID_id else '-',
        item.loginDateTime.strftime('%I:%M %p') if item.isLogIn and item.loginDateTime else '-',
        item.logoutDateTime.strftime('%I:%M %p') if item.isLogOut and item.logoutDateTime else '-',
        item.login_location or '-',
        item.login_remark or '-',
        item.logout_location or '-',
        item.logout_remark or '-',
    ] for item in col]
    return _xlsx_report_response('attendance-staff', [{
        'title': 'Attendance',
        'report_title': 'Attendance Report',
        'report_subtitle': 'My attendance detail',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('From', startDate.strftime('%d/%m/%Y')), ('To', endDate.strftime('%d/%m/%Y')), ('Staff', user.name)],
        'tables': [{'title': 'Attendance Detail', 'headers': ['Date', 'Staff', 'Login', 'Logout', 'Login Location', 'Login Remark', 'Logout Location', 'Logout Remark'], 'rows': rows}],
    }])


def _attendance_month_reference(request):
    raw_date = request.GET.get('cDate') or request.GET.get('monthDate')
    if raw_date:
        try:
            return datetime.strptime(raw_date, '%d/%m/%Y').date()
        except Exception:
            pass
    return datetime.today().date()


def _attendance_monthly_rows(request, staff_queryset, month_start, month_end):
    today = datetime.today().date()
    day_numbers = list(range(1, month_end.day + 1))
    attendance_qs = tenant_qs(request, Attendance).filter(
        isDeleted=False,
        staffID__in=staff_queryset,
        datetime__date__range=[month_start, month_end],
    ).select_related('staffID').order_by('staffID__name', 'datetime')
    attendance_map = defaultdict(set)
    for record in attendance_qs:
        day = None
        if getattr(record, 'loginDateTime', None):
            day = record.loginDateTime.date()
        elif getattr(record, 'datetime', None):
            day = record.datetime.date()
        if day:
            attendance_map[record.staffID_id].add(day)

    rows = []
    for staff in staff_queryset:
        present_days = attendance_map.get(staff.id, set())
        statuses = []
        present_count = 0
        absent_count = 0
        holiday_count = 0
        for day_number in day_numbers:
            current_day = month_start.replace(day=day_number)
            if current_day in present_days:
                status = 'P'
                present_count += 1
            elif current_day > today:
                status = '-'
            elif current_day.weekday() == 6:
                status = 'H'
                holiday_count += 1
            else:
                status = 'A'
                absent_count += 1
            statuses.append(status)
        rows.append({
            'staff': staff,
            'statuses': statuses,
            'present_count': present_count,
            'absent_count': absent_count,
            'holiday_count': holiday_count,
        })
    return day_numbers, rows


def generate_attendance_monthly_pdf_staff_report(request):
    reference_date = _attendance_month_reference(request)
    month_start = reference_date.replace(day=1)
    month_end = reference_date.replace(day=monthrange(reference_date.year, reference_date.month)[1])
    staff = tenant_qs(request, StaffUser).get(user_ID__id=request.user.pk)
    day_numbers, rows = _attendance_monthly_rows(request, [staff], month_start, month_end)
    context = {
        'month_label': f'{month_name[reference_date.month]} {reference_date.year}',
        'month_start': month_start,
        'month_end': month_end,
        'day_numbers': day_numbers,
        'rows': rows,
        'staffName': staff.name,
        'generated_on': timezone.now(),
        'is_single_staff': True,
    }
    context.update(_pdf_base_context(request, [
        f'Month: {month_name[reference_date.month]} {reference_date.year}',
        f'Staff: {staff.name}',
        'Legend: P/A/H/-',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename=attendance-monthly-{reference_date.strftime("%Y-%m")}-{staff.name}.pdf'
    html = render_to_string("home/report/attendanceMonthlyMatrixPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A4 landscape; margin: .55cm; }')])
    return response


def generate_attendance_monthly_excel_staff_report(request):
    reference_date = _attendance_month_reference(request)
    month_start = reference_date.replace(day=1)
    month_end = reference_date.replace(day=monthrange(reference_date.year, reference_date.month)[1])
    staff = tenant_qs(request, StaffUser).get(user_ID__id=request.user.pk)
    day_numbers, rows = _attendance_monthly_rows(request, [staff], month_start, month_end)
    excel_rows = [[staff.name] + row['statuses'] + [row['present_count'], row['absent_count'], row['holiday_count']] for row in rows]
    headers = ['Staff'] + [str(day) for day in day_numbers] + ['P', 'A', 'H']
    return _xlsx_report_response('attendance-monthly', [{
        'title': 'Attendance Monthly',
        'report_title': 'Monthly Attendance Register',
        'report_subtitle': f'{month_name[reference_date.month]} {reference_date.year}',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('Month', f'{month_name[reference_date.month]} {reference_date.year}'), ('Staff', staff.name), ('Legend', 'P/A/H/-')],
        'tables': [{'title': 'Attendance Matrix', 'headers': headers, 'rows': excel_rows}],
    }])


def generate_attendance_pdf_admin_report(request):
    cDate = request.GET.get('cDate')
    eDate = request.GET.get('eDate')
    staffID = request.GET.get('staffID')
    startDate = datetime.strptime(cDate, '%d/%m/%Y')
    endDate = datetime.strptime(eDate, '%d/%m/%Y')
    if staffID == 'All':
        col = tenant_qs(request, Attendance).select_related().filter(datetime__range=(
            startDate.date(), endDate.date() + timedelta(days=1)),
            isDeleted__exact=False).order_by('staffID__name')
        staffName = "All"
    else:
        col = tenant_qs(request, Attendance).select_related().filter(datetime__range=(
            startDate.date(), endDate.date() + timedelta(days=1)),
            isDeleted__exact=False, staffID_id=int(staffID)).order_by('datetime')
        user = tenant_qs(request, StaffUser).get(id=int(staffID))
        staffName = user.name
    context = {
        'startDate': startDate,
        'endDate': endDate,
        'col': col,
        'staffName': staffName,
    }
    context.update(_pdf_base_context(request, [
        f'From: {startDate.strftime("%d/%m/%Y")}',
        f'To: {endDate.strftime("%d/%m/%Y")}',
        f'Staff: {staffName}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = "report.pdf"
    html = render_to_string("home/report/adminAttendanceReportPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A5; margin: .3cm ; }')])
    return response


def generate_attendance_excel_admin_report(request):
    cDate = request.GET.get('cDate')
    eDate = request.GET.get('eDate')
    staffID = request.GET.get('staffID')
    startDate = datetime.strptime(cDate, '%d/%m/%Y')
    endDate = datetime.strptime(eDate, '%d/%m/%Y')
    if staffID == 'All':
        col = tenant_qs(request, Attendance).select_related('staffID').filter(
            datetime__range=(startDate.date(), endDate.date() + timedelta(days=1)),
            isDeleted__exact=False
        ).order_by('staffID__name', 'datetime')
        staffName = 'All'
    else:
        col = tenant_qs(request, Attendance).select_related('staffID').filter(
            datetime__range=(startDate.date(), endDate.date() + timedelta(days=1)),
            isDeleted__exact=False,
            staffID_id=int(staffID)
        ).order_by('datetime')
        staffName = tenant_qs(request, StaffUser).get(id=int(staffID)).name
    rows = [[
        item.datetime.strftime('%d/%m/%Y') if item.datetime else '-',
        item.staffID.name if item.staffID_id else '-',
        item.loginDateTime.strftime('%I:%M %p') if item.isLogIn and item.loginDateTime else '-',
        item.logoutDateTime.strftime('%I:%M %p') if item.isLogOut and item.logoutDateTime else '-',
        item.login_location or '-',
        item.login_remark or '-',
        item.logout_location or '-',
        item.logout_remark or '-',
    ] for item in col]
    return _xlsx_report_response('attendance-report', [{
        'title': 'Attendance',
        'report_title': 'Attendance Report',
        'report_subtitle': 'Admin attendance detail',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('From', startDate.strftime('%d/%m/%Y')), ('To', endDate.strftime('%d/%m/%Y')), ('Staff', staffName)],
        'tables': [{'title': 'Attendance Detail', 'headers': ['Date', 'Staff', 'Login', 'Logout', 'Login Location', 'Login Remark', 'Logout Location', 'Logout Remark'], 'rows': rows}],
    }])


def generate_attendance_monthly_pdf_admin_report(request):
    reference_date = _attendance_month_reference(request)
    month_start = reference_date.replace(day=1)
    month_end = reference_date.replace(day=monthrange(reference_date.year, reference_date.month)[1])
    staffID = request.GET.get('staffID')
    if not staffID or staffID == 'All':
        staff_queryset = list(tenant_qs(request, StaffUser).filter(isDeleted=False).order_by('name'))
        staff_name = 'All Staff'
    else:
        staff = tenant_qs(request, StaffUser).get(id=int(staffID))
        staff_queryset = [staff]
        staff_name = staff.name
    day_numbers, rows = _attendance_monthly_rows(request, staff_queryset, month_start, month_end)
    context = {
        'month_label': f'{month_name[reference_date.month]} {reference_date.year}',
        'month_start': month_start,
        'month_end': month_end,
        'day_numbers': day_numbers,
        'rows': rows,
        'staffName': staff_name,
        'generated_on': timezone.now(),
        'is_single_staff': len(staff_queryset) == 1,
    }
    context.update(_pdf_base_context(request, [
        f'Month: {month_name[reference_date.month]} {reference_date.year}',
        f'Staff: {staff_name}',
        'Legend: P/A/H/-',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename=attendance-monthly-{reference_date.strftime("%Y-%m")}.pdf'
    html = render_to_string("home/report/attendanceMonthlyMatrixPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A4 landscape; margin: .55cm; }')])
    return response


def generate_attendance_monthly_excel_admin_report(request):
    reference_date = _attendance_month_reference(request)
    month_start = reference_date.replace(day=1)
    month_end = reference_date.replace(day=monthrange(reference_date.year, reference_date.month)[1])
    staffID = request.GET.get('staffID')
    if not staffID or staffID == 'All':
        staff_queryset = list(tenant_qs(request, StaffUser).filter(isDeleted=False).order_by('name'))
        staff_name = 'All Staff'
    else:
        staff = tenant_qs(request, StaffUser).get(id=int(staffID))
        staff_queryset = [staff]
        staff_name = staff.name
    day_numbers, rows = _attendance_monthly_rows(request, staff_queryset, month_start, month_end)
    excel_rows = [[row['staff'].name] + row['statuses'] + [row['present_count'], row['absent_count'], row['holiday_count']] for row in rows]
    headers = ['Staff'] + [str(day) for day in day_numbers] + ['P', 'A', 'H']
    return _xlsx_report_response('attendance-monthly', [{
        'title': 'Attendance Monthly',
        'report_title': 'Monthly Attendance Register',
        'report_subtitle': f'{month_name[reference_date.month]} {reference_date.year}',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('Month', f'{month_name[reference_date.month]} {reference_date.year}'), ('Staff', staff_name), ('Legend', 'P/A/H/-')],
        'tables': [{'title': 'Attendance Matrix', 'headers': headers, 'rows': excel_rows}],
    }])
# -------------------Message List---------------
class MessageListJson(BaseDatatableView):
    order_columns = ['messageTo', 'phone', 'message', 'datetime', 'status']
    def get_initial_queryset(self):
        # if 'Admin' in group_names:
        return tenant_qs(self.request, WhatsappMessageStatus).filter(isDeleted__exact=False).only(
            'id', 'messageTo', 'phone', 'message', 'datetime', 'status'
        )
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(messageTo__icontains=search) | Q(phone__icontains=search) | Q(message__icontains=search)
                | Q(status__icontains=search) | Q(datetime__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if item.status == 'Success':
                status = '''<div class="ui tiny green label">
              Success
            </div>'''
            else:
                status = '''<a class="ui tiny red label" onclick="ResendMsg('{}')">
                             Fail
                           </a>'''.format(item.pk)
            json_data.append([
                escape(item.messageTo),
                escape(item.phone),
                escape(item.message),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                status,
            ])
        return json_data
class AuditLogListJson(BaseDatatableView):
    order_columns = ['datetime', 'actor__username', 'content_type__model', 'action', 'object_repr', 'object_id']
    def get_initial_queryset(self):
        if not request_user_has_audit_access(self.request.user):
            return AuditLog.objects.none()
        return _tenant_audit_log_queryset(self.request).only(
            'id', 'datetime', 'action', 'object_id', 'object_repr',
            'changes', 'snapshot', 'actor__username', 'content_type__model',
        )
    def filter_queryset(self, qs):
        return _apply_audit_log_filters(self.request, qs)
    def prepare_results(self, qs):
        data = []
        for item in qs:
            actor = escape(item.actor.username) if item.actor_id else 'System'
            model_name = escape(item.content_type.model.replace('_', ' ').title())
            action_label = escape(item.get_action_display())
            changes_count = len(item.changes or {})
            detail_btn = (
                '<button class="ui tiny teal basic icon button audit-detail-trigger" '
                'type="button" title="View detail" data-tooltip="View detail" data-position="left center" '
                'onclick="openAuditDetail(\'{}\', {})">'
                '<i class="eye icon"></i></button>'
            ).format(reverse('homeApp:get_audit_log_detail_api'), item.pk)
            data.append([
                escape(_format_audit_datetime(item.datetime)),
                actor,
                model_name,
                action_label,
                escape(item.object_repr),
                escape(str(item.object_id)),
                escape(str(changes_count)),
                detail_btn,
            ])
        return data
def request_user_has_audit_access(user):
    return _has_any_permission(user, 'home.can_view_audit_logs', 'home.can_view_party_credentials')
def get_audit_log_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not request_user_has_audit_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    audit_id = request.GET.get('id')
    if not str(audit_id).isdigit():
        return JsonResponse({'message': 'invalid'}, status=400, safe=False)
    obj = get_object_or_404(_tenant_audit_log_queryset(request), pk=int(audit_id))
    return JsonResponse({'data': _audit_log_detail_payload(obj)}, safe=False)
def export_audit_log_csv(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.can_export_audit_logs', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    qs = _apply_audit_log_filters(request, _tenant_audit_log_queryset(request)).order_by('-datetime')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="audit-log-report.csv"'
    writer = csv.writer(response)
    writer.writerow(['Datetime', 'Actor', 'Model', 'Action', 'Object ID', 'Object', 'Changes', 'Snapshot'])
    for item in qs:
        payload = _audit_log_detail_payload(item)
        writer.writerow([
            payload['datetime'],
            payload['actor'],
            payload['model'],
            payload['action'],
            payload['object_id'],
            payload['object_repr'],
            payload['changes_pretty'],
            payload['snapshot_pretty'],
        ])
    return response


def export_audit_log_excel(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.can_export_audit_logs', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    qs = _apply_audit_log_filters(request, _tenant_audit_log_queryset(request)).order_by('-datetime')
    rows = []
    for item in qs:
        payload = _audit_log_detail_payload(item)
        rows.append([
            payload['datetime'],
            payload['actor'],
            payload['model'],
            payload['action'],
            payload['object_id'],
            payload['object_repr'],
            payload['changes_pretty'],
            payload['snapshot_pretty'],
        ])
    return _xlsx_report_response('audit-log-report', [{
        'title': 'Audit Log',
        'report_title': 'Audit Log Report',
        'report_subtitle': 'Filtered audit history',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('Generated By', _pdf_base_context(request).get('pdf_generated_by'))],
        'tables': [{'title': 'Audit Entries', 'headers': ['Datetime', 'Actor', 'Model', 'Action', 'Object ID', 'Object', 'Changes', 'Snapshot'], 'rows': rows}],
    }])
# ----------------------Orders-----------------
@transaction.atomic
def add_order_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _can_add_order(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        build_date = request.POST.get('buildDate')
        party_id = request.POST.get('party')
        order_text = request.POST.get('orderText')
        remark = request.POST.get('remark')
        assigned_to = request.POST.get('assignedTo')
        lat = request.POST.get('lat')
        lng = request.POST.get('lng')
        loc = request.POST.get('loc')
        obj = Order()
        assign_tenant(obj, request)
        obj.orderText = order_text
        obj.remark = remark
        if _can_set_order_date(request.user) and build_date:
            obj.buildDate = datetime.strptime(build_date, '%d/%m/%Y').date()
        else:
            obj.buildDate = datetime.today().date()
        if party_id:
            obj.partyID_id = int(party_id)
        if lat:
            obj.latitude = lat
        if lng:
            obj.longitude = lng
        if loc and loc != 'N/A':
            obj.location = loc
        staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk, isDeleted=False).only('id').first()
        if staff:
            obj.createdBy_id = staff.pk
            obj.assignedTo_id = staff.pk
        if assigned_to:
            obj.assignedTo_id = int(assigned_to)
        # If staff enters text, consider it submitted; otherwise draft.
        obj.status = Order.Status.SUBMITTED if (order_text or '').strip() else Order.Status.DRAFT
        obj.save()
        # Auto-generate order number like Sales.paymentID.
        obj.orderNumber = _next_tenant_sequence_value(request, TenantSequence.Scope.ORDER)
        obj.save(update_fields=['orderNumber', 'lastUpdatedOn'])
        _create_order_status_event(request, obj, None, obj.status, 'Order created')
        for f in request.FILES.getlist('images'):
            att = OrderAttachment(order=obj)
            assign_tenant(att, request)
            att.image = f
            att.save()
        return JsonResponse({'message': 'success', 'order_id': obj.pk}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def update_order_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = request.POST.get('ID')
        obj = tenant_qs(request, Order).select_related('createdBy').get(pk=int(order_id))
        if not _can_edit_order_record(request.user, obj):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        build_date = request.POST.get('buildDate')
        party_id = request.POST.get('party')
        order_text = request.POST.get('orderText')
        remark = request.POST.get('remark')
        status = request.POST.get('status')
        previous_status = _normalize_order_status(obj.status)
        lat = request.POST.get('lat')
        lng = request.POST.get('lng')
        loc = request.POST.get('loc')
        assigned_to = request.POST.get('assignedTo')
        if order_text is not None:
            obj.orderText = order_text
        if remark is not None:
            obj.remark = remark
        if _can_set_order_date(request.user) and build_date:
            obj.buildDate = datetime.strptime(build_date, '%d/%m/%Y').date()
        if party_id:
            obj.partyID_id = int(party_id)
        if assigned_to:
            obj.assignedTo_id = int(assigned_to)
        elif assigned_to == '':
            obj.assignedTo = None
        if status in dict(Order.Status.choices):
            _set_order_tracking_timestamps(
                obj,
                status,
                tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk, isDeleted=False).only('id').first(),
            )
        if lat:
            obj.latitude = lat
        if lng:
            obj.longitude = lng
        if loc is not None and loc != 'N/A':
            obj.location = loc
        obj.save()
        if _normalize_order_status(obj.status) != previous_status:
            _create_order_status_event(request, obj, previous_status, obj.status, request.POST.get('statusNote', ''))
        for f in request.FILES.getlist('images'):
            att = OrderAttachment(order=obj)
            assign_tenant(att, request)
            att.image = f
            att.save()
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def delete_order(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = request.POST.get('userID')
        obj = tenant_qs(request, Order).get(pk=int(order_id))
        if not _can_delete_order_record(request.user, obj):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        obj.isDeleted = True
        obj.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def approve_order(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _can_approve_orders(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        order_id = request.POST.get('userID')
        obj = tenant_qs(request, Order).get(pk=int(order_id))
        staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk, isDeleted=False).only('id').first()
        previous_status = _normalize_order_status(obj.status)
        _set_order_tracking_timestamps(obj, Order.Status.APPROVED, staff)
        obj.save(update_fields=['status', 'approvedOn', 'approvedBy', 'lastUpdatedOn'])
        _create_order_status_event(request, obj, previous_status, obj.status, 'Order confirmed')
        log_audit_event(
            obj,
            AuditLog.Action.APPROVE,
            actor=request.user,
            changes={
                'status': {'from': previous_status, 'to': Order.Status.APPROVED},
                'approvedBy_id': {'from': None, 'to': getattr(staff, 'pk', None)},
                'approvedOn': {'from': None, 'to': obj.approvedOn.isoformat() if obj.approvedOn else None},
            },
            snapshot=snapshot_instance(obj),
        )
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def transition_order_status_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = request.POST.get('orderID')
        target_status = _normalize_order_status(request.POST.get('status'))
        note = request.POST.get('note', '')
        obj = tenant_qs(request, Order).get(pk=int(order_id), isDeleted=False)
        if not (_can_approve_orders(request.user) or _can_edit_order_record(request.user, obj)):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        current_status = _normalize_order_status(obj.status)
        if target_status not in _order_allowed_next_statuses(obj):
            return JsonResponse({'message': 'invalid_transition'}, status=400, safe=False)
        if target_status == Order.Status.DELIVERED and obj.partyID_id:
            otp_code = (request.POST.get('otpCode') or '').strip()
            if not (otp_code.isdigit() and len(otp_code) == 6):
                return JsonResponse({'message': 'otp_required'}, status=400, safe=False)
            payload = get_order_delivery_otp_payload(obj.partyID.tenant_id, obj.partyID_id) or {}
            if not payload or str(payload.get('otp')) != otp_code:
                return JsonResponse({'message': 'otp_invalid'}, status=400, safe=False)
        staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk, isDeleted=False).only('id').first()
        _set_order_tracking_timestamps(obj, target_status, staff)
        update_fields = ['status', 'lastUpdatedOn']
        if target_status == Order.Status.APPROVED:
            update_fields.extend(['approvedOn', 'approvedBy'])
        elif target_status == Order.Status.PACKED:
            update_fields.append('packedOn')
        elif target_status == Order.Status.OUT_FOR_DELIVERY:
            update_fields.append('outForDeliveryOn')
        elif target_status == Order.Status.DELIVERED:
            obj.deliveryOtpVerified = bool(obj.partyID_id)
            obj.deliveryOtpVerifiedOn = timezone.now() if obj.partyID_id else None
            obj.deliveryOtpVerifiedBy = staff
            update_fields.extend(['deliveredOn', 'deliveryOtpVerified', 'deliveryOtpVerifiedOn', 'deliveryOtpVerifiedBy'])
        elif target_status == Order.Status.CANCELLED:
            update_fields.append('cancelledOn')
        elif target_status == Order.Status.RETURNED:
            update_fields.append('returnedOn')
        obj.save(update_fields=update_fields)
        if target_status == Order.Status.DELIVERED and obj.partyID_id:
            try:
                clear_order_delivery_otp(obj.partyID.tenant_id, obj.partyID_id)
                set_order_delivery_status(
                    obj.partyID.tenant_id,
                    obj.partyID_id,
                    'delivered',
                    meta={
                        'orderID': obj.pk,
                        'orderNumber': obj.orderNumber or str(obj.pk),
                        'verifiedOn': timezone.now().isoformat(),
                    },
                )
            except Exception:
                pass
        _create_order_status_event(request, obj, current_status, target_status, note)
        return JsonResponse({'message': 'success', 'statusLabel': _order_status_label(obj.status)}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def delete_order_attachment(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        att_id = request.POST.get('userID')
        att = tenant_qs(request, OrderAttachment).select_related('order').get(pk=int(att_id))
        if not _can_edit_order_record(request.user, att.order):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        att.isDeleted = True
        att.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
class OrdersByAdminListJson(BaseDatatableView):
    # Must match the number of columns returned in prepare_results.
    order_columns = [
        'orderNumber', 'partyID__name', 'buildDate', 'status',
        'approvedBy__name', 'approvedOn',
        'createdBy__name', 'lastUpdatedOn', 'datetime',
        'id', 'id'
    ]
    def get_initial_queryset(self):
        queryset = _orders_list_base_qs(self.request).filter(isDeleted=False)
        if not _can_filter_all_orders(self.request.user):
            queryset = queryset.filter(createdBy__user_ID_id=self.request.user.pk)
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            staffID = self.request.GET.get("staffID")
            if startDateV and endDateV:
                sDate = datetime.strptime(startDateV, '%d/%m/%Y').date()
                eDate = datetime.strptime(endDateV, '%d/%m/%Y').date()
                queryset = queryset.filter(buildDate__range=[sDate, eDate])
            if _can_filter_all_orders(self.request.user) and staffID and staffID != 'All':
                queryset = queryset.filter(createdBy_id=int(staffID))
            return queryset
        except Exception:
            return queryset
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(orderNumber__icontains=search)
                | Q(partyID__name__icontains=search)
                | Q(status__icontains=search)
                | Q(remark__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        can_approve = _can_approve_orders(self.request.user)
        can_edit = _can_edit_order(self.request.user)
        for item in qs:
            normalized_status = _normalize_order_status(getattr(item, 'status', None))
            can_confirm = normalized_status == Order.Status.SUBMITTED
            editable_statuses = {Order.Status.DRAFT, Order.Status.SUBMITTED}
            action_parts = []
            if can_approve and can_confirm:
                action_parts.append(
                    '''<button data-inverted="" data-tooltip="Confirm" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="showApproveOrderModal('{}')" class="ui circular purple icon button"><i class="check circle icon"></i></button>'''.format(item.pk)
                )
            # Always provide a detail page.
            action_parts.append(
                '''<a href="/order_detail/{}/" data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular teal icon button"><i class="eye icon"></i></a>'''.format(item.pk)
            )
            # Allow editing for users with edit capability.
            if can_edit and normalized_status in editable_statuses:
                action_parts.append(
                    '''<a href="/edit_order/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular green icon button"><i class="pen icon"></i></a>'''.format(item.pk)
                )
            action_parts.append(
                '''<button data-inverted="" data-tooltip="Download PDF" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="downloadOrderPdf('{}')" class="ui circular blue icon button"><i class="file pdf outline icon"></i></button>'''.format(item.pk)
            )
            if _can_delete_order_record(self.request.user, item):
                action_parts.append(
                    '''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk)
                )
            action = ''.join(action_parts) or '<div class="ui tiny grey label">View Only</div>'
            party = item.partyID.name if getattr(item, 'partyID', None) else '-'
            created_by = item.createdBy.name if getattr(item, 'createdBy', None) else '-'
            approved_by = item.approvedBy.name if getattr(item, 'approvedBy', None) else '-'
            approved_on = item.approvedOn.strftime('%d-%m-%Y %I:%M %p') if getattr(item, 'approvedOn', None) else '-'
            status_label = _order_status_label(item.status)
            att_count = tenant_qs(self.request, OrderAttachment).filter(isDeleted=False, order_id=item.pk).count()
            json_data.append([
                escape(_order_public_id(item)),
                escape(party),
                escape(item.buildDate.strftime('%d-%m-%Y') if item.buildDate else '-'),
                status_label,
                escape(approved_by),
                escape(approved_on),
                escape(created_by),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(str(att_count)),
                action,
            ])
        return json_data
class OrdersByStaffListJson(BaseDatatableView):
    # Must match the number of columns returned in prepare_results.
    order_columns = [
        'orderNumber', 'partyID__name', 'buildDate', 'status',
        'lastUpdatedOn', 'datetime', 'id', 'id'
    ]
    def get_initial_queryset(self):
        queryset = _orders_list_base_qs(self.request).filter(isDeleted=False)
        if not _can_filter_all_orders(self.request.user):
            queryset = queryset.filter(createdBy__user_ID_id=self.request.user.pk)
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            if startDateV and endDateV:
                sDate = datetime.strptime(startDateV, '%d/%m/%Y').date()
                eDate = datetime.strptime(endDateV, '%d/%m/%Y').date()
                queryset = queryset.filter(buildDate__range=[sDate, eDate])
            return queryset
        except Exception:
            return queryset
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(orderNumber__icontains=search)
                | Q(partyID__name__icontains=search)
                | Q(status__icontains=search)
                | Q(remark__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        can_edit = _can_edit_order(self.request.user)
        for item in qs:
            normalized_status = _normalize_order_status(getattr(item, 'status', None))
            action_parts = []
            action_parts.append(
                '''<a href="/order_detail/{}/" data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular teal icon button"><i class="eye icon"></i></a>'''.format(item.pk)
            )
            # Staff can edit only before approval; the edit page locks after approval anyway.
            if can_edit and normalized_status in {Order.Status.DRAFT, Order.Status.SUBMITTED}:
                action_parts.append(
                    '''<a href="/edit_order/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular green icon button"><i class="pen icon"></i></a>'''.format(item.pk)
                )
            action_parts.append(
                '''<button data-inverted="" data-tooltip="Download PDF" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="downloadOrderPdf('{}')" class="ui circular blue icon button"><i class="file pdf outline icon"></i></button>'''.format(item.pk)
            )
            if _can_delete_order_record(self.request.user, item):
                action_parts.append(
                    '''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk)
                )
            action = ''.join(action_parts) or '<div class="ui tiny grey label">View Only</div>'
            party = item.partyID.name if getattr(item, 'partyID', None) else '-'
            status_label = _order_status_label(item.status)
            att_count = tenant_qs(self.request, OrderAttachment).filter(isDeleted=False, order_id=item.pk).count()
            json_data.append([
                escape(_order_public_id(item)),
                escape(party),
                escape(item.buildDate.strftime('%d-%m-%Y') if item.buildDate else '-'),
                status_label,
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(str(att_count)),
                action,
            ])
        return json_data


def export_orders_list_excel(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    qs = _orders_list_base_qs(request).filter(isDeleted=False)
    if not _can_filter_all_orders(request.user):
        qs = qs.filter(createdBy__user_ID_id=request.user.pk)
        staff_name = 'My Orders'
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
            Q(orderNumber__icontains=search) | Q(partyID__name__icontains=search) |
            Q(status__icontains=search) | Q(remark__icontains=search)
        )
    rows = []
    for item in qs.order_by('-datetime'):
        att_count = tenant_qs(request, OrderAttachment).filter(isDeleted=False, order_id=item.pk).count()
        rows.append([
            item.orderNumber or str(item.pk),
            item.partyID.name if item.partyID_id else '-',
            item.buildDate.strftime('%d/%m/%Y') if item.buildDate else '-',
            item.get_status_display(),
            item.approvedBy.name if item.approvedBy_id else '-',
            item.approvedOn.strftime('%d/%m/%Y %I:%M %p') if item.approvedOn else '-',
            item.createdBy.name if item.createdBy_id else '-',
            item.lastUpdatedOn.strftime('%d/%m/%Y %I:%M %p') if item.lastUpdatedOn else '-',
            item.datetime.strftime('%d/%m/%Y %I:%M %p') if item.datetime else '-',
            att_count,
            item.remark or '-',
        ])
    return _xlsx_report_response('orders-list', [{
        'title': 'Orders',
        'report_title': 'Order List Export',
        'report_subtitle': 'Detailed filtered order rows',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('From', start_date.strftime('%d/%m/%Y')), ('To', end_date.strftime('%d/%m/%Y')), ('Staff', staff_name), ('Search', search or '-')],
        'tables': [{'title': 'Orders', 'headers': ['Order No', 'Party', 'Date', 'Status', 'Approved By', 'Approved On', 'Created By', 'Updated', 'Created', 'Images', 'Remark'], 'rows': rows}],
    }])
def export_orders_list_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    qs = _orders_list_base_qs(request).filter(isDeleted=False)
    if not _can_filter_all_orders(request.user):
        qs = qs.filter(createdBy__user_ID_id=request.user.pk)
        staff_name = 'My Orders'
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
            Q(orderNumber__icontains=search) | Q(partyID__name__icontains=search) |
            Q(status__icontains=search) | Q(remark__icontains=search)
        )
    rows = []
    for item in qs.order_by('-datetime'):
        rows.append({
            'order_number': _order_public_id(item),
            'party_name': item.partyID.name if item.partyID_id else '-',
            'build_date': item.buildDate.strftime('%d/%m/%Y') if item.buildDate else '-',
            'status': item.get_status_display(),
            'approved_by': item.approvedBy.name if item.approvedBy_id else '-',
            'approved_on': item.approvedOn.strftime('%d/%m/%Y %I:%M %p') if item.approvedOn else '-',
            'created_by': item.createdBy.name if item.createdBy_id else '-',
            'updated_on': item.lastUpdatedOn.strftime('%d/%m/%Y %I:%M %p') if item.lastUpdatedOn else '-',
            'created_on': item.datetime.strftime('%d/%m/%Y %I:%M %p') if item.datetime else '-',
            'remark': item.remark or '-',
            'images_count': tenant_qs(request, OrderAttachment).filter(isDeleted=False, order_id=item.pk).count(),
        })
    context = {
        'start_date': start_date,
        'end_date': end_date,
        'staff_name': staff_name,
        'search': search or '-',
        'rows': rows,
        'total_orders': len(rows),
    }
    context.update(_pdf_base_context(request, [
        f'From: {start_date.strftime("%d/%m/%Y")}',
        f'To: {end_date.strftime("%d/%m/%Y")}',
        f'Staff: {staff_name}',
    ]))
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename=orders-list.pdf'
    html = render_to_string('home/report/ordersListPDF.html', context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A4 landscape; margin: 0.7cm; }')])
    return response
def generate_order_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.view_order', 'home.can_manage_orders', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    order_id = request.GET.get('id')
    if not order_id:
        return JsonResponse({'message': 'error'}, status=400, safe=False)
    order = get_object_or_404(
        tenant_qs(request, Order).select_related('partyID', 'createdBy', 'approvedBy'),
        pk=int(order_id),
    )
    # Staff can only export their own orders unless admin-like.
    if not _can_filter_all_orders(request.user):
        if getattr(order.createdBy, 'user_ID_id', None) != request.user.pk:
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    attachments = tenant_qs(request, OrderAttachment).filter(isDeleted=False, order=order).order_by('id')
    location = order.location or ('{},{}'.format(order.latitude, order.longitude) if order.latitude and order.longitude else '-')
    context = {
        'order': order,
        'attachments': attachments,
        'location': location,
    }
    context.update(_pdf_base_context(request, [
        f'Order: {order.orderNumber or order.pk}',
        f'Party: {getattr(getattr(order, "partyID", None), "name", "-")}',
        f'Status: {order.get_status_display()}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f"attachment; filename=order-{order.orderNumber or order.pk}.pdf"
    html = render_to_string("home/orders/orderPDF.html", context)
    # base_url allows WeasyPrint to resolve media/static URLs.
    base_url = request.build_absolute_uri('/')
    HTML(string=html, base_url=base_url).write_pdf(response, stylesheets=[CSS(string='@page { size: A4; margin: 1cm; }')])
    return response
# ----------------------Sales-----------------
@transaction.atomic
def add_inventory_item_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        item = InventoryItem()
        assign_tenant(item, request)
        _apply_inventory_item_payload(request, item)
        item.save()
        rebuild_inventory_stock(item.tenant, [item.pk])
        return JsonResponse({'message': 'success', 'itemId': item.pk}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_item_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        item = tenant_qs(request, InventoryItem).get(pk=int(request.POST.get('itemId')), isDeleted=False)
        _apply_inventory_item_payload(request, item)
        item.save()
        rebuild_inventory_stock(item.tenant, [item.pk])
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_item_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        item = tenant_qs(request, InventoryItem).get(pk=int(request.POST.get('itemId')), isDeleted=False)
        item.isDeleted = True
        item.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        InventoryStock.objects.filter(tenant=item.tenant, item=item).update(isDeleted=True)
        InventoryLedgerEntry.objects.filter(tenant=item.tenant, item=item).update(isDeleted=True)
        _bump_inventory_list_cache_version('items', tenant=item.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def get_inventory_snapshot_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    tenant = get_current_tenant(request)
    return JsonResponse({'message': 'success', 'data': stock_snapshot_for_tenant(tenant)}, safe=False)


def list_inventory_items_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    tenant = get_current_tenant(request)
    cache_key = _inventory_list_cache_key('items', 'all', request_or_user=request, tenant=tenant)
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse({'message': 'success', 'data': cached}, safe=False)
    non_gst_enabled = bool(getattr(tenant, 'nonGstShadowEnabled', False))
    items = tenant_qs(request, InventoryItem).filter(isDeleted=False).order_by('name')
    payload = []
    stocks = defaultdict(lambda: {'gst': 0.0, 'non_gst': 0.0})
    for row in InventoryStock.objects.filter(tenant=tenant, isDeleted=False, item__isDeleted=False):
        stocks[row.item_id][row.stockBucket] = float(row.quantityOnHand or 0.0)
    today = _safe_local_date()
    expiry_cutoff = today + timedelta(days=30)
    batch_summary = defaultdict(lambda: {'count': 0, 'expired': 0, 'expiring': 0, 'nearest': None})
    for batch in InventoryBatch.objects.filter(tenant=tenant, isDeleted=False, item__isDeleted=False, quantityOnHand__gt=0).only('item_id', 'expiryDate'):
        summary = batch_summary[batch.item_id]
        summary['count'] += 1
        if batch.expiryDate:
            if summary['nearest'] is None or batch.expiryDate < summary['nearest']:
                summary['nearest'] = batch.expiryDate
            if batch.expiryDate < today:
                summary['expired'] += 1
            elif batch.expiryDate <= expiry_cutoff:
                summary['expiring'] += 1
    for item in items:
        item_batch_summary = batch_summary[item.pk]
        payload.append({
            'ID': item.pk,
            'Name': item.name,
            'SKU': item.sku or '',
            'Barcode': item.barcode or '',
            'CategoryID': item.category_id,
            'CategoryName': item.categoryName or '',
            'BrandName': item.brandName or '',
            'UnitID': item.unit_id,
            'UnitName': item.unitName or 'pcs',
            'UnitAllowsDecimal': bool(getattr(item.unit, 'allowsDecimal', False)) if item.unit_id else True,
            'TaxProfileID': item.taxProfile_id,
            'HSN': item.hsnCode or '',
            'Description': item.description or '',
            'GSTRate': float(item.gstRate or 0.0),
            'DefaultStockBucket': item.defaultStockBucket if non_gst_enabled else InventoryItem.StockBucket.GST,
            'DefaultPurchasePrice': float(item.defaultPurchasePrice or 0.0),
            'DefaultSalesPrice': float(item.defaultSalesPrice or 0.0),
            'MRP': float(item.mrp or 0.0),
            'MinimumStockLevel': float(item.minimumStockLevel or 0.0),
            'ReorderLevel': float(item.reorderLevel or 0.0),
            'RequiresBatchTracking': bool(item.requiresBatchTracking),
            'IsSaleBlocked': bool(item.isSaleBlocked),
            'BatchCount': item_batch_summary['count'],
            'ExpiredBatchCount': item_batch_summary['expired'],
            'ExpiringBatchCount': item_batch_summary['expiring'],
            'NearestExpiryDate': item_batch_summary['nearest'].strftime('%d/%m/%Y') if item_batch_summary['nearest'] else '',
            'GSTStock': stocks[item.pk]['gst'],
            'NonGSTStock': stocks[item.pk]['non_gst'] if non_gst_enabled else 0.0,
            'IsLowStock': (stocks[item.pk]['gst'] + stocks[item.pk]['non_gst']) <= float(item.minimumStockLevel or 0.0),
        })
    cache.set(cache_key, payload, timeout=60 * 10)
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def list_inventory_batches_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_has_any_permission(
        request,
        'home.view_inventoryitem',
        'home.can_manage_inventory',
        'home.can_view_party_credentials',
    ):
        return JsonResponse({'message': 'forbidden'}, status=403)
    item = _parse_inventory_item(request, request.GET.get('itemId'))
    stock_bucket = _normalize_stock_bucket(request, request.GET.get('stockBucket'), item)
    warehouse = _parse_inventory_warehouse(request, request.GET.get('warehouseId'))
    today = _safe_local_date()
    stock_rows = _available_batch_stock_queryset(
        item=item,
        stock_bucket=stock_bucket,
        warehouse=warehouse,
        today=today,
    ).select_related('batch', 'warehouse')
    payload = []
    for row in stock_rows:
        batch = row.batch
        payload.append({
            'ID': batch.pk,
            'BatchNumber': batch.batchNumber or '',
            'ManufactureDate': batch.manufactureDate.strftime('%d/%m/%Y') if batch.manufactureDate else '',
            'ExpiryDate': batch.expiryDate.strftime('%d/%m/%Y') if batch.expiryDate else '',
            'MRP': float(batch.mrp or 0.0),
            'PurchaseRate': float(batch.purchaseRate or 0.0),
            'QuantityOnHand': float(row.quantityOnHand or 0.0),
            'WarehouseID': row.warehouse_id,
            'WarehouseName': row.warehouse.name if row.warehouse_id else '',
        })
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


@transaction.atomic
def add_inventory_category_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        category = InventoryCategory()
        assign_tenant(category, request)
        _apply_inventory_category_payload(request, category)
        category.save()
        _bump_inventory_list_cache_version('categories', tenant=category.tenant)
        return JsonResponse({'message': 'success', 'categoryId': category.pk}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_category_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        category = tenant_qs(request, InventoryCategory).get(pk=int(request.POST.get('categoryId')), isDeleted=False)
        _apply_inventory_category_payload(request, category)
        category.save()
        InventoryItem.objects.filter(tenant=category.tenant, category=category, isDeleted=False).update(categoryName=category.name)
        _bump_inventory_list_cache_version('categories', tenant=category.tenant)
        _bump_inventory_list_cache_version('items', tenant=category.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_category_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        category = tenant_qs(request, InventoryCategory).get(pk=int(request.POST.get('categoryId')), isDeleted=False)
        InventoryItem.objects.filter(tenant=category.tenant, category=category, isDeleted=False).update(category=None)
        category.isDeleted = True
        category.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        _bump_inventory_list_cache_version('categories', tenant=category.tenant)
        _bump_inventory_list_cache_version('items', tenant=category.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_categories_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    cache_key = _inventory_list_cache_key('categories', 'all', request_or_user=request)
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse({'message': 'success', 'data': cached}, safe=False)
    rows = tenant_qs(request, InventoryCategory).filter(isDeleted=False).order_by('name')
    payload = [{
        'ID': row.pk,
        'Name': row.name,
        'Code': row.code or '',
        'Description': row.description or '',
        'IsActive': bool(row.isActive),
        'ItemCount': row.items.filter(isDeleted=False).count(),
    } for row in rows]
    cache.set(cache_key, payload, timeout=60 * 30)
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


@transaction.atomic
def add_inventory_unit_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        unit = InventoryUnit()
        assign_tenant(unit, request)
        _apply_inventory_unit_payload(request, unit)
        unit.save()
        _bump_inventory_list_cache_version('units', tenant=unit.tenant)
        return JsonResponse({'message': 'success', 'unitId': unit.pk}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_unit_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        unit = tenant_qs(request, InventoryUnit).get(pk=int(request.POST.get('unitId')), isDeleted=False)
        _apply_inventory_unit_payload(request, unit)
        unit.save()
        InventoryItem.objects.filter(tenant=unit.tenant, unit=unit, isDeleted=False).update(unitName=unit.symbol)
        _bump_inventory_list_cache_version('units', tenant=unit.tenant)
        _bump_inventory_list_cache_version('items', tenant=unit.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_unit_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        unit = tenant_qs(request, InventoryUnit).get(pk=int(request.POST.get('unitId')), isDeleted=False)
        InventoryItem.objects.filter(tenant=unit.tenant, unit=unit, isDeleted=False).update(unit=None)
        unit.isDeleted = True
        unit.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        _bump_inventory_list_cache_version('units', tenant=unit.tenant)
        _bump_inventory_list_cache_version('items', tenant=unit.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_units_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    cache_key = _inventory_list_cache_key('units', 'all', request_or_user=request)
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse({'message': 'success', 'data': cached}, safe=False)
    rows = tenant_qs(request, InventoryUnit).filter(isDeleted=False).order_by('name')
    payload = [{
        'ID': row.pk,
        'Name': row.name,
        'Symbol': row.symbol,
        'AllowsDecimal': bool(row.allowsDecimal),
        'Description': row.description or '',
        'IsActive': bool(row.isActive),
        'ItemCount': row.items.filter(isDeleted=False).count(),
    } for row in rows]
    cache.set(cache_key, payload, timeout=60 * 30)
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


@transaction.atomic
def add_inventory_tax_profile_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        tax_profile = InventoryTaxProfile()
        assign_tenant(tax_profile, request)
        _apply_inventory_tax_profile_payload(request, tax_profile)
        tax_profile.save()
        _bump_inventory_list_cache_version('tax_profiles', tenant=tax_profile.tenant)
        return JsonResponse({'message': 'success', 'taxProfileId': tax_profile.pk}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_tax_profile_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        tax_profile = tenant_qs(request, InventoryTaxProfile).get(pk=int(request.POST.get('taxProfileId')), isDeleted=False)
        _apply_inventory_tax_profile_payload(request, tax_profile)
        tax_profile.save()
        InventoryItem.objects.filter(tenant=tax_profile.tenant, taxProfile=tax_profile, isDeleted=False).update(
            hsnCode=tax_profile.hsnCode,
            gstRate=tax_profile.gstRate,
        )
        _bump_inventory_list_cache_version('tax_profiles', tenant=tax_profile.tenant)
        _bump_inventory_list_cache_version('items', tenant=tax_profile.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_tax_profile_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        tax_profile = tenant_qs(request, InventoryTaxProfile).get(pk=int(request.POST.get('taxProfileId')), isDeleted=False)
        InventoryItem.objects.filter(tenant=tax_profile.tenant, taxProfile=tax_profile, isDeleted=False).update(taxProfile=None)
        tax_profile.isDeleted = True
        tax_profile.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        _bump_inventory_list_cache_version('tax_profiles', tenant=tax_profile.tenant)
        _bump_inventory_list_cache_version('items', tenant=tax_profile.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_tax_profiles_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    cache_key = _inventory_list_cache_key('tax_profiles', 'all', request_or_user=request)
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse({'message': 'success', 'data': cached}, safe=False)
    rows = tenant_qs(request, InventoryTaxProfile).filter(isDeleted=False).order_by('name')
    payload = [{
        'ID': row.pk,
        'Name': row.name,
        'HSNCode': row.hsnCode,
        'GSTRate': float(row.gstRate or 0.0),
        'CessRate': float(row.cessRate or 0.0),
        'Description': row.description or '',
        'IsActive': bool(row.isActive),
        'ItemCount': row.items.filter(isDeleted=False).count(),
    } for row in rows]
    cache.set(cache_key, payload, timeout=60 * 30)
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


@transaction.atomic
def add_inventory_warehouse_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        warehouse = InventoryWarehouse()
        assign_tenant(warehouse, request)
        warehouse.name = (request.POST.get('name') or '').strip()
        warehouse.code = (request.POST.get('code') or '').strip() or None
        warehouse.address = (request.POST.get('address') or '').strip() or None
        warehouse.isActive = str(request.POST.get('isActive') or '1').lower() not in {'0', 'false', 'no', 'off'}
        warehouse.isDefault = str(request.POST.get('isDefault') or '').lower() in {'1', 'true', 'yes', 'on'}
        if not warehouse.name:
            raise ValueError('Warehouse name is required.')
        warehouse.save()
        if warehouse.isDefault:
            InventoryWarehouse.objects.filter(
                tenant=warehouse.tenant,
                isDeleted=False,
            ).exclude(pk=warehouse.pk).update(isDefault=False)
        elif not InventoryWarehouse.objects.filter(tenant=warehouse.tenant, isDeleted=False, isDefault=True).exists():
            warehouse.isDefault = True
            warehouse.save(update_fields=['isDefault', 'lastUpdatedOn'])
        _bump_inventory_list_cache_version('warehouses', tenant=warehouse.tenant)
        return JsonResponse({'message': 'success', 'warehouseId': warehouse.pk}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_warehouse_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        warehouse = tenant_qs(request, InventoryWarehouse).get(pk=int(request.POST.get('warehouseId')), isDeleted=False)
        warehouse.name = (request.POST.get('name') or '').strip()
        warehouse.code = (request.POST.get('code') or '').strip() or None
        warehouse.address = (request.POST.get('address') or '').strip() or None
        warehouse.isDefault = str(request.POST.get('isDefault') or '').lower() in {'1', 'true', 'yes', 'on'}
        if not warehouse.name:
            raise ValueError('Warehouse name is required.')
        warehouse.save()
        if warehouse.isDefault:
            InventoryWarehouse.objects.filter(tenant=warehouse.tenant, isDeleted=False).exclude(pk=warehouse.pk).update(isDefault=False)
        _bump_inventory_list_cache_version('warehouses', tenant=warehouse.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_warehouse_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        warehouse = tenant_qs(request, InventoryWarehouse).get(pk=int(request.POST.get('warehouseId')), isDeleted=False)
        replacement = InventoryWarehouse.objects.filter(tenant=warehouse.tenant, isDeleted=False).exclude(pk=warehouse.pk).order_by('-isDefault', 'id').first()
        InventoryOpeningLine.objects.filter(tenant=warehouse.tenant, warehouse=warehouse, isDeleted=False).update(warehouse=replacement)
        PurchaseLine.objects.filter(tenant=warehouse.tenant, warehouse=warehouse, isDeleted=False).update(warehouse=replacement)
        SalesLine.objects.filter(tenant=warehouse.tenant, warehouse=warehouse, isDeleted=False).update(warehouse=replacement)
        CashCounterLine.objects.filter(tenant=warehouse.tenant, warehouse=warehouse, isDeleted=False).update(warehouse=replacement)
        InventoryLedgerEntry.objects.filter(tenant=warehouse.tenant, warehouse=warehouse, isDeleted=False).update(warehouse=replacement)
        warehouse.isDeleted = True
        warehouse.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        if replacement and not InventoryWarehouse.objects.filter(tenant=warehouse.tenant, isDeleted=False, isDefault=True).exists():
            replacement.isDefault = True
            replacement.save(update_fields=['isDefault', 'lastUpdatedOn'])
        _bump_inventory_list_cache_version('warehouses', tenant=warehouse.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_warehouses_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    tenant = get_current_tenant(request)
    cache_key = _inventory_list_cache_key('warehouses', 'active', request_or_user=request, tenant=tenant)
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse({'message': 'success', 'data': cached}, safe=False)
    warehouses = InventoryWarehouse.objects.filter(
        tenant=tenant,
        isDeleted=False,
        isActive=True,
    ).order_by('-isDefault', 'name')
    if not warehouses.exists():
        get_or_create_default_warehouse(tenant)
        warehouses = InventoryWarehouse.objects.filter(
            tenant=tenant,
            isDeleted=False,
            isActive=True,
        ).order_by('-isDefault', 'name')
    payload = [{
        'ID': warehouse.pk,
        'Name': warehouse.name,
        'Code': warehouse.code or '',
        'Address': warehouse.address or '',
        'IsDefault': bool(warehouse.isDefault),
    } for warehouse in warehouses]
    cache.set(cache_key, payload, timeout=60 * 30)
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def list_inventory_ledger_entries_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    tenant = get_current_tenant(request)
    rows = InventoryLedgerEntry.objects.filter(
        tenant=tenant,
        isDeleted=False,
        item__isDeleted=False,
    ).select_related('item', 'warehouse').order_by('-entryDate', '-id')[:200]
    source_ids = defaultdict(list)
    for row in rows:
        if row.source_object_id:
            source_ids[row.sourceType].append(row.source_object_id)

    detail_map = {}
    if source_ids.get(InventoryLedgerEntry.SourceType.PURCHASE):
        for line in PurchaseLine.objects.filter(pk__in=source_ids[InventoryLedgerEntry.SourceType.PURCHASE]).values('id', 'purchaseInvoice_id'):
            detail_map[(InventoryLedgerEntry.SourceType.PURCHASE, line['id'])] = {
                'DetailUrl': reverse('homeApp:purchase_detail', args=[line['purchaseInvoice_id']]),
                'DetailLabel': 'View Purchase',
            }
    if source_ids.get(InventoryLedgerEntry.SourceType.SALES):
        for line in SalesLine.objects.filter(pk__in=source_ids[InventoryLedgerEntry.SourceType.SALES]).values('id', 'sales_id'):
            detail_map[(InventoryLedgerEntry.SourceType.SALES, line['id'])] = {
                'DetailUrl': reverse('homeApp:edit_sales', args=[line['sales_id']]),
                'DetailLabel': 'View Sale',
            }
    if source_ids.get(InventoryLedgerEntry.SourceType.CASH_COUNTER):
        for line in CashCounterLine.objects.filter(pk__in=source_ids[InventoryLedgerEntry.SourceType.CASH_COUNTER]).values('id', 'cashCounter_id'):
            detail_map[(InventoryLedgerEntry.SourceType.CASH_COUNTER, line['id'])] = {
                'DetailUrl': reverse('homeApp:edit_cash_counter', args=[line['cashCounter_id']]),
                'DetailLabel': 'View Counter',
            }
    if source_ids.get(InventoryLedgerEntry.SourceType.ADJUSTMENT):
        for line in InventoryAdjustmentLine.objects.filter(pk__in=source_ids[InventoryLedgerEntry.SourceType.ADJUSTMENT]).values('id', 'voucher_id'):
            detail_map[(InventoryLedgerEntry.SourceType.ADJUSTMENT, line['id'])] = {
                'DetailAction': 'adjustment',
                'DetailId': line['voucher_id'],
                'DetailLabel': 'View Adjustment',
            }
    transfer_source_ids = source_ids.get(InventoryLedgerEntry.SourceType.TRANSFER_OUT, []) + source_ids.get(InventoryLedgerEntry.SourceType.TRANSFER_IN, [])
    if transfer_source_ids:
        for line in InventoryTransferLine.objects.filter(pk__in=transfer_source_ids).values('id', 'voucher_id'):
            detail_map[(InventoryLedgerEntry.SourceType.TRANSFER_OUT, line['id'])] = {
                'DetailAction': 'transfer',
                'DetailId': line['voucher_id'],
                'DetailLabel': 'View Transfer',
            }
            detail_map[(InventoryLedgerEntry.SourceType.TRANSFER_IN, line['id'])] = {
                'DetailAction': 'transfer',
                'DetailId': line['voucher_id'],
                'DetailLabel': 'View Transfer',
            }
    if source_ids.get(InventoryLedgerEntry.SourceType.PURCHASE_RETURN):
        for line in PurchaseReturnLine.objects.filter(pk__in=source_ids[InventoryLedgerEntry.SourceType.PURCHASE_RETURN]).values('id', 'voucher_id'):
            detail_map[(InventoryLedgerEntry.SourceType.PURCHASE_RETURN, line['id'])] = {
                'DetailAction': 'purchase_return',
                'DetailId': line['voucher_id'],
                'DetailLabel': 'View Purchase Return',
            }
    if source_ids.get(InventoryLedgerEntry.SourceType.SALES_RETURN):
        for line in SalesReturnLine.objects.filter(pk__in=source_ids[InventoryLedgerEntry.SourceType.SALES_RETURN]).values('id', 'voucher_id'):
            detail_map[(InventoryLedgerEntry.SourceType.SALES_RETURN, line['id'])] = {
                'DetailAction': 'sales_return',
                'DetailId': line['voucher_id'],
                'DetailLabel': 'View Sales Return',
            }
    payload = [{
        'ID': row.pk,
        'EntryDate': row.entryDate.strftime('%d/%m/%Y') if row.entryDate else '',
        'EntryDateSort': row.entryDate.isoformat() if row.entryDate else '',
        'ItemName': row.item.name,
        'WarehouseName': row.warehouse.name if row.warehouse_id else '',
        'StockBucket': row.stockBucket,
        'SourceType': row.sourceType,
        'QuantityDelta': float(row.quantityDelta or 0.0),
        'UnitRate': float(row.unitRate or 0.0),
        'TotalValue': float(row.totalValue or 0.0),
        'ReferenceNumber': row.referenceNumber or '',
        'Remark': row.remark or '',
        'DetailUrl': (detail_map.get((row.sourceType, row.source_object_id)) or {}).get('DetailUrl', ''),
        'DetailAction': (detail_map.get((row.sourceType, row.source_object_id)) or {}).get('DetailAction', ''),
        'DetailId': (detail_map.get((row.sourceType, row.source_object_id)) or {}).get('DetailId', ''),
        'DetailLabel': (detail_map.get((row.sourceType, row.source_object_id)) or {}).get('DetailLabel', ''),
    } for row in rows]
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def list_inventory_batch_report_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_has_any_permission(
        request,
        'home.view_inventoryitem',
        'home.can_manage_inventory',
        'home.can_view_party_credentials',
    ):
        return JsonResponse({'message': 'forbidden'}, status=403)
    tenant = get_current_tenant(request)
    today = _safe_local_date()
    warning_days = int(request.GET.get('days') or 30)
    warning_cutoff = today + timedelta(days=max(0, warning_days))
    rows = InventoryBatchStock.objects.filter(
        tenant=tenant,
        isDeleted=False,
        batch__isDeleted=False,
        item__isDeleted=False,
        quantityOnHand__gt=0,
    ).select_related('batch', 'item', 'warehouse').order_by('batch__expiryDate', 'item__name', 'batch__batchNumber')
    payload = []
    summary = {
        'TotalBatchRows': 0,
        'ExpiredBatchRows': 0,
        'ExpiringBatchRows': 0,
        'HealthyBatchRows': 0,
        'ExpiredQuantity': 0.0,
        'ExpiringQuantity': 0.0,
        'HealthyQuantity': 0.0,
        'WarningDays': max(0, warning_days),
    }
    for row in rows:
        expiry_date = row.batch.expiryDate
        if expiry_date and expiry_date < today:
            status = 'Expired'
        elif expiry_date and expiry_date <= warning_cutoff:
            status = 'Expiring Soon'
        else:
            status = 'Healthy'
        quantity = float(row.quantityOnHand or 0.0)
        summary['TotalBatchRows'] += 1
        if status == 'Expired':
            summary['ExpiredBatchRows'] += 1
            summary['ExpiredQuantity'] += quantity
        elif status == 'Expiring Soon':
            summary['ExpiringBatchRows'] += 1
            summary['ExpiringQuantity'] += quantity
        else:
            summary['HealthyBatchRows'] += 1
            summary['HealthyQuantity'] += quantity
        payload.append({
            'BatchID': row.batch_id,
            'ItemID': row.item_id,
            'ItemName': row.item.name,
            'BatchNumber': row.batch.batchNumber or '',
            'WarehouseName': row.warehouse.name if row.warehouse_id else '',
            'StockBucket': row.stockBucket,
            'QuantityOnHand': quantity,
            'ManufactureDate': row.batch.manufactureDate.strftime('%d/%m/%Y') if row.batch.manufactureDate else '',
            'ExpiryDate': expiry_date.strftime('%d/%m/%Y') if expiry_date else '',
            'ExpiryDateSort': expiry_date.isoformat() if expiry_date else '',
            'DaysToExpiry': (expiry_date - today).days if expiry_date else None,
            'MRP': float(row.batch.mrp or 0.0),
            'PurchaseRate': float(row.batch.purchaseRate or 0.0),
            'Status': status,
        })
    for key in ('ExpiredQuantity', 'ExpiringQuantity', 'HealthyQuantity'):
        summary[key] = round(summary[key], 4)
    return JsonResponse({'message': 'success', 'data': payload, 'summary': summary}, safe=False)


def list_inventory_batch_ledger_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_has_any_permission(
        request,
        'home.view_inventoryitem',
        'home.can_manage_inventory',
        'home.can_view_party_credentials',
    ):
        return JsonResponse({'message': 'forbidden'}, status=403)
    raw_batch_id = str(request.GET.get('batchId') or '').strip()
    if not raw_batch_id.isdigit():
        return JsonResponse({'message': 'error', 'error': 'Invalid batch selection.'}, status=400)
    batch = tenant_qs(request, InventoryBatch).filter(pk=int(raw_batch_id), isDeleted=False).select_related('item').first()
    if batch is None:
        return JsonResponse({'message': 'error', 'error': 'Batch not found.'}, status=404)
    rows = list(InventoryLedgerEntry.objects.filter(
        tenant=batch.tenant,
        isDeleted=False,
        batch=batch,
    ).select_related('warehouse').order_by('entryDate', 'id'))
    stock_rows = list(InventoryBatchStock.objects.filter(
        tenant=batch.tenant,
        isDeleted=False,
        batch=batch,
        quantityOnHand__gt=0,
    ).select_related('warehouse').order_by('warehouse__name', 'stockBucket'))
    purchase_lines = list(PurchaseLine.objects.filter(
        tenant=batch.tenant,
        isDeleted=False,
        batch=batch,
        purchaseInvoice__isDeleted=False,
    ).select_related('purchaseInvoice', 'purchaseInvoice__partyID').order_by('purchaseInvoice__buildDate', 'id')[:10])
    running = 0.0
    payload = []
    for row in rows:
        running += float(row.quantityDelta or 0.0)
        payload.append({
            'ID': row.pk,
            'EntryDate': row.entryDate.strftime('%d/%m/%Y') if row.entryDate else '',
            'EntryDateSort': row.entryDate.isoformat() if row.entryDate else '',
            'WarehouseName': row.warehouse.name if row.warehouse_id else '',
            'SourceType': row.sourceType,
            'QuantityDelta': float(row.quantityDelta or 0.0),
            'RunningBalance': round(running, 4),
            'UnitRate': float(row.unitRate or 0.0),
            'TotalValue': float(row.totalValue or 0.0),
            'ReferenceNumber': row.referenceNumber or '',
            'Remark': row.remark or '',
        })
    warehouse_payload = [{
        'WarehouseID': row.warehouse_id,
        'WarehouseName': row.warehouse.name if row.warehouse_id else '',
        'StockBucket': row.stockBucket,
        'QuantityOnHand': float(row.quantityOnHand or 0.0),
    } for row in stock_rows]
    purchase_payload = [{
        'PurchaseID': line.purchaseInvoice_id,
        'PurchaseDisplayID': line.purchaseInvoice.purchaseID or '',
        'InvoiceNumber': line.purchaseInvoice.invoiceNumber or '',
        'InvoiceDate': line.purchaseInvoice.buildDate.strftime('%d/%m/%Y') if line.purchaseInvoice.buildDate else '',
        'Party': line.purchaseInvoice.partyID.name if line.purchaseInvoice.partyID_id else '',
        'Quantity': float(line.quantity or 0.0),
        'UnitCost': float(line.unitCost or 0.0),
        'LineTotal': float(line.lineTotal or 0.0),
    } for line in purchase_lines]
    today = _safe_local_date()
    if batch.expiryDate and batch.expiryDate < today:
        status = 'Expired'
    elif batch.expiryDate and batch.expiryDate <= today + timedelta(days=30):
        status = 'Expiring Soon'
    else:
        status = 'Healthy'
    return JsonResponse({'message': 'success', 'data': {
        'BatchID': batch.pk,
        'ItemName': batch.item.name,
        'BatchNumber': batch.batchNumber or '',
        'ManufactureDate': batch.manufactureDate.strftime('%d/%m/%Y') if batch.manufactureDate else '',
        'ExpiryDate': batch.expiryDate.strftime('%d/%m/%Y') if batch.expiryDate else '',
        'DaysToExpiry': (batch.expiryDate - today).days if batch.expiryDate else None,
        'Status': status,
        'QuantityOnHand': float(batch.quantityOnHand or 0.0),
        'MRP': float(batch.mrp or 0.0),
        'PurchaseRate': float(batch.purchaseRate or 0.0),
        'WarehouseStocks': warehouse_payload,
        'PurchaseSources': purchase_payload,
        'Rows': payload,
    }}, safe=False)


@transaction.atomic
def deactivate_expired_zero_stock_batches_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    tenant = get_current_tenant(request)
    today = _safe_local_date()
    batch_ids_with_stock = InventoryBatchStock.objects.filter(
        tenant=tenant,
        isDeleted=False,
        quantityOnHand__gt=0,
    ).values('batch_id')
    updated = InventoryBatch.objects.filter(
        tenant=tenant,
        isDeleted=False,
        isActive=True,
        expiryDate__lt=today,
        quantityOnHand__lte=0,
    ).exclude(
        pk__in=batch_ids_with_stock
    ).update(isActive=False, lastUpdatedOn=timezone.now())
    _bump_inventory_list_cache_version('items', tenant=tenant)
    return JsonResponse({'message': 'success', 'deactivatedCount': updated})


@transaction.atomic
def add_inventory_opening_stock_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        lines_payload = _request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one opening stock line is required.')
        voucher = InventoryOpeningVoucher()
        assign_tenant(voucher, request)
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        voucher.remark = request.POST.get('remark')
        voucher.createdBy = _staff_for_request(request)
        voucher.save()
        voucher.voucherNumber = _next_tenant_sequence_value(request, TenantSequence.Scope.INVENTORY_OPENING)
        voucher.save(update_fields=['voucherNumber', 'lastUpdatedOn'])
        warehouse = _parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        item_ids = []
        for row in lines_payload:
            item = _parse_inventory_item(request, row.get('itemId'))
            quantity = _safe_sales_float(row.get('quantity'))
            if quantity <= 0:
                raise ValueError('Opening quantity must be greater than zero.')
            stock_bucket = _normalize_stock_bucket(request, row.get('stockBucket'), item)
            unit_cost = _safe_sales_float(row.get('unitCost') or row.get('unitPrice'))
            line_total = _safe_sales_float(row.get('lineTotal')) or round(quantity * unit_cost, 2)
            batch = _resolve_inventory_batch(request, row, item=item, stock_bucket=stock_bucket, warehouse=warehouse, quantity=quantity, direction='in')
            InventoryOpeningLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                warehouse=warehouse,
                item=item,
                batch=batch,
                stockBucket=stock_bucket,
                quantity=quantity,
                unitCost=unit_cost,
                lineTotal=line_total,
                remark=(row.get('remark') or '')[:500],
            )
            item_ids.append(item.pk)
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success', 'voucherNumber': voucher.voucherNumber}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_opening_vouchers_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    rows = tenant_qs(request, InventoryOpeningVoucher).filter(isDeleted=False).select_related('createdBy').order_by('-entryDate', '-id')[:100]
    payload = []
    for voucher in rows:
        payload.append({
            'ID': voucher.pk,
            'VoucherNumber': voucher.voucherNumber or '',
            'EntryDate': voucher.entryDate.strftime('%d/%m/%Y') if voucher.entryDate else '',
            'Remark': voucher.remark or '',
            'CreatedBy': voucher.createdBy.name if voucher.createdBy_id else '-',
            'LineCount': voucher.lines.filter(isDeleted=False).count(),
        })
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def _voucher_line_rows_from_payload(request, lines_payload, *, quantity_field='quantity', rate_field='unitCost', batch_direction='in', warehouse=None):
    rows = []
    item_ids = []
    for row in lines_payload:
        item = _parse_inventory_item(request, row.get('itemId'))
        quantity = _safe_sales_float(row.get(quantity_field))
        if quantity <= 0:
            raise ValueError('Voucher quantity must be greater than zero.')
        stock_bucket = _normalize_stock_bucket(request, row.get('stockBucket'), item)
        unit_rate = _safe_sales_float(row.get(rate_field) or row.get('unitPrice') or row.get('unitCost'))
        line_total = _safe_sales_float(row.get('lineTotal')) or round(quantity * unit_rate, 2)
        batch = _resolve_inventory_batch(request, row, item=item, stock_bucket=stock_bucket, warehouse=warehouse, quantity=quantity, direction=batch_direction)
        rows.append({
            'item': item,
            'batch': batch,
            'quantity': quantity,
            'stockBucket': stock_bucket,
            'unitRate': unit_rate,
            'lineTotal': line_total,
            'remark': (row.get('remark') or '')[:500],
        })
        item_ids.append(item.pk)
    return rows, item_ids


def _serialize_voucher_lines(lines_qs, *, quantity_field='quantity', rate_field='unitCost'):
    payload = []
    for line in lines_qs.select_related('item', 'batch').order_by('id'):
        payload.append({
            'itemId': line.item_id,
            'itemName': line.item.name,
            'batchId': line.batch_id,
            'batchNumber': line.batch.batchNumber if line.batch_id else '',
            'manufactureDate': line.batch.manufactureDate.strftime('%d/%m/%Y') if line.batch_id and line.batch.manufactureDate else '',
            'expiryDate': line.batch.expiryDate.strftime('%d/%m/%Y') if line.batch_id and line.batch.expiryDate else '',
            'batchMrp': float(line.batch.mrp or 0.0) if line.batch_id else 0.0,
            'stockBucket': line.stockBucket,
            'quantity': float(getattr(line, quantity_field) or 0.0),
            'unitCost': float(getattr(line, rate_field) or 0.0),
            'unitPrice': float(getattr(line, rate_field) or 0.0),
            'lineTotal': float(line.lineTotal or 0.0),
            'remark': line.remark or '',
        })
    return payload


@transaction.atomic
def add_inventory_adjustment_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        lines_payload = _request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one adjustment line is required.')
        voucher = InventoryAdjustmentVoucher()
        assign_tenant(voucher, request)
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        voucher.remark = request.POST.get('remark')
        voucher.createdBy = _staff_for_request(request)
        voucher.save()
        voucher.voucherNumber = _next_tenant_sequence_value(request, TenantSequence.Scope.INVENTORY_ADJUSTMENT)
        voucher.save(update_fields=['voucherNumber', 'lastUpdatedOn'])
        warehouse = _parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        batch_direction = 'out' if str(request.POST.get('direction') or 'increase').lower() == 'decrease' else 'in'
        rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitCost', batch_direction=batch_direction, warehouse=warehouse)
        for row in rows:
            quantity_delta = row['quantity']
            if str(request.POST.get('direction') or 'increase').lower() == 'decrease':
                quantity_delta = -quantity_delta
            InventoryAdjustmentLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                warehouse=warehouse,
                item=row['item'],
                batch=row['batch'],
                stockBucket=row['stockBucket'],
                quantityDelta=quantity_delta,
                unitCost=row['unitRate'],
                lineTotal=row['lineTotal'] if quantity_delta >= 0 else -row['lineTotal'],
                remark=row['remark'],
            )
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success', 'voucherId': voucher.pk, 'voucherNumber': voucher.voucherNumber}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_adjustment_vouchers_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    rows = tenant_qs(request, InventoryAdjustmentVoucher).filter(isDeleted=False).order_by('-entryDate', '-id')[:100]
    payload = [{
        'ID': row.pk,
        'VoucherNumber': row.voucherNumber or '',
        'EntryDate': row.entryDate.strftime('%d/%m/%Y') if row.entryDate else '',
        'Remark': row.remark or '',
        'LineCount': row.lines.filter(isDeleted=False).count(),
    } for row in rows]
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def get_inventory_adjustment_voucher_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, InventoryAdjustmentVoucher).get(pk=int(request.GET.get('voucherId')), isDeleted=False)
        lines = _serialize_voucher_lines(voucher.lines.filter(isDeleted=False), quantity_field='quantityDelta', rate_field='unitCost')
        for line in lines:
            line['quantity'] = abs(float(line.get('quantity') or 0.0))
        return JsonResponse({'message': 'success', 'data': {
            'ID': voucher.pk,
            'VoucherNumber': voucher.voucherNumber or '',
            'EntryDate': voucher.entryDate.strftime('%d/%m/%Y') if voucher.entryDate else '',
            'WarehouseID': voucher.lines.filter(isDeleted=False).values_list('warehouse_id', flat=True).first(),
            'Direction': 'decrease' if voucher.lines.filter(isDeleted=False, quantityDelta__lt=0).exists() else 'increase',
            'Remark': voucher.remark or '',
            'Lines': lines,
        }}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_adjustment_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, InventoryAdjustmentVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        prior_item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        lines_payload = _request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one adjustment line is required.')
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        voucher.remark = request.POST.get('remark')
        voucher.save(update_fields=['entryDate', 'remark', 'lastUpdatedOn'])
        voucher.lines.filter(isDeleted=False).update(isDeleted=True)
        warehouse = _parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        batch_direction = 'out' if str(request.POST.get('direction') or 'increase').lower() == 'decrease' else 'in'
        rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitCost', batch_direction=batch_direction, warehouse=warehouse)
        for row in rows:
            quantity_delta = row['quantity']
            if str(request.POST.get('direction') or 'increase').lower() == 'decrease':
                quantity_delta = -quantity_delta
            InventoryAdjustmentLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                warehouse=warehouse,
                item=row['item'],
                batch=row['batch'],
                stockBucket=row['stockBucket'],
                quantityDelta=quantity_delta,
                unitCost=row['unitRate'],
                lineTotal=row['lineTotal'] if quantity_delta >= 0 else -row['lineTotal'],
                remark=row['remark'],
            )
        rebuild_inventory_stock(voucher.tenant, list(set(prior_item_ids + item_ids)))
        validate_non_negative_inventory(voucher.tenant, list(set(prior_item_ids + item_ids)))
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_adjustment_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, InventoryAdjustmentVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        voucher.isDeleted = True
        voucher.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def add_inventory_transfer_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        lines_payload = _request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one transfer line is required.')
        voucher = InventoryTransferVoucher()
        assign_tenant(voucher, request)
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        voucher.fromWarehouse = _parse_inventory_warehouse(request, request.POST.get('fromWarehouseId'))
        voucher.toWarehouse = _parse_inventory_warehouse(request, request.POST.get('toWarehouseId'))
        if voucher.fromWarehouse_id == voucher.toWarehouse_id:
            raise ValueError('From and To warehouse cannot be same.')
        voucher.remark = request.POST.get('remark')
        voucher.createdBy = _staff_for_request(request)
        voucher.save()
        voucher.voucherNumber = _next_tenant_sequence_value(request, TenantSequence.Scope.INVENTORY_TRANSFER)
        voucher.save(update_fields=['voucherNumber', 'lastUpdatedOn'])
        rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitCost', batch_direction='out', warehouse=voucher.fromWarehouse)
        for row in rows:
            InventoryTransferLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                item=row['item'],
                batch=row['batch'],
                stockBucket=row['stockBucket'],
                quantity=row['quantity'],
                unitCost=row['unitRate'],
                lineTotal=row['lineTotal'],
                remark=row['remark'],
            )
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success', 'voucherId': voucher.pk, 'voucherNumber': voucher.voucherNumber}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_transfer_vouchers_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    rows = tenant_qs(request, InventoryTransferVoucher).filter(isDeleted=False).select_related('fromWarehouse', 'toWarehouse').order_by('-entryDate', '-id')[:100]
    payload = [{
        'ID': row.pk,
        'VoucherNumber': row.voucherNumber or '',
        'EntryDate': row.entryDate.strftime('%d/%m/%Y') if row.entryDate else '',
        'FromWarehouse': row.fromWarehouse.name if row.fromWarehouse_id else '-',
        'ToWarehouse': row.toWarehouse.name if row.toWarehouse_id else '-',
        'Remark': row.remark or '',
        'LineCount': row.lines.filter(isDeleted=False).count(),
    } for row in rows]
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def get_inventory_transfer_voucher_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, InventoryTransferVoucher).select_related('fromWarehouse', 'toWarehouse').get(pk=int(request.GET.get('voucherId')), isDeleted=False)
        return JsonResponse({'message': 'success', 'data': {
            'ID': voucher.pk,
            'VoucherNumber': voucher.voucherNumber or '',
            'EntryDate': voucher.entryDate.strftime('%d/%m/%Y') if voucher.entryDate else '',
            'FromWarehouseID': voucher.fromWarehouse_id,
            'ToWarehouseID': voucher.toWarehouse_id,
            'Remark': voucher.remark or '',
            'Lines': _serialize_voucher_lines(voucher.lines.filter(isDeleted=False), quantity_field='quantity', rate_field='unitCost'),
        }}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_transfer_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, InventoryTransferVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        prior_item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        lines_payload = _request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one transfer line is required.')
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        voucher.fromWarehouse = _parse_inventory_warehouse(request, request.POST.get('fromWarehouseId'))
        voucher.toWarehouse = _parse_inventory_warehouse(request, request.POST.get('toWarehouseId'))
        if voucher.fromWarehouse_id == voucher.toWarehouse_id:
            raise ValueError('From and To warehouse cannot be same.')
        voucher.remark = request.POST.get('remark')
        voucher.save(update_fields=['entryDate', 'fromWarehouse', 'toWarehouse', 'remark', 'lastUpdatedOn'])
        voucher.lines.filter(isDeleted=False).update(isDeleted=True)
        rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitCost', batch_direction='out', warehouse=voucher.fromWarehouse)
        for row in rows:
            InventoryTransferLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                item=row['item'],
                batch=row['batch'],
                stockBucket=row['stockBucket'],
                quantity=row['quantity'],
                unitCost=row['unitRate'],
                lineTotal=row['lineTotal'],
                remark=row['remark'],
            )
        rebuild_inventory_stock(voucher.tenant, list(set(prior_item_ids + item_ids)))
        validate_non_negative_inventory(voucher.tenant, list(set(prior_item_ids + item_ids)))
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_transfer_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, InventoryTransferVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        voucher.isDeleted = True
        voucher.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def add_purchase_return_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        lines_payload = _request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one purchase return line is required.')
        voucher = PurchaseReturnVoucher()
        assign_tenant(voucher, request)
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        raw_purchase_id = str(request.POST.get('purchaseInvoiceId') or '').strip()
        if raw_purchase_id.isdigit():
            voucher.purchaseInvoice = tenant_qs(request, PurchaseInvoice).filter(pk=int(raw_purchase_id), isDeleted=False).first()
        voucher.partyID = _parse_sales_party(request, request.POST.get('party')) if request.POST.get('party') else None
        if voucher.partyID_id is None and voucher.purchaseInvoice_id:
            voucher.partyID = voucher.purchaseInvoice.partyID
        voucher.remark = request.POST.get('remark')
        voucher.createdBy = _staff_for_request(request)
        voucher.save()
        voucher.voucherNumber = _next_tenant_sequence_value(request, TenantSequence.Scope.PURCHASE_RETURN)
        voucher.save(update_fields=['voucherNumber', 'lastUpdatedOn'])
        warehouse = _parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitCost', batch_direction='out', warehouse=warehouse)
        voucher.totalAmount = 0.0
        for row in rows:
            PurchaseReturnLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                warehouse=warehouse,
                item=row['item'],
                batch=row['batch'],
                stockBucket=row['stockBucket'],
                quantity=row['quantity'],
                unitCost=row['unitRate'],
                lineTotal=row['lineTotal'],
                remark=row['remark'],
            )
            voucher.totalAmount += row['lineTotal']
        voucher.save(update_fields=['totalAmount', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success', 'voucherId': voucher.pk, 'voucherNumber': voucher.voucherNumber}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_purchase_return_vouchers_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    rows = tenant_qs(request, PurchaseReturnVoucher).filter(isDeleted=False).select_related('partyID', 'purchaseInvoice').order_by('-entryDate', '-id')[:100]
    payload = [{
        'ID': row.pk,
        'VoucherNumber': row.voucherNumber or '',
        'EntryDate': row.entryDate.strftime('%d/%m/%Y') if row.entryDate else '',
        'Party': row.partyID.name if row.partyID_id else '-',
        'PurchaseInvoice': row.purchaseInvoice.invoiceNumber if row.purchaseInvoice_id else '-',
        'TotalAmount': float(row.totalAmount or 0.0),
        'LineCount': row.lines.filter(isDeleted=False).count(),
    } for row in rows]
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def get_purchase_return_voucher_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, PurchaseReturnVoucher).select_related('partyID', 'purchaseInvoice').get(pk=int(request.GET.get('voucherId')), isDeleted=False)
        warehouse_id = voucher.lines.filter(isDeleted=False).values_list('warehouse_id', flat=True).first()
        return JsonResponse({'message': 'success', 'data': {
            'ID': voucher.pk,
            'VoucherNumber': voucher.voucherNumber or '',
            'EntryDate': voucher.entryDate.strftime('%d/%m/%Y') if voucher.entryDate else '',
            'WarehouseID': warehouse_id,
            'PurchaseInvoiceID': voucher.purchaseInvoice_id,
            'PartyID': voucher.partyID_id,
            'Remark': voucher.remark or '',
            'Lines': _serialize_voucher_lines(voucher.lines.filter(isDeleted=False), quantity_field='quantity', rate_field='unitCost'),
        }}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_purchase_return_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, PurchaseReturnVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        prior_item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        lines_payload = _request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one purchase return line is required.')
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        raw_purchase_id = str(request.POST.get('purchaseInvoiceId') or '').strip()
        voucher.purchaseInvoice = tenant_qs(request, PurchaseInvoice).filter(pk=int(raw_purchase_id), isDeleted=False).first() if raw_purchase_id.isdigit() else None
        voucher.partyID = _parse_sales_party(request, request.POST.get('party')) if request.POST.get('party') else None
        if voucher.partyID_id is None and voucher.purchaseInvoice_id:
            voucher.partyID = voucher.purchaseInvoice.partyID
        voucher.remark = request.POST.get('remark')
        voucher.save(update_fields=['entryDate', 'purchaseInvoice', 'partyID', 'remark', 'lastUpdatedOn'])
        voucher.lines.filter(isDeleted=False).update(isDeleted=True)
        warehouse = _parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitCost', batch_direction='out', warehouse=warehouse)
        voucher.totalAmount = 0.0
        for row in rows:
            PurchaseReturnLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                warehouse=warehouse,
                item=row['item'],
                batch=row['batch'],
                stockBucket=row['stockBucket'],
                quantity=row['quantity'],
                unitCost=row['unitRate'],
                lineTotal=row['lineTotal'],
                remark=row['remark'],
            )
            voucher.totalAmount += row['lineTotal']
        voucher.save(update_fields=['totalAmount', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, list(set(prior_item_ids + item_ids)))
        validate_non_negative_inventory(voucher.tenant, list(set(prior_item_ids + item_ids)))
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_purchase_return_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, PurchaseReturnVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        voucher.isDeleted = True
        voucher.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def add_sales_return_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        lines_payload = _request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one sales return line is required.')
        voucher = SalesReturnVoucher()
        assign_tenant(voucher, request)
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        source_type, source_id = _parse_return_source_ref(request.POST.get('salesId'))
        if source_type == 'cash_counter' and source_id:
            voucher.cashCounter = tenant_qs(request, CashCounter).filter(pk=source_id, isDeleted=False).first()
        elif source_id:
            voucher.sales = tenant_qs(request, Sales).filter(pk=source_id, isDeleted=False).first()
        voucher.partyID = _parse_sales_party(request, request.POST.get('party')) if request.POST.get('party') else None
        if voucher.partyID_id is None and voucher.sales_id:
            voucher.partyID = voucher.sales.partyID
        if voucher.partyID_id is None and voucher.cashCounter_id:
            voucher.partyID = voucher.cashCounter.partyID
        voucher.remark = request.POST.get('remark')
        voucher.createdBy = _staff_for_request(request)
        voucher.save()
        voucher.voucherNumber = _next_tenant_sequence_value(request, TenantSequence.Scope.SALES_RETURN)
        voucher.save(update_fields=['voucherNumber', 'lastUpdatedOn'])
        warehouse = _parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitPrice', batch_direction='in', warehouse=warehouse)
        voucher.totalAmount = 0.0
        for row in rows:
            SalesReturnLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                warehouse=warehouse,
                item=row['item'],
                batch=row['batch'],
                stockBucket=row['stockBucket'],
                quantity=row['quantity'],
                unitPrice=row['unitRate'],
                lineTotal=row['lineTotal'],
                remark=row['remark'],
            )
            voucher.totalAmount += row['lineTotal']
        voucher.save(update_fields=['totalAmount', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success', 'voucherId': voucher.pk, 'voucherNumber': voucher.voucherNumber}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_sales_return_vouchers_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    rows = tenant_qs(request, SalesReturnVoucher).filter(isDeleted=False).select_related('partyID', 'sales', 'cashCounter').order_by('-entryDate', '-id')[:100]
    payload = [{
        'ID': row.pk,
        'VoucherNumber': row.voucherNumber or '',
        'EntryDate': row.entryDate.strftime('%d/%m/%Y') if row.entryDate else '',
        'Party': row.partyID.name if row.partyID_id else '-',
        'SalesInvoice': row.sales.invoiceNumber if row.sales_id else (row.cashCounter.invoiceNumber if row.cashCounter_id else '-'),
        'TotalAmount': float(row.totalAmount or 0.0),
        'LineCount': row.lines.filter(isDeleted=False).count(),
    } for row in rows]
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def get_sales_return_voucher_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, SalesReturnVoucher).select_related('partyID', 'sales', 'cashCounter').get(pk=int(request.GET.get('voucherId')), isDeleted=False)
        warehouse_id = voucher.lines.filter(isDeleted=False).values_list('warehouse_id', flat=True).first()
        return JsonResponse({'message': 'success', 'data': {
            'ID': voucher.pk,
            'VoucherNumber': voucher.voucherNumber or '',
            'EntryDate': voucher.entryDate.strftime('%d/%m/%Y') if voucher.entryDate else '',
            'WarehouseID': warehouse_id,
            'SalesID': voucher.sales_id if voucher.sales_id else (f'cash_counter:{voucher.cashCounter_id}' if voucher.cashCounter_id else None),
            'PartyID': voucher.partyID_id,
            'Remark': voucher.remark or '',
            'Lines': _serialize_voucher_lines(voucher.lines.filter(isDeleted=False), quantity_field='quantity', rate_field='unitPrice'),
        }}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_sales_return_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, SalesReturnVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        prior_item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        lines_payload = _request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one sales return line is required.')
        voucher.entryDate = datetime.strptime(request.POST.get('entryDate'), '%d/%m/%Y').date()
        source_type, source_id = _parse_return_source_ref(request.POST.get('salesId'))
        voucher.sales = None
        voucher.cashCounter = None
        if source_type == 'cash_counter' and source_id:
            voucher.cashCounter = tenant_qs(request, CashCounter).filter(pk=source_id, isDeleted=False).first()
        elif source_id:
            voucher.sales = tenant_qs(request, Sales).filter(pk=source_id, isDeleted=False).first()
        voucher.partyID = _parse_sales_party(request, request.POST.get('party')) if request.POST.get('party') else None
        if voucher.partyID_id is None and voucher.sales_id:
            voucher.partyID = voucher.sales.partyID
        if voucher.partyID_id is None and voucher.cashCounter_id:
            voucher.partyID = voucher.cashCounter.partyID
        voucher.remark = request.POST.get('remark')
        voucher.save(update_fields=['entryDate', 'sales', 'cashCounter', 'partyID', 'remark', 'lastUpdatedOn'])
        voucher.lines.filter(isDeleted=False).update(isDeleted=True)
        warehouse = _parse_inventory_warehouse(request, request.POST.get('warehouseId'))
        rows, item_ids = _voucher_line_rows_from_payload(request, lines_payload, rate_field='unitPrice', batch_direction='in', warehouse=warehouse)
        voucher.totalAmount = 0.0
        for row in rows:
            SalesReturnLine.objects.create(
                tenant=voucher.tenant,
                voucher=voucher,
                warehouse=warehouse,
                item=row['item'],
                batch=row['batch'],
                stockBucket=row['stockBucket'],
                quantity=row['quantity'],
                unitPrice=row['unitRate'],
                lineTotal=row['lineTotal'],
                remark=row['remark'],
            )
            voucher.totalAmount += row['lineTotal']
        voucher.save(update_fields=['totalAmount', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, list(set(prior_item_ids + item_ids)))
        validate_non_negative_inventory(voucher.tenant, list(set(prior_item_ids + item_ids)))
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_sales_return_voucher_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_inventory_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        voucher = tenant_qs(request, SalesReturnVoucher).get(pk=int(request.POST.get('voucherId')), isDeleted=False)
        item_ids = list(voucher.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        voucher.isDeleted = True
        voucher.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        rebuild_inventory_stock(voucher.tenant, item_ids)
        validate_non_negative_inventory(voucher.tenant, item_ids)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_purchase_invoices_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_purchase_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    rows = tenant_qs(request, PurchaseInvoice).filter(isDeleted=False).select_related('partyID', 'createdBy').order_by('-buildDate', '-id')[:100]
    payload = []
    for purchase in rows:
        payload.append({
            'ID': purchase.pk,
            'PurchaseID': _purchase_public_id(purchase),
            'InvoiceNumber': purchase.invoiceNumber,
            'InvoiceDate': purchase.buildDate.strftime('%d/%m/%Y') if purchase.buildDate else '',
            'Party': purchase.partyID.name if purchase.partyID_id else '-',
            'GSTTreatment': purchase.gstTreatment,
            'ClaimStatus': purchase.claimStatus,
            'TotalAmount': float(purchase.totalAmount or 0.0),
            'LineCount': purchase.lines.filter(isDeleted=False).count(),
            'RevisionCount': purchase.revisions.filter(isDeleted=False).count(),
        })
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def get_purchase_invoice_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_purchase_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        purchase = tenant_qs(request, PurchaseInvoice).select_related('partyID', 'createdBy').get(pk=int(request.GET.get('purchaseId')), isDeleted=False)
        lines = [{
            'itemId': line.item_id,
            'itemName': line.item.name,
            'batchId': line.batch_id,
            'batchNumber': line.batch.batchNumber if line.batch_id else '',
            'manufactureDate': line.batch.manufactureDate.strftime('%d/%m/%Y') if line.batch_id and line.batch.manufactureDate else '',
            'expiryDate': line.batch.expiryDate.strftime('%d/%m/%Y') if line.batch_id and line.batch.expiryDate else '',
            'batchMrp': float(line.batch.mrp or 0.0) if line.batch_id else 0.0,
            'stockBucket': line.stockBucket,
            'quantity': float(line.quantity or 0.0),
            'unitCost': float(line.unitCost or 0.0),
            'taxableAmount': float(line.taxableAmount or 0.0),
            'gstRate': float(line.gstRate or 0.0),
            'gstAmount': float(line.gstAmount or 0.0),
            'lineTotal': float(line.lineTotal or 0.0),
            'remark': line.remark or '',
        } for line in purchase.lines.filter(isDeleted=False).select_related('item', 'batch').order_by('id')]
        revisions = [{
            'changedOn': revision.datetime.strftime('%d/%m/%Y %I:%M %p') if revision.datetime else '',
            'previousGstTreatment': revision.previousGstTreatment or '',
            'newGstTreatment': revision.newGstTreatment,
            'previousClaimStatus': revision.previousClaimStatus or '',
            'newClaimStatus': revision.newClaimStatus,
            'reason': revision.reason or '',
            'changedBy': revision.changedBy.name if revision.changedBy_id else '-',
        } for revision in purchase.revisions.filter(isDeleted=False).select_related('changedBy').order_by('-datetime')]
        return JsonResponse({'message': 'success', 'data': {
            'ID': purchase.pk,
            'PurchaseID': _purchase_public_id(purchase),
            'PartyID': purchase.partyID_id,
            'InvoiceNumber': purchase.invoiceNumber,
            'InvoiceDate': purchase.buildDate.strftime('%d/%m/%Y') if purchase.buildDate else '',
            'GSTTreatment': purchase.gstTreatment,
            'ClaimStatus': purchase.claimStatus,
            'Remark': purchase.remark or '',
            'Lines': lines,
            'Revisions': revisions,
        }}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def add_purchase_invoice_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_purchase_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        lines_payload = _request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one purchase line is required.')
        purchase = PurchaseInvoice()
        assign_tenant(purchase, request)
        purchase.partyID = _parse_sales_party(request, request.POST.get('party'))
        purchase.invoiceNumber = (request.POST.get('invoiceNumber') or '').strip()
        if not purchase.invoiceNumber:
            raise ValueError('Purchase invoice number is required.')
        purchase.buildDate = datetime.strptime(request.POST.get('invoiceDate'), '%d/%m/%Y').date()
        purchase.gstTreatment = _normalize_purchase_gst_treatment(request, request.POST.get('gstTreatment'))
        lines_payload = _normalize_purchase_lines_for_treatment(purchase.gstTreatment, lines_payload)
        purchase.claimStatus = _normalize_purchase_claim_status(purchase.gstTreatment, request.POST.get('claimStatus'))
        purchase.remark = request.POST.get('remark')
        purchase.createdBy = _staff_for_request(request)
        purchase.subtotalAmount, purchase.gstAmount, purchase.totalAmount = _purchase_totals_from_lines(lines_payload)
        purchase.save()
        purchase.purchaseID = _next_tenant_sequence_value(request, TenantSequence.Scope.PURCHASE)
        purchase.save(update_fields=['purchaseID', 'lastUpdatedOn'])
        _sync_purchase_lines(request, purchase, lines_payload)
        return JsonResponse({'message': 'success', 'purchaseId': purchase.pk}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_purchase_invoice_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_purchase_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        purchase = tenant_qs(request, PurchaseInvoice).get(pk=int(request.POST.get('purchaseId')))
        previous_treatment = purchase.gstTreatment
        previous_claim_status = purchase.claimStatus
        previous_snapshot = snapshot_instance(purchase)
        lines_payload = _request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one purchase line is required.')
        purchase.partyID = _parse_sales_party(request, request.POST.get('party'))
        purchase.invoiceNumber = (request.POST.get('invoiceNumber') or '').strip()
        purchase.buildDate = datetime.strptime(request.POST.get('invoiceDate'), '%d/%m/%Y').date()
        purchase.gstTreatment = _normalize_purchase_gst_treatment(request, request.POST.get('gstTreatment'))
        lines_payload = _normalize_purchase_lines_for_treatment(purchase.gstTreatment, lines_payload)
        purchase.claimStatus = _normalize_purchase_claim_status(purchase.gstTreatment, request.POST.get('claimStatus'))
        purchase.remark = request.POST.get('remark')
        purchase.subtotalAmount, purchase.gstAmount, purchase.totalAmount = _purchase_totals_from_lines(lines_payload)
        purchase.save()
        if previous_treatment != purchase.gstTreatment or previous_claim_status != purchase.claimStatus:
            PurchaseInvoiceRevision.objects.create(
                tenant=purchase.tenant,
                purchaseInvoice=purchase,
                previousGstTreatment=previous_treatment,
                newGstTreatment=purchase.gstTreatment,
                previousClaimStatus=previous_claim_status,
                newClaimStatus=purchase.claimStatus,
                reason=(request.POST.get('revisionReason') or '')[:500],
                snapshot=previous_snapshot,
                changedBy=_staff_for_request(request),
            )
        _sync_purchase_lines(request, purchase, lines_payload)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_purchase_invoice_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_purchase_records(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        purchase = tenant_qs(request, PurchaseInvoice).get(pk=int(request.POST.get('purchaseId')))
        item_ids = list(purchase.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        purchase.isDeleted = True
        purchase.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        rebuild_inventory_stock(purchase.tenant, item_ids)
        validate_non_negative_inventory(purchase.tenant, item_ids)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def get_non_gst_shadow_status_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_non_gst_shadow(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    tenant = get_current_tenant(request)
    return JsonResponse({'message': 'success', 'data': {
        'enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'hasCode': bool(getattr(tenant, 'nonGstShadowCodeHash', '')),
        'tenantName': getattr(tenant, 'name', ''),
        'inventoryEnabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advancedInvoicingEnabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
    }}, safe=False)


@transaction.atomic
def configure_non_gst_shadow_code_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_non_gst_shadow(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    tenant = get_current_tenant(request)
    if tenant is None:
        return JsonResponse({'message': 'error', 'error': 'Tenant not found.'}, status=400)
    raw_code = str(request.POST.get('accessCode') or '').strip()
    password = str(request.POST.get('password') or '')
    if len(raw_code) != 6 or not raw_code.isdigit():
        return JsonResponse({'message': 'error', 'error': 'Access code must be exactly 6 digits.'}, safe=False)
    if not password:
        return JsonResponse({'message': 'error', 'error': 'Current password is required.'}, safe=False)
    if not request.user.check_password(password):
        return JsonResponse({'message': 'error', 'error': 'Current password is incorrect.'}, safe=False)
    before_snapshot = snapshot_instance(tenant)
    tenant.set_non_gst_shadow_code(raw_code)
    tenant.save(update_fields=['nonGstShadowCodeHash', 'lastUpdatedOn'])
    log_audit_event(
        tenant,
        AuditLog.Action.UPDATE,
        actor=request.user,
        changes={'nonGstShadowCodeHash': {'from': bool(before_snapshot.get('nonGstShadowCodeHash')), 'to': True}},
        snapshot=snapshot_instance(tenant),
    )
    return JsonResponse({'message': 'success'}, safe=False)


@transaction.atomic
def toggle_non_gst_shadow_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_non_gst_shadow(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    tenant = get_current_tenant(request)
    if tenant is None:
        return JsonResponse({'message': 'error', 'error': 'Tenant not found.'}, status=400)
    desired_state = _normalize_bool(request.POST.get('enabled'))
    password = str(request.POST.get('password') or '')
    raw_code = str(request.POST.get('accessCode') or '').strip()
    if not password:
        return JsonResponse({'message': 'error', 'error': 'Current password is required.'}, safe=False)
    if not request.user.check_password(password):
        return JsonResponse({'message': 'error', 'error': 'Current password is incorrect.'}, safe=False)
    if not tenant.nonGstShadowCodeHash:
        return JsonResponse({'message': 'error', 'error': 'Set the 6-digit Non-GST passcode first.'}, safe=False)
    if len(raw_code) != 6 or not raw_code.isdigit():
        return JsonResponse({'message': 'error', 'error': 'Enter the valid 6-digit passcode.'}, safe=False)
    if not check_password(raw_code, tenant.nonGstShadowCodeHash):
        return JsonResponse({'message': 'error', 'error': 'Invalid 6-digit passcode.'}, safe=False)
    before_enabled = bool(tenant.nonGstShadowEnabled)
    tenant.nonGstShadowEnabled = desired_state
    tenant.save(update_fields=['nonGstShadowEnabled', 'lastUpdatedOn'])
    log_audit_event(
        tenant,
        AuditLog.Action.UPDATE,
        actor=request.user,
        changes={'nonGstShadowEnabled': {'from': before_enabled, 'to': desired_state}},
        snapshot=snapshot_instance(tenant),
    )
    return JsonResponse({'message': 'success', 'enabled': desired_state}, safe=False)


@transaction.atomic
def update_tenant_feature_flags_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _request_can_manage_non_gst_shadow(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    tenant = get_current_tenant(request)
    if tenant is None:
        return JsonResponse({'message': 'error', 'error': 'Tenant not found.'}, status=400)
    password = str(request.POST.get('password') or '')
    raw_code = str(request.POST.get('accessCode') or '').strip()
    if not password:
        return JsonResponse({'message': 'error', 'error': 'Current password is required.'}, safe=False)
    if not request.user.check_password(password):
        return JsonResponse({'message': 'error', 'error': 'Current password is incorrect.'}, safe=False)
    if not tenant.nonGstShadowCodeHash:
        return JsonResponse({'message': 'error', 'error': 'Set the 6-digit Non-GST passcode first.'}, safe=False)
    if len(raw_code) != 6 or not raw_code.isdigit():
        return JsonResponse({'message': 'error', 'error': 'Enter the valid 6-digit passcode.'}, safe=False)
    if not check_password(raw_code, tenant.nonGstShadowCodeHash):
        return JsonResponse({'message': 'error', 'error': 'Invalid 6-digit passcode.'}, safe=False)
    before_snapshot = snapshot_instance(tenant)
    tenant.inventoryEnabled = _normalize_bool(request.POST.get('inventoryEnabled'))
    tenant.advancedInvoicingEnabled = _normalize_bool(request.POST.get('advancedInvoicingEnabled'))
    tenant.save(update_fields=['inventoryEnabled', 'advancedInvoicingEnabled', 'lastUpdatedOn'])
    log_audit_event(
        tenant,
        AuditLog.Action.UPDATE,
        actor=request.user,
        changes={
            'inventoryEnabled': {'from': bool(before_snapshot.get('inventoryEnabled', True)), 'to': tenant.inventoryEnabled},
            'advancedInvoicingEnabled': {'from': bool(before_snapshot.get('advancedInvoicingEnabled', True)), 'to': tenant.advancedInvoicingEnabled},
        },
        snapshot=snapshot_instance(tenant),
    )
    return JsonResponse({'message': 'success', 'data': {
        'inventoryEnabled': tenant.inventoryEnabled,
        'advancedInvoicingEnabled': tenant.advancedInvoicingEnabled,
    }}, safe=False)


def _normalize_sales_mode(mode: str | None) -> str:
    value = str(mode or Sales.Mode.CASH).strip()
    aliases = {
        'Card': Sales.Mode.ONLINE,
        'Card/Online': Sales.Mode.ONLINE,
    }
    if value in aliases:
        return aliases[value]
    valid = {choice[0] for choice in Sales.Mode.choices}
    return value if value in valid else Sales.Mode.CASH


def _parse_sales_party(request, party_value: str | None) -> Party:
    raw = str(party_value or '').strip()
    if not raw:
        raise ValueError('Party is required.')
    if '@' in raw:
        raw = raw.rsplit('@', 1)[-1].strip()
    if not raw.isdigit():
        raise ValueError('Invalid party selection.')
    return tenant_qs(request, Party).select_related().get(pk=int(raw))


def _parse_sales_bank_id(request, bank_value: str | None) -> int | None:
    raw = str(bank_value or '').strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError('Invalid bank selection.')
    bank = tenant_qs(request, Bank).filter(isDeleted=False, pk=int(raw)).only('id').first()
    if not bank:
        raise ValueError('Selected bank was not found.')
    return bank.pk


def _safe_sales_float(value) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _safe_local_date():
    now_value = timezone.now()
    return timezone.localtime(now_value).date() if timezone.is_aware(now_value) else now_value.date()


def _normalize_gst_number(raw_value) -> str:
    return str(raw_value or '').strip().upper()


UNION_TERRITORY_GST_STATE_CODES = {'04', '26', '31', '35', '38', '97'}
GST_STATE_CODE_NAME_MAP = {
    '01': 'jammu and kashmir',
    '02': 'himachal pradesh',
    '03': 'punjab',
    '04': 'chandigarh',
    '05': 'uttarakhand',
    '06': 'haryana',
    '07': 'delhi',
    '08': 'rajasthan',
    '09': 'uttar pradesh',
    '10': 'bihar',
    '11': 'sikkim',
    '12': 'arunachal pradesh',
    '13': 'nagaland',
    '14': 'manipur',
    '15': 'mizoram',
    '16': 'tripura',
    '17': 'meghalaya',
    '18': 'assam',
    '19': 'west bengal',
    '20': 'jharkhand',
    '21': 'odisha',
    '22': 'chhattisgarh',
    '23': 'madhya pradesh',
    '24': 'gujarat',
    '26': 'dadra and nagar haveli and daman and diu',
    '27': 'maharashtra',
    '29': 'karnataka',
    '30': 'goa',
    '31': 'lakshadweep',
    '32': 'kerala',
    '33': 'tamil nadu',
    '34': 'puducherry',
    '35': 'andaman and nicobar islands',
    '36': 'telangana',
    '37': 'andhra pradesh',
    '38': 'ladakh',
    '97': 'other territory',
}


def _gst_state_code_from_number(gst_number: str | None) -> str:
    gst_number = _normalize_gst_number(gst_number)
    if len(gst_number) >= 2 and gst_number[:2].isdigit():
        return gst_number[:2]
    return ''


def _is_utgst_state_code(state_code: str | None) -> bool:
    return str(state_code or '').zfill(2) in UNION_TERRITORY_GST_STATE_CODES


def _normalize_place_of_supply_value(value: str | None) -> str:
    normalized = str(value or '').strip().lower()
    normalized = normalized.replace('&', 'and')
    normalized = ' '.join(normalized.replace('-', ' ').replace(',', ' ').split())
    return normalized


def _gst_state_code_from_place_of_supply(place_of_supply: str | None) -> str:
    normalized = _normalize_place_of_supply_value(place_of_supply)
    if not normalized:
        return ''
    if len(normalized) >= 2 and normalized[:2].isdigit():
        return normalized[:2]
    for code, state_name in GST_STATE_CODE_NAME_MAP.items():
        if normalized == state_name or normalized.startswith(state_name + ' ') or state_name in normalized:
            return code
    return ''


def _gst_breakup_from_sale(sale: Sales, tenant) -> dict:
    seller_state_code = _gst_state_code_from_number(getattr(tenant, 'gstNumber', None))
    buyer_state_code = _gst_state_code_from_number(getattr(sale, 'buyerGstNumber', None))
    place_of_supply_state_code = _gst_state_code_from_place_of_supply(getattr(sale, 'placeOfSupply', None))
    effective_buyer_state_code = buyer_state_code or place_of_supply_state_code
    taxable_total = round(float(sale.taxableAmount or 0.0), 2)
    gst_total = round(float(sale.gstAmount or 0.0), 2)
    is_interstate = bool(seller_state_code and effective_buyer_state_code and seller_state_code != effective_buyer_state_code)
    breakdown = {
        'seller_state_code': seller_state_code,
        'buyer_state_code': buyer_state_code,
        'place_of_supply_state_code': place_of_supply_state_code,
        'effective_buyer_state_code': effective_buyer_state_code,
        'tax_mode_label': 'IGST' if is_interstate else ('CGST + UTGST' if _is_utgst_state_code(seller_state_code) else 'CGST + SGST'),
        'cgst_total': 0.0,
        'sgst_total': 0.0,
        'utgst_total': 0.0,
        'igst_total': 0.0,
        'taxable_total': taxable_total,
        'gst_total': gst_total,
    }
    if is_interstate:
        breakdown['igst_total'] = gst_total
        return breakdown
    half_tax = round(gst_total / 2.0, 2)
    breakdown['cgst_total'] = half_tax
    other_half = round(gst_total - half_tax, 2)
    if _is_utgst_state_code(seller_state_code):
        breakdown['utgst_total'] = other_half
    else:
        breakdown['sgst_total'] = other_half
    return breakdown


def _gst_breakup_from_cash_counter(counter: CashCounter, tenant) -> dict:
    seller_state_code = _gst_state_code_from_number(getattr(tenant, 'gstNumber', None))
    buyer_state_code = _gst_state_code_from_number(getattr(counter, 'buyerGstNumber', None))
    place_of_supply_state_code = _gst_state_code_from_place_of_supply(getattr(counter, 'placeOfSupply', None))
    effective_buyer_state_code = buyer_state_code or place_of_supply_state_code
    taxable_total = round(float(counter.taxableAmount or 0.0), 2)
    gst_total = round(float(counter.gstAmount or 0.0), 2)
    is_interstate = bool(seller_state_code and effective_buyer_state_code and seller_state_code != effective_buyer_state_code)
    breakdown = {
        'seller_state_code': seller_state_code,
        'buyer_state_code': buyer_state_code,
        'place_of_supply_state_code': place_of_supply_state_code,
        'effective_buyer_state_code': effective_buyer_state_code,
        'tax_mode_label': 'IGST' if is_interstate else ('CGST + UTGST' if _is_utgst_state_code(seller_state_code) else 'CGST + SGST'),
        'cgst_total': 0.0,
        'sgst_total': 0.0,
        'utgst_total': 0.0,
        'igst_total': 0.0,
        'taxable_total': taxable_total,
        'gst_total': gst_total,
    }
    if is_interstate:
        breakdown['igst_total'] = gst_total
        return breakdown
    half_tax = round(gst_total / 2.0, 2)
    breakdown['cgst_total'] = half_tax
    other_half = round(gst_total - half_tax, 2)
    if _is_utgst_state_code(seller_state_code):
        breakdown['utgst_total'] = other_half
    else:
        breakdown['sgst_total'] = other_half
    return breakdown


def _cash_counter_invoice_action_buttons(item: CashCounter, user) -> str:
    actions = []
    if item.mode in _cash_counter_billing_modes() and getattr(item, 'invoiceNumber', None):
        actions.append(
            '''<a href="/api/generate_cash_counter_invoice_pdf/?id={}" data-inverted="" data-tooltip="Download Invoice" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular teal icon button"><i class="file pdf outline icon"></i></a>'''.format(item.pk)
        )
    if _can_manage_cash_counter_records(user):
        actions.append(
            '''<a href="/edit_cash_counter/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button green"><i class="pen icon"></i></a>'''.format(item.pk)
        )
        actions.append(
            '''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk)
        )
    elif not actions:
        return '<div class="ui tiny label">Denied</div>'
    return ''.join(actions)


def _normalize_invoice_kind(raw_value: str | None, party: Party | None = None, *, series: InvoiceSeries | None = None, request=None) -> str:
    value = str(raw_value or '').strip().lower()
    valid = {choice[0] for choice in Sales.InvoiceKind.choices}
    series_nature = getattr(series, 'invoiceNature', InvoiceSeries.InvoiceNature.MIXED)
    advanced_enabled = _advanced_invoicing_enabled(request) if request is not None else True
    if advanced_enabled and series is not None and getattr(series, 'lockInvoiceNature', False) and series_nature in valid:
        if value in valid and value != series_nature:
            raise ValueError(f'Invoice series {series.seriesCode} is locked for {series.get_invoiceNature_display()} invoices.')
        return series_nature
    if value in valid:
        return value
    if advanced_enabled and series_nature in valid:
        return series_nature
    if party and getattr(party, 'isB2B', False):
        return Sales.InvoiceKind.B2B
    return Sales.InvoiceKind.B2C


def _request_line_items_present(request) -> bool:
    return 'lineItems' in request.POST


def _request_line_items_payload(request):
    raw = request.POST.get('lineItems')
    if raw is None or str(raw).strip() == '':
        return []
    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise ValueError('Invalid lineItems payload.') from exc
    if not isinstance(payload, list):
        raise ValueError('lineItems payload must be a list.')
    return payload


def _parse_inventory_category(request, raw_category_id):
    raw = str(raw_category_id or '').strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError('Invalid category selection.')
    category = tenant_qs(request, InventoryCategory).filter(isDeleted=False, pk=int(raw)).first()
    if category is None:
        raise ValueError('Inventory category not found.')
    return category


def _parse_inventory_tax_profile(request, raw_tax_profile_id):
    raw = str(raw_tax_profile_id or '').strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError('Invalid HSN / tax selection.')
    tax_profile = tenant_qs(request, InventoryTaxProfile).filter(isDeleted=False, pk=int(raw)).first()
    if tax_profile is None:
        raise ValueError('Inventory tax profile not found.')
    return tax_profile


def _parse_inventory_unit(request, raw_unit_id):
    raw = str(raw_unit_id or '').strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError('Invalid unit selection.')
    unit = tenant_qs(request, InventoryUnit).filter(isDeleted=False, pk=int(raw)).first()
    if unit is None:
        raise ValueError('Inventory unit not found.')
    return unit


def _apply_inventory_category_payload(request, category):
    category.name = (request.POST.get('name') or '').strip()
    if not category.name:
        raise ValueError('Category name is required.')
    category.code = (request.POST.get('code') or '').strip() or None
    category.description = (request.POST.get('description') or '').strip() or None
    category.isActive = str(request.POST.get('isActive') or '1').lower() not in {'0', 'false', 'no', 'off'}


def _apply_inventory_tax_profile_payload(request, tax_profile):
    tax_profile.name = (request.POST.get('name') or '').strip()
    if not tax_profile.name:
        raise ValueError('Tax profile name is required.')
    tax_profile.hsnCode = (request.POST.get('hsnCode') or '').strip()
    if not tax_profile.hsnCode:
        raise ValueError('HSN code is required.')
    tax_profile.gstRate = _safe_sales_float(request.POST.get('gstRate'))
    tax_profile.cessRate = _safe_sales_float(request.POST.get('cessRate'))
    tax_profile.description = (request.POST.get('description') or '').strip() or None
    tax_profile.isActive = str(request.POST.get('isActive') or '1').lower() not in {'0', 'false', 'no', 'off'}


def _apply_inventory_unit_payload(request, unit):
    unit.name = (request.POST.get('name') or '').strip()
    if not unit.name:
        raise ValueError('Unit name is required.')
    unit.symbol = (request.POST.get('symbol') or '').strip()
    if not unit.symbol:
        raise ValueError('Unit symbol is required.')
    unit.allowsDecimal = str(request.POST.get('allowsDecimal') or '').lower() in {'1', 'true', 'yes', 'on'}
    unit.description = (request.POST.get('description') or '').strip() or None
    unit.isActive = str(request.POST.get('isActive') or '1').lower() not in {'0', 'false', 'no', 'off'}


def _apply_inventory_item_payload(request, item):
    item.name = (request.POST.get('name') or '').strip()
    if not item.name:
        raise ValueError('Item name is required.')
    item.sku = (request.POST.get('sku') or '').strip() or None
    item.barcode = (request.POST.get('barcode') or '').strip() or None
    item.category = _parse_inventory_category(request, request.POST.get('categoryId'))
    item.unit = _parse_inventory_unit(request, request.POST.get('unitId'))
    item.taxProfile = _parse_inventory_tax_profile(request, request.POST.get('taxProfileId'))
    item.categoryName = (request.POST.get('categoryName') or '').strip() or None
    if item.category_id:
        item.categoryName = item.category.name
    item.brandName = (request.POST.get('brandName') or '').strip() or None
    item.unitName = (request.POST.get('unitName') or '').strip() or 'pcs'
    if item.unit_id:
        item.unitName = item.unit.symbol
    item.hsnCode = (request.POST.get('hsnCode') or '').strip() or None
    item.description = (request.POST.get('description') or '').strip() or None
    item.defaultStockBucket = _normalize_stock_bucket(request, request.POST.get('defaultStockBucket'), item)
    item.gstRate = _safe_sales_float(request.POST.get('gstRate'))
    if item.taxProfile_id:
        item.hsnCode = item.taxProfile.hsnCode
        item.gstRate = item.taxProfile.gstRate
    item.defaultPurchasePrice = _safe_sales_float(request.POST.get('defaultPurchasePrice'))
    item.defaultSalesPrice = _safe_sales_float(request.POST.get('defaultSalesPrice'))
    item.mrp = _safe_sales_float(request.POST.get('mrp'))
    item.minimumStockLevel = _safe_sales_float(request.POST.get('minimumStockLevel'))
    item.reorderLevel = _safe_sales_float(request.POST.get('reorderLevel'))
    item.requiresBatchTracking = str(request.POST.get('requiresBatchTracking') or '').lower() in {'1', 'true', 'yes', 'on'}
    item.isSaleBlocked = str(request.POST.get('isSaleBlocked') or '').lower() in {'1', 'true', 'yes', 'on'}


def _line_items_total(request) -> float:
    total = 0.0
    for row in _request_line_items_payload(request):
        total += _safe_sales_float(row.get('lineTotal'))
    return round(total, 2)


def _sales_totals_from_lines(lines):
    taxable_total = 0.0
    gst_total = 0.0
    grand_total = 0.0
    for row in lines:
        taxable_total += _safe_sales_float(row.get('taxableAmount'))
        gst_total += _safe_sales_float(row.get('gstAmount'))
        grand_total += _safe_sales_float(row.get('lineTotal'))
    return round(taxable_total, 2), round(gst_total, 2), round(grand_total, 2)


def _validate_inventory_quantity_for_unit(item: InventoryItem, quantity: float, *, label='Quantity'):
    if quantity <= 0:
        return
    unit = getattr(item, 'unit', None)
    allows_decimal = True if unit is None else bool(getattr(unit, 'allowsDecimal', False))
    if allows_decimal:
        return
    if abs(quantity - round(quantity)) > 0.000001:
        unit_label = getattr(unit, 'symbol', None) or item.unitName or 'unit'
        raise ValueError(f'{label} for {item.name} must be a whole number because unit {unit_label} does not allow decimals.')


def _parse_inventory_item(request, raw_item_id):
    _assert_inventory_enabled(request)
    raw = str(raw_item_id or '').strip()
    if not raw.isdigit():
        raise ValueError('Invalid inventory item.')
    item = tenant_qs(request, InventoryItem).filter(isDeleted=False, pk=int(raw)).first()
    if item is None:
        raise ValueError('Inventory item not found.')
    return item


def _parse_inventory_warehouse(request, raw_warehouse_id=None):
    _assert_inventory_enabled(request)
    tenant = get_current_tenant(request)
    raw = str(raw_warehouse_id or '').strip()
    if not raw:
        return get_or_create_default_warehouse(tenant)
    if not raw.isdigit():
        raise ValueError('Invalid warehouse selection.')
    warehouse = InventoryWarehouse.objects.filter(
        tenant=tenant,
        isDeleted=False,
        isActive=True,
        pk=int(raw),
    ).first()
    if warehouse is None:
        raise ValueError('Warehouse not found.')
    return warehouse


def _normalize_stock_bucket(request, raw_bucket, item: InventoryItem) -> str:
    bucket = str(raw_bucket or '').strip().lower()
    valid = {choice[0] for choice in InventoryItem.StockBucket.choices}
    if bucket in valid:
        if bucket == InventoryItem.StockBucket.NON_GST:
            _assert_non_gst_enabled(request, label='Non-GST stock bucket')
        return bucket
    bucket = item.defaultStockBucket or InventoryItem.StockBucket.GST
    if bucket == InventoryItem.StockBucket.NON_GST:
        _assert_non_gst_enabled(request, label='Non-GST stock bucket')
    return bucket


def _parse_inventory_batch_date(value):
    raw = str(value or '').strip()
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
    raise ValueError('Invalid batch date. Use DD/MM/YYYY.')


def _available_batch_stock_queryset(*, item, stock_bucket, warehouse=None, today=None):
    today = today or _safe_local_date()
    qs = InventoryBatchStock.objects.filter(
        tenant=item.tenant,
        isDeleted=False,
        item=item,
        stockBucket=stock_bucket,
        quantityOnHand__gt=0,
        batch__isDeleted=False,
        batch__isActive=True,
    ).filter(
        Q(batch__expiryDate__isnull=True) | Q(batch__expiryDate__gte=today)
    ).annotate(
        expiry_missing=Case(
            When(batch__expiryDate__isnull=True, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    ).order_by('expiry_missing', 'batch__expiryDate', 'batch__batchNumber', 'batch_id')
    if warehouse is not None:
        qs = qs.filter(warehouse=warehouse)
    return qs


def _earliest_available_batch_for_fefo(*, item, stock_bucket, warehouse=None, today=None):
    row = _available_batch_stock_queryset(
        item=item,
        stock_bucket=stock_bucket,
        warehouse=warehouse,
        today=today,
    ).select_related('batch').first()
    return row.batch if row else None


def _resolve_inventory_batch(request, row, *, item, stock_bucket, warehouse=None, quantity=0.0, direction='in'):
    raw_batch_id = str(row.get('batchId') or row.get('batch_id') or '').strip()
    batch_number = str(row.get('batchNumber') or row.get('batchNo') or row.get('batch') or '').strip()
    manufacture_date = _parse_inventory_batch_date(row.get('manufactureDate') or row.get('mfgDate'))
    expiry_date = _parse_inventory_batch_date(row.get('expiryDate') or row.get('expDate'))
    purchase_rate = _safe_sales_float(row.get('purchaseRate') or row.get('unitCost') or row.get('unitPrice'))
    mrp = _safe_sales_float(row.get('batchMrp') or row.get('mrp'))
    if manufacture_date and expiry_date and expiry_date < manufacture_date:
        raise ValueError(f'Expiry date cannot be before manufacture date for {item.name}.')

    if raw_batch_id:
        if not raw_batch_id.isdigit():
            raise ValueError('Invalid batch selection.')
        batch = tenant_qs(request, InventoryBatch).filter(isDeleted=False, pk=int(raw_batch_id), item=item).first()
        if batch is None:
            raise ValueError('Selected batch was not found.')
        if direction == 'out' and batch.expiryDate and batch.expiryDate < _safe_local_date():
            raise ValueError(f'Batch {batch.batchNumber or batch.pk} for {item.name} is expired.')
        if direction == 'out' and warehouse is not None:
            available = InventoryBatchStock.objects.filter(
                tenant=item.tenant,
                isDeleted=False,
                batch=batch,
                warehouse=warehouse,
            ).values_list('quantityOnHand', flat=True).first()
            if available is None or float(available or 0.0) <= 0:
                raise ValueError(f'Batch {batch.batchNumber or batch.pk} for {item.name} is not available in {warehouse.name}.')
        if direction == 'out' and item.requiresBatchTracking:
            fefo_batch = _earliest_available_batch_for_fefo(item=item, stock_bucket=stock_bucket, warehouse=warehouse)
            if fefo_batch is not None and fefo_batch.pk != batch.pk:
                raise ValueError(
                    f'FEFO requires selling batch {fefo_batch.batchNumber or fefo_batch.pk} for {item.name} before {batch.batchNumber or batch.pk}.'
                )
        return batch

    has_batch_details = bool(batch_number or manufacture_date or expiry_date)
    if direction == 'out':
        if not has_batch_details and not item.requiresBatchTracking:
            return None
        batch_stock_qs = _available_batch_stock_queryset(
            item=item,
            stock_bucket=stock_bucket,
            warehouse=warehouse,
            today=_safe_local_date(),
        )
        if batch_number:
            batch_stock_qs = batch_stock_qs.filter(batch__batchNumber=batch_number)
        batch_stock = batch_stock_qs.select_related('batch').first()
        if batch_stock is None:
            raise ValueError(f'No available non-expired batch found for {item.name}.')
        return batch_stock.batch

    if not has_batch_details and not item.requiresBatchTracking:
        return None
    if item.requiresBatchTracking and not batch_number:
        raise ValueError(f'Batch number is required for {item.name}.')
    if item.requiresBatchTracking and not expiry_date:
        raise ValueError(f'Expiry date is required for batch-tracked item {item.name}.')
    if batch_number:
        existing_batch = InventoryBatch.objects.filter(
            tenant=item.tenant,
            item=item,
            stockBucket=stock_bucket,
            batchNumber=batch_number,
            isDeleted=False,
        ).first()
        has_date_conflict = existing_batch is not None and (
            existing_batch.manufactureDate != manufacture_date or existing_batch.expiryDate != expiry_date
        )
        has_rate_conflict = (
            existing_batch is not None
            and purchase_rate
            and float(existing_batch.purchaseRate or 0.0)
            and round(float(existing_batch.purchaseRate or 0.0), 2) != round(purchase_rate, 2)
        )
        has_mrp_conflict = (
            existing_batch is not None
            and mrp
            and float(existing_batch.mrp or 0.0)
            and round(float(existing_batch.mrp or 0.0), 2) != round(mrp, 2)
        )
        if has_date_conflict or has_rate_conflict or has_mrp_conflict:
            raise ValueError(
                f'Batch {batch_number} already exists for {item.name} with different manufacture, expiry, rate, or MRP details.'
            )
    batch, _created = InventoryBatch.objects.get_or_create(
        tenant=item.tenant,
        item=item,
        stockBucket=stock_bucket,
        batchNumber=batch_number or None,
        manufactureDate=manufacture_date,
        expiryDate=expiry_date,
        defaults={
            'warehouse': warehouse,
            'purchaseRate': purchase_rate,
            'mrp': mrp,
            'isActive': True,
        },
    )
    update_fields = []
    if warehouse and batch.warehouse_id is None:
        batch.warehouse = warehouse
        update_fields.append('warehouse')
    if purchase_rate and float(batch.purchaseRate or 0.0) != purchase_rate:
        batch.purchaseRate = purchase_rate
        update_fields.append('purchaseRate')
    if mrp and float(batch.mrp or 0.0) != mrp:
        batch.mrp = mrp
        update_fields.append('mrp')
    if update_fields:
        update_fields.append('lastUpdatedOn')
        batch.save(update_fields=update_fields)
    return batch


def _staff_for_request(request):
    return tenant_qs(request, StaffUser).get(user_ID_id=request.user.pk)


def _apply_sales_invoice_payload(request, sale: Sales, party: Party | None, *, line_items=None) -> None:
    line_items = line_items if line_items is not None else (_request_line_items_payload(request) if _request_line_items_present(request) else [])
    if _advanced_invoicing_enabled(request):
        sale.invoiceKind = _normalize_invoice_kind(
            request.POST.get('invoiceKind'),
            party,
            series=getattr(sale, 'invoiceSeries', None),
            request=request,
        )
        buyer_legal_name = (request.POST.get('buyerLegalName') or '').strip()
        if not buyer_legal_name and party:
            buyer_legal_name = (party.legalName or party.name or '').strip()
        sale.buyerLegalName = buyer_legal_name or None

        buyer_gst_number = _normalize_gst_number(request.POST.get('buyerGstNumber'))
        if not buyer_gst_number and party:
            buyer_gst_number = _normalize_gst_number(party.gstNumber)
        sale.buyerGstNumber = buyer_gst_number or None

        buyer_billing_address = (request.POST.get('buyerBillingAddress') or '').strip()
        if not buyer_billing_address and party:
            buyer_billing_address = (party.billingAddress or party.address or '').strip()
        sale.buyerBillingAddress = buyer_billing_address or None

        sale.placeOfSupply = (request.POST.get('placeOfSupply') or '').strip() or None
    else:
        sale.invoiceKind = Sales.InvoiceKind.B2C
        sale.buyerLegalName = None
        sale.buyerGstNumber = None
        sale.buyerBillingAddress = None
        sale.placeOfSupply = None

    if line_items:
        sale.taxableAmount, sale.gstAmount, computed_total = _sales_totals_from_lines(line_items)
        sale.roundOffAmount, payable_total = _sales_round_off_from_total(computed_total)
        if sale.modeOfPayment != Sales.Mode.MIX:
            sale.amount = payable_total
        elif abs(_safe_sales_float(sale.amount) - payable_total) > 0.01:
            raise ValueError(f'Mix sales total must match rounded payable amount of {payable_total:.2f}.')
    else:
        sale.taxableAmount = round(float(sale.amount or 0.0), 2)
        sale.gstAmount = 0.0
        sale.roundOffAmount = 0.0

    if sale.invoiceKind == Sales.InvoiceKind.B2C:
        sale.buyerGstNumber = None


def _cash_counter_billing_modes() -> set[str]:
    return {'Cash', 'Credit', 'Card', 'Mix', 'Return'}


def _cash_counter_return_source_modes() -> set[str]:
    return {'Cash', 'Credit', 'Card', 'Mix'}


def _parse_return_source_ref(raw_value: str | None):
    raw = str(raw_value or '').strip()
    if not raw:
        return None, None
    if ':' in raw:
        source_type, raw_id = raw.split(':', 1)
        source_type = str(source_type or '').strip().lower()
        raw_id = str(raw_id or '').strip()
        if raw_id.isdigit() and source_type in {'sale', 'cash_counter'}:
            return source_type, int(raw_id)
        return None, None
    if raw.isdigit():
        return 'sale', int(raw)
    return None, None


def list_sales_return_source_sales_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _has_return_source_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    allow_non_gst = _non_gst_shadow_enabled(request)
    data = []
    if _has_sales_access(request.user):
        qs = _sales_list_base_qs(request).filter(isDeleted=False).order_by('-buildDate', '-id')[:150]
        if not _can_filter_all_sales(request.user):
            qs = qs.filter(createdBy__user_ID_id=request.user.pk)
        for sale in qs:
            line_qs = sale.lines.filter(isDeleted=False)
            if not allow_non_gst:
                line_qs = line_qs.exclude(stockBucket=InventoryItem.StockBucket.NON_GST)
            if not line_qs.exists():
                continue
            party_name = getattr(getattr(sale, 'partyID', None), 'name', None) or 'Walk-in'
            invoice_no = sale.invoiceNumber or sale.paymentID or f'Sale#{sale.pk}'
            build_date = sale.buildDate.strftime('%d/%m/%Y') if sale.buildDate else ''
            data.append({
                'ID': sale.pk,
                'SourceType': 'sale',
                'SourceValue': f'sale:{sale.pk}',
                'InvoiceNumber': invoice_no,
                'PartyName': party_name,
                'BuildDate': build_date,
                'Amount': float(sale.amount or 0.0),
                'DisplayDetail': f'{invoice_no} | {party_name} | {build_date}',
            })
    cash_qs = tenant_qs(request, CashCounter).filter(
        isDeleted=False,
        mode__in=_cash_counter_return_source_modes(),
    ).order_by('-entryDate', '-id')[:150]
    if not _can_filter_all_cash_counter(request.user):
        cash_qs = cash_qs.filter(createdBy__user_ID_id=request.user.pk)
    for counter in cash_qs:
        line_qs = counter.lines.filter(isDeleted=False)
        if not allow_non_gst:
            line_qs = line_qs.exclude(stockBucket=InventoryItem.StockBucket.NON_GST)
        if not line_qs.exists():
            continue
        party_name = getattr(getattr(counter, 'partyID', None), 'name', None) or 'Walk-in'
        invoice_no = counter.invoiceNumber or counter.counterID or f'Counter#{counter.pk}'
        build_date = counter.entryDate.strftime('%d/%m/%Y') if counter.entryDate else ''
        data.append({
            'ID': counter.pk,
            'SourceType': 'cash_counter',
            'SourceValue': f'cash_counter:{counter.pk}',
            'InvoiceNumber': invoice_no,
            'PartyName': party_name,
            'BuildDate': build_date,
            'Amount': float(counter.amount or 0.0),
            'DisplayDetail': f'{invoice_no} | {party_name} | {build_date} | Cash Counter',
        })
    data.sort(key=lambda row: (row.get('BuildDate') or '', row.get('ID') or 0), reverse=True)
    return JsonResponse({'message': 'success', 'data': data}, safe=False)


def get_sales_return_source_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _has_return_source_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    source_type, source_id = _parse_return_source_ref(request.GET.get('sourceId') or request.GET.get('saleId'))
    if not source_id:
        return JsonResponse({'message': 'error', 'error': 'Original invoice is required.'}, status=400, safe=False)
    allow_non_gst = _non_gst_shadow_enabled(request)
    exclude_voucher_id = request.GET.get('excludeVoucherId')
    if source_type == 'cash_counter':
        source_obj = get_object_or_404(
            tenant_qs(request, CashCounter).select_related('partyID', 'createdBy', 'invoiceSeries'),
            pk=source_id,
            isDeleted=False,
        )
        if not _can_filter_all_cash_counter(request.user):
            if getattr(getattr(source_obj, 'createdBy', None), 'user_ID_id', None) != request.user.pk:
                return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        return_qs = tenant_qs(request, SalesReturnVoucher).filter(isDeleted=False, cashCounter=source_obj)
        line_qs = source_obj.lines.filter(isDeleted=False).select_related('item', 'batch').order_by('id')
        if not allow_non_gst:
            line_qs = line_qs.exclude(stockBucket=InventoryItem.StockBucket.NON_GST)
    else:
        source_obj = get_object_or_404(
            tenant_qs(request, Sales).select_related('partyID', 'createdBy', 'invoiceSeries'),
            pk=source_id,
            isDeleted=False,
        )
        if not _can_filter_all_sales(request.user):
            if getattr(getattr(source_obj, 'createdBy', None), 'user_ID_id', None) != request.user.pk:
                return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        return_qs = tenant_qs(request, SalesReturnVoucher).filter(isDeleted=False, sales=source_obj)
        line_qs = source_obj.lines.filter(isDeleted=False).select_related('item', 'batch').order_by('id')
        if not allow_non_gst:
            line_qs = line_qs.exclude(stockBucket=InventoryItem.StockBucket.NON_GST)
    if exclude_voucher_id and str(exclude_voucher_id).isdigit():
        return_qs = return_qs.exclude(pk=int(exclude_voucher_id))
    returned_map = defaultdict(float)
    for row in SalesReturnLine.objects.filter(
        tenant=source_obj.tenant,
        isDeleted=False,
        voucher__in=return_qs,
    ).values('item_id', 'stockBucket').annotate(total_qty=Sum('quantity')):
        returned_map[(row['item_id'], row['stockBucket'])] = float(row['total_qty'] or 0.0)

    lines = []
    for line in line_qs:
        key = (line.item_id, line.stockBucket)
        sold_qty = float(line.quantity or 0.0)
        prior_remaining = float(returned_map.get(key, 0.0))
        already_returned = min(prior_remaining, sold_qty)
        returned_map[key] = max(0.0, prior_remaining - already_returned)
        remaining_qty = max(0.0, sold_qty - already_returned)
        lines.append({
            'itemId': line.item_id,
            'itemName': line.item.name,
            'batchId': line.batch_id,
            'batchNumber': line.batch.batchNumber if line.batch_id else '',
            'manufactureDate': line.batch.manufactureDate.strftime('%d/%m/%Y') if line.batch_id and line.batch.manufactureDate else '',
            'expiryDate': line.batch.expiryDate.strftime('%d/%m/%Y') if line.batch_id and line.batch.expiryDate else '',
            'batchMrp': float(line.batch.mrp or 0.0) if line.batch_id else 0.0,
            'stockBucket': line.stockBucket,
            'quantity': sold_qty,
            'returnedQuantity': already_returned,
            'remainingQuantity': remaining_qty,
            'unitPrice': float(line.unitPrice or 0.0),
            'discountPercent': float(line.discountPercent or 0.0),
            'taxableAmount': float(line.taxableAmount or 0.0),
            'gstRate': float(line.gstRate or 0.0),
            'gstAmount': float(line.gstAmount or 0.0),
            'lineTotal': float(line.lineTotal or 0.0),
            'remark': line.remark or '',
        })
    return JsonResponse({'message': 'success', 'data': {
        'ID': source_obj.pk,
        'SourceType': source_type or 'sale',
        'SourceValue': f'{source_type or "sale"}:{source_obj.pk}',
        'InvoiceNumber': (
            source_obj.invoiceNumber or source_obj.paymentID or f'Sale#{source_obj.pk}'
            if source_type != 'cash_counter'
            else source_obj.invoiceNumber or source_obj.counterID or f'Counter#{source_obj.pk}'
        ),
        'BuildDate': (
            source_obj.buildDate.strftime('%d/%m/%Y') if getattr(source_obj, 'buildDate', None)
            else (source_obj.entryDate.strftime('%d/%m/%Y') if getattr(source_obj, 'entryDate', None) else '')
        ),
        'PartyID': source_obj.partyID_id,
        'PartyName': getattr(getattr(source_obj, 'partyID', None), 'name', None) or '',
        'BuyerLegalName': source_obj.buyerLegalName or getattr(getattr(source_obj, 'partyID', None), 'legalName', None) or '',
        'BuyerGstNumber': source_obj.buyerGstNumber or getattr(getattr(source_obj, 'partyID', None), 'gstNumber', None) or '',
        'BuyerBillingAddress': source_obj.buyerBillingAddress or getattr(getattr(source_obj, 'partyID', None), 'billingAddress', None) or getattr(getattr(source_obj, 'partyID', None), 'address', None) or '',
        'PlaceOfSupply': source_obj.placeOfSupply or '',
        'InvoiceKind': source_obj.invoiceKind or Sales.InvoiceKind.B2C,
        'Lines': lines,
    }}, safe=False)


def _apply_cash_counter_invoice_payload(request, counter: CashCounter, party: Party | None, *, line_items=None) -> None:
    line_items = line_items if line_items is not None else (_request_line_items_payload(request) if _request_line_items_present(request) else [])
    billing_mode = str(counter.mode or '') in _cash_counter_billing_modes()
    if billing_mode and _advanced_invoicing_enabled(request):
        counter.invoiceKind = _normalize_invoice_kind(
            request.POST.get('invoiceKind'),
            party,
            series=getattr(counter, 'invoiceSeries', None),
            request=request,
        )
        buyer_legal_name = (request.POST.get('buyerLegalName') or '').strip()
        if not buyer_legal_name and party:
            buyer_legal_name = (party.legalName or party.name or '').strip()
        counter.buyerLegalName = buyer_legal_name or None

        buyer_gst_number = _normalize_gst_number(request.POST.get('buyerGstNumber'))
        if not buyer_gst_number and party:
            buyer_gst_number = _normalize_gst_number(party.gstNumber)
        counter.buyerGstNumber = buyer_gst_number or None

        buyer_billing_address = (request.POST.get('buyerBillingAddress') or '').strip()
        if not buyer_billing_address and party:
            buyer_billing_address = (party.billingAddress or party.address or '').strip()
        counter.buyerBillingAddress = buyer_billing_address or None

        counter.placeOfSupply = (request.POST.get('placeOfSupply') or '').strip() or None
    else:
        counter.invoiceKind = CashCounter.InvoiceKind.B2C
        counter.buyerLegalName = None
        counter.buyerGstNumber = None
        counter.buyerBillingAddress = None
        counter.placeOfSupply = None

    if billing_mode and line_items:
        counter.taxableAmount, counter.gstAmount, computed_total = _sales_totals_from_lines(line_items)
        counter.roundOffAmount, payable_total = _sales_round_off_from_total(computed_total)
        if counter.mode != 'Mix':
            counter.amount = payable_total
        elif abs((_safe_sales_float(counter.mixCashAmount) + _safe_sales_float(counter.mixCardAmount)) - payable_total) > 0.01:
            raise ValueError(f'Mix cash counter total must match rounded payable amount of {payable_total:.2f}.')
    else:
        base_amount = _safe_sales_float(counter.amount if counter.mode != 'Mix' else (_safe_sales_float(counter.mixCashAmount) + _safe_sales_float(counter.mixCardAmount)))
        counter.taxableAmount = round(base_amount, 2)
        counter.gstAmount = 0.0
        counter.roundOffAmount = 0.0

    if counter.invoiceKind == CashCounter.InvoiceKind.B2C:
        counter.buyerGstNumber = None


def _sync_sales_lines(request, sale: Sales):
    if not _inventory_feature_enabled(request) or not _request_line_items_present(request):
        return
    existing_pairs = list(sale.lines.filter(isDeleted=False).values_list('item_id', 'stockBucket'))
    existing_item_ids = [item_id for item_id, _bucket in existing_pairs]
    sale.lines.filter(isDeleted=False).update(isDeleted=True)
    new_item_ids = []
    new_pairs = []
    warehouse = _parse_inventory_warehouse(request, request.POST.get('warehouseId'))
    for row in _request_line_items_payload(request):
        item = _parse_inventory_item(request, row.get('itemId'))
        if item.isSaleBlocked:
            raise ValueError(f'Sale is blocked for inventory item: {item.name}.')
        quantity = _safe_sales_float(row.get('quantity'))
        if quantity <= 0:
            raise ValueError('Sales quantity must be greater than zero.')
        _validate_inventory_quantity_for_unit(item, quantity, label='Sales quantity')
        stock_bucket = _normalize_stock_bucket(request, row.get('stockBucket'), item)
        batch = _resolve_inventory_batch(request, row, item=item, stock_bucket=stock_bucket, warehouse=warehouse, quantity=quantity, direction='out')
        unit_price = _safe_sales_float(row.get('unitPrice'))
        discount_percent = max(0.0, min(100.0, _safe_sales_float(row.get('discountPercent'))))
        gross_amount = round(quantity * unit_price, 2)
        discounted_taxable = round(gross_amount - ((gross_amount * discount_percent) / 100.0), 2)
        taxable_amount = discounted_taxable
        gst_rate = _safe_sales_float(row.get('gstRate'))
        if stock_bucket == InventoryItem.StockBucket.NON_GST:
            gst_rate = 0.0
            gst_amount = 0.0
        else:
            gst_amount = round(taxable_amount * gst_rate / 100.0, 2)
        line_total = round(taxable_amount + gst_amount, 2)
        SalesLine.objects.create(
            tenant=sale.tenant,
            sales=sale,
            warehouse=warehouse,
            item=item,
            batch=batch,
            hsnCode=item.hsnCode,
            stockBucket=stock_bucket,
            quantity=quantity,
            unitPrice=unit_price,
            discountPercent=discount_percent,
            taxableAmount=taxable_amount,
            gstRate=gst_rate,
            gstAmount=gst_amount,
            lineTotal=line_total,
            remark=(row.get('remark') or '')[:500],
        )
        new_item_ids.append(item.pk)
        new_pairs.append((item.pk, stock_bucket))
    affected_item_ids = set(existing_item_ids + new_item_ids)
    rebuild_inventory_stock(sale.tenant, affected_item_ids)
    validate_non_negative_inventory(sale.tenant, affected_item_ids, item_bucket_pairs=list(set(existing_pairs + new_pairs)))


def _apply_proforma_invoice_payload(request, proforma: ProformaInvoice, party: Party | None, *, line_items=None) -> None:
    line_items = line_items if line_items is not None else (_request_line_items_payload(request) if _request_line_items_present(request) else [])
    if _advanced_invoicing_enabled(request):
        proforma.invoiceKind = _normalize_invoice_kind(
            request.POST.get('invoiceKind'),
            party,
            series=getattr(proforma, 'invoiceSeries', None),
            request=request,
        )
        buyer_legal_name = (request.POST.get('buyerLegalName') or '').strip()
        if not buyer_legal_name and party:
            buyer_legal_name = (party.legalName or party.name or '').strip()
        proforma.buyerLegalName = buyer_legal_name or None

        buyer_gst_number = _normalize_gst_number(request.POST.get('buyerGstNumber'))
        if not buyer_gst_number and party:
            buyer_gst_number = _normalize_gst_number(party.gstNumber)
        proforma.buyerGstNumber = buyer_gst_number or None

        buyer_billing_address = (request.POST.get('buyerBillingAddress') or '').strip()
        if not buyer_billing_address and party:
            buyer_billing_address = (party.billingAddress or party.address or '').strip()
        proforma.buyerBillingAddress = buyer_billing_address or None
        proforma.placeOfSupply = (request.POST.get('placeOfSupply') or '').strip() or None
    else:
        proforma.invoiceKind = ProformaInvoice.InvoiceKind.B2C
        proforma.buyerLegalName = None
        proforma.buyerGstNumber = None
        proforma.buyerBillingAddress = None
        proforma.placeOfSupply = None

    if line_items:
        proforma.taxableAmount, proforma.gstAmount, computed_total = _sales_totals_from_lines(line_items)
        proforma.roundOffAmount, proforma.amount = _sales_round_off_from_total(computed_total)
    else:
        proforma.amount = _safe_sales_float(request.POST.get('amount'))
        if proforma.amount <= 0:
            raise ValueError('Amount must be greater than zero.')
        proforma.taxableAmount = round(float(proforma.amount or 0.0), 2)
        proforma.gstAmount = 0.0
        proforma.roundOffAmount = 0.0

    if proforma.invoiceKind == ProformaInvoice.InvoiceKind.B2C:
        proforma.buyerGstNumber = None


def _sync_proforma_lines(request, proforma: ProformaInvoice):
    if not _inventory_feature_enabled(request) or not _request_line_items_present(request):
        return
    proforma.lines.filter(isDeleted=False).update(isDeleted=True)
    warehouse = _parse_inventory_warehouse(request, request.POST.get('warehouseId'))
    for row in _request_line_items_payload(request):
        item = _parse_inventory_item(request, row.get('itemId'))
        if item.isSaleBlocked:
            raise ValueError(f'Sale is blocked for inventory item: {item.name}.')
        quantity = _safe_sales_float(row.get('quantity'))
        if quantity <= 0:
            raise ValueError('Proforma quantity must be greater than zero.')
        _validate_inventory_quantity_for_unit(item, quantity, label='Proforma quantity')
        stock_bucket = _normalize_stock_bucket(request, row.get('stockBucket'), item)
        batch = _resolve_inventory_batch(request, row, item=item, stock_bucket=stock_bucket, warehouse=warehouse, quantity=quantity, direction='out')
        unit_price = _safe_sales_float(row.get('unitPrice'))
        discount_percent = max(0.0, min(100.0, _safe_sales_float(row.get('discountPercent'))))
        gross_amount = round(quantity * unit_price, 2)
        taxable_amount = round(gross_amount - ((gross_amount * discount_percent) / 100.0), 2)
        gst_rate = _safe_sales_float(row.get('gstRate'))
        if stock_bucket == InventoryItem.StockBucket.NON_GST:
            gst_rate = 0.0
            gst_amount = 0.0
        else:
            gst_amount = round(taxable_amount * gst_rate / 100.0, 2)
        line_total = round(taxable_amount + gst_amount, 2)
        ProformaInvoiceLine.objects.create(
            tenant=proforma.tenant,
            proformaInvoice=proforma,
            warehouse=warehouse,
            item=item,
            batch=batch,
            hsnCode=item.hsnCode,
            stockBucket=stock_bucket,
            quantity=quantity,
            unitPrice=unit_price,
            discountPercent=discount_percent,
            taxableAmount=taxable_amount,
            gstRate=gst_rate,
            gstAmount=gst_amount,
            lineTotal=line_total,
            remark=(row.get('remark') or '')[:500],
        )


def _copy_proforma_lines_to_sale(proforma: ProformaInvoice, sale: Sales) -> list[int]:
    item_ids = []
    for line in proforma.lines.filter(isDeleted=False).select_related('item', 'warehouse', 'batch').order_by('id'):
        SalesLine.objects.create(
            tenant=sale.tenant,
            sales=sale,
            warehouse=line.warehouse,
            item=line.item,
            batch=line.batch,
            hsnCode=line.hsnCode,
            stockBucket=line.stockBucket,
            quantity=line.quantity,
            unitPrice=line.unitPrice,
            discountPercent=line.discountPercent,
            taxableAmount=line.taxableAmount,
            gstRate=line.gstRate,
            gstAmount=line.gstAmount,
            lineTotal=line.lineTotal,
            remark=line.remark,
        )
        item_ids.append(line.item_id)
    return item_ids


def _sync_cash_counter_lines(request, counter: CashCounter):
    if not _request_line_items_present(request):
        return
    existing_pairs = list(counter.lines.filter(isDeleted=False).values_list('item_id', 'stockBucket'))
    existing_item_ids = [item_id for item_id, _bucket in existing_pairs]
    counter.lines.filter(isDeleted=False).update(isDeleted=True)
    new_item_ids = []
    new_pairs = []
    warehouse = _parse_inventory_warehouse(request, request.POST.get('warehouseId'))
    for row in _request_line_items_payload(request):
        item = _parse_inventory_item(request, row.get('itemId'))
        if item.isSaleBlocked:
            raise ValueError(f'Sale is blocked for inventory item: {item.name}.')
        quantity = _safe_sales_float(row.get('quantity'))
        if quantity <= 0:
            raise ValueError('Cash counter quantity must be greater than zero.')
        _validate_inventory_quantity_for_unit(item, quantity, label='Cash counter quantity')
        stock_bucket = _normalize_stock_bucket(request, row.get('stockBucket'), item)
        batch = _resolve_inventory_batch(request, row, item=item, stock_bucket=stock_bucket, warehouse=warehouse, quantity=quantity, direction='out')
        unit_price = _safe_sales_float(row.get('unitPrice'))
        discount_percent = max(0.0, min(100.0, _safe_sales_float(row.get('discountPercent'))))
        gross_amount = round(quantity * unit_price, 2)
        discounted_taxable = round(gross_amount - ((gross_amount * discount_percent) / 100.0), 2)
        taxable_amount = discounted_taxable
        gst_rate = _safe_sales_float(row.get('gstRate'))
        if stock_bucket == InventoryItem.StockBucket.NON_GST:
            gst_rate = 0.0
            gst_amount = 0.0
        else:
            gst_amount = round(taxable_amount * gst_rate / 100.0, 2)
        line_total = round(taxable_amount + gst_amount, 2)
        CashCounterLine.objects.create(
            tenant=counter.tenant,
            cashCounter=counter,
            warehouse=warehouse,
            item=item,
            batch=batch,
            hsnCode=item.hsnCode,
            stockBucket=stock_bucket,
            quantity=quantity,
            unitPrice=unit_price,
            discountPercent=discount_percent,
            taxableAmount=taxable_amount,
            gstRate=gst_rate,
            gstAmount=gst_amount,
            lineTotal=line_total,
            remark=(row.get('remark') or '')[:500],
        )
        new_item_ids.append(item.pk)
        new_pairs.append((item.pk, stock_bucket))
    affected_item_ids = set(existing_item_ids + new_item_ids)
    rebuild_inventory_stock(counter.tenant, affected_item_ids)
    validate_non_negative_inventory(counter.tenant, affected_item_ids, item_bucket_pairs=list(set(existing_pairs + new_pairs)))


def _sync_cash_counter_return_voucher(request, counter: CashCounter):
    linked_voucher = getattr(counter, 'linkedSalesReturnVoucher', None)
    prior_pairs = list(linked_voucher.lines.filter(isDeleted=False).values_list('item_id', 'stockBucket')) if linked_voucher else []
    prior_item_ids = [item_id for item_id, _bucket in prior_pairs]
    current_lines = list(counter.lines.filter(isDeleted=False).select_related('item', 'warehouse').order_by('id'))
    current_item_ids = [line.item_id for line in current_lines]
    current_pairs = [(line.item_id, line.stockBucket) for line in current_lines]

    if (counter.mode or '') != 'Return' or not current_lines:
        if linked_voucher and not linked_voucher.isDeleted:
            linked_voucher.isDeleted = True
            linked_voucher.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        if counter.linkedSalesReturnVoucher_id:
            counter.linkedSalesReturnVoucher = None
            counter.save(update_fields=['linkedSalesReturnVoucher', 'lastUpdatedOn'])
        affected_item_ids = set(prior_item_ids + current_item_ids)
        if affected_item_ids:
            rebuild_inventory_stock(counter.tenant, affected_item_ids)
            validate_non_negative_inventory(
                counter.tenant,
                affected_item_ids,
                item_bucket_pairs=list(set(prior_pairs + current_pairs)),
            )
        return

    voucher = linked_voucher or SalesReturnVoucher(tenant=counter.tenant)
    source_type, source_id = _parse_return_source_ref(request.POST.get('salesId'))
    if not source_id:
        raise ValueError('Original invoice is required for sales return.')
    voucher.sales = None
    voucher.cashCounter = None
    if source_type == 'cash_counter':
        voucher.cashCounter = tenant_qs(request, CashCounter).filter(
            pk=source_id,
            isDeleted=False,
            mode__in=_cash_counter_return_source_modes(),
        ).first()
        if voucher.cashCounter is None:
            raise ValueError('Selected cash counter invoice was not found.')
    else:
        voucher.sales = tenant_qs(request, Sales).filter(pk=source_id, isDeleted=False).first()
        if voucher.sales is None:
            raise ValueError('Selected sales invoice was not found.')
    voucher.entryDate = counter.entryDate.date() if getattr(counter, 'entryDate', None) and hasattr(counter.entryDate, 'date') else getattr(counter, 'entryDate', None)
    voucher.partyID = counter.partyID
    voucher.remark = counter.remark
    voucher.createdBy = counter.createdBy
    voucher.totalAmount = 0.0
    voucher.isDeleted = False
    if voucher.pk:
        voucher.save(update_fields=['sales', 'cashCounter', 'entryDate', 'partyID', 'remark', 'createdBy', 'totalAmount', 'isDeleted', 'lastUpdatedOn'])
    else:
        voucher.save()
        voucher.voucherNumber = _next_tenant_sequence_value(request, TenantSequence.Scope.SALES_RETURN)
        voucher.save(update_fields=['voucherNumber', 'lastUpdatedOn'])

    voucher.lines.filter(isDeleted=False).update(isDeleted=True)
    total_amount = 0.0
    for line in current_lines:
        SalesReturnLine.objects.create(
            tenant=voucher.tenant,
            voucher=voucher,
            warehouse=line.warehouse,
            item=line.item,
            batch=line.batch,
            stockBucket=line.stockBucket,
            quantity=line.quantity,
            unitPrice=line.unitPrice,
            lineTotal=line.lineTotal,
            remark=line.remark,
        )
        total_amount += float(line.lineTotal or 0.0)
    voucher.totalAmount = round(total_amount, 2)
    voucher.save(update_fields=['totalAmount', 'lastUpdatedOn'])

    if counter.linkedSalesReturnVoucher_id != voucher.pk:
        counter.linkedSalesReturnVoucher = voucher
        counter.save(update_fields=['linkedSalesReturnVoucher', 'lastUpdatedOn'])

    affected_item_ids = set(prior_item_ids + current_item_ids)
    if affected_item_ids:
        rebuild_inventory_stock(counter.tenant, affected_item_ids)
        validate_non_negative_inventory(
            counter.tenant,
            affected_item_ids,
            item_bucket_pairs=list(set(prior_pairs + current_pairs)),
        )


def _purchase_totals_from_lines(lines):
    subtotal = 0.0
    gst_total = 0.0
    grand_total = 0.0
    for row in lines:
        subtotal += _safe_sales_float(row.get('taxableAmount'))
        gst_total += _safe_sales_float(row.get('gstAmount'))
        grand_total += _safe_sales_float(row.get('lineTotal'))
    return round(subtotal, 2), round(gst_total, 2), round(grand_total, 2)


def _normalize_purchase_lines_for_treatment(gst_treatment, lines):
    normalized = []
    force_non_gst = gst_treatment in {'non_gst', 'non_gst_claimable'}
    for row in lines:
        quantity = _safe_sales_float(row.get('quantity'))
        unit_cost = _safe_sales_float(row.get('unitCost'))
        taxable_amount = round(quantity * unit_cost, 2)
        gst_rate = _safe_sales_float(row.get('gstRate'))
        gst_amount = _safe_sales_float(row.get('gstAmount'))
        stock_bucket = row.get('stockBucket')
        if force_non_gst:
            gst_rate = 0.0
            gst_amount = 0.0
            stock_bucket = 'non_gst'
        else:
            gst_amount = round(taxable_amount * gst_rate / 100.0, 2)
        line_total = round(taxable_amount + gst_amount, 2)
        normalized.append({
            'itemId': row.get('itemId'),
            'stockBucket': stock_bucket,
            'batchId': row.get('batchId') or row.get('batch_id') or '',
            'batchNumber': row.get('batchNumber') or row.get('batchNo') or row.get('batch') or '',
            'manufactureDate': row.get('manufactureDate') or row.get('mfgDate') or '',
            'expiryDate': row.get('expiryDate') or row.get('expDate') or '',
            'batchMrp': row.get('batchMrp') or row.get('mrp') or '',
            'quantity': quantity,
            'unitCost': unit_cost,
            'taxableAmount': round(taxable_amount, 2),
            'gstRate': round(gst_rate, 2),
            'gstAmount': round(gst_amount, 2),
            'lineTotal': round(line_total, 2),
            'remark': row.get('remark') or '',
        })
    return normalized


def _sales_round_off_from_total(total_amount):
    total_amount = round(_safe_sales_float(total_amount), 2)
    rounded_total = float(round(total_amount))
    round_off = round(rounded_total - total_amount, 2)
    return round_off, round(total_amount + round_off, 2)


def _sync_purchase_lines(request, purchase: PurchaseInvoice, lines_payload):
    existing_pairs = list(purchase.lines.filter(isDeleted=False).values_list('item_id', 'stockBucket'))
    existing_item_ids = [item_id for item_id, _bucket in existing_pairs]
    purchase.lines.filter(isDeleted=False).update(isDeleted=True)
    new_item_ids = []
    new_pairs = []
    warehouse = _parse_inventory_warehouse(request, request.POST.get('warehouseId'))
    for row in lines_payload:
        item = _parse_inventory_item(request, row.get('itemId'))
        quantity = _safe_sales_float(row.get('quantity'))
        if quantity <= 0:
            raise ValueError('Purchase quantity must be greater than zero.')
        stock_bucket = _normalize_stock_bucket(request, row.get('stockBucket'), item)
        batch = _resolve_inventory_batch(request, row, item=item, stock_bucket=stock_bucket, warehouse=warehouse, quantity=quantity, direction='in')
        unit_cost = _safe_sales_float(row.get('unitCost'))
        taxable_amount = _safe_sales_float(row.get('taxableAmount')) or round(quantity * unit_cost, 2)
        gst_rate = _safe_sales_float(row.get('gstRate'))
        gst_amount = _safe_sales_float(row.get('gstAmount'))
        line_total = _safe_sales_float(row.get('lineTotal')) or round(taxable_amount + gst_amount, 2)
        PurchaseLine.objects.create(
            tenant=purchase.tenant,
            purchaseInvoice=purchase,
            warehouse=warehouse,
            item=item,
            batch=batch,
            stockBucket=stock_bucket,
            quantity=quantity,
            unitCost=unit_cost,
            taxableAmount=taxable_amount,
            gstRate=gst_rate,
            gstAmount=gst_amount,
            lineTotal=line_total,
            remark=(row.get('remark') or '')[:500],
        )
        new_item_ids.append(item.pk)
        new_pairs.append((item.pk, stock_bucket))
    affected_item_ids = set(existing_item_ids + new_item_ids)
    rebuild_inventory_stock(purchase.tenant, affected_item_ids)
    validate_non_negative_inventory(
        purchase.tenant,
        affected_item_ids,
        item_bucket_pairs=list(set(existing_pairs + new_pairs)),
    )


def _apply_sales_mode_payload(request, obj: Sales) -> None:
    mode = _normalize_sales_mode(
        request.POST.get("paymentMode")
        or request.POST.get("salesMode")
        or request.POST.get("modeOfPayment")
    )
    amount = _safe_sales_float(request.POST.get("amount"))
    if amount <= 0 and _inventory_feature_enabled(request) and _request_line_items_present(request):
        amount = _line_items_total(request)
    mix_cash = _safe_sales_float(request.POST.get("amountMixCash"))
    mix_online = _safe_sales_float(request.POST.get("amountMixOnline"))
    bank_id = _parse_sales_bank_id(request, request.POST.get("bank"))

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
                invoiceSeriesId = request.POST.get("invoiceSeriesId")
                requestedInvoiceNumber = request.POST.get("requestedInvoiceNumber")
                colDate = request.POST.get("colDate")
                party = request.POST.get("party")
                remark = request.POST.get("remark")
                cus = _parse_sales_party(request, party)
                inventory_enabled = _inventory_feature_enabled(request)
                line_items = _request_line_items_payload(request) if (inventory_enabled and _request_line_items_present(request)) else []
                obj = Sales()
                assign_tenant(obj, request)
                obj.partyID_id = cus.pk
                series, sequence_number, invoice_number = _reserve_invoice_number(
                    request,
                    InvoiceSeries.Module.SALES,
                    invoiceSeriesId,
                    preferred_number=requestedInvoiceNumber or None,
                )
                obj.invoiceSeries = series
                obj.invoiceSequenceNumber = sequence_number
                obj.invoiceNumber = invoice_number
                _apply_sales_mode_payload(request, obj)
                obj.remark = remark
                obj.buildDate = datetime.strptime(colDate, '%d/%m/%Y').date()
                _apply_sales_invoice_payload(request, obj, cus, line_items=line_items)
                user = _staff_for_request(request)
                obj.createdBy_id = user.pk
                obj.save()
                obj.salesID = _next_tenant_sequence_value(request, TenantSequence.Scope.SALES)
                obj.paymentID = _next_tenant_sequence_value(request, TenantSequence.Scope.SALES_PAYMENT)
                obj.save(update_fields=['salesID', 'paymentID', 'lastUpdatedOn'])
                if inventory_enabled:
                    _sync_sales_lines(request, obj)
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
                            _link_sale_to_order(request, source_order, obj, note='Sale created from order')
                    except Exception:
                        pass
                sync_book_transactions(obj)
                sync_party_ledger_entries_for_object(obj)
            # try:
            #     msg = "Your order has been dispatched Ag. Bill No. {} Dt. {} Amt. Rs. {}, Please confirm received the material. If you have any queries about your order, Please feel free contact on this no 7005607770. Thanks, BSS".format(obj.invoiceNumber, obj.buildDate.strftime('%d-%m-%Y'), obj.amount)
            #     try:
            #         numbers = str(obj.partyID.phone).split('.')
            #         for n in numbers:
            #             send_message(n, msg, obj.partyID.name)
            #     except:
            #         send_message(obj.partyID.phone, msg, obj.partyID.name)
            # except:
            #     pass
            return JsonResponse({'message': 'success', 'invoiceNumber': obj.invoiceNumber}, safe=False)
        except Exception as exc:
            return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
def list_order_sale_link_candidates_api(request):
    if request.method != 'GET':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = int(request.GET.get('orderID'))
        order_obj = tenant_qs(request, Order).select_related('partyID').get(pk=order_id, isDeleted=False)
        qs = tenant_qs(request, Sales).filter(isDeleted=False).select_related('partyID', 'createdBy').order_by('-buildDate', '-id')
        linked_elsewhere_ids = set(
            tenant_qs(request, Order)
            .filter(isDeleted=False, linkedSales__isnull=False)
            .exclude(pk=order_obj.pk)
            .values_list('linkedSales_id', flat=True)
        )
        if order_obj.partyID_id:
            qs = qs.filter(partyID_id=order_obj.partyID_id)
        rows = []
        for sale in qs[:40]:
            rows.append({
                'ID': sale.pk,
                'SalesID': _sales_public_id(sale),
                'InvoiceNumber': sale.invoiceNumber or '-',
                'Party': sale.partyID.name if sale.partyID_id else '-',
                'Amount': float(sale.amount or 0.0),
                'BuildDate': sale.buildDate.strftime('%d/%m/%Y') if sale.buildDate else '',
                'CreatedBy': sale.createdBy.name if getattr(sale, 'createdBy', None) else '-',
                'IsLinkedToThisOrder': bool(order_obj.linkedSales_id == sale.pk),
                'IsLinkedElsewhere': bool(sale.pk in linked_elsewhere_ids),
            })
        return JsonResponse({'message': 'success', 'data': rows}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
def link_order_to_sale_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = int(request.POST.get('orderID'))
        sale_id = int(request.POST.get('saleID'))
        order_obj = tenant_qs(request, Order).get(pk=order_id, isDeleted=False)
        sale_obj = tenant_qs(request, Sales).get(pk=sale_id, isDeleted=False)
        existing_order = tenant_qs(request, Order).filter(isDeleted=False, linkedSales=sale_obj).exclude(pk=order_obj.pk).first()
        if existing_order:
            return JsonResponse({'message': 'already_linked', 'error': f'Sale already linked to {existing_order.orderNumber or existing_order.pk}.'}, status=400, safe=False)
        _link_sale_to_order(request, order_obj, sale_obj, note='Existing sale linked to order')
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def add_proforma_invoice_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.add_proformainvoice', 'home.can_manage_proforma_invoice'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        invoice_series_id = request.POST.get('invoiceSeriesId')
        requested_invoice_number = request.POST.get('requestedInvoiceNumber')
        party = _parse_sales_party(request, request.POST.get('party'))
        line_items = _request_line_items_payload(request) if (_inventory_feature_enabled(request) and _request_line_items_present(request)) else []
        obj = ProformaInvoice()
        assign_tenant(obj, request)
        obj.partyID_id = party.pk
        series, sequence_number, invoice_number = _reserve_invoice_number(
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
        _apply_proforma_invoice_payload(request, obj, party, line_items=line_items)
        obj.createdBy_id = _staff_for_request(request).pk
        obj.save()
        obj.proformaID = _next_tenant_sequence_value(request, TenantSequence.Scope.PROFORMA_INVOICE)
        obj.save(update_fields=['proformaID', 'lastUpdatedOn'])
        _sync_proforma_lines(request, obj)
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
        if not _can_edit_proforma_record(request.user, obj):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        party = _parse_sales_party(request, request.POST.get('party'))
        line_items = _request_line_items_payload(request) if (_inventory_feature_enabled(request) and _request_line_items_present(request)) else []
        obj.partyID_id = party.pk
        obj.remark = (request.POST.get('remark') or '').strip() or None
        obj.terms = (request.POST.get('terms') or '').strip() or None
        obj.buildDate = datetime.strptime(request.POST.get('colDate'), '%d/%m/%Y').date()
        valid_until_raw = (request.POST.get('validUntil') or '').strip()
        obj.validUntil = datetime.strptime(valid_until_raw, '%d/%m/%Y').date() if valid_until_raw else None
        status = (request.POST.get('status') or obj.status or ProformaInvoice.Status.DRAFT).strip()
        if status in {choice[0] for choice in ProformaInvoice.Status.choices} and status != ProformaInvoice.Status.CONVERTED:
            obj.status = status
        _apply_proforma_invoice_payload(request, obj, party, line_items=line_items)
        obj.save()
        _sync_proforma_lines(request, obj)
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
    if not _has_any_permission(request.user, 'home.delete_proformainvoice', 'home.can_manage_proforma_invoice'):
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
    if not _can_convert_proforma_invoice(request.user):
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
        series, sequence_number, invoice_number = _reserve_invoice_number(
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
        sale.modeOfPayment = _normalize_sales_mode(request.POST.get('paymentMode') or Sales.Mode.CREDIT)
        sale.remark = (request.POST.get('remark') or proforma.remark or f'Converted from {proforma.invoiceNumber}')[:500]
        sale.buildDate = datetime.strptime((request.POST.get('colDate') or proforma.buildDate.strftime('%d/%m/%Y')), '%d/%m/%Y').date()
        sale.createdBy_id = _staff_for_request(request).pk

        if sale.modeOfPayment == Sales.Mode.ONLINE:
            sale.bankID_id = _parse_sales_bank_id(request, request.POST.get('bank'))
            if not sale.bankID_id:
                raise ValueError('Select a bank for online sales.')
        elif sale.modeOfPayment == Sales.Mode.MIX:
            sale.mixCashAmount = _safe_sales_float(request.POST.get('amountMixCash'))
            sale.mixOnlineAmount = _safe_sales_float(request.POST.get('amountMixOnline'))
            sale.bankID_id = _parse_sales_bank_id(request, request.POST.get('bank'))
            if sale.mixCashAmount <= 0 or sale.mixOnlineAmount <= 0 or not sale.bankID_id:
                raise ValueError('Cash amount, online amount, and bank are required for mix sales.')
            if abs(round(sale.mixCashAmount + sale.mixOnlineAmount, 2) - round(float(sale.amount or 0.0), 2)) > 0.01:
                raise ValueError('Mix sales total must match proforma amount.')
        sale.save()
        sale.salesID = _next_tenant_sequence_value(request, TenantSequence.Scope.SALES)
        sale.paymentID = _next_tenant_sequence_value(request, TenantSequence.Scope.SALES_PAYMENT)
        sale.save(update_fields=['salesID', 'paymentID', 'lastUpdatedOn'])

        item_ids = _copy_proforma_lines_to_sale(proforma, sale)
        if item_ids:
            rebuild_inventory_stock(sale.tenant, item_ids)
            validate_non_negative_inventory(sale.tenant, item_ids)
        sync_book_transactions(sale)
        sync_party_ledger_entries_for_object(sale)

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
        if _can_filter_all_proforma(self.request.user):
            return ['proformaID', 'partyID__name', 'invoiceNumber', 'status', 'amount', 'id', 'buildDate', 'validUntil', 'createdBy__name', 'convertedSales__invoiceNumber', 'remark']
        return ['proformaID', 'partyID__name', 'invoiceNumber', 'status', 'amount', 'buildDate', 'validUntil', 'createdBy__name', 'convertedSales__invoiceNumber', 'remark', 'id']

    def get_initial_queryset(self):
        queryset = tenant_qs(self.request, ProformaInvoice).filter(isDeleted=False).select_related('partyID', 'createdBy', 'convertedSales')
        if not _can_filter_all_proforma(self.request.user):
            queryset = queryset.filter(createdBy__user_ID_id=self.request.user.pk)
        try:
            start_date = datetime.strptime(self.request.GET.get('startDate'), '%d/%m/%Y').date()
            end_date = datetime.strptime(self.request.GET.get('endDate'), '%d/%m/%Y').date()
            queryset = queryset.filter(buildDate__range=[start_date, end_date])
            status = (self.request.GET.get('status') or '').strip()
            if status and status != 'All':
                queryset = queryset.filter(status=status)
            staff_id = self.request.GET.get('staffID')
            if _can_filter_all_proforma(self.request.user) and staff_id and staff_id != 'All':
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
        admin_mode = _can_filter_all_proforma(self.request.user)
        for item in qs:
            action_parts = []
            if _can_edit_proforma_record(self.request.user, item):
                action_parts.append('''<a href="/edit_proforma_invoice/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button green"><i class="pen icon"></i></a>'''.format(item.pk))
            action_parts.append('''<button data-inverted="" data-tooltip="Download Proforma" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="downloadProformaInvoice('{}')" class="ui circular blue icon button"><i class="file pdf outline icon"></i></button>'''.format(item.pk))
            if _can_convert_proforma_invoice(self.request.user) and item.status != ProformaInvoice.Status.CONVERTED and item.status != ProformaInvoice.Status.CANCELLED:
                action_parts.append('''<button data-inverted="" data-tooltip="Convert to Sales" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="showConvertModal('{}')" class="ui circular purple icon button"><i class="exchange alternate icon"></i></button>'''.format(item.pk))
            if _has_any_permission(self.request.user, 'home.delete_proformainvoice', 'home.can_manage_proforma_invoice') and item.status != ProformaInvoice.Status.CONVERTED:
                action_parts.append('''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk))
            status_class = {
                ProformaInvoice.Status.DRAFT: 'grey',
                ProformaInvoice.Status.SENT: 'blue',
                ProformaInvoice.Status.ACCEPTED: 'green',
                ProformaInvoice.Status.CONVERTED: 'purple',
                ProformaInvoice.Status.CANCELLED: 'red',
            }.get(item.status, 'grey')
            converted_ref = getattr(getattr(item, 'convertedSales', None), 'invoiceNumber', None) or '-'
            row = [
                escape(_proforma_public_id(item)),
                escape(item.partyID.name if item.partyID_id else '-'),
                escape(item.invoiceNumber or '-'),
                f'<div class="ui tiny {status_class} label">{escape(item.get_status_display())}</div>',
                formatINR(format(item.amount or 0.0, '.0f')),
            ]
            if admin_mode:
                row.append(''.join(action_parts) or '<div class="ui tiny grey label">View Only</div>')
            row.extend([
                escape(item.buildDate.strftime('%d-%m-%Y') if item.buildDate else '-'),
                escape(item.validUntil.strftime('%d-%m-%Y') if item.validUntil else '-'),
                escape(item.createdBy.name if item.createdBy_id else '-'),
                escape(converted_ref),
                escape(item.remark or '-'),
            ])
            if not admin_mode:
                row.append(''.join(action_parts) or '<div class="ui tiny grey label">View Only</div>')
            json_data.append(row)
        return json_data


def generate_proforma_invoice_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_proforma_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    proforma_id = request.GET.get('id')
    if not proforma_id or not str(proforma_id).isdigit():
        return JsonResponse({'message': 'error', 'error': 'Proforma id is required.'}, status=400, safe=False)
    proforma = tenant_object_or_superuser(
        request,
        ProformaInvoice.objects.select_related('partyID', 'createdBy', 'tenant'),
        pk=int(proforma_id),
        isDeleted=False,
    )
    if proforma is None:
        return JsonResponse({'message': 'error', 'error': 'Proforma invoice not found.'}, status=404, safe=False)
    if not _can_filter_all_proforma(request.user):
        if getattr(getattr(proforma, 'createdBy', None), 'user_ID_id', None) != request.user.pk:
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    tenant = get_current_tenant(request)
    lines = list(proforma.lines.filter(isDeleted=False).select_related('item').order_by('id'))
    gst_breakup = _gst_breakup_from_sale(proforma, tenant)
    total_before_round_off = round(float(gst_breakup['taxable_total'] or 0.0) + float(gst_breakup['gst_total'] or 0.0), 2)
    context = {
        'proforma': proforma,
        'lines': lines,
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
    context.update(_pdf_base_context(request, [
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
    if not _can_filter_all_proforma(request.user):
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
    if not _has_any_permission(request.user, 'home.can_view_proforma_invoice_reports', 'home.can_manage_proforma_invoice', 'home.can_view_audit_logs'):
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
    return _xlsx_report_response('proforma-list', [{
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
    if not _has_any_permission(request.user, 'home.can_view_proforma_invoice_reports', 'home.can_manage_proforma_invoice', 'home.can_view_audit_logs'):
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
    context.update(_pdf_base_context(request, [
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


class SalesByAdminListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID__name', 'invoiceNumber', 'modeOfPayment', 'bankID__name', 'amount', 'id', 'buildDate', 'createdBy__name',
                     'isApproved', 'approvedBy__name', 'approvedOn',
                     'datetime', 'remark'
                     ]
    def get_initial_queryset(self):
        queryset = _sales_list_base_qs(self.request).filter(isDeleted__exact=False)
        if not _can_filter_all_sales(self.request.user):
            queryset = queryset.filter(createdBy__user_ID_id=self.request.user.pk)
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            staffID = self.request.GET.get("staffID")
            sDate = datetime.strptime(startDateV, '%d/%m/%Y')
            eDate = datetime.strptime(endDateV, '%d/%m/%Y')
            queryset = queryset.filter(buildDate__range=[sDate.date(), eDate.date()])
            if _can_filter_all_sales(self.request.user) and staffID and staffID != 'All':
                queryset = queryset.filter(createdBy_id=int(staffID))
            return queryset
        except:
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
            if _can_approve_sales(self.request.user):
                action_parts.append('''<button data-inverted="" data-tooltip="Make Approval" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="showConfirmationModal('{}')" class="ui circular facebook icon button purple"><i class="check circle icon"></i></button>'''.format(item.pk))
            if _can_edit_sales(self.request.user):
                action_parts.append('''<a href="/edit_sales/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button green"><i class="pen icon"></i></a>'''.format(item.pk))
            action_parts.append('''<button data-inverted="" data-tooltip="Download Invoice" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="downloadSalesInvoice('{}')" class="ui circular blue icon button"><i class="file pdf outline icon"></i></button>'''.format(item.pk))
            if _can_delete_sales(self.request.user):
                action_parts.append('''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk))
            action = ''.join(action_parts) or '<div class="ui tiny grey label">View Only</div>'
            try:
                createdBy = item.createdBy.name
            except:
                createdBy = '-'
            try:
                approvedBy = item.approvedBy.name
            except:
                approvedBy = '-'
            bank_name = item.bankID.name if getattr(item, 'bankID', None) else '-'
            payment_mode = item.modeOfPayment or '-'
            if payment_mode == Sales.Mode.MIX:
                payment_detail = 'Cash: {} | Online: {}'.format(
                    formatINR(format(item.mixCashAmount or 0.0, '.0f')),
                    formatINR(format(item.mixOnlineAmount or 0.0, '.0f')),
                )
            elif payment_mode == Sales.Mode.ONLINE:
                payment_detail = 'Online via {}'.format(bank_name)
            elif payment_mode == Sales.Mode.CREDIT:
                payment_detail = 'Credit sale'
            else:
                payment_detail = 'Cash sale'
            if item.isApproved == True:
                isApproved = '''<div class="ui tiny green label">
                             Yes
                           </div>'''
            else:
                isApproved = '''<div class="ui tiny red label">
                             No
                           </div>'''
            json_data.append([
                escape(_sales_public_id(item)),
                escape(item.partyID.name),
                escape(item.invoiceNumber),
                escape(payment_mode),
                escape(payment_detail),
                formatINR(format(item.amount, '.0f')),
                action,
                escape(item.buildDate.strftime('%d-%m-%Y')),
                createdBy,
                isApproved,
                approvedBy,
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
        queryset = _sales_list_base_qs(self.request).filter(isDeleted__exact=False)
        if not _can_filter_all_sales(self.request.user):
            queryset = queryset.filter(createdBy__user_ID_id=self.request.user.pk)
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            staffID = self.request.GET.get("staffID")
            sDate = datetime.strptime(startDateV, '%d/%m/%Y')
            eDate = datetime.strptime(endDateV, '%d/%m/%Y')
            queryset = queryset.filter(buildDate__range=[sDate.date(), eDate.date()])
            if _can_filter_all_sales(self.request.user) and staffID and staffID != 'All':
                queryset = queryset.filter(createdBy_id=int(staffID))
            return queryset
        except:
            return queryset.filter(buildDate__exact=datetime.today().date())
    #
    # else:
    #
    #     return Sales.objects.select_related().filter(isDeleted__exact=False,
    #                                                       buildDate__exact=datetime.today().date(), createdBy__user_ID_id=self.request.user.pk)
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
            # Staff can only update sales before approval; after approval the edit page becomes read-only.
            if _can_edit_sales(self.request.user):
                if item.isApproved and not (_can_manage_sales(self.request.user) or _can_approve_sales(self.request.user) or self.request.user.has_perm('home.can_view_party_credentials')):
                    action_parts.append('<div class="ui tiny orange label">Approved</div>')
                else:
                    action_parts.append('''<a href="/edit_sales/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button green"><i class="pen icon"></i></a>'''.format(item.pk))
            action_parts.append('''<button data-inverted="" data-tooltip="Download Invoice" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="downloadSalesInvoice('{}')" class="ui circular blue icon button"><i class="file pdf outline icon"></i></button>'''.format(item.pk))
            if _can_delete_sales(self.request.user):
                action_parts.append('''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk))
            action = ''.join(action_parts) or '<div class="ui tiny grey label">View Only</div>'
            try:
                createdBy = item.createdBy.name
            except:
                createdBy = '-'
            bank_name = item.bankID.name if getattr(item, 'bankID', None) else '-'
            payment_mode = item.modeOfPayment or '-'
            if payment_mode == Sales.Mode.MIX:
                payment_detail = 'Cash: {} | Online: {}'.format(
                    formatINR(format(item.mixCashAmount or 0.0, '.0f')),
                    formatINR(format(item.mixOnlineAmount or 0.0, '.0f')),
                )
            elif payment_mode == Sales.Mode.ONLINE:
                payment_detail = 'Online via {}'.format(bank_name)
            elif payment_mode == Sales.Mode.CREDIT:
                payment_detail = 'Credit sale'
            else:
                payment_detail = 'Cash sale'
            json_data.append([
                escape(_sales_public_id(item)),
                escape(item.partyID.name),
                escape(item.invoiceNumber),
                escape(payment_mode),
                escape(payment_detail),
                formatINR(format(item.amount, '.0f')),
                escape(item.buildDate.strftime('%d-%m-%Y')),
                createdBy,
                escape(item.approvedOn.strftime('%d-%m-%Y %I:%M %p') if item.approvedOn else '-'),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.remark),
                action,
            ])
        return json_data
def generate_sales_report(request):
    cDate = request.GET.get('cDate')
    staffID = request.GET.get('staffID')
    colDate = datetime.strptime(cDate, '%d/%m/%Y')
    a_total = 0.0
    if staffID == 'All':
        col = tenant_qs(request, Sales).select_related().filter(
            buildDate__exact=colDate.date(),
            isDeleted__exact=False
        ).order_by('createdBy__name')
        staffName = 'All'
    else:
        col = tenant_qs(request, Sales).select_related().filter(
            buildDate__exact=colDate.date(),
            isDeleted__exact=False,
            createdBy_id=int(staffID)
        ).order_by(
            'createdBy__name')
        user = tenant_qs(request, StaffUser).get(pk=int(staffID))
        staffName = user.name + ' - ' + user.partyGroupID.name
    cash_total = 0.0
    online_total = 0.0
    credit_total = 0.0
    mix_total = 0.0
    approved_count = 0
    pending_count = 0
    top_customer_map = defaultdict(float)
    for a in col:
        a_total += a.amount
        if a.modeOfPayment == Sales.Mode.CASH:
            cash_total += a.amount
        elif a.modeOfPayment == Sales.Mode.ONLINE:
            online_total += a.amount
        elif a.modeOfPayment == Sales.Mode.CREDIT:
            credit_total += a.amount
        elif a.modeOfPayment == Sales.Mode.MIX:
            mix_total += a.amount
        if a.isApproved:
            approved_count += 1
        else:
            pending_count += 1
        if getattr(a, 'partyID', None):
            top_customer_map[a.partyID.name] += float(a.amount or 0.0)
    total_count = len(col)
    avg_bill = a_total / total_count if total_count else 0.0
    top_customers = sorted(top_customer_map.items(), key=lambda item: item[1], reverse=True)[:5]
    context = {
        'date': colDate,
        'col': col,
        'staffName': staffName,
        'total': a_total,
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
    context.update(_pdf_base_context(request, [
        f'Date: {colDate.strftime("%d/%m/%Y")}',
        f'Staff: {staffName}',
        f'Invoices: {total_count}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = "report.pdf"
    html = render_to_string("home/report/salesReportPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A4 portrait; margin: .65cm; }')])
    return response


def search_sales_invoice_lookup_api(request):
    if request.method != 'GET':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_sales_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)

    query = (request.GET.get('q') or '').strip()
    today_only = str(request.GET.get('today') or '').strip().lower() in {'1', 'true', 'yes'}

    qs = tenant_qs(request, Sales).filter(isDeleted=False).select_related('partyID', 'createdBy', 'bankID')
    if not _can_filter_all_sales(request.user):
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


def generate_sales_invoice_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_sales_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    sale_id = request.GET.get('id')
    if not sale_id or not str(sale_id).isdigit():
        return JsonResponse({'message': 'error', 'error': 'Sale id is required.'}, status=400, safe=False)

    sale = get_object_or_404(
        tenant_qs(request, Sales).select_related('partyID', 'createdBy', 'approvedBy', 'bankID', 'tenant'),
        pk=int(sale_id),
        isDeleted=False,
    )
    if not _can_filter_all_sales(request.user):
        if getattr(getattr(sale, 'createdBy', None), 'user_ID_id', None) != request.user.pk:
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)

    tenant = get_current_tenant(request)
    lines = list(sale.lines.filter(isDeleted=False).select_related('item').order_by('id'))
    gst_breakup = _gst_breakup_from_sale(sale, tenant)
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
    context.update(_pdf_base_context(request, [
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
    if not _has_sales_access(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    sale_id = request.GET.get('id')
    if not sale_id or not str(sale_id).isdigit():
        return JsonResponse({'message': 'error', 'error': 'Sale id is required.'}, status=400, safe=False)

    sale = get_object_or_404(
        tenant_qs(request, Sales).select_related('partyID', 'createdBy', 'bankID', 'tenant'),
        pk=int(sale_id),
        isDeleted=False,
    )
    if not _can_filter_all_sales(request.user):
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
    context = {
        'counter': counter,
        'lines': list(counter.lines.filter(isDeleted=False).select_related('item').order_by('id')),
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
    context = {
        'counter': counter,
        'lines': lines,
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


def generate_sales_report_excel(request):
    cDate = request.GET.get('cDate')
    staffID = request.GET.get('staffID')
    colDate = datetime.strptime(cDate, '%d/%m/%Y')
    if staffID == 'All':
        col = tenant_qs(request, Sales).select_related('partyID', 'createdBy', 'bankID').filter(
            buildDate__exact=colDate.date(),
            isDeleted__exact=False
        ).order_by('createdBy__name', 'invoiceNumber')
        staffName = 'All'
    else:
        col = tenant_qs(request, Sales).select_related('partyID', 'createdBy', 'bankID').filter(
            buildDate__exact=colDate.date(),
            isDeleted__exact=False,
            createdBy_id=int(staffID)
        ).order_by('createdBy__name', 'invoiceNumber')
        user = tenant_qs(request, StaffUser).get(pk=int(staffID))
        staffName = user.name + ' - ' + user.partyGroupID.name
    cash_total = 0.0
    online_total = 0.0
    credit_total = 0.0
    mix_total = 0.0
    approved_count = 0
    pending_count = 0
    a_total = 0.0
    rows = []
    for a in col:
        amount = float(a.amount or 0.0)
        a_total += amount
        if a.modeOfPayment == Sales.Mode.CASH:
            cash_total += amount
        elif a.modeOfPayment == Sales.Mode.ONLINE:
            online_total += amount
        elif a.modeOfPayment == Sales.Mode.CREDIT:
            credit_total += amount
        elif a.modeOfPayment == Sales.Mode.MIX:
            mix_total += amount
        if a.isApproved:
            approved_count += 1
        else:
            pending_count += 1
        rows.append([
            a.invoiceNumber or '-',
            a.paymentID or '-',
            a.partyID.name if a.partyID_id else '-',
            a.createdBy.name if a.createdBy_id else '-',
            a.modeOfPayment or '-',
            getattr(a.bankID, 'name', '-') if getattr(a, 'bankID', None) else '-',
            amount,
            a.buildDate.strftime('%d/%m/%Y') if a.buildDate else '-',
            'Approved' if a.isApproved else 'Pending',
            a.remark or '-',
        ])
    total_count = len(rows)
    avg_bill = a_total / total_count if total_count else 0.0
    return _xlsx_report_response('sales-report', [{
        'title': 'Sales Report',
        'report_title': 'Sales Report',
        'report_subtitle': 'Invoice-wise sales export',
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
                'rows': [
                    ['Total Sales', a_total],
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
    qs = _sales_list_base_qs(request).filter(isDeleted=False)
    if not _can_filter_all_sales(request.user):
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
    return _xlsx_report_response('sales-list', [{
        'title': 'Sales',
        'report_title': 'Sales List Export',
        'report_subtitle': 'Detailed filtered sales rows',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('From', start_date.strftime('%d/%m/%Y')), ('To', end_date.strftime('%d/%m/%Y')), ('Staff', staff_name), ('Search', search or '-')],
        'tables': [{'title': 'Sales', 'headers': ['Payment ID', 'Party', 'Invoice Number', 'Mode', 'Payment Detail', 'Amount', 'Invoice Date', 'Created By', 'Approved', 'Approved By', 'Approved On', 'Added On', 'Remark'], 'rows': rows}],
    }])
@transaction.atomic
def delete_sales(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        if not _can_delete_sales(request.user):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        try:
            id = request.POST.get("userID")
            obj = tenant_qs(request, Sales).select_related().get(pk=int(id))
            item_ids = list(obj.lines.filter(isDeleted=False).values_list('item_id', flat=True))
            obj.isDeleted = True
            obj.save()
            rebuild_inventory_stock(obj.tenant, item_ids)
            validate_non_negative_inventory(obj.tenant, item_ids)
            sync_book_transactions(obj)
            sync_party_ledger_entries_for_object(obj)
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def update_sales_by_admin_api(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        try:
            colDate = request.POST.get("colDate")
            party = request.POST.get("party")
            remark = request.POST.get("remark")
            ID = request.POST.get("ID")
            cus = _parse_sales_party(request, party)
            inventory_enabled = _inventory_feature_enabled(request)
            line_items = _request_line_items_payload(request) if (inventory_enabled and _request_line_items_present(request)) else []
            obj = tenant_qs(request, Sales).get(pk=int(ID))
            if not _can_edit_sales_record(request.user, obj):
                return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
            obj.partyID_id = cus.pk
            _apply_sales_mode_payload(request, obj)
            obj.remark = remark
            obj.buildDate = datetime.strptime(colDate, '%d/%m/%Y').date()
            _apply_sales_invoice_payload(request, obj, cus, line_items=line_items)
            obj.save()
            if inventory_enabled:
                _sync_sales_lines(request, obj)
            sync_book_transactions(obj)
            sync_party_ledger_entries_for_object(obj)
            return JsonResponse({'message': 'success'}, safe=False)
        except Exception as exc:
            return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
@transaction.atomic
def send_message_sales(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'detail': 'Invalid request method.'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized', 'detail': 'Please login to approve sales.'}, status=401, safe=False)
    if not _can_approve_sales(request.user):
        return JsonResponse({'message': 'forbidden', 'detail': 'You do not have permission to approve sales.'}, status=403, safe=False)
    try:
        id = request.POST.get("userID")
        obj = tenant_qs(request, Sales).select_related().get(pk=int(id))
        if obj.isApproved:
            return JsonResponse({'message': 'success', 'detail': 'Sale is already approved.'}, safe=False)
        user = tenant_qs(request, StaffUser).get(user_ID__id=request.user.pk)
        obj.isApproved = True
        obj.approvedBy_id = user.pk
        obj.approvedOn = timezone.now()
        obj.save()
        sync_book_transactions(obj)
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
        sync_party_ledger_entries_for_object(obj)
        try:
            msg = "Your sale has been approved. Bill No. {} Dt. {} Amt. Rs. {}. Please contact 7005607770 if you have any questions. Thanks, BSS".format(
                obj.invoiceNumber, obj.buildDate.strftime('%d-%m-%Y'), obj.amount)
            try:
                numbers = str(obj.partyID.phone).split('.')
                for n in numbers:
                    send_message(n, msg, obj.partyID.name)
            except:
                send_message(obj.partyID.phone, msg, obj.partyID.name)
        except:
            pass
        return JsonResponse({'message': 'success', 'detail': 'Sale approved successfully.'}, safe=False)
    except:
        return JsonResponse({'message': 'error', 'detail': 'Unable to approve sale.'}, safe=False)
@transaction.atomic
def re_send_message_sales(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("userID")
            obj = tenant_qs(request, WhatsappMessageStatus).select_related().get(pk=int(id))
            try:
                msg = tenant_qs(request, WhatsappMessage).filter(isDeleted__exact=False).last()
                if msg and msg.used < msg.balance:
                    try:
                        # r = requests.get(
                        #     msg.name + "send?number=91" + obj.phone + "&type=text&message=" + obj.message + "&instance_id=" + msg.instanceID + "&access_token=" + msg.apiKey,
                        #     verify=False)
                        # data = r.json()
                        # if data['status'] == 'success':
                        #     obj.status = 'Success'
                        #     msg.used = (msg.used + 1)
                        #     msg.save()
                        #     obj.save()
                        #     return JsonResponse({'message': 'success'}, safe=False)
                        # else:
                        obj.status = 'Fail'
                        obj.save()
                        return JsonResponse({'message': 'error'}, safe=False)
                    except:
                        obj.status = 'Fail'
                        obj.save()
                        return JsonResponse({'message': 'error'}, safe=False)
            except:
                return JsonResponse({'message': 'error'}, safe=False)
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
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
                sync_book_transactions(obj)
                sync_party_ledger_entries_for_object(obj)
            return JsonResponse({'message': 'success', 'invoiceNumber': obj.invoiceNumber}, safe=False)
        except Exception as exc:
            return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
class CashCounterByStaffListJson(BaseDatatableView):
    order_columns = ['counterID', 'invoiceNumber', 'amount', 'createdBy.name',
                     'datetime', 'remark'
                     ]
    def get_initial_queryset(self):
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
                createdBy = item.createdBy.name
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
                createdBy = item.createdBy.name
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
                createdBy = item.createdBy.name
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
                createdBy = item.createdBy.name
            except:
                createdBy = '-'
            try:
                party = item.partyID.name
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
                createdBy = item.createdBy.name
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
                createdBy = item.createdBy.name
            except:
                createdBy = '-'
            try:
                party = item.partyID.name
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
                createdBy = item.createdBy.name
            except:
                createdBy = '-'
            try:
                party = item.partyID.name
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
                createdBy = item.createdBy.name
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
            sync_book_transactions(obj)
            sync_party_ledger_entries_for_object(obj)
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
            sync_book_transactions(obj)
            sync_party_ledger_entries_for_object(obj)
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


def generate_daily_summary_pdf(request):
    cDate = request.GET.get('cDate')
    report_date = datetime.strptime(cDate, '%d/%m/%Y').date() if cDate else datetime.today().date()
    sales_qs = tenant_qs(request, Sales).filter(isDeleted=False, buildDate=report_date)
    if not _can_filter_all_sales(request.user):
        sales_qs = sales_qs.filter(createdBy__user_ID_id=request.user.pk)
    collection_qs = tenant_qs(request, Collection).filter(isDeleted=False, isApproved=True, collectionDateTime__date=report_date)
    if not _can_filter_all_collections(request.user):
        collection_qs = collection_qs.filter(collectedBy__user_ID_id=request.user.pk)
    cash_counter_qs = _cash_counter_list_base_qs(request).filter(isDeleted=False, datetime__date=report_date)
    if not _can_filter_all_cash_counter(request.user):
        cash_counter_qs = cash_counter_qs.filter(createdBy__user_ID_id=request.user.pk)

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
    if not _can_filter_all_collections(request.user):
        pending_collection_qs = pending_collection_qs.filter(collectedBy__user_ID_id=request.user.pk)
    pending_collections = pending_collection_qs.count()
    top_sales_staff = list(
        sales_qs.values('createdBy__name')
        .annotate(total=Sum('amount'))
        .order_by('-total')[:5]
    )
    top_collection_staff = list(
        collection_qs.values('collectedBy__name')
        .annotate(total=Sum('paidAmount'))
        .order_by('-total')[:5]
    )
    context = {
        'report_date': report_date,
        'sales_total': sales_total,
        'sales_count': sales_qs.count(),
        'collection_total': collection_total,
        'collection_count': collection_qs.count(),
        'cash_counter_total': cash_counter_total,
        'expense_total': expense_total,
        'return_total': return_total,
        'pending_sales': pending_sales,
        'pending_collections': pending_collections,
        'top_sales_staff': top_sales_staff,
        'top_collection_staff': top_collection_staff,
        'generated_on': timezone.now(),
    }
    context.update(_pdf_base_context(request, [
        f'Date: {report_date.strftime("%d/%m/%Y")}',
        'Scope: Daily business summary',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename=daily-summary-{report_date.strftime("%Y-%m-%d")}.pdf'
    html = render_to_string("home/report/dailySummaryPDF.html", context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A4 portrait; margin: .7cm; }')])
    return response


def generate_daily_summary_excel(request):
    cDate = request.GET.get('cDate')
    report_date = datetime.strptime(cDate, '%d/%m/%Y').date() if cDate else datetime.today().date()
    sales_qs = tenant_qs(request, Sales).filter(isDeleted=False, buildDate=report_date)
    if not _can_filter_all_sales(request.user):
        sales_qs = sales_qs.filter(createdBy__user_ID_id=request.user.pk)
    collection_qs = tenant_qs(request, Collection).filter(isDeleted=False, isApproved=True, collectionDateTime__date=report_date)
    if not _can_filter_all_collections(request.user):
        collection_qs = collection_qs.filter(collectedBy__user_ID_id=request.user.pk)
    cash_counter_qs = _cash_counter_list_base_qs(request).filter(isDeleted=False, datetime__date=report_date)
    if not _can_filter_all_cash_counter(request.user):
        cash_counter_qs = cash_counter_qs.filter(createdBy__user_ID_id=request.user.pk)
    sales_total = float(sales_qs.aggregate(total=Sum('amount'))['total'] or 0.0)
    collection_total = float(collection_qs.aggregate(total=Sum('paidAmount'))['total'] or 0.0)
    cash_counter_total = float(cash_counter_qs.aggregate(total=Sum('amount'))['total'] or 0.0)
    expense_total = float(cash_counter_qs.filter(mode='Expense').aggregate(total=Sum('amount'))['total'] or 0.0)
    return_total = float(cash_counter_qs.filter(mode='Return').aggregate(total=Sum('amount'))['total'] or 0.0)
    pending_sales = sales_qs.filter(isApproved=False).count()
    pending_collection_qs = tenant_qs(request, Collection).filter(isDeleted=False, collectionDateTime__date=report_date, isApproved=False)
    if not _can_filter_all_collections(request.user):
        pending_collection_qs = pending_collection_qs.filter(collectedBy__user_ID_id=request.user.pk)
    pending_collections = pending_collection_qs.count()
    top_sales_staff = list(sales_qs.values_list('createdBy__name').annotate(total=Sum('amount')).order_by('-total')[:5])
    top_collection_staff = list(collection_qs.values_list('collectedBy__name').annotate(total=Sum('paidAmount')).order_by('-total')[:5])
    return _xlsx_report_response('daily-summary', [{
        'title': 'Daily Summary',
        'report_title': 'Daily Summary Report',
        'report_subtitle': 'Daily owner snapshot',
        'metadata': [
            ('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')),
            ('Date', report_date.strftime('%d/%m/%Y')),
            ('Generated By', _pdf_base_context(request).get('pdf_generated_by')),
        ],
        'tables': [
            {'title': 'Business Summary', 'headers': ['Metric', 'Value'], 'rows': [
                ['Sales Total', sales_total],
                ['Sales Count', sales_qs.count()],
                ['Collection Total', collection_total],
                ['Collection Count', collection_qs.count()],
                ['Cash Counter Total', cash_counter_total],
                ['Expense Total', expense_total],
                ['Return Total', return_total],
                ['Pending Sales', pending_sales],
                ['Pending Collections', pending_collections],
            ]},
            {'title': 'Top Sales Staff', 'headers': ['Staff', 'Amount'], 'rows': [[label or '-', float(total or 0.0)] for label, total in top_sales_staff]},
            {'title': 'Top Collection Staff', 'headers': ['Staff', 'Amount'], 'rows': [[label or '-', float(total or 0.0)] for label, total in top_collection_staff]},
        ],
    }])


def generate_monthly_business_summary_excel(request):
    reference = _attendance_month_reference(request)
    month_start = reference.replace(day=1)
    month_end = reference.replace(day=monthrange(reference.year, reference.month)[1])
    sales_qs = tenant_qs(request, Sales).filter(isDeleted=False, buildDate__range=[month_start, month_end])
    if not _can_filter_all_sales(request.user):
        sales_qs = sales_qs.filter(createdBy__user_ID_id=request.user.pk)
    collection_qs = tenant_qs(request, Collection).filter(isDeleted=False, isApproved=True, collectionDateTime__date__range=[month_start, month_end])
    if not _can_filter_all_collections(request.user):
        collection_qs = collection_qs.filter(collectedBy__user_ID_id=request.user.pk)
    daily_sales = {item['buildDate']: float(item['total'] or 0.0) for item in sales_qs.values('buildDate').annotate(total=Sum('amount'))}
    daily_collections = {item['collectionDateTime__date']: float(item['total'] or 0.0) for item in collection_qs.values('collectionDateTime__date').annotate(total=Sum('paidAmount'))}
    rows = []
    for day in range(1, month_end.day + 1):
        current = month_start.replace(day=day)
        rows.append([current.strftime('%d/%m/%Y'), daily_sales.get(current, 0.0), daily_collections.get(current, 0.0)])
    return _xlsx_report_response('monthly-business-summary', [{
        'title': 'Monthly Business',
        'report_title': 'Monthly Business Summary',
        'report_subtitle': f'{month_name[reference.month]} {reference.year}',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('Month', f'{month_name[reference.month]} {reference.year}'), ('Generated By', _pdf_base_context(request).get('pdf_generated_by'))],
        'tables': [
            {'title': 'Monthly Totals', 'headers': ['Metric', 'Value'], 'rows': [['Sales Total', float(sales_qs.aggregate(total=Sum('amount'))['total'] or 0.0)], ['Collection Total', float(collection_qs.aggregate(total=Sum('paidAmount'))['total'] or 0.0)], ['Sales Count', sales_qs.count()], ['Collection Count', collection_qs.count()]]},
            {'title': 'Day Wise Movement', 'headers': ['Date', 'Sales', 'Collections'], 'rows': rows},
        ],
    }])
@transaction.atomic
def add_collection_from_cash_counter_api(request):
    if request.method == 'POST':
        try:
            transferredParty = request.POST.get("TransferredParty")
            party = request.POST.get("party")
            paymentMode = request.POST.get("paymentMode")
            amountPaid = request.POST.get("amountPaid")
            bank = request.POST.get("bank")
            detail = request.POST.get("detail")
            remark = request.POST.get("remark")
            lat = request.POST.get("lat")
            lng = request.POST.get("lng")
            chequeDate = request.POST.get("chequeDate")
            bank_required_modes = {'Online', 'Cheque', 'Cheque CC'}
            if paymentMode in bank_required_modes and not bank:
                return JsonResponse({'message': 'bank_required'}, safe=False)
            if paymentMode in {'Cheque', 'Cheque CC'} and not chequeDate:
                return JsonResponse({'message': 'cheque_date_required'}, safe=False)
            c = str(party).split('@')
            cus = tenant_qs(request, Party).select_related().get(pk=int(c[1]))
            obj = CollectionCashCounter()
            assign_tenant(obj, request)
            obj.isTallied = False
            obj.partyID_id = cus.pk
            obj.modeOfPayment = paymentMode
            obj.paidAmount = float(amountPaid)
            if paymentMode in bank_required_modes:
                obj.bankID_id = int(bank)
            else:
                obj.bankID = None
            if paymentMode in {'Cheque', 'Cheque CC'}:
                obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
            if paymentMode == 'Party':
                try:
                    p = str(transferredParty).split('@')
                    tParty = tenant_qs(request, Party).select_related().get(pk=int(p[1]))
                    obj.transferredPartyID_id = int(tParty.pk)
                except:
                    pass
            obj.detail = detail
            obj.remark = remark
            obj.latitude = lat
            obj.longitude = lng
            user = tenant_qs(request, StaffUser).get(user_ID_id=request.user.pk)
            obj.collectedBy_id = user.pk
            obj.isApproved = True
            obj.approvedBy_id = user.pk
            obj.approvedOn = timezone.now()
            try:
                # obj.collectionAddress = deduct_location_balance(lat, lng)
                obj.collectionAddress = "From Shop Counter"
            except:
                pass
            obj.save()
            obj.paymentID = _next_tenant_sequence_value(request, TenantSequence.Scope.CASH_COUNTER_COLLECTION)
            obj.collectionDateTime = obj.datetime
            obj.save()
            sync_book_transactions(obj)
            sync_party_ledger_entries_for_object(obj)
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)
class CashCounterCollectionListJson(BaseDatatableView):
    order_columns = ['paymentID', 'partyID.name', 'paidAmount', 'modeOfPayment', 'collectedBy.name',
                     'collectionDateTime', 'datetime',
                     'bankID.name', 'detail', 'chequeDate',
                     'transferredPartyID',
                     'remark', 'action'
                     ]
    def get_initial_queryset(self):
        if _can_filter_all_cash_counter(self.request.user):
            try:
                startDateV = self.request.GET.get("startDate")
                endDateV = self.request.GET.get("endDate")
                sDate = datetime.strptime(startDateV, '%d/%m/%Y')
                eDate = datetime.strptime(endDateV, '%d/%m/%Y')
                return _cash_counter_collection_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                             collectionDateTime__date__range=[
                                                                                 sDate.date(),
                                                                                 eDate.date()])
            except:
                return _cash_counter_collection_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                             datetime__date=datetime.today().date())
        else:
            return _cash_counter_collection_list_base_qs(self.request).filter(isDeleted__exact=False,
                                                                         datetime__date=datetime.today().date(),
                                                                         collectedBy__user_ID_id=self.request.user.pk)
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(paymentID__icontains=search) | Q(partyID__name__icontains=search) | Q(
                    transferredPartyID__name__icontains=search) | Q(
                    paidAmount__icontains=search) | Q(
                    modeOfPayment__icontains=search)
                | Q(bankID__name__icontains=search) | Q(chequeDate__icontains=search) | Q(detail__icontains=search) | Q(
                    remark__icontains=search)
                | Q(datetime__icontains=search) | Q(collectionDateTime__icontains=search) | Q(
                    collectionAddress__icontains=search) | Q(
                    collectedBy__name__icontains=search)
                | Q(approvedBy__name__icontains=search) | Q(isApproved__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action_parts = []
            if _can_review_collections(self.request.user) and not item.isApproved:
                action_parts.append('''<button data-inverted="" data-tooltip="Make Approval" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="showConfirmationModal('c{}')" class="ui circular facebook icon button purple"><i class="check circle icon"></i></button>'''.format(item.pk))
            if _can_manage_cash_counter_records(self.request.user):
                action_parts.append('''<a href="/edit_cash_counter_collection/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button green"><i class="pen icon"></i></a>'''.format(item.pk))
                action_parts.append('''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delUser('c{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk))
            action = ''.join(action_parts)
            if not action:
                action = '''<div class="ui tiny label">
                  Denied
                </div>'''
            if item.modeOfPayment == 'Cheque':
                try:
                    chequeDate = item.chequeDate.strftime('%d-%m-%Y')
                except:
                    chequeDate = '-'
            else:
                chequeDate = '-'
            try:
                bank = item.bankID.name
            except:
                bank = '-'
            try:
                tparty = item.transferredPartyID.name
            except:
                tparty = '-'
            try:
                collectedBy = item.collectedBy.name
            except:
                collectedBy = '-'
            json_data.append([
                escape(item.paymentID),
                escape(item.partyID.name),
                formatINR(format(item.paidAmount, '.0f')),
                escape(item.modeOfPayment),
                collectedBy,
                escape(item.collectionDateTime.strftime('%d-%m-%Y')),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(bank),
                escape(item.detail),
                escape(chequeDate),
                escape(tparty),
                escape(item.remark),
                action,
            ])
        return json_data
@transaction.atomic
def edit_cash_counter_collection_by_admin_api(request):
    if request.method == 'POST':
        try:
            ID = request.POST.get("ID")
            cDate = request.POST.get("cDate")
            party = request.POST.get("party")
            transferredParty = request.POST.get("TransferredParty")
            paymentMode = request.POST.get("paymentMode")
            amountPaid = request.POST.get("amountPaid")
            bank = request.POST.get("bank")
            detail = request.POST.get("detail")
            remark = request.POST.get("remark")
            # c = str(party).split('@')
            chequeDate = request.POST.get("chequeDate")
            bank_required_modes = {'Online', 'Cheque', 'Cheque CC'}
            if paymentMode in bank_required_modes and not bank:
                return JsonResponse({'message': 'bank_required'}, safe=False)
            if paymentMode in {'Cheque', 'Cheque CC'} and not chequeDate:
                return JsonResponse({'message': 'cheque_date_required'}, safe=False)
            # cus = Party.objects.select_related().get(pk=int(c[1]))
            cus = tenant_qs(request, Party).select_related().get(pk=int(party))
            obj = tenant_qs(request, CollectionCashCounter).get(pk=int(ID))
            if _can_review_collections(request.user):
                obj.partyID_id = cus.pk
                obj.modeOfPayment = paymentMode
                obj.paidAmount = float(amountPaid)
                obj.detail = detail
                obj.remark = remark
                obj.transferredPartyID = None
                # Preserve time when changing date (avoid 12:00 AM).
                try:
                    c_date_obj = datetime.strptime(cDate, '%d/%m/%Y').date()
                    base_dt = obj.collectionDateTime or obj.datetime or timezone.now()
                    try:
                        base_dt = timezone.localtime(base_dt)
                    except Exception:
                        pass
                    obj.collectionDateTime = datetime.combine(c_date_obj, base_dt.timetz().replace(microsecond=0))
                except Exception:
                    obj.collectionDateTime = datetime.strptime(cDate, '%d/%m/%Y')
                if paymentMode in bank_required_modes:
                    obj.bankID_id = int(bank)
                else:
                    obj.bankID = None
                if paymentMode in {'Cheque', 'Cheque CC'}:
                    obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
                else:
                    obj.chequeDate = None
                if paymentMode == 'Party':
                    try:
                        tparty = tenant_qs(request, Party).select_related().get(pk=int(transferredParty))
                        obj.transferredPartyID_id = tparty.pk
                    except:
                        pass
                obj.save()
                sync_book_transactions(obj)
                sync_party_ledger_entries_for_object(obj)
            # if 'Moderator' in request.user.groups.values_list('name', flat=True):
            #     if paymentMode == 'Cheque':
            #         obj.chequeDate = datetime.strptime(chequeDate, '%d/%m/%Y')
            #
            #     obj.save()
            # try:
            #     msg = "Sir, Our Executive has collected the payment of {} Rs.{}/- from you, Kindly confirm the same. If you have any query Please feel free contact on this no. 7005607770. Thanks, BSS".format(
            #         obj.modeOfPayment, obj.paidAmount)
            #     send_whatsapp_message(obj.partyID.phone, msg)
            # except:
            #     pass
            return JsonResponse({'message': 'success'}, safe=False)
        except:
            return JsonResponse({'message': 'error'}, safe=False)


@cache_page(60 * 5)
def api_business_snapshot(request):
    start_date, end_date = _report_range(request)
    group_by = request.GET.get('groupBy', 'day')
    range_days = max((end_date - start_date).days + 1, 1)
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=range_days - 1)
    sales_qs = tenant_qs(request, Sales).filter(isDeleted=False, buildDate__range=[start_date, end_date])
    collection_qs = tenant_qs(request, Collection).filter(isDeleted=False, collectionDateTime__date__range=[start_date, end_date])
    order_qs = tenant_qs(request, Order).filter(isDeleted=False, buildDate__range=[start_date, end_date])
    cash_qs = tenant_qs(request, CashCounter).filter(isDeleted=False, datetime__date__range=[start_date, end_date])
    prev_sales_qs = tenant_qs(request, Sales).filter(isDeleted=False, buildDate__range=[prev_start, prev_end])
    prev_collection_qs = tenant_qs(request, Collection).filter(isDeleted=False, collectionDateTime__date__range=[prev_start, prev_end])
    prev_cash_qs = tenant_qs(request, CashCounter).filter(isDeleted=False, datetime__date__range=[prev_start, prev_end])
    prev_order_qs = tenant_qs(request, Order).filter(isDeleted=False, buildDate__range=[prev_start, prev_end])

    sales_total = float(sales_qs.aggregate(total=Sum('amount'))['total'] or 0.0)
    collection_total = float(collection_qs.aggregate(total=Sum('paidAmount'))['total'] or 0.0)
    cash_total = float(cash_qs.aggregate(total=Sum('amount'))['total'] or 0.0)
    order_total = order_qs.count()
    prev_sales_total = float(prev_sales_qs.aggregate(total=Sum('amount'))['total'] or 0.0)
    prev_collection_total = float(prev_collection_qs.aggregate(total=Sum('paidAmount'))['total'] or 0.0)
    prev_cash_total = float(prev_cash_qs.aggregate(total=Sum('amount'))['total'] or 0.0)
    prev_order_total = prev_order_qs.count()

    sales_by_day = {
        item['buildDate']: float(item['total'] or 0.0)
        for item in sales_qs.values('buildDate').annotate(total=Sum('amount'))
    }
    collections_by_day = {
        item['collectionDateTime__date']: float(item['total'] or 0.0)
        for item in collection_qs.values('collectionDateTime__date').annotate(total=Sum('paidAmount'))
    }
    cash_by_day = {
        item['datetime__date']: float(item['total'] or 0.0)
        for item in cash_qs.values('datetime__date').annotate(total=Sum('amount'))
    }
    date_points = _date_points(start_date, end_date)
    grouped_trend = _group_trend_series(
        date_points,
        {
            'sales': sales_by_day,
            'collections': collections_by_day,
            'cash_counter': cash_by_day,
        },
        group_by=group_by,
    )

    staff_sales_rows = sales_qs.values('createdBy__name').annotate(sales_total=Sum('amount'), sales_count=Count('id'))
    staff_collection_rows = collection_qs.values('collectedBy__name').annotate(collection_total=Sum('paidAmount'), collection_count=Count('id'))
    staff_map = defaultdict(lambda: {'sales': 0.0, 'collections': 0.0, 'count': 0})
    for row in staff_sales_rows:
        label = row.get('createdBy__name') or 'Unassigned'
        staff_map[label]['sales'] += float(row.get('sales_total') or 0.0)
        staff_map[label]['count'] += int(row.get('sales_count') or 0)
    for row in staff_collection_rows:
        label = row.get('collectedBy__name') or 'Unassigned'
        staff_map[label]['collections'] += float(row.get('collection_total') or 0.0)
        staff_map[label]['count'] += int(row.get('collection_count') or 0)
    top_staff = sorted([
        {
            'label': label,
            'sales': values['sales'],
            'collections': values['collections'],
            'total': values['sales'] + values['collections'],
            'count': values['count'],
        }
        for label, values in staff_map.items()
    ], key=lambda item: item['total'], reverse=True)[:8]

    customer_sales = sales_qs.values('partyID__name').annotate(total=Sum('amount'))
    customer_collections = collection_qs.values('partyID__name').annotate(total=Sum('paidAmount'))
    customer_map = defaultdict(float)
    for row in customer_sales:
        customer_map[row.get('partyID__name') or 'Unknown'] += float(row.get('total') or 0.0)
    for row in customer_collections:
        customer_map[row.get('partyID__name') or 'Unknown'] += float(row.get('total') or 0.0)
    top_customers = sorted([
        {'label': label, 'amount': amount}
        for label, amount in customer_map.items()
    ], key=lambda item: item['amount'], reverse=True)[:8]

    party_map = {
        party.id: {
            'name': party.name or 'Unknown',
            'opening': float(party.openingBalance or 0.0),
        }
        for party in tenant_qs(request, Party).filter(isDeleted=False).only('id', 'name', 'openingBalance')
    }
    ledger_rows = tenant_qs(request, PartyLedgerEntry).filter(isDeleted=False).values('party').annotate(
        debit_total=Sum('debit'),
        credit_total=Sum('credit'),
    )
    outstanding_total = 0.0
    outstanding_customers = 0
    for row in ledger_rows:
        party_id = row.get('party')
        party_info = party_map.get(party_id)
        if not party_info:
            continue
        closing = party_info['opening'] + float(row.get('debit_total') or 0.0) - float(row.get('credit_total') or 0.0)
        if closing > 0:
            outstanding_customers += 1
            outstanding_total += closing

    pending_sales = tenant_qs(request, Sales).filter(isDeleted=False, isApproved=False).count()
    pending_collections = tenant_qs(request, Collection).filter(isDeleted=False, isApproved=False).count()
    pending_orders = tenant_qs(request, Order).filter(isDeleted=False, status__in=[Order.Status.DRAFT, Order.Status.SUBMITTED]).count()
    failed_messages = tenant_qs(request, WhatsappMessageStatus).filter(isDeleted=False).exclude(status='Success').count()
    untallied_collections = tenant_qs(request, Collection).filter(isDeleted=False, isTallied=False).count()
    missing_logout = tenant_qs(request, Attendance).filter(isDeleted=False, isLogIn=True, isLogOut=False).count()
    returns_total = float(tenant_qs(request, CashCounter).filter(isDeleted=False, mode='Return', datetime__date__range=[start_date, end_date]).aggregate(total=Sum('amount'))['total'] or 0.0)
    expenses_total = float(tenant_qs(request, CashCounter).filter(isDeleted=False, mode='Expense', datetime__date__range=[start_date, end_date]).aggregate(total=Sum('amount'))['total'] or 0.0)
    approvals_total = pending_sales + pending_collections + pending_orders
    total_business_value = sales_total + collection_total + cash_total
    collection_coverage = ((collection_total / sales_total) * 100.0) if sales_total else 0.0
    top_staff_share = ((top_staff[0]['total'] / total_business_value) * 100.0) if top_staff and total_business_value > 0 else 0.0
    top5_customer_total = sum(item['amount'] for item in top_customers[:5])
    top5_customer_share = ((top5_customer_total / total_business_value) * 100.0) if total_business_value > 0 else 0.0
    active_staff = len([item for item in top_staff if item['total'] > 0])
    insights = []
    insights.append({
        'title': 'Revenue movement',
        'body': f"Sales moved {round(_pct_change(sales_total, prev_sales_total), 1)}% against the previous period.",
        'tone': 'teal' if sales_total >= prev_sales_total else 'red',
    })
    insights.append({
        'title': 'Collection coverage',
        'body': f"Collections are {round((collection_total / sales_total) * 100.0, 1) if sales_total else 0}% of sales in the selected range.",
        'tone': 'blue',
    })
    insights.append({
        'title': 'Staff concentration',
        'body': f"Top staff contributed {round(top_staff_share, 1)}% of total visible business value.",
        'tone': 'violet',
    })
    insights.append({
        'title': 'Customer concentration',
        'body': f"Top 5 customers account for {round(top5_customer_share, 1)}% of the tracked business value.",
        'tone': 'orange',
    })
    insights.append({
        'title': 'Outstanding position',
        'body': f"{outstanding_customers} customers currently hold { _money(outstanding_total) } in receivables.",
        'tone': 'red' if outstanding_total > collection_total else 'teal',
    })
    trend_peak = max(grouped_trend['sales']) if grouped_trend['sales'] else 0.0
    trend_peak_label = grouped_trend['labels'][grouped_trend['sales'].index(trend_peak)] if trend_peak and grouped_trend['labels'] else '-'
    trend_caption = (
        f"Sales peaked at {_money(trend_peak)} during {trend_peak_label}; "
        f"collections are covering {round(collection_coverage, 1)}% of sales in this range."
    )
    mix_leader_index = max(range(len([sales_total, collection_total, cash_total, float(approvals_total)])), key=lambda idx: [sales_total, collection_total, cash_total, float(approvals_total)][idx]) if total_business_value or approvals_total else 0
    mix_labels = ['Sales', 'Collections', 'Cash Counter', 'Pending Approvals']
    mix_caption = f"{mix_labels[mix_leader_index]} is the biggest visible slice in this snapshot."
    staff_caption = f"{active_staff} staff contributed activity; the top contributor accounts for {round(top_staff_share, 1)}% of visible value."
    customer_caption = f"Top 5 customers account for {round(top5_customer_share, 1)}% of tracked business."
    waterfall_caption = f"After expenses and returns, the net visible effect is {_money(sales_total + collection_total + cash_total - expenses_total - returns_total)}."
    data = {
        'summary': [
            {'label': 'Sales', 'value': _money(sales_total), 'delta': round(_pct_change(sales_total, prev_sales_total), 1), 'previous': _money(prev_sales_total), 'action_url': reverse('homeApp:report_sales')},
            {'label': 'Collections', 'value': _money(collection_total), 'delta': round(_pct_change(collection_total, prev_collection_total), 1), 'previous': _money(prev_collection_total), 'action_url': reverse('homeApp:report_collections')},
            {'label': 'Cash Counter', 'value': _money(cash_total), 'delta': round(_pct_change(cash_total, prev_cash_total), 1), 'previous': _money(prev_cash_total), 'action_url': reverse('homeApp:report_cash_closing')},
            {'label': 'Orders', 'value': order_total, 'delta': round(_pct_change(order_total, prev_order_total), 1), 'previous': prev_order_total, 'action_url': reverse('homeApp:report_orders')},
            {'label': 'Pending Approvals', 'value': approvals_total, 'delta': 0.0, 'previous': '-', 'action_url': reverse('homeApp:report_pending_approvals')},
        ],
        'trend': grouped_trend,
        'lastUpdated': timezone.now().strftime('%d %b %Y %I:%M %p'),
        'trendCaption': trend_caption,
        'mix': {
            'labels': ['Sales', 'Collections', 'Cash Counter', 'Pending Approvals'],
            'values': [sales_total, collection_total, cash_total, float(pending_sales + pending_collections + pending_orders)],
            'urls': [
                reverse('homeApp:report_sales'),
                reverse('homeApp:report_collections'),
                reverse('homeApp:report_cash_closing'),
                reverse('homeApp:report_pending_approvals'),
            ],
            'caption': mix_caption,
        },
        'top_staff': [
            {
                'label': item['label'],
                'sales': item['sales'],
                'collections': item['collections'],
                'total': item['total'],
                'count': item['count'],
                'totalDisplay': _money(item['total']),
                'action_url': reverse('homeApp:report_staff_activity'),
            }
            for item in top_staff
        ],
        'top_staff_caption': staff_caption,
        'top_customers': [
            {
                'label': item['label'],
                'amount': item['amount'],
                'amountDisplay': _money(item['amount']),
                'action_url': reverse('homeApp:report_party_ledger'),
            }
            for item in top_customers
        ],
        'top_customers_caption': customer_caption,
        'exceptions': [
            {'label': 'Pending Sales', 'value': pending_sales, 'tone': 'amber', 'action_url': reverse('homeApp:report_pending_approvals')},
            {'label': 'Pending Collections', 'value': pending_collections, 'tone': 'amber', 'action_url': reverse('homeApp:report_pending_approvals')},
            {'label': 'Pending Orders', 'value': pending_orders, 'tone': 'amber', 'action_url': reverse('homeApp:report_pending_approvals')},
            {'label': 'Failed Messages', 'value': failed_messages, 'tone': 'red' if failed_messages else 'green', 'action_url': reverse('homeApp:report_failed_messages')},
            {'label': 'Untallied Collections', 'value': untallied_collections, 'tone': 'amber', 'action_url': reverse('homeApp:report_collections')},
            {'label': 'Missing Logout', 'value': missing_logout, 'tone': 'red' if missing_logout else 'green', 'action_url': reverse('homeApp:report_attendance')},
        ],
        'comparisons': [
            {'label': 'Sales vs previous', 'current': _money(sales_total), 'previous': _money(prev_sales_total), 'delta': round(_pct_change(sales_total, prev_sales_total), 1)},
            {'label': 'Collections vs previous', 'current': _money(collection_total), 'previous': _money(prev_collection_total), 'delta': round(_pct_change(collection_total, prev_collection_total), 1)},
            {'label': 'Orders vs previous', 'current': order_total, 'previous': prev_order_total, 'delta': round(_pct_change(order_total, prev_order_total), 1)},
        ],
        'insights': insights,
        'waterfall': {
            'labels': ['Sales', 'Collections', 'Cash Counter', 'Expenses', 'Returns', 'Net Effect'],
            'values': [sales_total, collection_total, cash_total, -expenses_total, -returns_total, sales_total + collection_total + cash_total - expenses_total - returns_total],
            'colors': ['#0f766e', '#2563eb', '#f97316', '#dc2626', '#be123c', '#111827'],
            'caption': waterfall_caption,
        },
        'health': [
            {
                'label': 'Collection Coverage',
                'value': f"{round(collection_coverage, 1)}%",
                'subtext': f"Against {_money(sales_total)} sales",
                'tone': 'teal' if collection_coverage >= 80 else 'amber',
                'action_url': reverse('homeApp:report_collections'),
            },
            {
                'label': 'Outstanding',
                'value': _money(outstanding_total),
                'subtext': f"{outstanding_customers} customers pending",
                'tone': 'red' if outstanding_total > collection_total else 'amber',
                'action_url': reverse('homeApp:report_outstanding'),
            },
            {
                'label': 'Customer Concentration',
                'value': f"{round(top5_customer_share, 1)}%",
                'subtext': 'Top 5 customer contribution',
                'tone': 'violet',
                'action_url': reverse('homeApp:report_party_ledger'),
            },
            {
                'label': 'Active Staff',
                'value': active_staff,
                'subtext': f"{len(top_staff)} staff visible in leaderboard",
                'tone': 'blue',
                'action_url': reverse('homeApp:report_staff_activity'),
            },
        ],
    }
    return JsonResponse({'data': data}, safe=False)


def _build_revenue_trend_data(request):
    start_date, end_date = _report_range(request)
    group_by = request.GET.get('groupBy', 'day')
    range_days = max((end_date - start_date).days + 1, 1)
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=range_days - 1)
    qs = tenant_qs(request, Sales).filter(isDeleted=False, buildDate__range=[start_date, end_date])
    prev_qs = tenant_qs(request, Sales).filter(isDeleted=False, buildDate__range=[prev_start, prev_end])
    modes = qs.values(label=F('modeOfPayment')).annotate(amount=Sum('amount'), count=Count('id')).order_by('-amount', 'label')
    top_customers = qs.values(label=F('partyID__name')).annotate(amount=Sum('amount'), count=Count('id')).order_by('-amount', 'label')[:10]
    trend_by_day = {
        item['buildDate']: float(item['amount'] or 0.0)
        for item in qs.values('buildDate').annotate(amount=Sum('amount'))
    }
    points = _date_points(start_date, end_date)
    trend = _group_trend_series(
        points,
        {
            'sales': trend_by_day,
            'collections': {},
            'cash_counter': {},
        },
        group_by=group_by,
    )
    revenue_total = float(qs.aggregate(total=Sum('amount'))['total'] or 0.0)
    prev_revenue_total = float(prev_qs.aggregate(total=Sum('amount'))['total'] or 0.0)
    invoice_count = qs.count()
    approved_count = qs.filter(isApproved=True).count()
    pending_count = qs.filter(isApproved=False).count()
    avg_invoice = (revenue_total / invoice_count) if invoice_count else 0.0
    approved_share = ((approved_count / invoice_count) * 100.0) if invoice_count else 0.0
    top5_share = ((sum(float(item['amount'] or 0.0) for item in top_customers[:5]) / revenue_total) * 100.0) if revenue_total else 0.0
    data = {
        'summary': [
            {'label': 'Revenue', 'value': _money(revenue_total), 'delta': round(_pct_change(revenue_total, prev_revenue_total), 1)},
            {'label': 'Invoices', 'value': invoice_count, 'delta': round(_pct_change(invoice_count, prev_qs.count()), 1)},
            {'label': 'Approved', 'value': approved_count, 'delta': 0.0},
            {'label': 'Pending', 'value': pending_count, 'delta': 0.0},
        ],
        'health': [
            {'label': 'Average Bill', 'value': _money(avg_invoice), 'subtext': 'Revenue per invoice', 'tone': 'teal'},
            {'label': 'Approval Share', 'value': f"{round(approved_share, 1)}%", 'subtext': 'Approved invoices over total', 'tone': 'blue'},
            {'label': 'Top 5 Customers', 'value': f"{round(top5_share, 1)}%", 'subtext': 'Share of total revenue', 'tone': 'violet'},
        ],
        'trend': trend,
        'trend_caption': f"Revenue is grouped by {group_by}; average bill value is {_money(avg_invoice)} in this range.",
        'mode_rows': [
            {
                'label': row.get('label') or 'Unknown',
                'amount': float(row.get('amount') or 0.0),
                'amountDisplay': _money(row.get('amount') or 0.0),
                'count': row.get('count') or 0,
            }
            for row in modes
        ],
        'customer_rows': [
            {
                'label': row.get('label') or 'Unknown',
                'amount': float(row.get('amount') or 0.0),
                'amountDisplay': _money(row.get('amount') or 0.0),
                'count': row.get('count') or 0,
            }
            for row in top_customers
        ],
        'insights': [
            {
                'title': 'Revenue movement',
                'body': f"Revenue moved {round(_pct_change(revenue_total, prev_revenue_total), 1)}% against the previous period.",
            },
            {
                'title': 'Approval control',
                'body': f"{approved_count} of {invoice_count} invoices are approved in the selected range.",
            },
            {
                'title': 'Customer concentration',
                'body': f"Top 5 customers contribute {round(top5_share, 1)}% of revenue.",
            },
        ],
        'mode_caption': 'Payment mode mix shows how revenue is being realized across cash, online, credit, and mixed bills.',
        'customer_caption': 'Top customers highlight where the revenue concentration sits.',
    }
    return start_date, end_date, group_by, data


@cache_page(60 * 5)
def api_revenue_trend(request):
    _, _, _, data = _build_revenue_trend_data(request)
    return JsonResponse({'data': data}, safe=False)


def _build_collection_trend_data(request):
    start_date, end_date = _report_range(request)
    group_by = request.GET.get('groupBy', 'day')
    range_days = max((end_date - start_date).days + 1, 1)
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=range_days - 1)
    qs = tenant_qs(request, Collection).filter(isDeleted=False, collectionDateTime__date__range=[start_date, end_date])
    prev_qs = tenant_qs(request, Collection).filter(isDeleted=False, collectionDateTime__date__range=[prev_start, prev_end])
    modes = qs.values(label=F('modeOfPayment')).annotate(amount=Sum('paidAmount'), count=Count('id')).order_by('-amount', 'label')
    top_customers = qs.values(label=F('partyID__name')).annotate(amount=Sum('paidAmount'), count=Count('id')).order_by('-amount', 'label')[:10]
    top_staff = qs.values(label=F('collectedBy__name')).annotate(amount=Sum('paidAmount'), count=Count('id')).order_by('-amount', 'label')[:10]
    trend_by_day = {
        item['collectionDateTime__date']: float(item['amount'] or 0.0)
        for item in qs.values('collectionDateTime__date').annotate(amount=Sum('paidAmount'))
    }
    points = _date_points(start_date, end_date)
    trend = _group_trend_series(
        points,
        {
            'sales': trend_by_day,
            'collections': {},
            'cash_counter': {},
        },
        group_by=group_by,
    )
    collection_total = float(qs.aggregate(total=Sum('paidAmount'))['total'] or 0.0)
    prev_collection_total = float(prev_qs.aggregate(total=Sum('paidAmount'))['total'] or 0.0)
    entry_count = qs.count()
    approved_count = qs.filter(isApproved=True).count()
    tallied_count = qs.filter(isTallied=True).count()
    otp_count = qs.filter(otpVerified=True).count()
    avg_collection = (collection_total / entry_count) if entry_count else 0.0
    approved_share = ((approved_count / entry_count) * 100.0) if entry_count else 0.0
    tally_share = ((tallied_count / entry_count) * 100.0) if entry_count else 0.0
    data = {
        'summary': [
            {'label': 'Collected', 'value': _money(collection_total), 'delta': round(_pct_change(collection_total, prev_collection_total), 1)},
            {'label': 'Entries', 'value': entry_count, 'delta': round(_pct_change(entry_count, prev_qs.count()), 1)},
            {'label': 'Approved', 'value': approved_count, 'delta': 0.0},
            {'label': 'Tallied', 'value': tallied_count, 'delta': 0.0},
        ],
        'health': [
            {'label': 'Average Collection', 'value': _money(avg_collection), 'subtext': 'Collected per entry', 'tone': 'teal'},
            {'label': 'Approval Share', 'value': f"{round(approved_share, 1)}%", 'subtext': 'Approved entries over total', 'tone': 'blue'},
            {'label': 'Tally Share', 'value': f"{round(tally_share, 1)}%", 'subtext': 'Tallied entries over total', 'tone': 'violet'},
            {'label': 'OTP Verified', 'value': otp_count, 'subtext': 'Entries with OTP verification', 'tone': 'amber'},
        ],
        'trend': trend,
        'trend_caption': f"Collections are grouped by {group_by}; average collection size is {_money(avg_collection)} in this range.",
        'mode_rows': [
            {
                'label': row.get('label') or 'Unknown',
                'amount': float(row.get('amount') or 0.0),
                'amountDisplay': _money(row.get('amount') or 0.0),
                'count': row.get('count') or 0,
            }
            for row in modes
        ],
        'customer_rows': [
            {
                'label': row.get('label') or 'Unknown',
                'amount': float(row.get('amount') or 0.0),
                'amountDisplay': _money(row.get('amount') or 0.0),
                'count': row.get('count') or 0,
            }
            for row in top_customers
        ],
        'staff_rows': [
            {
                'label': row.get('label') or 'Unassigned',
                'amount': float(row.get('amount') or 0.0),
                'amountDisplay': _money(row.get('amount') or 0.0),
                'count': row.get('count') or 0,
            }
            for row in top_staff
        ],
        'insights': [
            {
                'title': 'Collection movement',
                'body': f"Collections moved {round(_pct_change(collection_total, prev_collection_total), 1)}% against the previous period.",
            },
            {
                'title': 'Control quality',
                'body': f"{approved_count} approved and {tallied_count} tallied entries are in the selected range.",
            },
            {
                'title': 'Recovery channel',
                'body': f"OTP verification was completed on {otp_count} entries in this range.",
            },
        ],
        'mode_caption': 'Collection modes show where recovery is happening across cash, online, cheque, and transfer flows.',
        'customer_caption': 'Top customers show which parties are contributing most to recovery.',
        'staff_caption': 'Collector leaderboard shows who is driving the recovery effort.',
    }
    return start_date, end_date, group_by, data


@cache_page(60 * 5)
def api_collection_trend(request):
    _, _, _, data = _build_collection_trend_data(request)
    return JsonResponse({'data': data}, safe=False)


def _build_receivables_data(request):
    _, end_date = _report_range(request)
    parties = tenant_qs(request, Party).filter(isDeleted=False).select_related('assignTo').order_by('name')
    rows = []
    aging = {'0-7': 0.0, '8-15': 0.0, '16-30': 0.0, '31-60': 0.0, '60+': 0.0}
    staff_map = defaultdict(float)

    def bucket_for_days(days):
        if days <= 7:
            return '0-7'
        if days <= 15:
            return '8-15'
        if days <= 30:
            return '16-30'
        if days <= 60:
            return '31-60'
        return '60+'

    for party in parties:
        agg = tenant_qs(request, PartyLedgerEntry).filter(
            isDeleted=False,
            party=party,
            txDateTime__date__lte=end_date,
        ).aggregate(
            debit=Sum('debit'),
            credit=Sum('credit'),
            last_tx=Max('txDateTime'),
        )
        closing = float(party.openingBalance or 0.0) + float(agg['debit'] or 0.0) - float(agg['credit'] or 0.0)
        if closing <= 0:
            continue
        last_tx = agg.get('last_tx')
        age_days = (end_date - last_tx.date()).days if last_tx else 999
        age_bucket = bucket_for_days(age_days)
        aging[age_bucket] += closing
        staff_label = party.assignTo.name if party.assignTo_id else 'Unassigned'
        staff_map[staff_label] += closing
        rows.append({
            'party': party.name or '-',
            'staff': staff_label,
            'closing': _money(closing),
            'closing_raw': closing,
            'last_activity': last_tx.strftime('%d %b %Y') if last_tx else '-',
            'age_days': age_days if age_days < 999 else '-',
            'age_bucket': age_bucket,
            'ledger_url': reverse('homeApp:party_ledger', args=[party.pk]),
        })
    rows = sorted(rows, key=lambda item: item['closing_raw'], reverse=True)
    total_outstanding = sum(item['closing_raw'] for item in rows)
    avg_outstanding = (total_outstanding / len(rows)) if rows else 0.0
    high_risk_amount = aging['31-60'] + aging['60+']
    top5_share = ((sum(item['closing_raw'] for item in rows[:5]) / total_outstanding) * 100.0) if total_outstanding else 0.0
    staff_rows = sorted([
        {'label': label, 'amount': amount, 'count': 0}
        for label, amount in staff_map.items()
    ], key=lambda item: item['amount'], reverse=True)[:8]
    data = {
        'summary': [
            {'label': 'Outstanding Customers', 'value': len(rows)},
            {'label': 'Total Outstanding', 'value': _money(total_outstanding)},
            {'label': 'Average Outstanding', 'value': _money(avg_outstanding)},
            {'label': '31+ Day Risk', 'value': _money(high_risk_amount)},
        ],
        'aging_rows': [{'label': key, 'amount': _money(val), 'amount_raw': round(val, 2), 'count': 0} for key, val in aging.items()],
        'table_rows': rows[:200],
        'top_customers': [
            {
                'label': row['party'],
                'amount': round(row['closing_raw'], 2),
                'amountDisplay': row['closing'],
                'action_url': row['ledger_url'],
            }
            for row in rows[:8]
        ],
        'staff_rows': [
            {
                'label': row['label'],
                'amount': round(row['amount'], 2),
                'amountDisplay': _money(row['amount']),
            }
            for row in staff_rows
        ],
        'insights': [
            {
                'title': 'Concentration',
                'body': f"Top 5 customers account for {round(top5_share, 1)}% of total outstanding.",
            },
            {
                'title': 'Overdue pressure',
                'body': f"{_money(high_risk_amount)} sits in 31+ day buckets.",
            },
            {
                'title': 'As of date',
                'body': f"Receivables are calculated up to {end_date.strftime('%d %b %Y')}.",
            },
        ],
        'aging_caption': f"{_money(high_risk_amount)} of outstanding is older than 30 days.",
        'customers_caption': f"{len(rows)} customers have receivables; average exposure is {_money(avg_outstanding)}.",
        'staff_caption': 'Exposure by assigned staff helps identify where follow-up needs to happen.',
    }
    return end_date, data


@cache_page(60 * 5)
def api_receivables(request):
    _, data = _build_receivables_data(request)
    return JsonResponse({'data': data}, safe=False)


def _build_staff_productivity_data(request):
    start_date, end_date = _report_range(request)
    range_days = max((end_date - start_date).days + 1, 1)
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=range_days - 1)
    rows = []
    attendance_qs = tenant_qs(request, Attendance).filter(isDeleted=False, loginDateTime__date__range=[start_date, end_date])
    attendance_prev_qs = tenant_qs(request, Attendance).filter(isDeleted=False, loginDateTime__date__range=[prev_start, prev_end])
    active_staff = tenant_qs(request, StaffUser).filter(isDeleted=False).order_by('name')
    present_staff = attendance_qs.filter(isLogIn=True).values_list('staffID_id', flat=True).distinct()
    prev_present_staff = attendance_prev_qs.filter(isLogIn=True).values_list('staffID_id', flat=True).distinct()
    for staff in active_staff:
        sales_total = tenant_qs(request, Sales).filter(isDeleted=False, createdBy=staff, buildDate__range=[start_date, end_date]).aggregate(total=Sum('amount'))['total'] or 0.0
        collection_total = tenant_qs(request, Collection).filter(isDeleted=False, collectedBy=staff, collectionDateTime__date__range=[start_date, end_date]).aggregate(total=Sum('paidAmount'))['total'] or 0.0
        order_count = tenant_qs(request, Order).filter(isDeleted=False, createdBy=staff, buildDate__range=[start_date, end_date]).count()
        attendance_count = attendance_qs.filter(staffID=staff, isLogIn=True).count()
        business_value = float(sales_total) + float(collection_total)
        rows.append({
            'label': staff.name or '-',
            'amount': business_value,
            'count': order_count,
            'sales': float(sales_total),
            'collections': float(collection_total),
            'attendance': attendance_count,
            'efficiency': round((business_value / attendance_count), 2) if attendance_count else 0.0,
        })
    rows = sorted(rows, key=lambda item: item['amount'], reverse=True)
    staff_with_activity = [row for row in rows if row['amount'] > 0 or row['count'] > 0]
    total_sales = sum(row['sales'] for row in rows)
    total_collections = sum(row['collections'] for row in rows)
    total_orders = sum(row['count'] for row in rows)
    top_share = ((sum(row['amount'] for row in rows[:3]) / sum(row['amount'] for row in rows)) * 100.0) if rows and sum(row['amount'] for row in rows) else 0.0
    present_count = len(set(present_staff))
    prev_present_count = len(set(prev_present_staff))
    data = {
        'summary': [
            {'label': 'Active Staff', 'value': len(rows), 'delta': 0.0},
            {'label': 'Staff With Activity', 'value': len(staff_with_activity), 'delta': 0.0},
            {'label': 'Present Staff', 'value': present_count, 'delta': round(_pct_change(present_count, prev_present_count), 1)},
            {'label': 'Orders Captured', 'value': total_orders, 'delta': 0.0},
        ],
        'health': [
            {'label': 'Sales Value', 'value': _money(total_sales), 'subtext': 'Captured by staff in selected range', 'tone': 'teal'},
            {'label': 'Collection Value', 'value': _money(total_collections), 'subtext': 'Collected by staff in selected range', 'tone': 'blue'},
            {'label': 'Top 3 Concentration', 'value': f"{round(top_share, 1)}%", 'subtext': 'Share of visible staff business', 'tone': 'violet'},
        ],
        'leaderboard': [
            {
                'label': row['label'],
                'amount': row['amount'],
                'amountDisplay': _money(row['amount']),
                'count': row['count'],
                'sales': row['sales'],
                'collections': row['collections'],
                'attendance': row['attendance'],
                'efficiency': row['efficiency'],
            }
            for row in rows[:20]
        ],
        'insights': [
            {
                'title': 'Team coverage',
                'body': f"{len(staff_with_activity)} of {len(rows)} staff showed measurable business activity.",
            },
            {
                'title': 'Concentration',
                'body': f"Top 3 staff account for {round(top_share, 1)}% of visible business value.",
            },
            {
                'title': 'Attendance signal',
                'body': f"{present_count} staff marked attendance in this period.",
            },
        ],
        'sales_collection_caption': 'Sales and collections together show who is driving revenue and recovery.',
        'orders_caption': 'Order volume by staff helps separate booking activity from billing activity.',
        'efficiency_caption': 'Business value per attendance day highlights who converts presence into output.',
    }
    return start_date, end_date, data


@cache_page(60 * 5)
def api_staff_productivity(request):
    _, _, data = _build_staff_productivity_data(request)
    return JsonResponse({'data': data}, safe=False)


@cache_page(60 * 5)
def api_cash_position(request):
    start_date, end_date = _report_range(request)
    group_by = request.GET.get('groupBy', 'day')
    range_days = max((end_date - start_date).days + 1, 1)
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=range_days - 1)
    cashbook_qs = tenant_qs(request, BookTransaction).filter(isDeleted=False, account__type=BookAccount.Type.CASH, txDateTime__date__range=[start_date, end_date])
    bankbook_qs = tenant_qs(request, BookTransaction).filter(isDeleted=False, account__type=BookAccount.Type.BANK, txDateTime__date__range=[start_date, end_date])
    cash_in = cashbook_qs.filter(direction=BookTransaction.Direction.IN).aggregate(total=Sum('amount'))['total'] or 0.0
    cash_out = cashbook_qs.filter(direction=BookTransaction.Direction.OUT).aggregate(total=Sum('amount'))['total'] or 0.0
    bank_in = bankbook_qs.filter(direction=BookTransaction.Direction.IN).aggregate(total=Sum('amount'))['total'] or 0.0
    bank_out = bankbook_qs.filter(direction=BookTransaction.Direction.OUT).aggregate(total=Sum('amount'))['total'] or 0.0
    prev_qs = tenant_qs(request, BookTransaction).filter(isDeleted=False, txDateTime__date__range=[prev_start, prev_end])
    prev_cash_in = prev_qs.filter(account__type=BookAccount.Type.CASH, direction=BookTransaction.Direction.IN).aggregate(total=Sum('amount'))['total'] or 0.0
    prev_cash_out = prev_qs.filter(account__type=BookAccount.Type.CASH, direction=BookTransaction.Direction.OUT).aggregate(total=Sum('amount'))['total'] or 0.0
    prev_bank_in = prev_qs.filter(account__type=BookAccount.Type.BANK, direction=BookTransaction.Direction.IN).aggregate(total=Sum('amount'))['total'] or 0.0
    prev_bank_out = prev_qs.filter(account__type=BookAccount.Type.BANK, direction=BookTransaction.Direction.OUT).aggregate(total=Sum('amount'))['total'] or 0.0
    all_qs = tenant_qs(request, BookTransaction).filter(isDeleted=False, txDateTime__date__range=[start_date, end_date])
    sources = all_qs.values(label=F('source')).annotate(amount=Sum('amount'), count=Count('id')).order_by('-amount', 'label')
    accounts = all_qs.values(label=F('account__name')).annotate(
        amount=Sum(
            Case(
                When(direction=BookTransaction.Direction.IN, then=F('amount')),
                default=ExpressionWrapper(F('amount') * Value(-1.0), output_field=FloatField()),
                output_field=FloatField(),
            )
        ),
        count=Count('id'),
    ).order_by('-amount', 'label')
    in_by_day = {
        item['txDateTime__date']: float(item['amount'] or 0.0)
        for item in all_qs.filter(direction=BookTransaction.Direction.IN).values('txDateTime__date').annotate(amount=Sum('amount'))
    }
    out_by_day = {
        item['txDateTime__date']: float(item['amount'] or 0.0)
        for item in all_qs.filter(direction=BookTransaction.Direction.OUT).values('txDateTime__date').annotate(amount=Sum('amount'))
    }
    points = _date_points(start_date, end_date)
    trend = _group_trend_series(
        points,
        {
            'sales': in_by_day,
            'collections': out_by_day,
            'cash_counter': {},
        },
        group_by=group_by,
    )
    net_movement = float(cash_in) - float(cash_out) + float(bank_in) - float(bank_out)
    transfer_total = all_qs.filter(source=BookTransaction.Source.TRANSFER).aggregate(total=Sum('amount'))['total'] or 0.0
    data = {
        'summary': [
            {'label': 'Cash In', 'value': _money(cash_in), 'delta': round(_pct_change(cash_in, prev_cash_in), 1)},
            {'label': 'Cash Out', 'value': _money(cash_out), 'delta': round(_pct_change(cash_out, prev_cash_out), 1)},
            {'label': 'Bank In', 'value': _money(bank_in), 'delta': round(_pct_change(bank_in, prev_bank_in), 1)},
            {'label': 'Bank Out', 'value': _money(bank_out), 'delta': round(_pct_change(bank_out, prev_bank_out), 1)},
        ],
        'health': [
            {'label': 'Net Movement', 'value': _money(net_movement), 'subtext': 'Cash + bank combined', 'tone': 'teal' if net_movement >= 0 else 'red'},
            {'label': 'Transfer Volume', 'value': _money(transfer_total), 'subtext': 'Internal movement tracked', 'tone': 'blue'},
            {'label': 'Active Accounts', 'value': len([row for row in accounts if float(row.get('amount') or 0.0) != 0.0]), 'subtext': 'Accounts with movement', 'tone': 'violet'},
        ],
        'trend': trend,
        'trend_caption': f"Inflow and outflow are grouped by {group_by}. Net movement for the selected range is {_money(net_movement)}.",
        'source_rows': _rows_with_money(sources),
        'account_rows': _rows_with_money(accounts[:10]),
        'source_caption': 'Source mix shows which modules are driving book movements.',
        'account_caption': 'Net movement by account highlights where cash or bank pressure is building.',
    }
    return JsonResponse({'data': data}, safe=False)


def _build_order_pipeline_data(request):
    start_date, end_date = _report_range(request)
    group_by = request.GET.get('groupBy', 'day')
    range_days = max((end_date - start_date).days + 1, 1)
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=range_days - 1)
    qs = tenant_qs(request, Order).filter(isDeleted=False, buildDate__range=[start_date, end_date])
    prev_qs = tenant_qs(request, Order).filter(isDeleted=False, buildDate__range=[prev_start, prev_end])
    status_rows = qs.values(label=F('status')).annotate(amount=Count('id'), count=Count('id')).order_by('-count', 'label')
    staff_rows = qs.values(label=F('createdBy__name')).annotate(amount=Count('id'), count=Count('id')).order_by('-count', 'label')[:10]
    customer_rows = qs.values(label=F('partyID__name')).annotate(amount=Count('id'), count=Count('id')).order_by('-count', 'label')[:8]
    status_counts = {row['label'] or 'Unknown': row['count'] for row in status_rows}
    orders_by_day = {
        item['buildDate']: float(item['count'] or 0.0)
        for item in qs.values('buildDate').annotate(count=Count('id'))
    }
    points = _date_points(start_date, end_date)
    trend = _group_trend_series(
        points,
        {
            'sales': orders_by_day,
            'collections': {},
            'cash_counter': {},
        },
        group_by=group_by,
    )
    total_orders = qs.count()
    submitted = qs.filter(status=Order.Status.SUBMITTED).count()
    approved = qs.filter(status=Order.Status.APPROVED).count()
    fulfilled = qs.filter(status=Order.Status.FULFILLED).count()
    cancelled = qs.filter(status=Order.Status.CANCELLED).count()
    prev_total_orders = prev_qs.count()
    approval_rate = ((approved + fulfilled) / total_orders * 100.0) if total_orders else 0.0
    fulfillment_rate = (fulfilled / total_orders * 100.0) if total_orders else 0.0
    data = {
        'summary': [
            {'label': 'Orders', 'value': total_orders, 'delta': round(_pct_change(total_orders, prev_total_orders), 1)},
            {'label': 'Submitted', 'value': submitted, 'delta': 0.0},
            {'label': 'Approved', 'value': approved, 'delta': 0.0},
            {'label': 'Fulfilled', 'value': fulfilled, 'delta': 0.0},
        ],
        'health': [
            {'label': 'Approval Rate', 'value': f"{round(approval_rate, 1)}%", 'subtext': 'Approved + fulfilled over total', 'tone': 'teal' if approval_rate >= 70 else 'amber'},
            {'label': 'Fulfillment Rate', 'value': f"{round(fulfillment_rate, 1)}%", 'subtext': 'Fulfilled over total orders', 'tone': 'blue'},
            {'label': 'Cancelled', 'value': cancelled, 'subtext': 'Orders lost in this range', 'tone': 'red' if cancelled else 'teal'},
        ],
        'trend': trend,
        'trend_caption': f"Order creation is grouped by {group_by}; approval rate is {round(approval_rate, 1)}% for this range.",
        'status_rows': [
            {
                'label': (row['label'] or 'Unknown').replace('_', ' ').title(),
                'amount': row['count'],
                'count': row['count'],
            }
            for row in status_rows
        ],
        'staff_rows': [
            {
                'label': row.get('label') or 'Unassigned',
                'amount': row.get('count') or 0,
                'count': row.get('count') or 0,
            }
            for row in staff_rows
        ],
        'customer_rows': [
            {
                'label': row.get('label') or 'Unknown',
                'amount': row.get('count') or 0,
                'count': row.get('count') or 0,
            }
            for row in customer_rows
        ],
        'status_caption': 'Status split shows where the pipeline is getting stuck.',
        'staff_caption': 'Top staff by orders captured in the selected range.',
        'customer_caption': 'Top customers by order count.',
        'insights': [
            {
                'title': 'Approval flow',
                'body': f"{approved + fulfilled} of {total_orders} orders have crossed approval.",
            },
            {
                'title': 'Fulfillment',
                'body': f"{fulfilled} orders were fulfilled while {submitted} remain submitted and {cancelled} were cancelled.",
            },
            {
                'title': 'Period comparison',
                'body': f"Orders moved {round(_pct_change(total_orders, prev_total_orders), 1)}% against the previous period.",
            },
        ],
    }
    return start_date, end_date, group_by, data


@cache_page(60 * 5)
def api_order_pipeline(request):
    _, _, _, data = _build_order_pipeline_data(request)
    return JsonResponse({'data': data}, safe=False)


def export_revenue_trend_pdf(request):
    start_date, end_date, group_by, data = _build_revenue_trend_data(request)
    sections = [
        {'title': 'Payment Mode Summary', 'headers': ['Mode', 'Amount', 'Count'], 'rows': [[row['label'], row['amountDisplay'], row['count']] for row in data.get('mode_rows', [])]},
        {'title': 'Top Customers', 'headers': ['Customer', 'Amount', 'Count'], 'rows': [[row['label'], row['amountDisplay'], row['count']] for row in data.get('customer_rows', [])]},
    ]
    return _analytics_export_response(request, 'Revenue Trend Report', f'Grouped by {group_by}', data.get('summary'), data.get('health'), data.get('insights'), sections, [f'From: {start_date.strftime("%d/%m/%Y")}', f'To: {end_date.strftime("%d/%m/%Y")}', f'Group By: {group_by}'], 'revenue-trend', 'pdf')


def export_revenue_trend_excel(request):
    start_date, end_date, group_by, data = _build_revenue_trend_data(request)
    sections = [
        {'title': 'Payment Mode Summary', 'headers': ['Mode', 'Amount', 'Count'], 'rows': [[row['label'], row['amountDisplay'], row['count']] for row in data.get('mode_rows', [])]},
        {'title': 'Top Customers', 'headers': ['Customer', 'Amount', 'Count'], 'rows': [[row['label'], row['amountDisplay'], row['count']] for row in data.get('customer_rows', [])]},
    ]
    return _analytics_export_response(request, 'Revenue Trend Report', f'Grouped by {group_by}', data.get('summary'), data.get('health'), data.get('insights'), sections, [f'From: {start_date.strftime("%d/%m/%Y")}', f'To: {end_date.strftime("%d/%m/%Y")}', f'Group By: {group_by}'], 'revenue-trend', 'excel')


def export_collection_trend_pdf(request):
    start_date, end_date, group_by, data = _build_collection_trend_data(request)
    sections = [
        {'title': 'Collection Mode Summary', 'headers': ['Mode', 'Amount', 'Count'], 'rows': [[row['label'], row['amountDisplay'], row['count']] for row in data.get('mode_rows', [])]},
        {'title': 'Top Customers', 'headers': ['Customer', 'Amount', 'Count'], 'rows': [[row['label'], row['amountDisplay'], row['count']] for row in data.get('customer_rows', [])]},
        {'title': 'Top Collectors', 'headers': ['Staff', 'Amount', 'Count'], 'rows': [[row['label'], row['amountDisplay'], row['count']] for row in data.get('staff_rows', [])]},
    ]
    return _analytics_export_response(request, 'Collection Trend Report', f'Grouped by {group_by}', data.get('summary'), data.get('health'), data.get('insights'), sections, [f'From: {start_date.strftime("%d/%m/%Y")}', f'To: {end_date.strftime("%d/%m/%Y")}', f'Group By: {group_by}'], 'collection-trend', 'pdf')


def export_collection_trend_excel(request):
    start_date, end_date, group_by, data = _build_collection_trend_data(request)
    sections = [
        {'title': 'Collection Mode Summary', 'headers': ['Mode', 'Amount', 'Count'], 'rows': [[row['label'], row['amountDisplay'], row['count']] for row in data.get('mode_rows', [])]},
        {'title': 'Top Customers', 'headers': ['Customer', 'Amount', 'Count'], 'rows': [[row['label'], row['amountDisplay'], row['count']] for row in data.get('customer_rows', [])]},
        {'title': 'Top Collectors', 'headers': ['Staff', 'Amount', 'Count'], 'rows': [[row['label'], row['amountDisplay'], row['count']] for row in data.get('staff_rows', [])]},
    ]
    return _analytics_export_response(request, 'Collection Trend Report', f'Grouped by {group_by}', data.get('summary'), data.get('health'), data.get('insights'), sections, [f'From: {start_date.strftime("%d/%m/%Y")}', f'To: {end_date.strftime("%d/%m/%Y")}', f'Group By: {group_by}'], 'collection-trend', 'excel')


def export_receivables_pdf(request):
    end_date, data = _build_receivables_data(request)
    sections = [
        {'title': 'Aging Buckets', 'headers': ['Bucket', 'Amount'], 'rows': [[row['label'], row['amount']] for row in data.get('aging_rows', [])]},
        {'title': 'Customer Outstanding', 'headers': ['Customer', 'Assigned Staff', 'Outstanding', 'Last Activity', 'Age Days', 'Bucket'], 'rows': [[row['party'], row['staff'], row['closing'], row['last_activity'], row['age_days'], row['age_bucket']] for row in data.get('table_rows', [])]},
    ]
    return _analytics_export_response(request, 'Receivables Aging Report', f'As of {end_date.strftime("%d %b %Y")}', data.get('summary'), [], data.get('insights'), sections, [f'As Of: {end_date.strftime("%d/%m/%Y")}'], 'receivables-aging', 'pdf')


def export_receivables_excel(request):
    end_date, data = _build_receivables_data(request)
    sections = [
        {'title': 'Aging Buckets', 'headers': ['Bucket', 'Amount'], 'rows': [[row['label'], row['amount']] for row in data.get('aging_rows', [])]},
        {'title': 'Customer Outstanding', 'headers': ['Customer', 'Assigned Staff', 'Outstanding', 'Last Activity', 'Age Days', 'Bucket'], 'rows': [[row['party'], row['staff'], row['closing'], row['last_activity'], row['age_days'], row['age_bucket']] for row in data.get('table_rows', [])]},
    ]
    return _analytics_export_response(request, 'Receivables Aging Report', f'As of {end_date.strftime("%d %b %Y")}', data.get('summary'), [], data.get('insights'), sections, [f'As Of: {end_date.strftime("%d/%m/%Y")}'], 'receivables-aging', 'excel')


def export_staff_productivity_pdf(request):
    start_date, end_date, data = _build_staff_productivity_data(request)
    sections = [
        {'title': 'Staff Productivity Detail', 'headers': ['Staff', 'Business Value', 'Orders', 'Sales', 'Collections', 'Attendance', 'Efficiency'], 'rows': [[row['label'], row['amountDisplay'], row['count'], _money(row['sales']), _money(row['collections']), row['attendance'], _money(row['efficiency'])] for row in data.get('leaderboard', [])]},
    ]
    return _analytics_export_response(request, 'Staff Productivity Report', 'Sales, collections, orders and attendance-backed efficiency', data.get('summary'), data.get('health'), data.get('insights'), sections, [f'From: {start_date.strftime("%d/%m/%Y")}', f'To: {end_date.strftime("%d/%m/%Y")}'], 'staff-productivity', 'pdf')


def export_staff_productivity_excel(request):
    start_date, end_date, data = _build_staff_productivity_data(request)
    sections = [
        {'title': 'Staff Productivity Detail', 'headers': ['Staff', 'Business Value', 'Orders', 'Sales', 'Collections', 'Attendance', 'Efficiency'], 'rows': [[row['label'], row['amountDisplay'], row['count'], _money(row['sales']), _money(row['collections']), row['attendance'], _money(row['efficiency'])] for row in data.get('leaderboard', [])]},
    ]
    return _analytics_export_response(request, 'Staff Productivity Report', 'Sales, collections, orders and attendance-backed efficiency', data.get('summary'), data.get('health'), data.get('insights'), sections, [f'From: {start_date.strftime("%d/%m/%Y")}', f'To: {end_date.strftime("%d/%m/%Y")}'], 'staff-productivity', 'excel')


def export_order_pipeline_pdf(request):
    start_date, end_date, group_by, data = _build_order_pipeline_data(request)
    sections = [
        {'title': 'Status Split', 'headers': ['Status', 'Count'], 'rows': [[row['label'], row['count']] for row in data.get('status_rows', [])]},
        {'title': 'Top Staff', 'headers': ['Staff', 'Orders'], 'rows': [[row['label'], row['count']] for row in data.get('staff_rows', [])]},
        {'title': 'Top Customers', 'headers': ['Customer', 'Orders'], 'rows': [[row['label'], row['count']] for row in data.get('customer_rows', [])]},
    ]
    return _analytics_export_response(request, 'Order Pipeline Report', f'Grouped by {group_by}', data.get('summary'), data.get('health'), data.get('insights'), sections, [f'From: {start_date.strftime("%d/%m/%Y")}', f'To: {end_date.strftime("%d/%m/%Y")}', f'Group By: {group_by}'], 'order-pipeline', 'pdf')


def export_order_pipeline_excel(request):
    start_date, end_date, group_by, data = _build_order_pipeline_data(request)
    sections = [
        {'title': 'Status Split', 'headers': ['Status', 'Count'], 'rows': [[row['label'], row['count']] for row in data.get('status_rows', [])]},
        {'title': 'Top Staff', 'headers': ['Staff', 'Orders'], 'rows': [[row['label'], row['count']] for row in data.get('staff_rows', [])]},
        {'title': 'Top Customers', 'headers': ['Customer', 'Orders'], 'rows': [[row['label'], row['count']] for row in data.get('customer_rows', [])]},
    ]
    return _analytics_export_response(request, 'Order Pipeline Report', f'Grouped by {group_by}', data.get('summary'), data.get('health'), data.get('insights'), sections, [f'From: {start_date.strftime("%d/%m/%Y")}', f'To: {end_date.strftime("%d/%m/%Y")}', f'Group By: {group_by}'], 'order-pipeline', 'excel')


@cache_page(60 * 2)
def api_exception_dashboard(request):
    start_date, end_date = _report_range(request)
    pending_sales_qs = tenant_qs(request, Sales).filter(isDeleted=False, isApproved=False, buildDate__range=[start_date, end_date])
    pending_collection_qs = tenant_qs(request, Collection).filter(isDeleted=False, isApproved=False, collectionDateTime__date__range=[start_date, end_date])
    pending_order_qs = tenant_qs(request, Order).filter(isDeleted=False, status__in=[Order.Status.DRAFT, Order.Status.SUBMITTED], buildDate__range=[start_date, end_date])
    failed_msg_qs = tenant_qs(request, WhatsappMessageStatus).filter(isDeleted=False).exclude(status='Success').filter(datetime__date__range=[start_date, end_date])
    untallied_qs = tenant_qs(request, Collection).filter(isDeleted=False, isTallied=False, collectionDateTime__date__range=[start_date, end_date])
    return_qs = tenant_qs(request, CashCounter).filter(isDeleted=False, mode='Return', datetime__date__range=[start_date, end_date])
    missing_logout_qs = tenant_qs(request, Attendance).filter(isDeleted=False, isLogIn=True, isLogOut=False, loginDateTime__date__range=[start_date, end_date])
    failed_messages = failed_msg_qs.count()
    pending_sales = pending_sales_qs.count()
    pending_collections = pending_collection_qs.count()
    pending_orders = pending_order_qs.count()
    untallied_amount = float(untallied_qs.aggregate(total=Sum('paidAmount'))['total'] or 0.0)
    untallied_count = untallied_qs.count()
    return_amount = float(return_qs.aggregate(total=Sum('amount'))['total'] or 0.0)
    return_count = return_qs.count()
    missing_logout = missing_logout_qs.count()
    attention_total = pending_sales + pending_collections + pending_orders + failed_messages + untallied_count + missing_logout
    data = {
        'summary': [
            {'label': 'Pending Sales Approval', 'value': pending_sales},
            {'label': 'Pending Collection Approval', 'value': pending_collections},
            {'label': 'Pending Order Approval', 'value': pending_orders},
            {'label': 'Failed Messages', 'value': failed_messages},
        ],
        'health': [
            {'label': 'Attention Items', 'value': attention_total, 'subtext': 'Open issues in selected range', 'tone': 'red' if attention_total else 'teal'},
            {'label': 'Untallied Amount', 'value': _money(untallied_amount), 'subtext': f'{untallied_count} collection entries', 'tone': 'amber'},
            {'label': 'Return Value', 'value': _money(return_amount), 'subtext': f'{return_count} return entries', 'tone': 'amber'},
            {'label': 'Missing Logout', 'value': missing_logout, 'subtext': 'Attendance closures missing', 'tone': 'red' if missing_logout else 'teal'},
        ],
        'rows': [
            {'label': 'Pending Sales Approval', 'amount': pending_sales, 'count': pending_sales, 'tone': 'amber'},
            {'label': 'Pending Collection Approval', 'amount': pending_collections, 'count': pending_collections, 'tone': 'amber'},
            {'label': 'Pending Order Approval', 'amount': pending_orders, 'count': pending_orders, 'tone': 'amber'},
            {'label': 'Failed Messages', 'amount': failed_messages, 'count': failed_messages, 'tone': 'red' if failed_messages else 'green'},
            {'label': 'Untallied Collections', 'amount': untallied_amount, 'amountDisplay': _money(untallied_amount), 'count': untallied_count, 'tone': 'amber'},
            {'label': 'Returns', 'amount': return_amount, 'amountDisplay': _money(return_amount), 'count': return_count, 'tone': 'amber'},
            {'label': 'Missing Logout', 'amount': missing_logout, 'count': missing_logout, 'tone': 'red' if missing_logout else 'green'},
        ],
        'insights': [
            {
                'title': 'Approval backlog',
                'body': f"{pending_sales + pending_collections + pending_orders} approvals are open in the selected range.",
            },
            {
                'title': 'Recovery risk',
                'body': f"Untallied collections hold {_money(untallied_amount)} that still needs closure.",
            },
            {
                'title': 'Operations hygiene',
                'body': f"{missing_logout} attendance records remain incomplete and {failed_messages} messages failed.",
            },
        ],
        'count_caption': 'Count-based exceptions show operational backlog and unresolved actions.',
        'value_caption': 'Value-based exceptions show where money or control risk is sitting.',
    }
    return JsonResponse({'data': data}, safe=False)


@cache_page(60 * 5)
def api_failed_messages(request):
    rows = [
        {
            'label': item.messageTo or '-',
            'amount': item.phone or '-',
            'count': item.datetime.strftime('%d-%m-%Y %I:%M %p'),
        }
        for item in tenant_qs(request, WhatsappMessageStatus).filter(isDeleted=False).exclude(status='Success').order_by('-datetime')[:100]
    ]
    data = {
        'summary': [
            {'label': 'Failed Messages', 'value': len(rows)},
        ],
        'rows': rows,
    }
    return JsonResponse({'data': data}, safe=False)


def export_failed_messages_excel(request):
    qs = tenant_qs(request, WhatsappMessageStatus).filter(isDeleted=False).exclude(status='Success').order_by('-datetime')[:500]
    rows = [[item.messageTo or '-', item.phone or '-', item.status or '-', item.datetime.strftime('%d-%m-%Y %I:%M %p') if item.datetime else '-', item.message or '-'] for item in qs]
    return _xlsx_report_response('failed-messages', [{
        'title': 'Failed Messages',
        'report_title': 'Failed Message Report',
        'report_subtitle': 'Recent failed outbound messages',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('Generated By', _pdf_base_context(request).get('pdf_generated_by'))],
        'tables': [{'title': 'Failed Messages', 'headers': ['Recipient', 'Phone', 'Status', 'Datetime', 'Message'], 'rows': rows}],
    }])


def export_pending_approvals_excel(request):
    start_date, end_date = _report_range(request)
    pending_sales_qs = tenant_qs(request, Sales).filter(isDeleted=False, isApproved=False, buildDate__range=[start_date, end_date]).select_related('partyID', 'createdBy')
    pending_collection_qs = tenant_qs(request, Collection).filter(isDeleted=False, isApproved=False, collectionDateTime__date__range=[start_date, end_date]).select_related('partyID', 'collectedBy')
    pending_order_qs = tenant_qs(request, Order).filter(isDeleted=False, status__in=[Order.Status.DRAFT, Order.Status.SUBMITTED], buildDate__range=[start_date, end_date]).select_related('partyID', 'createdBy')
    sales_rows = [[item.invoiceNumber or '-', item.partyID.name if item.partyID_id else '-', item.createdBy.name if item.createdBy_id else '-', float(item.amount or 0.0), item.buildDate.strftime('%d/%m/%Y') if item.buildDate else '-'] for item in pending_sales_qs]
    collection_rows = [[item.paymentID or '-', item.partyID.name if item.partyID_id else '-', item.collectedBy.name if item.collectedBy_id else '-', float(item.paidAmount or 0.0), item.collectionDateTime.strftime('%d/%m/%Y %I:%M %p') if item.collectionDateTime else '-'] for item in pending_collection_qs]
    order_rows = [[item.orderNumber or '-', item.partyID.name if item.partyID_id else '-', item.createdBy.name if item.createdBy_id else '-', item.get_status_display(), item.buildDate.strftime('%d/%m/%Y') if item.buildDate else '-'] for item in pending_order_qs]
    return _xlsx_report_response('pending-approvals', [{
        'title': 'Pending Approvals',
        'report_title': 'Pending Approval Report',
        'report_subtitle': f'{start_date.strftime("%d/%m/%Y")} to {end_date.strftime("%d/%m/%Y")}',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('From', start_date.strftime('%d/%m/%Y')), ('To', end_date.strftime('%d/%m/%Y')), ('Generated By', _pdf_base_context(request).get('pdf_generated_by'))],
        'tables': [
            {'title': 'Approval Summary', 'headers': ['Queue', 'Count'], 'rows': [['Sales', pending_sales_qs.count()], ['Collections', pending_collection_qs.count()], ['Orders', pending_order_qs.count()]]},
            {'title': 'Pending Sales', 'headers': ['Invoice No', 'Customer', 'Staff', 'Amount', 'Date'], 'rows': sales_rows},
            {'title': 'Pending Collections', 'headers': ['Payment ID', 'Customer', 'Collector', 'Amount', 'Collected On'], 'rows': collection_rows},
            {'title': 'Pending Orders', 'headers': ['Order No', 'Customer', 'Staff', 'Status', 'Date'], 'rows': order_rows},
        ],
    }])
