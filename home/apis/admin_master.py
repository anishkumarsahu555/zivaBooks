from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Sum, Value, F, FloatField, ExpressionWrapper
from django.db.models.functions import Cast, Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from home.audit import log_audit_event, snapshot_instance
from home.books import ensure_bank_account
from home.cache_keys import party_list_cache_key, party_list_version_cache_key
from home.credential_links import DEFAULT_CREDENTIAL_LINK_MINUTES, create_credential_share_token
from home.models import *
from home.navigation import _effective_permission_names
from home.services.collections import has_any_permission as _has_any_permission
from home.services.invoices import normalize_gst_number as _normalize_gst_number
from home.services.reports import formatINR
from home.services.sequences import next_tenant_sequence_value as _next_tenant_sequence_value
from utils.logger import logger
from home.tenancy import assign_tenant, ensure_membership, get_current_tenant, tenant_object_or_superuser, tenant_qs
from home.utils.feature_guards import check_staff_user_limit

User = get_user_model()
DEFAULT_AVATAR_URL = "/static/images/default-avatar.svg"

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
def _party_balance_label(balance):
    amount = float(balance or 0.0)
    display = f'₹ {formatINR(format(abs(amount), ".2f"))}'
    if amount > 0:
        return f'<span class="ui tiny orange label">Due {display}</span>'
    if amount < 0:
        return f'<span class="ui tiny teal label">Advance {display}</span>'
    return '<span class="ui tiny grey label">Settled</span>'


def _filter_party_balance_queryset(request, qs):
    party_group = str(request.GET.get('partyGroup') or '').strip()
    staff = str(request.GET.get('staffID') or '').strip()
    balance_type = str(request.GET.get('balanceType') or '').strip()
    if party_group.isdigit():
        qs = qs.filter(partyGroupID_id=int(party_group))
    if staff.isdigit():
        qs = qs.filter(assignTo_id=int(staff))
    if balance_type == 'due':
        qs = qs.filter(current_balance__gt=0)
    elif balance_type == 'advance':
        qs = qs.filter(current_balance__lt=0)
    elif balance_type == 'settled':
        qs = qs.filter(current_balance=0)
    return qs


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


def _required_decimal_from_request(value, field_label):
    try:
        return Decimal(str(value if value is not None else '0').strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f'{field_label} must be a valid number.')
def _tenant_admin_group_name(tenant):
    tenant_slug = getattr(tenant, 'slug', 'tenant')
    return f'{tenant_slug}:Tenant Admin'


