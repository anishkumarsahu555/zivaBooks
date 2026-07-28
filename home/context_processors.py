from .dashboard import build_dashboard_state
from .navigation import build_sidebar_sections
from .tenancy import get_current_tenant, get_staff_user_for_request


STAFF_LOCATION_ADMIN_PERMS = (
    'home.can_manage_staff',
    'home.can_manage_staff_groups',
    'home.can_view_party_credentials',
    'home.can_view_staff_locations',
    'home.can_view_all_attendance',
    'home.can_view_attendance_reports',
)


def app_navigation(request):
    if hasattr(request, '_app_navigation_context_cache'):
        return request._app_navigation_context_cache
    user = getattr(request, 'user', None)
    is_authenticated = bool(user and user.is_authenticated)
    tenant = get_current_tenant(request) if is_authenticated else None
    sidebar_sections = build_sidebar_sections(request)
    staff_user = get_staff_user_for_request(request) if is_authenticated else None
    is_staff_location_admin = bool(
        user and any(user.has_perm(perm_name) for perm_name in STAFF_LOCATION_ADMIN_PERMS)
    )
    dashboard_state = build_dashboard_state(request, sidebar_sections, tenant=tenant, staff_user=staff_user)
    is_customer_login = False
    if is_authenticated and tenant:
        from .navigation import _tenant_membership_role
        from .models import TenantMembership
        is_customer_login = (_tenant_membership_role(request, tenant) == TenantMembership.Role.PARTY)
    from .utils.feature_guards import tenant_has_feature, check_staff_user_limit
    has_gps_tracking = tenant_has_feature(tenant, 'gps_tracking') if tenant else True
    seat_allowed, seat_msg, max_seats, active_count = check_staff_user_limit(tenant, target_will_be_active=True) if tenant else (True, None, 0, 0)

    context = {
        'sidebar_sections': sidebar_sections,
        'current_tenant': tenant,
        'current_staff_user': staff_user,
        'staff_location_autotrack_enabled': bool(staff_user and not is_staff_location_admin and has_gps_tracking),
        'dashboard_state': dashboard_state,
        'is_customer_login': is_customer_login,
        'has_gps_tracking_feature': has_gps_tracking,
        'staff_seat_limit_reached': not seat_allowed,
        'staff_seat_limit_msg': seat_msg or '',
    }
    request._app_navigation_context_cache = context
    return context
