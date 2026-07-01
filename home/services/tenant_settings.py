from django.contrib.auth.hashers import check_password

from home.navigation import _effective_permission_names


def normalize_bool(raw):
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def request_has_any_permission(request, *perm_names):
    if len(perm_names) == 1 and isinstance(perm_names[0], (list, tuple, set)):
        perm_names = tuple(perm_names[0])
    effective_permissions = _effective_permission_names(request)
    return any(perm_name in effective_permissions for perm_name in perm_names)


def request_can_manage_tenant_settings(request):
    return request_has_any_permission(
        request,
        'home.can_view_party_credentials',
        'home.can_manage_staff',
    )


def tenant_settings_access_code_error(tenant, raw_code):
    if not tenant.nonGstShadowCodeHash:
        return 'Set the 6-digit Non-GST passcode first.'
    if len(raw_code) != 6 or not raw_code.isdigit():
        return 'Enter the valid 6-digit passcode.'
    if not check_password(raw_code, tenant.nonGstShadowCodeHash):
        return 'Invalid 6-digit passcode.'
    return None
