from datetime import date

from django.urls import reverse

from .dashboard_alerts import build_action_alerts
from .dashboard_charts import build_dashboard_chart_cards
from .navigation import _effective_permission_names


def _build_license_message(tenant):
    try:
        from activation.models import Validity
    except Exception:
        return ''

    qs = Validity.objects.all()
    if tenant is not None:
        validity = qs.filter(tenant=tenant).order_by('-expiryDate', '-id').first()
        if validity is None:
            validity = qs.filter(tenant__isnull=True).order_by('-expiryDate', '-id').first()
    else:
        validity = qs.filter(tenant__isnull=True).order_by('-expiryDate', '-id').first()

    if validity and validity.expiryDate:
        if validity.expiryDate < date.today():
            return 'Activation expired. Please contact ZivaInfotech for activation.'
        return f'Your App License is Valid till {validity.expiryDate.strftime("%d-%m-%Y")}'
    return 'You are using a trial version.'


SECTION_TONES = {
    'General': 'ocean',
    'Staff': 'sun',
    'Collections': 'mint',
    'Sales': 'ember',
    'Orders': 'ink',
    'Cash Counter': 'berry',
    'Party': 'sky',
    'Attendance': 'violet',
    'Banks': 'aqua',
    'Message History': 'rose',
    'Audit': 'slate',
    'Profile': 'slate',
}


def _has_any_perm(request_or_user, perm_names):
    effective_permissions = _effective_permission_names(request_or_user)
    return any(perm_name in effective_permissions for perm_name in perm_names)


def _is_admin_like(request_or_user):
    return _has_any_perm(request_or_user, [
        'home.can_manage_staff',
        'home.can_manage_staff_groups',
        'home.can_approve_collection',
        'home.can_manage_sales',
        'home.can_manage_orders',
        'home.can_manage_cash_counter',
        'home.can_manage_books',
    ])


def _should_show_license_message(request_or_user):
    return _has_any_perm(request_or_user, [
        'home.can_manage_staff',
        'home.can_view_party_credentials',
    ])


def _can_filter_cash_counter(request_or_user):
    return _has_any_perm(request_or_user, [
        'home.can_manage_cash_counter',
        'home.can_view_party_credentials',
        'home.can_approve_collection',
    ])


