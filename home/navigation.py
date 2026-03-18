from django.urls import reverse


ADMIN_LIKE_PERMS = {
    'home.can_manage_staff',
    'home.can_manage_staff_groups',
    'home.can_manage_parties',
    'home.can_view_party_credentials',
    'home.can_approve_collection',
    'home.can_view_audit_logs',
}


def _has_any_perm(user, perm_names):
    return any(user.has_perm(perm_name) for perm_name in perm_names)


def _nav_item(label, url_name, icon, perm=None):
    return {
        'label': label,
        'url': reverse(url_name),
        'icon': icon,
        'perm': perm,
    }


def _perm_match(user, perm):
    if perm is None:
        return True
    if isinstance(perm, (list, tuple, set)):
        return any(user.has_perm(name) for name in perm)
    return user.has_perm(perm)


def _filter_items(user, items):
    return [item for item in items if _perm_match(user, item.get('perm'))]


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
                _nav_item('Take Collection', 'homeApp:take_collection', 'hand holding usd', 'home.add_collection'),
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
                _nav_item('Attendance Report', 'homeApp:attendance_report_admin', 'calendar alternate', (
                    'home.can_manage_staff',
                    'home.can_view_party_credentials',
                    'home.can_approve_collection',
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
                    'home.can_manage_sales',
                    'home.can_view_audit_logs',
                    'home.can_approve_collection',
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
                _nav_item('Collection List', 'homeApp:my_collection', 'book', (
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
                _nav_item('Sales List', 'homeApp:my_sales_list', 'book', (
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
                _nav_item('Sales List', 'homeApp:my_sales_list', 'book', (
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
                _nav_item('Collection List', 'homeApp:my_collection', 'book', (
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
                _nav_item('Sales List', 'homeApp:my_sales_list', 'book', (
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


def build_sidebar_sections(user):
    if not getattr(user, 'is_authenticated', False):
        return []
    is_admin_like = _has_any_perm(user, ADMIN_LIKE_PERMS)
    party_admin_perms = ('home.can_manage_parties', 'home.can_view_party_credentials')

    sections = [
        {
            'title': 'General',
            'items': [
                _nav_item('Dashboard', 'homeApp:homepage', 'tachometer alternate'),
            ],
        },
        {
            'title': 'Staff',
            'items': _filter_items(user, [
                _nav_item('Staff Groups', 'homeApp:staff_group_list', 'users cog', 'home.can_manage_staff_groups'),
                _nav_item('Staff List', 'homeApp:user_list', 'user tie', 'home.can_manage_staff'),
            ]),
        },
        {
            'title': 'Party',
            'items': _filter_items(user, [
                _nav_item('Party Group List', 'homeApp:party_group_list', 'user friends', party_admin_perms),
                _nav_item('Party List', 'homeApp:party_list', 'address book', party_admin_perms),
                _nav_item('Bank List', 'homeApp:bank_list', 'university', party_admin_perms),
            ]),
        },
        {
            'title': 'Collections',
            'items': _filter_items(user, [
                _nav_item('Take Collection', 'homeApp:add_collection', 'hand holding usd', 'home.add_collection'),
                _nav_item('My Collections', 'homeApp:my_collection', 'book', (
                    'home.view_collection',
                    'home.add_collection',
                    'home.change_collection',
                )),
                _nav_item('Collection List', 'homeApp:collection_list', 'clipboard list', (
                    'home.can_approve_collection',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Cheque Reminder List', 'homeApp:cheque_reminder_list', 'bell', (
                    'home.can_approve_collection',
                    'home.can_view_party_credentials',
                )),
                _nav_item('Take Collection (Admin)', 'homeApp:take_collection', 'hand holding usd', (
                    'home.can_approve_collection',
                    'home.can_view_party_credentials',
                )),
            ]),
        },
        {
            'title': 'Sales',
            'items': _filter_items(user, [
                _nav_item('Add Sale', 'homeApp:add_sales', 'shopping cart', 'home.add_sales'),
                _nav_item('My Sales List', 'homeApp:my_sales_list', 'book', (
                    'home.view_sales',
                    'home.add_sales',
                    'home.change_sales',
                    'home.can_manage_sales',
                )),
                _nav_item('Sales List', 'homeApp:sales_list', 'clipboard list', (
                    'home.can_manage_sales',
                    'home.can_view_party_credentials',
                )),
            ]),
        },
        {
            'title': 'Orders',
            'items': _filter_items(user, [
                _nav_item('Add Order', 'homeApp:add_order', 'clipboard outline', 'home.add_order'),
                _nav_item('My Orders List', 'homeApp:my_orders_list', 'list alternate outline', (
                    'home.view_order',
                    'home.add_order',
                    'home.change_order',
                    'home.can_manage_orders',
                )),
                _nav_item('Orders List', 'homeApp:orders_list', 'clipboard list', (
                    'home.can_manage_orders',
                    'home.can_view_party_credentials',
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
            'title': 'Attendance',
            'items': _filter_items(user, [
                _nav_item("Today's Attendance", 'homeApp:add_attendance', 'calendar check', (
                    'home.add_attendance',
                    'home.view_attendance',
                    'home.change_attendance',
                )),
                _nav_item('Attendance Report', 'homeApp:attendance_report_admin', 'calendar alternate', (
                    'home.can_manage_staff',
                    'home.can_view_party_credentials',
                    'home.can_approve_collection',
                )),
                _nav_item('Attendance Report (Mine)', 'homeApp:attendance_report', 'calendar alternate outline', (
                    'home.view_attendance',
                    'home.add_attendance',
                    'home.change_attendance',
                )),
            ]),
        },
        {
            'title': 'Message History',
            'items': _filter_items(user, [
                _nav_item('Message List', 'homeApp:message_list', 'comment dots outline', (
                    'home.can_manage_sales',
                    'home.can_view_audit_logs',
                    'home.can_approve_collection',
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
            visible_sections.append(section)
    return visible_sections
