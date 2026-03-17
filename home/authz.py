LEGACY_GROUP_PERMISSION_MAP = {
    'Owner': [
        'home.can_manage_staff_groups',
        'home.can_manage_staff',
        'home.can_manage_parties',
        'home.can_manage_sales',
        'home.can_manage_cash_counter',
        'home.can_view_audit_logs',
    ],
    'Admin': [
        'home.can_manage_staff',
        'home.can_view_party_credentials',
        'home.can_manage_parties',
    ],
    'Moderator': [
        'home.can_approve_collection',
        'home.can_manage_sales',
        'home.can_view_audit_logs',
    ],
    'Collection': ['home.add_collection'],
    'SalesAdmin': ['home.can_manage_sales'],
    'CashCounter': ['home.can_manage_cash_counter'],
    'NormalStaff': ['home.view_attendance'],
    'Customer': ['home.view_party'],
    'Viewer': [
        'home.can_view_collection_reports',
        'home.can_view_sales_reports',
        'home.can_view_cash_counter_reports',
    ],
}

LEGACY_GROUP_ALIASES = {
    'Owner': {'Admin', 'Moderator'},
    'Admin': {'Owner'},
    'Moderator': {'Owner'},
}


def legacy_permission_names(group_name):
    names = set(LEGACY_GROUP_PERMISSION_MAP.get(group_name, []))
    for alias in LEGACY_GROUP_ALIASES.get(group_name, set()):
        names.update(LEGACY_GROUP_PERMISSION_MAP.get(alias, []))
    return sorted(names)


def legacy_group_matches(user, group_name):
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.groups.filter(name=group_name).exists():
        return True
    for alias in LEGACY_GROUP_ALIASES.get(group_name, set()):
        if user.groups.filter(name=alias).exists():
            return True
    permission_names = legacy_permission_names(group_name)
    return any(user.has_perm(permission_name) for permission_name in permission_names)


def legacy_group_names(user):
    names = set(user.groups.values_list('name', flat=True)) if getattr(user, 'is_authenticated', False) else set()
    for group_name in LEGACY_GROUP_PERMISSION_MAP:
        if legacy_group_matches(user, group_name):
            names.add(group_name)
    return names
