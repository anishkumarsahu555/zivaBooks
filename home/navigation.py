from django.contrib.auth.models import Group, Permission
from django.urls import reverse

from .models import TenantMembership
from .tenancy import get_current_tenant


ADMIN_LIKE_PERMS = {
    'home.can_manage_staff',
    'home.can_manage_staff_groups',
    'home.can_manage_parties',
    'home.can_view_party_credentials',
    'home.can_approve_collection',
    'home.can_view_audit_logs',
}


def _tenant_admin_group_name(tenant):
    tenant_slug = getattr(tenant, 'slug', 'tenant')
    return f'{tenant_slug}:Tenant Admin'


def _tenant_feature_enabled(request_or_user, attr_name, default=True):
    tenant = get_current_tenant(request_or_user)
    if tenant is None:
        return default
    return bool(getattr(tenant, attr_name, default))


def _show_non_gst_control_nav(request_or_user):
    return (
        _tenant_feature_enabled(request_or_user, 'inventoryEnabled', True)
        or _tenant_feature_enabled(request_or_user, 'advancedInvoicingEnabled', True)
    )


def _all_permission_names(user):
    if not getattr(user, 'is_authenticated', False):
        return set()
    cached = getattr(user, '_all_permission_names_cache', None)
    if cached is not None:
        return cached
    permissions = set(user.get_all_permissions())
    user._all_permission_names_cache = permissions
    return permissions


def _tenant_permission_names(request_or_user, tenant):
    request = request_or_user if hasattr(request_or_user, 'META') else None
    if request is not None and hasattr(request, '_tenant_permission_names_cache'):
        return request._tenant_permission_names_cache

    cached = getattr(tenant, '_tenant_permission_names_cache', None)
    if cached is not None:
        if request is not None:
            request._tenant_permission_names_cache = cached
        return cached

    tenant_admin_group = (
        Group.objects
        .filter(name=_tenant_admin_group_name(tenant))
        .prefetch_related('permissions__content_type')
        .first()
    )
    if tenant_admin_group is None:
        permissions = set()
    else:
        permissions = {
            f'{permission.content_type.app_label}.{permission.codename}'
            for permission in tenant_admin_group.permissions.all()
        }
    tenant._tenant_permission_names_cache = permissions
    if request is not None:
        request._tenant_permission_names_cache = permissions
    return permissions


def _tenant_membership_role(request_or_user, tenant):
    user = getattr(request_or_user, 'user', request_or_user)
    if not getattr(user, 'is_authenticated', False) or tenant is None:
        return None
    request = request_or_user if hasattr(request_or_user, 'META') else None
    if request is not None and hasattr(request, '_tenant_membership_role_cache'):
        return request._tenant_membership_role_cache
    cached = getattr(user, '_tenant_membership_role_cache', None)
    if cached is not None:
        return cached
    membership = (
        TenantMembership.objects
        .filter(user=user, tenant=tenant)
        .order_by('-isDefault', 'id')
        .values_list('role', flat=True)
        .first()
    )
    if request is not None:
        request._tenant_membership_role_cache = membership
    user._tenant_membership_role_cache = membership
    return membership


def _effective_permission_names(request_or_user):
    user = getattr(request_or_user, 'user', request_or_user)
    if not getattr(user, 'is_authenticated', False):
        return set()
    if hasattr(request_or_user, '_tenant_effective_perm_cache'):
        return request_or_user._tenant_effective_perm_cache
    cached = getattr(user, '_tenant_effective_perm_cache', None)
    if cached is not None:
        return cached

    user_permissions = _all_permission_names(user)
    tenant = get_current_tenant(request_or_user)
    if tenant is None:
        if hasattr(request_or_user, 'META'):
            request_or_user._tenant_effective_perm_cache = user_permissions
        user._tenant_effective_perm_cache = user_permissions
        return user_permissions

    tenant_permissions = _tenant_permission_names(request_or_user, tenant)
    membership_role = _tenant_membership_role(request_or_user, tenant)
    if not tenant_permissions:
        if hasattr(request_or_user, 'META'):
            request_or_user._tenant_effective_perm_cache = user_permissions
        user._tenant_effective_perm_cache = user_permissions
        return user_permissions
    if membership_role in {TenantMembership.Role.OWNER, TenantMembership.Role.ADMIN}:
        owner_admin_permissions = {
            f'{permission.content_type.app_label}.{permission.codename}'
            for permission in Permission.objects.select_related('content_type').filter(content_type__app_label='home')
        }
        effective_permissions = user_permissions | tenant_permissions | owner_admin_permissions
        if hasattr(request_or_user, 'META'):
            request_or_user._tenant_effective_perm_cache = effective_permissions
        user._tenant_effective_perm_cache = effective_permissions
        return effective_permissions
    effective_permissions = tenant_permissions if user.is_superuser else user_permissions & tenant_permissions
    if hasattr(request_or_user, 'META'):
        request_or_user._tenant_effective_perm_cache = effective_permissions
    user._tenant_effective_perm_cache = effective_permissions
    return effective_permissions