def build_dashboard_state(request, sidebar_sections, tenant=None, staff_user=None):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {
            'hero': {},
            'metric_groups': [],
            'action_sections': [],
            'license_message': '',
        }
    if hasattr(request, '_dashboard_state_cache'):
        return request._dashboard_state_cache
    effective_permissions = _effective_permission_names(request)

    display_name = (
        getattr(staff_user, 'name', None)
        or getattr(user, 'first_name', None)
        or getattr(user, 'username', 'User')
    )
    tenant_name = getattr(tenant, 'name', 'Workspace')
    action_sections = []
    enabled_modules = []
    for section in sidebar_sections:
        items = section.get('items', [])
        if not items:
            continue
        tone = SECTION_TONES.get(section['title'], 'ocean')
        action_sections.append({
            'title': section['title'],
            'tone': tone,
            'items': items,
        })
        if section['title'] not in ['General', 'Profile']:
            enabled_modules.append(section['title'])

    hero = {
        'title': f'Hello, {display_name}',
        'subtitle': f'{tenant_name} workspace is shaped by the permissions currently assigned to this user.',
        'tenant_name': tenant_name,
        'module_count': len(enabled_modules),
        'quick_count': sum(len(section['items']) for section in action_sections),
        'module_labels': enabled_modules[:6],
    }

    primary_module = enabled_modules[0] if enabled_modules else 'Workspace'
    metric_groups = [{
        'title': 'Access Snapshot',
        'subtitle': 'A quick summary of what this user can reach right now.',
        'tone': 'slate',
        'cards': [
            {
                'id': 'dashboard-static-modules',
                'field': 'static',
                'label': 'Enabled Modules',
                'description': 'Unique dashboard modules available to this user.',
                'icon': 'layer group',
                'action_label': 'Open Dashboard',
                'action_url': reverse('homeApp:homepage'),
                'static_value': hero['module_count'],
            },
            {
                'id': 'dashboard-static-actions',
                'field': 'static',
                'label': 'Quick Actions',
                'description': 'Direct navigation items rendered from current permissions.',
                'icon': 'bolt',
                'action_label': 'Browse Modules',
                'action_url': reverse('homeApp:homepage'),
                'static_value': hero['quick_count'],
            },
            {
                'id': 'dashboard-static-tenant',
                'field': 'static',
                'label': 'Workspace',
                'description': 'Current tenant context for the signed-in user.',
                'icon': 'building outline',
                'action_label': 'Open Dashboard',
                'action_url': reverse('homeApp:homepage'),
                'static_value': tenant_name,
            },
            {
                'id': 'dashboard-static-focus',
                'field': 'static',
                'label': 'Primary Focus',
                'description': 'The first active module available in this dashboard.',
                'icon': 'compass outline',
                'action_label': 'Open Dashboard',
                'action_url': reverse('homeApp:homepage'),
                'static_value': primary_module,
            },
        ],
    }]
    if _is_admin_like(request):
        metric_groups.append({
            'title': 'Control Center',
            'subtitle': 'Tenant-wide visibility across operational modules assigned to this user.',
            'tone': 'ocean',
            'endpoint': reverse('homeApp:get_admin_dashboard_report_api'),
            'cards': [
                {
                    'id': 'dashboard-admin-party-count',
                    'field': 'partyCount',
                    'label': 'Total Parties',
                    'description': 'All party records available in this tenant.',
                    'icon': 'users',
                    'action_label': 'Open Party List',
                    'action_url': reverse('homeApp:party_list'),
                },
                {
                    'id': 'dashboard-admin-staff-count',
                    'field': 'staffCount',
                    'label': 'Total Staff',
                    'description': 'All staff users currently active in the tenant.',
                    'icon': 'user tie',
                    'action_label': 'Open Staff List',
                    'action_url': reverse('homeApp:user_list'),
                },
                {
                    'id': 'dashboard-admin-location-usage',
                    'field': 'locationCount',
                    'label': 'Location Service',
                    'description': 'Current geolocation package usage.',
                    'icon': 'map marker alternate',
                    'action_label': 'Open Dashboard',
                    'action_url': reverse('homeApp:admin_home'),
                },
                {
                    'id': 'dashboard-admin-collection-total',
                    'field': 'collection',
                    'label': "Today's Collection",
                    'description': 'Tenant-wide collection total for today.',
                    'icon': 'hand holding usd',
                    'action_label': 'Open Collection List',
                    'action_url': reverse('homeApp:collection_list'),
                },
                {
                    'id': 'dashboard-admin-sales-total',
                    'field': 'Sale',
                    'label': "Today's Sales",
                    'description': 'Tenant-wide sales total for today.',
                    'icon': 'shopping cart',
                    'action_label': 'Open Sales List',
                    'action_url': reverse('homeApp:sales_list'),
                },
            ],
        })

    if _has_any_perm(request, ['home.add_collection', 'home.view_collection', 'home.change_collection']) and not _is_admin_like(request):
        metric_groups.append({
            'title': 'My Collections',
            'subtitle': 'Personal collection activity for the current user.',
            'tone': 'mint',
            'endpoint': reverse('homeApp:get_staff_dashboard_report_api'),
            'cards': [
                {
                    'id': 'dashboard-ops-party-count',
                    'field': 'partyCount',
                    'label': 'Assigned Parties',
                    'description': 'Parties mapped to your collection scope.',
                    'icon': 'users',
                    'action_label': 'Open My Collections',
                    'action_url': reverse('homeApp:my_collection'),
                },
                {
                    'id': 'dashboard-ops-collection-count',
                    'field': 'collection_totalCount',
                    'label': "Today's Count",
                    'description': 'Number of your collection entries today.',
                    'icon': 'tasks',
                    'action_label': 'Open My Collections',
                    'action_url': reverse('homeApp:my_collection'),
                },
                {
                    'id': 'dashboard-ops-collection-total',
                    'field': 'collection',
                    'label': "Today's Amount",
                    'description': 'Amount collected by you today.',
                    'icon': 'hand holding usd',
                    'action_label': 'Open My Collections',
                    'action_url': reverse('homeApp:my_collection'),
                },
                {
                    'id': 'dashboard-ops-collection-lifetime',
                    'field': 'collection_total',
                    'label': 'Lifetime Collection',
                    'description': 'Your total collection amount so far.',
                    'icon': 'chart line',
                    'action_label': 'Take Collection',
                    'action_url': reverse('homeApp:add_collection'),
                },
            ],
        })

    if _has_any_perm(request, ['home.view_cashcounter', 'home.view_collectioncashcounter', 'home.can_manage_cash_counter']):
        metric_groups.append({
            'title': 'Cash Counter',
            'subtitle': 'Counter activity shown in the scope available to this user.',
            'tone': 'berry',
            'endpoint': reverse(
                'homeApp:get_cash_counter_dashboard_report_admin_api'
                if _can_filter_cash_counter(request)
                else 'homeApp:get_cash_counter_dashboard_report_api'
            ),
            'cards': [
                {
                    'id': 'dashboard-cash-cash',
                    'field': 'cash_total',
                    'label': 'Cash',
                    'description': 'Cash entries in the current scope.',
                    'icon': 'money bill alternate outline',
                    'action_label': 'Open Counter List',
                    'action_url': reverse('homeApp:my_cash_counter_list'),
                },
                {
                    'id': 'dashboard-cash-card',
                    'field': 'card_total',
                    'label': 'Card',
                    'description': 'Card entries in the current scope.',
                    'icon': 'credit card outline',
                    'action_label': 'Open Counter List',
                    'action_url': reverse('homeApp:my_cash_counter_list'),
                },
                {
                    'id': 'dashboard-cash-credit',
                    'field': 'credit_total',
                    'label': 'Credit',
                    'description': 'Credit entries in the current scope.',
                    'icon': 'file invoice dollar',
                    'action_label': 'Open Counter List',
                    'action_url': reverse('homeApp:my_cash_counter_list'),
                },
                {
                    'id': 'dashboard-cash-collection',
                    'field': 'collection_total',
                    'label': 'Collection',
                    'description': 'Counter collection amount in scope.',
                    'icon': 'cash register',
                    'action_label': 'Open Counter List',
                    'action_url': reverse('homeApp:my_cash_counter_list'),
                },
                {
                    'id': 'dashboard-cash-expense',
                    'field': 'expense_total',
                    'label': 'Expense',
                    'description': 'Expenses recorded in the current scope.',
                    'icon': 'receipt',
                    'action_label': 'Open Counter List',
                    'action_url': reverse('homeApp:my_cash_counter_list'),
                },
                {
                    'id': 'dashboard-cash-mix',
                    'field': 'mix_cash_total',
                    'label': 'Mix Cash',
                    'description': 'Cash side of mix entries.',
                    'icon': 'random',
                    'action_label': 'Open Counter List',
                    'action_url': reverse('homeApp:my_cash_counter_list'),
                    'secondary_id': 'dashboard-cash-mix-card',
                    'secondary_field': 'mix_card_total',
                    'secondary_label': 'Mix Card',
                },
            ],
        })

    if _has_any_perm(request, ['home.can_manage_parties', 'home.can_view_party_credentials']):
        metric_groups.append({
            'title': 'Parties',
            'subtitle': 'Party coverage and assignment health in this tenant.',
            'tone': 'sky',
            'endpoint': reverse('homeApp:get_party_dashboard_report_api'),
            'cards': [
                {
                    'id': 'dashboard-party-total',
                    'field': 'party_total_count',
                    'label': 'Total Parties',
                    'description': 'All active parties in this tenant.',
                    'icon': 'address book',
                    'action_label': 'Open Party List',
                    'action_url': reverse('homeApp:party_list'),
                },
                {
                    'id': 'dashboard-party-groups',
                    'field': 'party_group_count',
                    'label': 'Party Groups',
                    'description': 'Active party group categories.',
                    'icon': 'users',
                    'action_label': 'Open Party Groups',
                    'action_url': reverse('homeApp:party_group_list'),
                },
                {
                    'id': 'dashboard-party-assigned',
                    'field': 'party_assigned_count',
                    'label': 'Assigned',
                    'description': 'Parties assigned to a staff user.',
                    'icon': 'user check',
                    'action_label': 'Open Party List',
                    'action_url': reverse('homeApp:party_list'),
                },
                {
                    'id': 'dashboard-party-unassigned',
                    'field': 'party_unassigned_count',
                    'label': 'Unassigned',
                    'description': 'Parties not assigned to any staff user.',
                    'icon': 'user times',
                    'action_label': 'Fix Assignments',
                    'action_url': reverse('homeApp:party_list'),
                },
                {
                    'id': 'dashboard-party-recent',
                    'field': 'party_recent_count',
                    'label': 'New (30d)',
                    'description': 'Parties created in the last 30 days.',
                    'icon': 'calendar plus outline',
                    'action_label': 'Open Party List',
                    'action_url': reverse('homeApp:party_list'),
                },
            ],
        })

    if _has_any_perm(request, ['home.view_bank', 'home.add_bank', 'home.change_bank', 'home.delete_bank', 'home.can_view_party_credentials']):
        metric_groups.append({
            'title': 'Banks',
            'subtitle': 'Bank coverage and usage across collections.',
            'tone': 'aqua',
            'endpoint': reverse('homeApp:get_bank_dashboard_report_api'),
            'cards': [
                {
                    'id': 'dashboard-bank-total',
                    'field': 'bank_total_count',
                    'label': 'Total Banks',
                    'description': 'All active bank entries in this tenant.',
                    'icon': 'university',
                    'action_label': 'Open Bank List',
                    'action_url': reverse('homeApp:bank_list'),
                },
                {
                    'id': 'dashboard-bank-used',
                    'field': 'bank_used_count',
                    'label': 'Banks Used',
                    'description': 'Banks referenced in collection entries.',
                    'icon': 'linkify',
                    'action_label': 'Open Collections',
                    'action_url': reverse('homeApp:collection_list') if 'home.view_collection' in effective_permissions else reverse('homeApp:bank_list'),
                },
                {
                    'id': 'dashboard-bank-with-account',
                    'field': 'bank_with_account_count',
                    'label': 'With Account No',
                    'description': 'Banks having an account number set.',
                    'icon': 'id card outline',
                    'action_label': 'Open Bank List',
                    'action_url': reverse('homeApp:bank_list'),
                },
                {
                    'id': 'dashboard-bank-missing-account',
                    'field': 'bank_missing_account_count',
                    'label': 'Missing Account No',
                    'description': 'Banks missing account numbers (cleanup).',
                    'icon': 'exclamation triangle',
                    'action_label': 'Open Bank List',
                    'action_url': reverse('homeApp:bank_list'),
                },
            ],
        })

    if _has_any_perm(request, ['home.view_sales', 'home.add_sales', 'home.change_sales', 'home.can_manage_sales']):
        metric_groups.append({
            'title': 'Sales Overview',
            'subtitle': 'Sales performance in the scope available to this user.',
            'tone': 'ember',
            'endpoint': reverse('homeApp:get_sales_dashboard_report_api'),
            'cards': [
                {
                    'id': 'dashboard-sales-today-total',
                    'field': 'sales_today_total',
                    'label': "Today's Sales",
                    'description': 'Total sales amount for today in current scope.',
                    'icon': 'shopping cart',
                    'action_label': 'Open Sales List',
                    'action_url': reverse('homeApp:my_sales_list'),
                },
                {
                    'id': 'dashboard-sales-today-count',
                    'field': 'sales_today_count',
                    'label': "Today's Count",
                    'description': 'Number of sales entries created today.',
                    'icon': 'hashtag',
                    'action_label': 'Open Sales List',
                    'action_url': reverse('homeApp:my_sales_list'),
                },
                {
                    'id': 'dashboard-sales-lifetime-total',
                    'field': 'sales_lifetime_total',
                    'label': 'Lifetime Sales',
                    'description': 'Total sales amount in accessible scope.',
                    'icon': 'chart bar',
                    'action_label': 'Open Sales List',
                    'action_url': reverse('homeApp:my_sales_list'),
                },
                {
                    'id': 'dashboard-sales-record-count',
                    'field': 'sales_record_count',
                    'label': 'Sales Records',
                    'description': 'Total number of accessible sales entries.',
                    'icon': 'clipboard list',
                    'action_label': 'Add Sale',
                    'action_url': reverse('homeApp:add_sales') if 'home.add_sales' in effective_permissions else reverse('homeApp:my_sales_list'),
                },
            ],
        })

    if _has_any_perm(request, ['home.view_order', 'home.add_order', 'home.change_order', 'home.can_manage_orders', 'home.can_approve_orders']):
        orders_list_url = reverse('homeApp:orders_list')
        metric_groups.append({
            'title': 'Orders Overview',
            'subtitle': 'Order volume and approval status in the scope available to this user.',
            'tone': 'ink',
            'endpoint': reverse('homeApp:get_orders_dashboard_report_api'),
            'cards': [
                {
                    'id': 'dashboard-orders-today-count',
                    'field': 'orders_today_count',
                    'label': "Today's Orders",
                    'description': 'Orders created for today in current scope.',
                    'icon': 'clipboard list',
                    'action_label': 'Open Orders',
                    'action_url': orders_list_url,
                },
                {
                    'id': 'dashboard-orders-pending-count',
                    'field': 'orders_pending_count',
                    'label': 'Pending Approval',
                    'description': 'Submitted orders waiting for approval.',
                    'icon': 'hourglass half',
                    'action_label': 'Review Orders',
                    'action_url': orders_list_url,
                },
                {
                    'id': 'dashboard-orders-approved-today',
                    'field': 'orders_approved_today_count',
                    'label': 'Approved Today',
                    'description': 'Orders approved today in current scope.',
                    'icon': 'check circle',
                    'action_label': 'Open Orders',
                    'action_url': orders_list_url,
                },
                {
                    'id': 'dashboard-orders-record-count',
                    'field': 'orders_record_count',
                    'label': 'Orders Records',
                    'description': 'Total number of accessible order entries.',
                    'icon': 'folder open',
                    'action_label': 'Add Order' if 'home.add_order' in effective_permissions else 'Open Orders',
                    'action_url': reverse('homeApp:add_order') if 'home.add_order' in effective_permissions else orders_list_url,
                },
            ],
        })

    if _has_any_perm(request, ['home.view_attendance', 'home.add_attendance', 'home.change_attendance']):
        metric_groups.append({
            'title': 'Attendance',
            'subtitle': 'Attendance state and current month activity for this user.',
            'tone': 'violet',
            'endpoint': reverse('homeApp:get_attendance_dashboard_report_api'),
            'cards': [
                {
                    'id': 'dashboard-attendance-status',
                    'field': 'attendance_today_status',
                    'label': 'Today Status',
                    'description': 'Latest attendance state for today.',
                    'icon': 'calendar check outline',
                    'action_label': 'Mark Attendance',
                    'action_url': reverse('homeApp:add_attendance'),
                },
                {
                    'id': 'dashboard-attendance-login',
                    'field': 'attendance_login_time',
                    'label': 'Login Time',
                    'description': 'Recorded login time for today.',
                    'icon': 'sign in alternate',
                    'action_label': 'Open Attendance',
                    'action_url': reverse('homeApp:add_attendance'),
                },
                {
                    'id': 'dashboard-attendance-logout',
                    'field': 'attendance_logout_time',
                    'label': 'Logout Time',
                    'description': 'Recorded logout time for today.',
                    'icon': 'sign out alternate',
                    'action_label': 'Open Attendance',
                    'action_url': reverse('homeApp:add_attendance'),
                },
                {
                    'id': 'dashboard-attendance-monthly',
                    'field': 'attendance_monthly_count',
                    'label': 'This Month',
                    'description': 'Attendance records created this month.',
                    'icon': 'calendar alternate outline',
                    'action_label': 'View Report',
                    'action_url': reverse('homeApp:attendance_report'),
                },
            ],
        })

    charts = build_dashboard_chart_cards(request)

    dashboard_state = {
        'hero': hero,
        'metric_groups': metric_groups,
        'action_alerts': build_action_alerts(request),
        'charts': charts,
        'charts_endpoint': reverse('homeApp:get_dashboard_charts_api') if charts else '',
        'action_sections': action_sections,
        'license_message': (
            (getattr(request, 'dashboard_license_message', '') or _build_license_message(tenant))
            if _should_show_license_message(request)
            else ''
        ),
    }
    request._dashboard_state_cache = dashboard_state
    return dashboard_state
