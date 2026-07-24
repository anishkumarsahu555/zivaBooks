TENANT_PERMISSION_BUNDLE_DEFS = [
    {
        'key': 'tenant_staff_groups',
        'label': 'Enable Staff Groups',
        'description': 'Manage staff-group presets and permission assignments.',
        'permissions': [
            'home.view_staffgroup',
            'home.add_staffgroup',
            'home.change_staffgroup',
            'home.delete_staffgroup',
            'home.can_manage_staff_groups',
            'home.can_view_staff_group_permissions',
        ],
    },
    {
        'key': 'tenant_staff_users',
        'label': 'Enable Staff Users',
        'description': 'Manage staff users, passwords, and staff-group assignment.',
        'permissions': [
            'home.view_staffuser',
            'home.add_staffuser',
            'home.change_staffuser',
            'home.delete_staffuser',
            'home.can_manage_staff',
            'home.can_reset_staff_password',
            'home.can_assign_staff_groups',
        ],
    },
    {
        'key': 'tenant_party_groups',
        'label': 'Enable Party Groups',
        'description': 'Manage party groups.',
        'permissions': [
            'home.view_partygroup',
            'home.add_partygroup',
            'home.change_partygroup',
            'home.delete_partygroup',
        ],
    },
    {
        'key': 'tenant_parties',
        'label': 'Enable Parties',
        'description': 'Manage parties and access party credentials.',
        'permissions': [
            'home.view_party',
            'home.add_party',
            'home.change_party',
            'home.delete_party',
            'home.view_partyledgerentry',
            'home.add_partyledgerentry',
            'home.change_partyledgerentry',
            'home.delete_partyledgerentry',
            'home.can_manage_parties',
            'home.can_view_party_credentials',
        ],
    },
    {
        'key': 'tenant_banks',
        'label': 'Enable Banks',
        'description': 'Manage banks independently from parties.',
        'permissions': [
            'home.view_bank',
            'home.add_bank',
            'home.change_bank',
            'home.delete_bank',
        ],
    },
    {
        'key': 'tenant_invoice_series',
        'label': 'Enable Invoice Series',
        'description': 'Manage invoice series ranges, padding, and defaults for sales and cash counter.',
        'permissions': [
            'home.view_invoiceseries',
            'home.add_invoiceseries',
            'home.change_invoiceseries',
            'home.delete_invoiceseries',
        ],
    },
    {
        'key': 'tenant_messages',
        'label': 'Enable Messages',
        'description': 'Access message history records.',
        'permissions': [
            'home.view_whatsappmessagestatus',
            'home.add_whatsappmessagestatus',
            'home.change_whatsappmessagestatus',
            'home.delete_whatsappmessagestatus',
        ],
    },
    {
        'key': 'tenant_ziva_ai',
        'label': 'Enable Ziva AI',
        'description': 'Access the Ziva AI assistant and chat history.',
        'permissions': [
            'home.can_access_ziva_ai',
        ],
    },
    {
        'key': 'tenant_collections',
        'label': 'Enable Collections',
        'description': 'Manage collection workflow, approvals, tally, and reporting.',
        'permissions': [
            'home.view_collection',
            'home.add_collection',
            'home.change_collection',
            'home.delete_collection',
            'home.can_approve_collection',
            'home.can_tally_collection',
            'home.can_view_collection_reports',
        ],
    },
    {
        'key': 'tenant_attendance',
        'label': 'Enable Attendance',
        'description': 'Access attendance register and attendance reports.',
        'permissions': [
            'home.view_attendance',
            'home.add_attendance',
            'home.change_attendance',
            'home.delete_attendance',
            'home.can_view_all_attendance',
            'home.can_view_attendance_reports',
        ],
    },
    {
        'key': 'tenant_orders',
        'label': 'Enable Orders',
        'description': 'Manage orders, approvals, and order reports.',
        'permissions': [
            'home.view_order',
            'home.add_order',
            'home.change_order',
            'home.delete_order',
            'home.view_orderattachment',
            'home.add_orderattachment',
            'home.change_orderattachment',
            'home.delete_orderattachment',
            'home.can_manage_orders',
            'home.can_approve_orders',
            'home.can_view_order_reports',
        ],
    },
    {
        'key': 'tenant_sales',
        'label': 'Enable Sales',
        'description': 'Manage sales, approvals, and sales reports.',
        'permissions': [
            'home.view_sales',
            'home.add_sales',
            'home.change_sales',
            'home.delete_sales',
            'home.can_manage_sales',
            'home.can_approve_sales',
            'home.can_view_sales_reports',
        ],
    },
    {
        'key': 'tenant_cash_counter',
        'label': 'Enable Cash Counter',
        'description': 'Manage cash-counter entries, related counter collections, and reports.',
        'permissions': [
            'home.view_cashcounter',
            'home.add_cashcounter',
            'home.change_cashcounter',
            'home.delete_cashcounter',
            'home.view_collectioncashcounter',
            'home.add_collectioncashcounter',
            'home.change_collectioncashcounter',
            'home.delete_collectioncashcounter',
            'home.can_manage_cash_counter',
            'home.can_view_cash_counter_reports',
        ],
    },
    {
        'key': 'tenant_books',
        'label': 'Enable Books',
        'description': 'Access cashbook, bankbook, and book-account management.',
        'permissions': [
            'home.view_bookaccount',
            'home.add_bookaccount',
            'home.change_bookaccount',
            'home.delete_bookaccount',
            'home.view_booktransaction',
            'home.add_booktransaction',
            'home.change_booktransaction',
            'home.delete_booktransaction',
            'home.can_view_bank_books',
            'home.can_manage_books',
        ],
    },
    {
        'key': 'tenant_inventory',
        'label': 'Enable Inventory & Stock Control',
        'description': 'Manage items, stock masters, adjustment vouchers, purchases, and stock reports.',
        'permissions': [
            'home.view_inventoryitem',
            'home.add_inventoryitem',
            'home.change_inventoryitem',
            'home.delete_inventoryitem',
            'home.can_manage_inventory',
            'home.can_view_inventory_reports',
            'home.view_purchaseinvoice',
            'home.add_purchaseinvoice',
            'home.change_purchaseinvoice',
            'home.delete_purchaseinvoice',
        ],
    },
    {
        'key': 'tenant_proforma',
        'label': 'Enable Proforma Invoices',
        'description': 'Manage proforma invoices, quotes, and estimate conversions.',
        'permissions': [
            'home.view_proformainvoice',
            'home.add_proformainvoice',
            'home.change_proformainvoice',
            'home.delete_proformainvoice',
            'home.can_manage_proforma_invoice',
            'home.can_convert_proforma_invoice',
            'home.can_view_proforma_invoice_reports',
        ],
    },
    {
        'key': 'tenant_gps_tracking',
        'label': 'Enable Field Staff GPS Location Tracking',
        'description': 'Access staff real-time location tracker and interactive route mapping.',
        'permissions': [
            'home.can_track_staff_location',
            'home.can_view_staff_locations',
        ],
    },
]


