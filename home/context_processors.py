from .dashboard import build_dashboard_state
from .navigation import build_sidebar_sections
from .tenancy import get_current_tenant, get_staff_user_for_request


def app_navigation(request):
    user = getattr(request, 'user', None)
    is_authenticated = bool(user and user.is_authenticated)
    tenant = get_current_tenant(request) if is_authenticated else None
    sidebar_sections = build_sidebar_sections(request)
    staff_user = get_staff_user_for_request(request) if is_authenticated else None
    dashboard_state = build_dashboard_state(request, sidebar_sections, tenant=tenant, staff_user=staff_user)
    return {
        'sidebar_sections': sidebar_sections,
        'current_tenant': tenant,
        'dashboard_state': dashboard_state,
    }