LOCATION_ASSIGNABLE_PERMISSION_CODENAMES = {
    'can_track_staff_location',
    'can_view_staff_locations',
    'can_access_ziva_ai',
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


def _request_can_manage_non_gst_shadow(request):
    return _request_has_any_permission(
        request,
        'home.can_view_party_credentials',
        'home.can_manage_staff',
    )


def _can_manage_party_master_data(user):
    return user.has_perm('home.can_manage_parties')
def _can_add_party(user):
    return user.has_perm('home.add_party') or _can_manage_party_master_data(user)
def _can_edit_party(user):
    return user.has_perm('home.change_party') or _can_manage_party_master_data(user)
def _can_delete_party(user):
    return user.has_perm('home.delete_party') or _can_manage_party_master_data(user)


def _validate_party_payload(request, *, is_edit=False):
    party_name = str(request.POST.get("partyName") or '').strip()
    phone_number = str(request.POST.get("phoneNumber") or '').strip()
    legal_name = str(request.POST.get("legalName") or '').strip() or None
    gst_number = _normalize_gst_number(request.POST.get("gstNumber")) or None
    billing_address = str(request.POST.get("billingAddress") or '').strip() or None
    address = str(request.POST.get("address") or '').strip() or None
    is_b2b = str(request.POST.get("isB2B") or '').lower() in {'1', 'true', 'yes', 'on'}
    opening_balance = _required_decimal_from_request(request.POST.get("openingBalance"), 'Opening balance')
    party_group_id = str(request.POST.get("partyGroup") or '').strip()
    staff_id = str(request.POST.get("staffID") or '').strip()

    if not party_name:
        raise ValueError('Party name is required.')
    if not phone_number:
        raise ValueError('Phone number is required.')
    if len(phone_number) > 20:
        raise ValueError('Phone number must be 20 characters or fewer.')
    if gst_number and len(gst_number) > 20:
        raise ValueError('GST number must be 20 characters or fewer.')
    if not party_group_id.isdigit():
        raise ValueError('Party group/station is required.')
    party_group = tenant_qs(request, PartyGroup).filter(pk=int(party_group_id), isDeleted=False).first()
    if party_group is None:
        raise ValueError('Selected party group/station was not found.')
    if not staff_id.isdigit():
        raise ValueError('Assigned staff is required.')
    staff = tenant_qs(request, StaffUser).filter(pk=int(staff_id), isDeleted=False).first()
    if staff is None:
        raise ValueError('Selected assigned staff was not found.')

    payload = {
        'party_name': party_name,
        'phone_number': phone_number,
        'legal_name': legal_name,
        'gst_number': gst_number,
        'is_b2b': is_b2b,
        'billing_address': billing_address,
        'address': address,
        'opening_balance': opening_balance,
        'party_group': party_group,
        'staff': staff,
    }
    if is_edit:
        edit_id = str(request.POST.get("EditId") or '').strip()
        if not edit_id.isdigit():
            raise ValueError('Valid party id is required.')
        payload['party'] = tenant_qs(request, Party).filter(id=int(edit_id), isDeleted=False).first()
        if payload['party'] is None:
            raise ValueError('Party was not found.')
    return payload
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
def _can_manage_sales(user):
    return user.has_perm('home.can_manage_sales')
def _can_approve_sales(user):
    return user.has_perm('home.can_approve_sales')
def _has_sales_access(user):
    return _has_any_permission(user, 'home.can_manage_sales', 'home.can_approve_sales', 'home.view_sales', 'home.add_sales', 'home.change_sales')
def _can_filter_all_sales(user):
    return _is_admin_like(user) or _can_manage_sales(user) or _can_approve_sales(user)
def _can_manage_inventory_records(user):
    return _has_any_permission(user, 'home.can_manage_inventory', 'home.add_inventoryitem', 'home.change_inventoryitem', 'home.can_view_party_credentials')
def _can_manage_purchase_records(user):
    return _has_any_permission(user, 'home.add_purchaseinvoice', 'home.change_purchaseinvoice', 'home.delete_purchaseinvoice', 'home.can_view_party_credentials')
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
def _sales_list_base_qs(request_or_user):
    return tenant_qs(request_or_user, Sales).select_related('partyID', 'createdBy', 'approvedBy').only(
        'id', 'paymentID', 'invoiceNumber', 'amount', 'buildDate', 'datetime', 'remark', 'isApproved',
        'partyID__name', 'createdBy__name', 'approvedBy__name'
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

            tenant = get_current_tenant(request)
            target_is_active = UserStatus == 'Active' or str(UserStatus).lower() in ('true', '1', 'active')
            is_allowed, error_msg, max_seats, active_count = check_staff_user_limit(tenant, target_will_be_active=target_is_active)
            if not is_allowed:
                return JsonResponse({'message': 'error', 'error': error_msg, 'code': 'USER_LIMIT_EXCEEDED'}, safe=False, status=400)
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
        except Exception as e:
            return JsonResponse({'message': 'error', 'error': f"Failed to save staff user: {str(e)}"}, safe=False, status=400)


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
        party_name = C_User.partyGroupID.name
    except:
        party = 'N/A'
        party_name = 'N/A'
    
    group_name = 'N/A'
    if C_User.staffGroup:
        group_name = C_User.staffGroup.name
    elif C_User.group:
        group_name = C_User.group

    data = {
        'ID': C_User.pk,
        'UserName': C_User.name,
        'UserPhone': C_User.phone,
        'UserAddress': C_User.address,
        'UserEmail': C_User.email,
        'UserPassword': C_User.userPassword,
        'UserGroup': C_User.staffGroup_id,
        'UserGroupName': group_name,
        'PartyGroup': party,
        'PartyGroupName': party_name,
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

            tenant = get_current_tenant(request)
            target_is_active = UserStatus == 'Active' or str(UserStatus).lower() in ('true', '1', 'active')
            is_allowed, error_msg, max_seats, active_count = check_staff_user_limit(
                tenant,
                target_will_be_active=target_is_active,
                editing_staff_id=EditUserId
            )
            if not is_allowed:
                return JsonResponse({'message': 'error', 'error': error_msg, 'code': 'USER_LIMIT_EXCEEDED'}, safe=False, status=400)
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
        except Exception as e:
            return JsonResponse({'message': 'error', 'error': f"Failed to update staff user: {str(e)}"}, safe=False, status=400)
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
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _can_add_party(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        payload = _validate_party_payload(request)
        obj = Party()
        assign_tenant(obj, request)
        obj.name = payload['party_name']
        obj.legalName = payload['legal_name']
        obj.phone = payload['phone_number']
        obj.gstNumber = payload['gst_number']
        obj.isB2B = payload['is_b2b']
        obj.openingBalance = payload['opening_balance']
        obj.assignTo = payload['staff']
        obj.address = payload['address']
        obj.billingAddress = payload['billing_address']
        obj.partyGroupID = payload['party_group']
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
    except ValueError as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=400, safe=False)
    except Exception:
        return JsonResponse({'message': 'error', 'error': 'Unable to add party.'}, status=500, safe=False)
@transaction.atomic
def generate_party_users_api(request):
    if request.method == 'GET':
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        if not _can_manage_party_master_data(request.user):
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
    order_columns = ['name', 'phone', 'gstNumber', 'openingBalance', 'current_balance', 'username', 'password', 'partyGroupID.name', 'assignTo.name', 'datetime']
    def get_initial_queryset(self):
        return tenant_qs(self.request, Party).select_related('partyGroupID', 'assignTo').filter(isDeleted__exact=False).only(
            'id', 'name', 'legalName', 'phone', 'gstNumber', 'isB2B', 'openingBalance', 'username', 'password', 'address', 'billingAddress', 'datetime',
            'partyGroupID__name', 'assignTo__name'
        ).annotate(
            ledger_debit=Coalesce(
                Sum('ledger_entries__debit', filter=Q(ledger_entries__isDeleted=False)),
                Value(0.0),
                output_field=FloatField(),
            ),
            ledger_credit=Coalesce(
                Sum('ledger_entries__credit', filter=Q(ledger_entries__isDeleted=False)),
                Value(0.0),
                output_field=FloatField(),
            ),
        ).annotate(
            current_balance=ExpressionWrapper(
                Cast(F('openingBalance'), FloatField()) + F('ledger_debit') - F('ledger_credit'),
                output_field=FloatField(),
            )
        )
    def filter_queryset(self, qs):
        qs = _filter_party_balance_queryset(self.request, qs)
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
                if item.assignTo:
                    usr = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.assignTo.pk}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.assignTo.name)}</span>'''
                else:
                    usr = 'N/A'
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
                _party_balance_label(getattr(item, 'current_balance', item.openingBalance)),
                username,
                password,
                party,
                usr,
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,
            ])
        return json_data


def party_balance_summary_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_party_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    rows = tenant_qs(request, Party).filter(isDeleted=False).annotate(
        ledger_debit=Coalesce(
            Sum('ledger_entries__debit', filter=Q(ledger_entries__isDeleted=False)),
            Value(0.0),
            output_field=FloatField(),
        ),
        ledger_credit=Coalesce(
            Sum('ledger_entries__credit', filter=Q(ledger_entries__isDeleted=False)),
            Value(0.0),
            output_field=FloatField(),
        ),
    ).annotate(
        current_balance=ExpressionWrapper(
            Cast(F('openingBalance'), FloatField()) + F('ledger_debit') - F('ledger_credit'),
            output_field=FloatField(),
        )
    )
    rows = _filter_party_balance_queryset(request, rows).values_list('current_balance', flat=True)
    receivable = 0.0
    advance = 0.0
    settled = 0
    party_count = 0
    for balance in rows:
        party_count += 1
        amount = float(balance or 0.0)
        if amount > 0:
            receivable += amount
        elif amount < 0:
            advance += abs(amount)
        else:
            settled += 1
    net = receivable - advance
    return JsonResponse({
        'message': 'success',
        'partyCount': party_count,
        'settledCount': settled,
        'receivable': receivable,
        'advance': advance,
        'net': net,
        'receivableDisplay': f'₹ {formatINR(format(receivable, ".2f"))}',
        'advanceDisplay': f'₹ {formatINR(format(advance, ".2f"))}',
        'netDisplay': f'₹ {formatINR(format(abs(net), ".2f"))}',
        'netType': 'receivable' if net > 0 else ('advance' if net < 0 else 'settled'),
    })


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
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _can_edit_party(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        payload = _validate_party_payload(request, is_edit=True)
        obj = payload['party']
        obj.name = payload['party_name']
        obj.legalName = payload['legal_name']
        obj.phone = payload['phone_number']
        obj.gstNumber = payload['gst_number']
        obj.isB2B = payload['is_b2b']
        obj.openingBalance = payload['opening_balance']
        obj.address = payload['address']
        obj.billingAddress = payload['billing_address']
        obj.partyGroupID = payload['party_group']
        obj.assignTo = payload['staff']
        obj.save()
        _bump_party_cache_version(request)
        return JsonResponse({'message': 'success'}, safe=False)
    except ValueError as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=400, safe=False)
    except Exception:
        return JsonResponse({'message': 'error', 'error': 'Unable to update party.'}, status=500, safe=False)
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