def tenant_permission_bundle_definitions():
    return TENANT_PERMISSION_BUNDLE_DEFS


def get_bundle_keys_for_license_plan(plan):
    """
    Returns the list of preset bundle keys corresponding to an active LicensePlan's enabled features.
    """
    if not plan:
        return [b['key'] for b in TENANT_PERMISSION_BUNDLE_DEFS]

    keys = [
        'tenant_messages',
        'tenant_collections',
        'tenant_orders',
        'tenant_audit',
    ]

    if getattr(plan, 'geofenced_attendance_enabled', False):
        keys.append('tenant_attendance')

    if getattr(plan, 'role_based_permissions_enabled', True):
        keys.extend(['tenant_staff_groups', 'tenant_staff_users'])

    if getattr(plan, 'party_ledgers_enabled', True):
        keys.extend(['tenant_party_groups', 'tenant_parties'])

    if getattr(plan, 'sales_billing_enabled', True):
        keys.extend(['tenant_sales', 'tenant_invoice_series'])

    if getattr(plan, 'proforma_estimates_enabled', True):
        keys.append('tenant_proforma')

    if getattr(plan, 'inventory_enabled', True):
        keys.append('tenant_inventory')

    if getattr(plan, 'multi_counter_pos_enabled', True):
        keys.append('tenant_cash_counter')

    if getattr(plan, 'cashbook_bankbook_enabled', True):
        keys.extend(['tenant_books', 'tenant_banks'])

    if getattr(plan, 'ziva_ai_enabled', False):
        keys.append('tenant_ziva_ai')

    if getattr(plan, 'gps_tracking_enabled', False):
        keys.append('tenant_gps_tracking')

    return list(dict.fromkeys(keys))


def get_permissions_for_license_plan(plan):
    """
    Returns the set of permission string names (e.g. 'home.can_access_ziva_ai') for a LicensePlan.
    """
    bundle_keys = get_bundle_keys_for_license_plan(plan)
    bundle_map = {b['key']: b['permissions'] for b in TENANT_PERMISSION_BUNDLE_DEFS}
    perm_strings = set()
    for key in bundle_keys:
        perm_strings.update(bundle_map.get(key, []))
    return perm_strings

