"""
Feature Module Enforcement Guards & View Decorators.
Enforces active LicensePlan module toggles on tenant views and API endpoints.
"""

from functools import wraps
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render


def check_staff_user_limit(tenant, target_will_be_active=True, editing_staff_id=None):
    """
    Validates if adding or activating a staff user exceeds the maximum allowed active staff users on the tenant's LicensePlan.
    Ignores deleted (isDeleted=True) and inactive (isActive='Inactive'/False) staff users.
    Returns (is_allowed, error_message, max_seats, active_count).
    """
    if not tenant or not target_will_be_active:
        return True, None, 0, 0

    plan = getattr(tenant, 'license_plan', None)
    if not plan:
        return True, None, 0, 0

    max_seats = getattr(plan, 'user_seats', 0)
    if not max_seats or max_seats <= 0:
        return True, None, 0, 0

    from home.models import StaffUser

    active_qs = StaffUser.objects.filter(
        tenant=tenant,
        isDeleted=False,
    ).filter(
        Q(isActive=True) | Q(isActive='Active') | Q(isActive='true') | Q(isActive='1')
    )

    if editing_staff_id:
        active_qs = active_qs.exclude(pk=int(editing_staff_id))

    active_count = active_qs.count()

    if active_count >= max_seats:
        plan_name = plan.name or 'Current Plan'
        msg = (
            f"Active staff user limit reached! Your active plan ({plan_name}) allows up to {max_seats} active staff member(s). "
            f"You currently have {active_count} active staff member(s). Please upgrade your License Plan to add or activate more staff members."
        )
        return False, msg, max_seats, active_count

    return True, None, max_seats, active_count


FEATURE_PLAN_MAPPINGS = {
    'sales_billing': 'sales_billing_enabled',
    'proforma_estimates': 'proforma_estimates_enabled',
    'custom_invoice_series': 'custom_invoice_series_enabled',
    'sales_returns': 'sales_returns_credit_notes_enabled',
    'thermal_printing': 'thermal_printing_enabled',
    'pdf_excel_exports': 'pdf_excel_exports_enabled',
    'gst_einvoicing': 'gst_einvoicing_enabled',
    'einvoice': 'gst_einvoicing_enabled',
    'eway_bill': 'eway_bill_enabled',
    'inventory': 'inventory_enabled',
    'fefo_batch_tracking': 'fefo_batch_tracking_enabled',
    'multi_warehouse': 'multi_warehouse_enabled',
    'stock_adjustment': 'stock_adjustment_vouchers_enabled',
    'zero_stock_alerts': 'zero_stock_alerts_enabled',
    'multi_counter_pos': 'multi_counter_pos_enabled',
    'dual_otp': 'dual_otp_enabled',
    'cashbook_bankbook': 'cashbook_bankbook_enabled',
    'party_ledgers': 'party_ledgers_enabled',
    'double_entry_journals': 'double_entry_journals_enabled',
    'gps_tracking': 'gps_tracking_enabled',
    'route_history': 'route_history_map_enabled',
    'geofenced_attendance': 'geofenced_attendance_enabled',
    'staff_attendance': 'geofenced_attendance_enabled',
    'attendance': 'geofenced_attendance_enabled',
    'ziva_ai': 'ziva_ai_enabled',
    'multi_branch': 'multi_branch_isolation_enabled',
    'shadow_mode': 'custom_shadow_mode_enabled',
    'role_permissions': 'role_based_permissions_enabled',
}


def tenant_has_feature(tenant, feature_code: str) -> bool:
    """
    Check if a specific feature module is enabled on the tenant or its active LicensePlan.
    """
    if not tenant:
        return True

    plan = getattr(tenant, 'license_plan', None)
    attr_name = FEATURE_PLAN_MAPPINGS.get(feature_code)

    if plan and attr_name and hasattr(plan, attr_name):
        return bool(getattr(plan, attr_name, True))

    # When no license plan is attached, fallback to Tenant direct settings & defaults
    if plan is None:
        if feature_code == 'inventory':
            return getattr(tenant, 'inventoryEnabled', True)
        if feature_code in ('gst_einvoicing', 'einvoice'):
            return getattr(tenant, 'einvoice_enabled', False)
        if feature_code == 'shadow_mode':
            return getattr(tenant, 'nonGstShadowEnabled', False)
        if feature_code in ('gps_tracking', 'route_history'):
            return getattr(tenant, 'enableAttendanceLocationMaps', True)
        return True

    return True


def require_tenant_feature(feature_code: str):
    """
    View decorator that restricts access if the required feature module is locked on the tenant's plan.
    Returns HTTP 403 JsonResponse for API requests or renders feature_locked.html for web requests.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            # Resolve tenant from request
            tenant = getattr(request, 'tenant', None)
            if not tenant and hasattr(request, 'user') and getattr(request.user, 'is_authenticated', False):
                membership = getattr(request.user, 'tenant_memberships', None)
                if membership:
                    active_mem = membership.filter(tenant__isActive=True).first()
                    if active_mem:
                        tenant = active_mem.tenant

            if tenant and not tenant_has_feature(tenant, feature_code):
                is_ajax_or_api = (
                    request.path.startswith('/api/') or
                    request.content_type == 'application/json' or
                    request.headers.get('x-requested-with') == 'XMLHttpRequest' or
                    request.headers.get('accept') == 'application/json'
                )

                if is_ajax_or_api:
                    return JsonResponse({
                        'status': 'error',
                        'code': 'FEATURE_LOCKED',
                        'feature_code': feature_code,
                        'message': f"The feature module '{feature_code.replace('_', ' ').title()}' is not included in your organization's active License Plan. Please contact your account owner to upgrade."
                    }, status=403)

                return render(request, 'home/feature_locked.html', {
                    'feature_code': feature_code,
                    'feature_title': feature_code.replace('_', ' ').title(),
                    'tenant': tenant,
                }, status=403)

            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator
