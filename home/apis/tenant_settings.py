from django.db import transaction
from django.http import JsonResponse

from home.audit import log_audit_event, snapshot_instance
from home.models import AuditLog
from home.services.tenant_settings import (
    normalize_bool,
    request_can_manage_tenant_settings,
    tenant_settings_access_code_error,
)
from home.tenancy import get_current_tenant


def get_non_gst_shadow_status_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not request_can_manage_tenant_settings(request):
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
    if not request_can_manage_tenant_settings(request):
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
    if not request_can_manage_tenant_settings(request):
        return JsonResponse({'message': 'forbidden'}, status=403)
    tenant = get_current_tenant(request)
    if tenant is None:
        return JsonResponse({'message': 'error', 'error': 'Tenant not found.'}, status=400)
    desired_state = normalize_bool(request.POST.get('enabled'))
    password = str(request.POST.get('password') or '')
    raw_code = str(request.POST.get('accessCode') or '').strip()
    if not password:
        return JsonResponse({'message': 'error', 'error': 'Current password is required.'}, safe=False)
    if not request.user.check_password(password):
        return JsonResponse({'message': 'error', 'error': 'Current password is incorrect.'}, safe=False)
    code_error = tenant_settings_access_code_error(tenant, raw_code)
    if code_error:
        return JsonResponse({'message': 'error', 'error': code_error}, safe=False)
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
    if not request_can_manage_tenant_settings(request):
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
    code_error = tenant_settings_access_code_error(tenant, raw_code)
    if code_error:
        return JsonResponse({'message': 'error', 'error': code_error}, safe=False)
    before_snapshot = snapshot_instance(tenant)
    tenant.inventoryEnabled = normalize_bool(request.POST.get('inventoryEnabled'))
    tenant.advancedInvoicingEnabled = normalize_bool(request.POST.get('advancedInvoicingEnabled'))
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