def _has_any_perm(request_or_user, perm_names):
    effective_permissions = _effective_permission_names(request_or_user)
    return any(perm_name in effective_permissions for perm_name in perm_names)


def _nav_item(label, url_name, icon, perm=None):
    return {
        'label': label,
        'url': reverse(url_name),
        'icon': icon,
        'perm': perm,
    }


def _perm_match(request_or_user, perm):
    effective_permissions = _effective_permission_names(request_or_user)
    if perm is None:
        return True
    if isinstance(perm, (list, tuple, set)):
        return any(name in effective_permissions for name in perm)
    return perm in effective_permissions


def _filter_items(request_or_user, items):
    return [item for item in items if _perm_match(request_or_user, item.get('perm'))]


def _admin_sections(user):
    sections = [
        {
            'title': 'General',
            'items': _filter_items(user, [
                _nav_item('Dashboard', 'homeApp:homepage', 'tachometer alternate', (
                    'home.can_manage_staff',
                    'home.can_view_party_credentials',
                )),
            ]),
        },
        {
            'title': 'Staff',
            'items': _filter_items(user, [
                _nav_item('Staff Groups', 'homeApp:staff_group_list', 'users cog', 'home.can_manage_staff_groups'),
                _nav_item('Staff List', 'homeApp:user_list', 'user tie', 'home.can_manage_staff'),
            ]),
        },
        {
            'title': 'Collections',
            'items': _filter_items(user, [
                _nav_item('Take Collection', 'homeApp:add_collection', 'hand holding usd', 'home.add_collection'),
                _nav_item('Collection List', 'homeApp:collection_list', 'book', 'home.view_collection'),
                _nav_item('Cheque Reminder List', 'homeApp:cheque_reminder_list', 'bell', 'home.view_collection'),
            ]),
        },
        {
            'title': 'Sales',
            'items': _filter_items(user, [
                _nav_item('Add Sale', 'homeApp:add_sales', 'shopping cart', 'home.add_sales'),
                _nav_item('Sales List', 'homeApp:sales_list', 'book', 'home.view_sales'),
            ]),
        },
        {
            'title': 'Inventory & Purchases',
            'items': _filter_items(user, [
                _nav_item('Invoice Series', 'homeApp:invoice_series_list', 'hashtag', (
                    'home.view_invoiceseries',
                    'home.add_invoiceseries',
                    'home.change_invoiceseries',
                    'home.delete_invoiceseries',
                )),
                *([_nav_item('Items', 'homeApp:inventory_items_page', 'boxes', (
                    'home.view_inventoryitem',
                    'home.add_inventoryitem',
                    'home.change_inventoryitem',
                    'home.can_manage_inventory',
                    'home.can_view_party_credentials',
                ))] if _tenant_feature_enabled(user, 'inventoryEnabled', True) else []),
                *([_nav_item('Analysis', 'homeApp:inventory_analysis_page', 'chart bar', (
                    'home.can_view_inventory_reports',
                    'home.view_inventoryitem',
                    'home.can_manage_inventory',
                    'home.can_view_party_credentials',
                ))] if _tenant_feature_enabled(user, 'inventoryEnabled', True) else []),
                *([_nav_item('Masters', 'homeApp:inventory_masters_page', 'tags', (
                    'home.view_inventoryitem',
                    'home.add_inventoryitem',
                    'home.change_inventoryitem',
                    'home.can_manage_inventory',
                    'home.can_view_party_credentials',
                ))] if _tenant_feature_enabled(user, 'inventoryEnabled', True) else []),
                *([_nav_item('Stock', 'homeApp:inventory_stock_page', 'warehouse', (
                    'home.view_inventoryitem',
                    'home.add_inventoryitem',
                    'home.change_inventoryitem',
                    'home.can_manage_inventory',
                    'home.can_view_party_credentials',
                ))] if _tenant_feature_enabled(user, 'inventoryEnabled', True) else []),
                *([_nav_item('Adjustments', 'homeApp:inventory_vouchers_page', 'exchange', (
                    'home.view_inventoryitem',
                    'home.add_inventoryitem',
                    'home.change_inventoryitem',
                    'home.can_manage_inventory',
                    'home.can_view_party_credentials',
                ))] if _tenant_feature_enabled(user, 'inventoryEnabled', True) else []),
                *([_nav_item('Purchases', 'homeApp:purchase_list', 'truck', (
                    'home.view_purchaseinvoice',
                    'home.add_purchaseinvoice',
                    'home.change_purchaseinvoice',
                    'home.can_view_party_credentials',
                ))] if _tenant_feature_enabled(user, 'inventoryEnabled', True) else []),
                *([_nav_item('Settings', 'homeApp:non_gst_shadow_page', 'user secret', (
                    'home.can_view_party_credentials',
                    'home.can_manage_staff',
                ))] if _show_non_gst_control_nav(user) else []),
            ]),
        },
        {
            'title': 'Cash Counter',
            'items': _filter_items(user, [
                _nav_item("Today's Cash Counter", 'homeApp:cash_counter', 'calendar check', 'home.can_manage_cash_counter'),
                _nav_item("Today's Collection Counter", 'homeApp:cash_counter_collection', 'calendar check', 'home.can_manage_cash_counter'),
                _nav_item('Cash Counter List', 'homeApp:my_cash_counter_list', 'calendar alternate', 'home.can_manage_cash_counter'),
            ]),
        },
        {
            'title': 'Party',
            'items': _filter_items(user, [
                _nav_item('Party Group List', 'homeApp:party_group_list', 'user friends', 'home.can_manage_parties'),
                _nav_item('Party List', 'homeApp:party_list', 'address book', 'home.can_manage_parties'),
            ]),
        },
        {
            'title': 'Attendance',
            'items': _filter_items(user, [
                _nav_item("Today's Attendance", 'homeApp:add_attendance', 'calendar check', (
                    'home.add_attendance',
                    'home.view_attendance',
                )),
                _nav_item('Attendance List', 'homeApp:attendance_list', 'calendar alternate', (
                    'home.can_view_all_attendance',
                    'home.can_view_attendance_reports',
                )),
            ]),
        },
        {
            'title': 'Banks',
            'items': _filter_items(user, [
                _nav_item('Bank List', 'homeApp:bank_list', 'university', (
                    'home.can_manage_parties',
                    'home.can_view_party_credentials',
                )),
            ]),
        },
        {
            'title': 'Message History',
            'items': _filter_items(user, [
                _nav_item('Message List', 'homeApp:message_list', 'comment dots outline', (
                    'home.view_whatsappmessagestatus',
                    'home.add_whatsappmessagestatus',
                    'home.change_whatsappmessagestatus',
                    'home.delete_whatsappmessagestatus',
                )),
            ]),
        },
        {
            'title': 'Audit',
            'items': _filter_items(user, [
                _nav_item('Audit Report', 'homeApp:audit_log_report', 'history', (
                    'home.can_view_audit_logs',
                    'home.can_export_audit_logs',
                )),
            ]),
        },
        {
            'title': 'Analytics',
            'items': _filter_items(user, [
                _nav_item('Business Snapshot', 'homeApp:analytics_overview', 'chart pie', (
                    'home.can_manage_staff',
                    'home.can_view_party_credentials',
                    'home.can_view_sales_reports',
                    'home.can_view_collection_reports',
                    'home.can_view_cash_counter_reports',
                )),
                _nav_item('Revenue Trend', 'homeApp:analytics_sales', 'rupee sign', (
                    'home.can_manage_sales',
                    'home.can_view_sales_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Collection Trend', 'homeApp:analytics_collections', 'bar chart', (
                    'home.can_approve_collection',
                    'home.can_view_collection_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Receivables', 'homeApp:analytics_receivables', 'balance scale right', (
                    'home.can_manage_parties',
                    'home.can_view_party_credentials',
                    'home.can_view_collection_reports',
                )),
                _nav_item('Staff Productivity', 'homeApp:analytics_staff', 'users', (
                    'home.can_manage_staff',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Cash Position', 'homeApp:analytics_books', 'book', (
                    'home.can_manage_books',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Order Pipeline', 'homeApp:analytics_orders', 'clipboard list', (
                    'home.can_manage_orders',
                    'home.can_view_order_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Exception Dashboard', 'homeApp:analytics_exceptions', 'exclamation triangle', (
                    'home.can_view_party_credentials',
                    'home.can_approve_collection',
                    'home.can_approve_sales',
                    'home.can_approve_orders',
                )),
            ]),
        },
        {
            'title': 'Reports',
            'items': _filter_items(user, [
                _nav_item('Daily Summary Report', 'homeApp:report_daily_summary', 'sun', (
                    'home.can_manage_staff',
                    'home.can_view_party_credentials',
                    'home.can_view_sales_reports',
                    'home.can_view_collection_reports',
                    'home.can_view_cash_counter_reports',
                )),
                _nav_item('Staff Performance Report', 'homeApp:report_staff_activity', 'id badge outline', (
                    'home.can_manage_staff',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Customer Outstanding Report', 'homeApp:report_outstanding', 'balance scale', (
                    'home.can_manage_parties',
                    'home.can_view_party_credentials',
                    'home.can_view_collection_reports',
                )),
                _nav_item('Customer Ledger Report', 'homeApp:report_party_ledger', 'address card outline', (
                    'home.can_manage_parties',
                    'home.can_view_party_credentials',
                    'home.view_party',
                )),
                _nav_item('Collection Report', 'homeApp:report_collections', 'file invoice dollar', (
                    'home.can_approve_collection',
                    'home.can_view_collection_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Sales Report', 'homeApp:report_sales', 'file alternate outline', (
                    'home.can_manage_sales',
                    'home.can_view_sales_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Cash Closing Report', 'homeApp:report_cash_closing', 'receipt', (
                    'home.can_manage_cash_counter',
                    'home.can_view_cash_counter_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Order Status Report', 'homeApp:report_orders', 'file outline', (
                    'home.can_manage_orders',
                    'home.can_view_order_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Attendance Report', 'homeApp:report_attendance', 'calendar', (
                    'home.can_view_all_attendance',
                    'home.can_view_attendance_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Pending Approval Report', 'homeApp:report_pending_approvals', 'clipboard check', (
                    'home.can_approve_collection',
                    'home.can_approve_sales',
                    'home.can_approve_orders',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Bank/Cash Movement Report', 'homeApp:report_bank_cash_movement', 'exchange alternate', (
                    'home.can_manage_books',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Failed Message Report', 'homeApp:report_failed_messages', 'comment alternate outline', (
                    'home.view_whatsappmessagestatus',
                    'home.add_whatsappmessagestatus',
                    'home.change_whatsappmessagestatus',
                    'home.delete_whatsappmessagestatus',
                )),
            ]),
        },
        {
            'title': 'Profile',
            'items': [
                _nav_item('My Profile', 'homeApp:my_profile', 'user'),
                _nav_item('Logout', 'homeApp:logout', 'sign out alternate'),
            ],
        },
    ]
    return [section for section in sections if section['items']]


def _collection_sections(user):
    sections = [
        {
            'title': 'General',
            'items': _filter_items(user, [
                _nav_item('Dashboard', 'homeApp:homepage', 'tachometer alternate', (
                    'home.add_collection',
                    'home.view_collection',
                    'home.can_manage_sales',
                )),
            ]),
        },
        {
            'title': 'Collections',
            'items': _filter_items(user, [
                _nav_item('Take Collection', 'homeApp:add_collection', 'hand holding usd', 'home.add_collection'),
                _nav_item('Collection List', 'homeApp:collection_list', 'book', (
                    'home.view_collection',
                    'home.add_collection',
                    'home.change_collection',
                )),
            ]),
        },
        {
            'title': 'Sales',
            'items': _filter_items(user, [
                _nav_item('Add Sale', 'homeApp:add_sales', 'shopping cart', 'home.add_sales'),
                _nav_item('Sales List', 'homeApp:sales_list', 'book', (
                    'home.view_sales',
                    'home.add_sales',
                    'home.change_sales',
                    'home.can_manage_sales',
                )),
            ]),
        },
        {
            'title': 'Profile',
            'items': [
                _nav_item('My Profile', 'homeApp:my_profile_collection', 'user'),
                _nav_item('Logout', 'homeApp:logout', 'sign out alternate'),
            ],
        },
    ]
    return [section for section in sections if section['items']]


def _staff_sections(user):
    sections = [
        {
            'title': 'General',
            'items': _filter_items(user, [
                _nav_item('Dashboard', 'homeApp:homepage', 'tachometer alternate', (
                    'home.view_attendance',
                    'home.add_attendance',
                    'home.change_attendance',
                )),
            ]),
        },
        {
            'title': 'Sales',
            'items': _filter_items(user, [
                _nav_item('Add Sale', 'homeApp:add_sales', 'shopping cart', 'home.add_sales'),
                _nav_item('Sales List', 'homeApp:sales_list', 'book', (
                    'home.view_sales',
                    'home.add_sales',
                    'home.change_sales',
                    'home.can_manage_sales',
                )),
            ]),
        },
        {
            'title': 'Profile',
            'items': [
                _nav_item('My Profile', 'homeApp:my_profile_collection', 'user'),
                _nav_item('Logout', 'homeApp:logout', 'sign out alternate'),
            ],
        },
    ]
    return [section for section in sections if section['items']]


def _sales_sections(user):
    sections = [
        {
            'title': 'General',
            'items': _filter_items(user, [
                _nav_item('Dashboard', 'homeApp:homepage', 'tachometer alternate', (
                    'home.view_sales',
                    'home.add_sales',
                    'home.change_sales',
                    'home.can_manage_sales',
                )),
            ]),
        },
        {
            'title': 'Collections',
            'items': _filter_items(user, [
                _nav_item('Take Collection', 'homeApp:add_collection', 'hand holding usd', 'home.add_collection'),
                _nav_item('Collection List', 'homeApp:collection_list', 'book', (
                    'home.view_collection',
                    'home.add_collection',
                    'home.change_collection',
                )),
            ]),
        },
        {
            'title': 'Sales',
            'items': _filter_items(user, [
                _nav_item('Add Sale', 'homeApp:add_sales', 'shopping cart', 'home.add_sales'),
                _nav_item('Sales List', 'homeApp:sales_list', 'book', (
                    'home.view_sales',
                    'home.change_sales',
                    'home.can_manage_sales',
                )),
            ]),
        },
        {
            'title': 'Profile',
            'items': [
                _nav_item('My Profile', 'homeApp:my_profile_collection', 'user'),
                _nav_item('Logout', 'homeApp:logout', 'sign out alternate'),
            ],
        },
    ]
    return [section for section in sections if section['items']]


def _cash_counter_sections(user):
    sections = [
        {
            'title': 'General',
            'items': _filter_items(user, [
                _nav_item('Dashboard', 'homeApp:homepage', 'tachometer alternate', (
                    'home.can_manage_cash_counter',
                    'home.view_cashcounter',
                )),
            ]),
        },
        {
            'title': 'Cash Counter',
            'items': _filter_items(user, [
                _nav_item("Today's Cash Counter", 'homeApp:cash_counter', 'calendar check', (
                    'home.can_manage_cash_counter',
                    'home.add_cashcounter',
                    'home.change_cashcounter',
                )),
                _nav_item("Today's Collection Counter", 'homeApp:cash_counter_collection', 'calendar check', (
                    'home.can_manage_cash_counter',
                    'home.add_collectioncashcounter',
                    'home.change_collectioncashcounter',
                )),
                _nav_item('Cash Counter List', 'homeApp:my_cash_counter_list', 'calendar alternate', (
                    'home.can_manage_cash_counter',
                    'home.view_cashcounter',
                    'home.view_collectioncashcounter',
                )),
            ]),
        },
        {
            'title': 'Sales',
            'items': _filter_items(user, [
                _nav_item('Add Sale', 'homeApp:add_sales', 'shopping cart', 'home.add_sales'),
                _nav_item('Sales List', 'homeApp:sales_list', 'book', (
                    'home.view_sales',
                    'home.add_sales',
                    'home.change_sales',
                    'home.can_manage_sales',
                )),
            ]),
        },
        {
            'title': 'Profile',
            'items': [
                _nav_item('My Profile', 'homeApp:my_profile_collection', 'user'),
                _nav_item('Logout', 'homeApp:logout', 'sign out alternate'),
            ],
        },
    ]
    return [section for section in sections if section['items']]


def _customer_sections(user):
    sections = [
        {
            'title': 'General',
            'items': _filter_items(user, [
                _nav_item('Dashboard', 'homeApp:homepage', 'tachometer alternate', 'home.view_party'),
            ]),
        },
        {
            'title': 'Account',
            'items': [
                _nav_item('Change Password', 'homeApp:customer_home', 'key'),
                _nav_item('Logout', 'homeApp:logout', 'sign out alternate'),
            ],
        },
    ]
    return [section for section in sections if section['items']]


def build_sidebar_sections(request_or_user):
    user = getattr(request_or_user, 'user', request_or_user)
    if not getattr(user, 'is_authenticated', False):
        return []
    if hasattr(request_or_user, '_sidebar_sections_cache'):
        return request_or_user._sidebar_sections_cache
    is_admin_like = _has_any_perm(request_or_user, ADMIN_LIKE_PERMS)
    party_admin_perms = ('home.can_manage_parties', 'home.can_view_party_credentials')
    bank_perms = (
        'home.view_bank',
        'home.add_bank',
        'home.change_bank',
        'home.delete_bank',
        'home.can_view_party_credentials',
    )

    sections = [
        {
            'title': 'General',
            'group_break_after': True,
            'items': [
                _nav_item('Dashboard', 'homeApp:homepage', 'tachometer alternate'),
            ],
        },
        {
            'title': 'Staff',
            'items': _filter_items(request_or_user, [
                _nav_item('Staff Groups', 'homeApp:staff_group_list', 'users cog', 'home.can_manage_staff_groups'),
                _nav_item('Staff List', 'homeApp:user_list', 'user tie', 'home.can_manage_staff'),
            ]),
        },
        {
            'title': 'Party',
            'items': _filter_items(request_or_user, [
                _nav_item('Party Group List', 'homeApp:party_group_list', 'user friends', party_admin_perms),
                _nav_item('Party List', 'homeApp:party_list', 'address book', party_admin_perms),
            ]),
        },
        {
            'title': 'Banks',
            'group_break_after': True,
            'items': _filter_items(request_or_user, [
                _nav_item('Bank List', 'homeApp:bank_list', 'university', bank_perms),
            ]),
        },
        {
            'title': 'Collections',
            'items': _filter_items(request_or_user, [
                _nav_item('Take Collection', 'homeApp:add_collection', 'hand holding usd', (
                    'home.add_collection',
                    'home.can_approve_collection',
                )),
                _nav_item('Collection List', 'homeApp:collection_list', 'book', (
                    'home.view_collection',
                    'home.add_collection',
                    'home.change_collection',
                    'home.can_approve_collection',
                )),
                _nav_item('Cheque Reminder List', 'homeApp:cheque_reminder_list', 'bell', (
                    'home.can_approve_collection',
                )),
            ]),
        },
        {
            'title': 'Sales',
            'items': _filter_items(request_or_user, [
                _nav_item('Add Sale', 'homeApp:add_sales', 'shopping cart', 'home.add_sales'),
                _nav_item('Sales List', 'homeApp:sales_list', 'book', (
                    'home.view_sales',
                    'home.add_sales',
                    'home.change_sales',
                    'home.can_manage_sales',
                    'home.can_approve_sales',
                )),
            ]),
        },
        {
            'title': 'Inventory & Purchases',
            'items': _filter_items(request_or_user, [
                _nav_item('Invoice Series', 'homeApp:invoice_series_list', 'hashtag', (
                    'home.view_invoiceseries',
                    'home.add_invoiceseries',
                    'home.change_invoiceseries',
                    'home.delete_invoiceseries',
                )),
                *([_nav_item('Items', 'homeApp:inventory_items_page', 'boxes', (
                    'home.view_inventoryitem',
                    'home.add_inventoryitem',
                    'home.change_inventoryitem',
                    'home.can_manage_inventory',
                    'home.can_view_party_credentials',
                ))] if _tenant_feature_enabled(request_or_user, 'inventoryEnabled', True) else []),
                *([_nav_item('Analysis', 'homeApp:inventory_analysis_page', 'chart bar', (
                    'home.can_view_inventory_reports',
                    'home.view_inventoryitem',
                    'home.can_manage_inventory',
                    'home.can_view_party_credentials',
                ))] if _tenant_feature_enabled(request_or_user, 'inventoryEnabled', True) else []),
                *([_nav_item('Masters', 'homeApp:inventory_masters_page', 'tags', (
                    'home.view_inventoryitem',
                    'home.add_inventoryitem',
                    'home.change_inventoryitem',
                    'home.can_manage_inventory',
                    'home.can_view_party_credentials',
                ))] if _tenant_feature_enabled(request_or_user, 'inventoryEnabled', True) else []),
                *([_nav_item('Stock', 'homeApp:inventory_stock_page', 'warehouse', (
                    'home.view_inventoryitem',
                    'home.add_inventoryitem',
                    'home.change_inventoryitem',
                    'home.can_manage_inventory',
                    'home.can_view_party_credentials',
                ))] if _tenant_feature_enabled(request_or_user, 'inventoryEnabled', True) else []),
                *([_nav_item('Adjustments', 'homeApp:inventory_vouchers_page', 'exchange', (
                    'home.view_inventoryitem',
                    'home.add_inventoryitem',
                    'home.change_inventoryitem',
                    'home.can_manage_inventory',
                    'home.can_view_party_credentials',
                ))] if _tenant_feature_enabled(request_or_user, 'inventoryEnabled', True) else []),
                *([_nav_item('Purchases', 'homeApp:purchase_list', 'truck', (
                    'home.view_purchaseinvoice',
                    'home.add_purchaseinvoice',
                    'home.change_purchaseinvoice',
                    'home.can_view_party_credentials',
                ))] if _tenant_feature_enabled(request_or_user, 'inventoryEnabled', True) else []),
                *([_nav_item('Settings', 'homeApp:non_gst_shadow_page', 'user secret', (
                    'home.can_view_party_credentials',
                    'home.can_manage_staff',
                ))] if _show_non_gst_control_nav(request_or_user) else []),
            ]),
        },
        {
            'title': 'Orders',
            'items': _filter_items(request_or_user, [
                _nav_item('Add Order', 'homeApp:add_order', 'clipboard outline', 'home.add_order'),
                _nav_item('Orders List', 'homeApp:orders_list', 'list alternate outline', (
                    'home.view_order',
                    'home.add_order',
                    'home.change_order',
                    'home.can_manage_orders',
                    'home.can_approve_orders',
                )),
            ]),
        },
        {
            'title': 'Cash Counter',
            'group_break_after': True,
            'items': _filter_items(request_or_user, [
                _nav_item("Today's Cash Counter", 'homeApp:cash_counter', 'calendar check', (
                    'home.can_manage_cash_counter',
                    'home.add_cashcounter',
                    'home.change_cashcounter',
                    'home.view_cashcounter',
                )),
                _nav_item("Today's Collection Counter", 'homeApp:cash_counter_collection', 'calendar check', (
                    'home.can_manage_cash_counter',
                    'home.add_collectioncashcounter',
                    'home.change_collectioncashcounter',
                    'home.view_collectioncashcounter',
                )),
                _nav_item('Cash Counter List', 'homeApp:my_cash_counter_list', 'calendar alternate', (
                    'home.can_manage_cash_counter',
                    'home.view_cashcounter',
                    'home.view_collectioncashcounter',
                )),
            ]),
        },
        {
            'title': 'Books',
            'items': _filter_items(request_or_user, [
                _nav_item('Cashbook', 'homeApp:cashbook', 'book', 'home.can_manage_books'),
                _nav_item('Bankbook', 'homeApp:bankbook', 'university', 'home.can_manage_books'),
            ]),
        },
        {
            'title': 'Attendance',
            'items': _filter_items(request_or_user, [
                _nav_item("Today's Attendance", 'homeApp:add_attendance', 'calendar check', (
                    'home.add_attendance',
                    'home.view_attendance',
                    'home.change_attendance',
                )),
                _nav_item('Attendance List', 'homeApp:attendance_list', 'calendar alternate', (
                    'home.view_attendance',
                    'home.add_attendance',
                    'home.change_attendance',
                    'home.can_view_all_attendance',
                    'home.can_view_attendance_reports',
                )),
            ]),
        },
        {
            'title': 'Message History',
            'items': _filter_items(request_or_user, [
                _nav_item('Message List', 'homeApp:message_list', 'comment dots outline', (
                    'home.view_whatsappmessagestatus',
                    'home.add_whatsappmessagestatus',
                    'home.change_whatsappmessagestatus',
                    'home.delete_whatsappmessagestatus',
                )),
            ]),
        },
        {
            'title': 'Audit',
            'items': _filter_items(request_or_user, [
                _nav_item('Audit Report', 'homeApp:audit_log_report', 'history', (
                    'home.can_view_audit_logs',
                    'home.can_export_audit_logs',
                )),
            ]),
        },
        {
            'title': 'Analytics',
            'items': _filter_items(request_or_user, [
                _nav_item('Business Snapshot', 'homeApp:analytics_overview', 'chart pie', (
                    'home.can_manage_staff',
                    'home.can_view_party_credentials',
                    'home.can_view_sales_reports',
                    'home.can_view_collection_reports',
                    'home.can_view_cash_counter_reports',
                )),
                _nav_item('Revenue Trend', 'homeApp:analytics_sales', 'rupee sign', (
                    'home.can_manage_sales',
                    'home.can_view_sales_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Collection Trend', 'homeApp:analytics_collections', 'bar chart', (
                    'home.can_approve_collection',
                    'home.can_view_collection_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Receivables', 'homeApp:analytics_receivables', 'balance scale right', (
                    'home.can_manage_parties',
                    'home.can_view_party_credentials',
                    'home.can_view_collection_reports',
                )),
                _nav_item('Staff Productivity', 'homeApp:analytics_staff', 'users', (
                    'home.can_manage_staff',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Cash Position', 'homeApp:analytics_books', 'book', (
                    'home.can_manage_books',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Order Pipeline', 'homeApp:analytics_orders', 'clipboard list', (
                    'home.can_manage_orders',
                    'home.can_view_order_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Exceptions', 'homeApp:analytics_exceptions', 'exclamation triangle', (
                    'home.can_view_party_credentials',
                    'home.can_approve_collection',
                    'home.can_approve_sales',
                    'home.can_approve_orders',
                )),
            ]),
        },
        {
            'title': 'Reports',
            'group_break_after': True,
            'items': _filter_items(request_or_user, [
                _nav_item('Daily Summary', 'homeApp:report_daily_summary', 'sun', (
                    'home.can_manage_staff',
                    'home.can_view_party_credentials',
                    'home.can_view_sales_reports',
                    'home.can_view_collection_reports',
                    'home.can_view_cash_counter_reports',
                )),
                _nav_item('Staff Performance', 'homeApp:report_staff_activity', 'id badge outline', (
                    'home.can_manage_staff',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Customer Outstanding', 'homeApp:report_outstanding', 'balance scale', (
                    'home.can_manage_parties',
                    'home.can_view_party_credentials',
                    'home.can_view_collection_reports',
                )),
                _nav_item('Customer Ledger', 'homeApp:report_party_ledger', 'address card outline', (
                    'home.can_manage_parties',
                    'home.can_view_party_credentials',
                    'home.view_party',
                )),
                _nav_item('Collections', 'homeApp:report_collections', 'file invoice dollar', (
                    'home.can_approve_collection',
                    'home.can_view_collection_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Sales', 'homeApp:report_sales', 'file alternate outline', (
                    'home.can_manage_sales',
                    'home.can_view_sales_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Cash Closing', 'homeApp:report_cash_closing', 'receipt', (
                    'home.can_manage_cash_counter',
                    'home.can_view_cash_counter_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Order Status', 'homeApp:report_orders', 'file outline', (
                    'home.can_manage_orders',
                    'home.can_view_order_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Attendance', 'homeApp:report_attendance', 'calendar', (
                    'home.can_view_all_attendance',
                    'home.can_view_attendance_reports',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Pending Approvals', 'homeApp:report_pending_approvals', 'clipboard check', (
                    'home.can_approve_collection',
                    'home.can_approve_sales',
                    'home.can_approve_orders',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Cash/Bank Movement', 'homeApp:report_bank_cash_movement', 'exchange alternate', (
                    'home.can_manage_books',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Failed Messages', 'homeApp:report_failed_messages', 'comment alternate outline', (
                    'home.view_whatsappmessagestatus',
                    'home.add_whatsappmessagestatus',
                    'home.change_whatsappmessagestatus',
                    'home.delete_whatsappmessagestatus',
                )),
            ]),
        },
        {
            'title': 'Profile',
            'items': [
                _nav_item('My Profile', 'homeApp:my_profile_collection', 'user'),
                _nav_item('Logout', 'homeApp:logout', 'sign out alternate'),
            ] if not is_admin_like else [
                _nav_item('My Profile', 'homeApp:my_profile', 'user'),
                _nav_item('Logout', 'homeApp:logout', 'sign out alternate'),
            ],
        },
    ]

    # Drop empty modules (except General/Profile which are always present).
    visible_sections = []
    for section in sections:
        if section['title'] in ('General', 'Profile') or section.get('items'):
            section['collapsed_by_default'] = True
            visible_sections.append(section)
    if hasattr(request_or_user, 'META'):
        request_or_user._sidebar_sections_cache = visible_sections
    else:
        user._sidebar_sections_cache = visible_sections
    return visible_sections
