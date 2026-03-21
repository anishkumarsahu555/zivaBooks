import csv
import os
from datetime import datetime, timedelta
from functools import wraps
from types import SimpleNamespace
from django.contrib.auth import logout, authenticate, login, update_session_auth_hash
from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import Q, Sum
from django.db.models.functions import Length
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
# Create your views here.
from django.views.decorators.csrf import ensure_csrf_cookie
# import xlwt
from activation.models import *
from activation.views import is_activated
from .models import *
from .books import ensure_books_for_tenant
from .navigation import build_sidebar_sections, _has_any_perm as _has_tenant_any_perm
from .tenancy import assign_tenant, get_current_tenant, get_staff_user_for_request, tenant_qs
from .otp import get_collection_otp_payload, get_collection_status_payload, OTP_TTL_SECONDS
# def login_or_logout(request, type):
#     try:
#         data = LoginAndLogoutStatus()
#         data.statusType = type
#         if request.user.username != 'anish':
#             staff = StaffUser.objects.select_related().get(user_ID_id=request.user.pk)
#             data.userID_id = staff.pk
#             data.save()
#     except:
#         pass
def _staff_users_with_permissions(request, *perm_names):
    queryset = tenant_qs(request, StaffUser).filter(isDeleted__exact=False)
    codenames = [perm_name.split('.', 1)[1] for perm_name in perm_names if '.' in perm_name]
    if not codenames:
        return queryset.none()
    permission_filter = (
        Q(
            staffGroup__djangoGroup__permissions__content_type__app_label='home',
            staffGroup__djangoGroup__permissions__codename__in=codenames,
        ) |
        Q(
            user_ID__user_permissions__content_type__app_label='home',
            user_ID__user_permissions__codename__in=codenames,
        )
    )
    return queryset.filter(permission_filter).distinct().order_by('name')
def _has_any_permission(user, perm_names):
    return any(user.has_perm(perm_name) for perm_name in perm_names)
def _tenant_has_perm(request_or_user, perm_name):
    return _has_tenant_any_perm(request_or_user, [perm_name])
def _user_has_portal_access(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    if build_sidebar_sections(user):
        return True
    return user.has_module_perms('home') or user.has_module_perms('activation')
def _is_party_portal_user(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    return TenantMembership.objects.filter(user=user, role=TenantMembership.Role.PARTY).exists()
def _ensure_party_portal_permissions(user):
    """
    Party/customer users need at least `home.view_party` for the customer portal views.
    Some installs may have the `Customer` group without permissions; repair it lazily.
    """
    try:
        perm = Permission.objects.filter(content_type__app_label='home', codename='view_party').first()
        if not perm:
            return
        if not user.has_perm('home.view_party'):
            user.user_permissions.add(perm)
        try:
            group, _ = Group.objects.get_or_create(name='Customer')
            if perm not in group.permissions.all():
                group.permissions.add(perm)
            if not user.groups.filter(id=group.id).exists():
                user.groups.add(group)
        except Exception:
            pass
    except Exception:
        return
def _tenant_admin_group_name(tenant):
    tenant_slug = getattr(tenant, 'slug', 'tenant')
    return f'{tenant_slug}:Tenant Admin'
def _assignable_permissions(request_or_user=None):
    user = getattr(request_or_user, 'user', request_or_user)
    queryset = Permission.objects.select_related('content_type').exclude(
        content_type__app_label__in=['admin', 'auth', 'contenttypes', 'sessions']
    ).order_by('content_type__app_label', 'content_type__model', 'name')
    tenant = get_current_tenant(request_or_user)
    if tenant is not None:
        tenant_admin_group = Group.objects.filter(name=_tenant_admin_group_name(tenant)).first()
        if tenant_admin_group is not None:
            allowed_ids = set(tenant_admin_group.permissions.values_list('id', flat=True))
            return [permission for permission in queryset if permission.id in allowed_ids]
        return []
    if not getattr(user, 'is_authenticated', False):
        return []
    allowed = set(user.get_all_permissions())
    return [
        permission for permission in queryset
        if f'{permission.content_type.app_label}.{permission.codename}' in allowed
    ]
def _permission_catalog(request_or_user=None):
    catalog = {}
    for permission in _assignable_permissions(request_or_user):
        module = permission.content_type.model.replace('_', ' ').title()
        if permission.codename.startswith('can_'):
            module = 'Custom ' + module
        catalog.setdefault(module, []).append(permission)
    return catalog
STAFF_GROUP_PRESET_DEFS = [
    {
        'key': 'sales_full',
        'label': 'Sales Admin',
        'description': 'Full sales administration with approval, delete, and reporting access.',
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
        'key': 'sales_add_list',
        'label': 'Sales Creator',
        'description': 'Can create sales and work only with MyList records.',
        'permissions': [
            'home.view_sales',
            'home.add_sales',
        ],
    },
    {
        'key': 'sales_add_edit_list',
        'label': 'Sales Editor',
        'description': 'Can create, update, and view only MyList sales without delete or approval.',
        'permissions': [
            'home.view_sales',
            'home.add_sales',
            'home.change_sales',
        ],
    },
    {
        'key': 'sales_view_only',
        'label': 'Sales Viewer',
        'description': 'Read-only access to MyList sales.',
        'permissions': [
            'home.view_sales',
        ],
    },
    {
        'key': 'sales_manager',
        'label': 'Sales Manager',
        'description': 'Manage sales workflows and reports without delete or approval.',
        'permissions': [
            'home.view_sales',
            'home.add_sales',
            'home.change_sales',
            'home.can_manage_sales',
            'home.can_view_sales_reports',
        ],
    },
    {
        'key': 'orders_full',
        'label': 'Orders Admin',
        'description': 'Full orders administration with approval, delete, and reporting access.',
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
        'key': 'orders_add_list',
        'label': 'Orders Creator',
        'description': 'Can create orders and work only with MyList records.',
        'permissions': [
            'home.view_order',
            'home.add_order',
            'home.view_orderattachment',
            'home.add_orderattachment',
        ],
    },
    {
        'key': 'orders_add_edit_list',
        'label': 'Orders Editor',
        'description': 'Can create, update, and view only MyList orders before approval.',
        'permissions': [
            'home.view_order',
            'home.add_order',
            'home.change_order',
            'home.view_orderattachment',
            'home.add_orderattachment',
            'home.change_orderattachment',
        ],
    },
    {
        'key': 'orders_view_only',
        'label': 'Orders Viewer',
        'description': 'Read-only access to MyList orders.',
        'permissions': [
            'home.view_order',
            'home.view_orderattachment',
        ],
    },
    {
        'key': 'orders_manager',
        'label': 'Orders Manager',
        'description': 'Manage orders and attachments with reporting access, without delete or approval.',
        'permissions': [
            'home.view_order',
            'home.add_order',
            'home.change_order',
            'home.view_orderattachment',
            'home.add_orderattachment',
            'home.change_orderattachment',
            'home.can_manage_orders',
            'home.can_view_order_reports',
        ],
    },
    {
        'key': 'collection_full',
        'label': 'Collections Admin',
        'description': 'Full collection administration with approval, tally, and reporting access.',
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
        'key': 'collection_add_list',
        'label': 'Collections Collector',
        'description': 'Can create collections and view only MyList collections.',
        'permissions': [
            'home.view_collection',
            'home.add_collection',
        ],
    },
    {
        'key': 'collection_add_edit_list',
        'label': 'Collections Editor',
        'description': 'Can create, update, and view only MyList collections.',
        'permissions': [
            'home.view_collection',
            'home.add_collection',
            'home.change_collection',
        ],
    },
    {
        'key': 'collection_view_only',
        'label': 'Collections Viewer',
        'description': 'Read-only access to MyList collections.',
        'permissions': [
            'home.view_collection',
        ],
    },
    {
        'key': 'collection_manager',
        'label': 'Collections Manager',
        'description': 'Manage collections and reports without delete or approval.',
        'permissions': [
            'home.view_collection',
            'home.add_collection',
            'home.change_collection',
            'home.can_tally_collection',
            'home.can_view_collection_reports',
        ],
    },
    {
        'key': 'cash_counter_full',
        'label': 'Cash Counter Admin',
        'description': 'Full cash-counter administration with counter collection and reporting access.',
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
        'key': 'cash_counter_add_list',
        'label': 'Cash Counter Operator',
        'description': 'Can create and view only MyList cash-counter records.',
        'permissions': [
            'home.view_cashcounter',
            'home.add_cashcounter',
            'home.view_collectioncashcounter',
            'home.add_collectioncashcounter',
        ],
    },
    {
        'key': 'cash_counter_add_edit_list',
        'label': 'Cash Counter Editor',
        'description': 'Can create, update, and view only MyList cash-counter records.',
        'permissions': [
            'home.view_cashcounter',
            'home.add_cashcounter',
            'home.change_cashcounter',
            'home.view_collectioncashcounter',
            'home.add_collectioncashcounter',
            'home.change_collectioncashcounter',
        ],
    },
    {
        'key': 'cash_counter_view_only',
        'label': 'Cash Counter Viewer',
        'description': 'Read-only access to MyList cash-counter records.',
        'permissions': [
            'home.view_cashcounter',
            'home.view_collectioncashcounter',
        ],
    },
    {
        'key': 'cash_counter_manager',
        'label': 'Cash Counter Manager',
        'description': 'Manage cash-counter workflows and reports without delete access.',
        'permissions': [
            'home.view_cashcounter',
            'home.add_cashcounter',
            'home.change_cashcounter',
            'home.view_collectioncashcounter',
            'home.add_collectioncashcounter',
            'home.change_collectioncashcounter',
            'home.can_manage_cash_counter',
            'home.can_view_cash_counter_reports',
        ],
    },
    {
        'key': 'party_full',
        'label': 'Parties Admin',
        'description': 'Full party, party-group, and bank administration with credential access.',
        'permissions': [
            'home.can_manage_parties',
            'home.can_view_party_credentials',
            'home.view_party',
            'home.add_party',
            'home.change_party',
            'home.delete_party',
            'home.view_partygroup',
            'home.add_partygroup',
            'home.change_partygroup',
            'home.delete_partygroup',
            'home.view_bank',
            'home.add_bank',
            'home.change_bank',
            'home.delete_bank',
        ],
    },
    {
        'key': 'party_editor',
        'label': 'Parties Editor',
        'description': 'Create and update party, party-group, and bank records without delete or credential access.',
        'permissions': [
            'home.can_manage_parties',
            'home.view_party',
            'home.add_party',
            'home.change_party',
            'home.view_partygroup',
            'home.add_partygroup',
            'home.change_partygroup',
            'home.view_bank',
            'home.add_bank',
            'home.change_bank',
        ],
    },
    {
        'key': 'party_manage',
        'label': 'Parties Manager',
        'description': 'Manage party, party-group, and bank lists without credential visibility.',
        'permissions': [
            'home.can_manage_parties',
            'home.view_party',
            'home.add_party',
            'home.change_party',
            'home.delete_party',
            'home.view_partygroup',
            'home.add_partygroup',
            'home.change_partygroup',
            'home.delete_partygroup',
            'home.view_bank',
            'home.add_bank',
            'home.change_bank',
            'home.delete_bank',
        ],
    },
    {
        'key': 'party_list_only',
        'label': 'Parties Viewer',
        'description': 'View-only access to party, party-group, and bank lists.',
        'permissions': [
            'home.can_manage_parties',
            'home.view_party',
            'home.view_partygroup',
            'home.view_bank',
        ],
    },
    {
        'key': 'party_ledger_manager',
        'label': 'Party Ledger Manager',
        'description': 'Manage party ledger entries across accessible parties.',
        'permissions': [
            'home.view_partyledgerentry',
            'home.add_partyledgerentry',
            'home.change_partyledgerentry',
            'home.delete_partyledgerentry',
        ],
    },
    {
        'key': 'party_ledger_admin',
        'label': 'Party Ledger Admin',
        'description': 'Full party-ledger control with create, update, and delete access.',
        'permissions': [
            'home.view_partyledgerentry',
            'home.add_partyledgerentry',
            'home.change_partyledgerentry',
            'home.delete_partyledgerentry',
        ],
    },
    {
        'key': 'party_ledger_editor',
        'label': 'Party Ledger Editor',
        'description': 'Create and update party ledger entries without delete access.',
        'permissions': [
            'home.view_partyledgerentry',
            'home.add_partyledgerentry',
            'home.change_partyledgerentry',
        ],
    },
    {
        'key': 'party_ledger_viewer',
        'label': 'Party Ledger Viewer',
        'description': 'Read-only access to party ledger entries.',
        'permissions': [
            'home.view_partyledgerentry',
        ],
    },
    {
        'key': 'party_groups_admin',
        'label': 'Party Groups Admin',
        'description': 'Full party-group administration.',
        'permissions': [
            'home.view_partygroup',
            'home.add_partygroup',
            'home.change_partygroup',
            'home.delete_partygroup',
        ],
    },
    {
        'key': 'party_groups_manager',
        'label': 'Party Groups Manager',
        'description': 'Manage party groups without delete access.',
        'permissions': [
            'home.view_partygroup',
            'home.add_partygroup',
            'home.change_partygroup',
        ],
    },
    {
        'key': 'party_groups_editor',
        'label': 'Party Groups Editor',
        'description': 'Create and update party groups.',
        'permissions': [
            'home.view_partygroup',
            'home.add_partygroup',
            'home.change_partygroup',
        ],
    },
    {
        'key': 'party_groups_viewer',
        'label': 'Party Groups Viewer',
        'description': 'Read-only access to party groups.',
        'permissions': [
            'home.view_partygroup',
        ],
    },
    {
        'key': 'banks_admin',
        'label': 'Banks Admin',
        'description': 'Full bank administration.',
        'permissions': [
            'home.view_bank',
            'home.add_bank',
            'home.change_bank',
            'home.delete_bank',
        ],
    },
    {
        'key': 'banks_manager',
        'label': 'Banks Manager',
        'description': 'Manage banks without delete access.',
        'permissions': [
            'home.view_bank',
            'home.add_bank',
            'home.change_bank',
        ],
    },
    {
        'key': 'banks_editor',
        'label': 'Banks Editor',
        'description': 'Create and update banks.',
        'permissions': [
            'home.view_bank',
            'home.add_bank',
            'home.change_bank',
        ],
    },
    {
        'key': 'banks_viewer',
        'label': 'Banks Viewer',
        'description': 'Read-only access to banks.',
        'permissions': [
            'home.view_bank',
        ],
    },
    {
        'key': 'invoice_series_admin',
        'label': 'Invoice Series Admin',
        'description': 'Full invoice-series administration.',
        'permissions': [
            'home.view_invoiceseries',
            'home.add_invoiceseries',
            'home.change_invoiceseries',
            'home.delete_invoiceseries',
        ],
    },
    {
        'key': 'invoice_series_manager',
        'label': 'Invoice Series Manager',
        'description': 'Manage invoice series without delete access.',
        'permissions': [
            'home.view_invoiceseries',
            'home.add_invoiceseries',
            'home.change_invoiceseries',
        ],
    },
    {
        'key': 'invoice_series_editor',
        'label': 'Invoice Series Editor',
        'description': 'Create and update invoice series.',
        'permissions': [
            'home.view_invoiceseries',
            'home.add_invoiceseries',
            'home.change_invoiceseries',
        ],
    },
    {
        'key': 'invoice_series_viewer',
        'label': 'Invoice Series Viewer',
        'description': 'Read-only access to invoice series.',
        'permissions': [
            'home.view_invoiceseries',
        ],
    },
    {
        'key': 'messages_admin',
        'label': 'Messages Admin',
        'description': 'Full access to message history records.',
        'permissions': [
            'home.view_whatsappmessagestatus',
            'home.add_whatsappmessagestatus',
            'home.change_whatsappmessagestatus',
            'home.delete_whatsappmessagestatus',
        ],
    },
    {
        'key': 'messages_manager',
        'label': 'Messages Manager',
        'description': 'Manage message history without delete access.',
        'permissions': [
            'home.view_whatsappmessagestatus',
            'home.add_whatsappmessagestatus',
            'home.change_whatsappmessagestatus',
        ],
    },
    {
        'key': 'messages_editor',
        'label': 'Messages Editor',
        'description': 'Create and update message history records.',
        'permissions': [
            'home.view_whatsappmessagestatus',
            'home.add_whatsappmessagestatus',
            'home.change_whatsappmessagestatus',
        ],
    },
    {
        'key': 'messages_viewer',
        'label': 'Messages Viewer',
        'description': 'Read-only access to message history.',
        'permissions': [
            'home.view_whatsappmessagestatus',
        ],
    },
    {
        'key': 'attendance_full',
        'label': 'Attendance Admin',
        'description': 'Full attendance administration with all-staff visibility and reports.',
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
        'key': 'attendance_add_edit_list',
        'label': 'Attendance Editor',
        'description': 'Mark and update your own attendance records.',
        'permissions': [
            'home.view_attendance',
            'home.add_attendance',
            'home.change_attendance',
        ],
    },
    {
        'key': 'attendance_add_list',
        'label': 'Attendance Operator',
        'description': 'Mark your attendance and view your own records.',
        'permissions': [
            'home.view_attendance',
            'home.add_attendance',
        ],
    },
    {
        'key': 'attendance_manager',
        'label': 'Attendance Manager',
        'description': 'Manage attendance and reports without delete access.',
        'permissions': [
            'home.view_attendance',
            'home.add_attendance',
            'home.change_attendance',
            'home.can_view_all_attendance',
            'home.can_view_attendance_reports',
        ],
    },
    {
        'key': 'attendance_view_only',
        'label': 'Attendance Viewer',
        'description': 'Read-only access to your own attendance records.',
        'permissions': [
            'home.view_attendance',
        ],
    },
    {
        'key': 'staff_full',
        'label': 'Staff Admin',
        'description': 'Full staff, staff-user, and staff-group administration.',
        'permissions': [
            'home.can_manage_staff_groups',
            'home.can_manage_staff',
            'home.can_reset_staff_password',
            'home.can_assign_staff_groups',
            'home.view_staffgroup',
            'home.add_staffgroup',
            'home.change_staffgroup',
            'home.delete_staffgroup',
            'home.view_staffuser',
            'home.add_staffuser',
            'home.change_staffuser',
            'home.delete_staffuser',
        ],
    },
    {
        'key': 'staff_manager',
        'label': 'Staff Manager',
        'description': 'Manage staff users and groups without delete access.',
        'permissions': [
            'home.can_manage_staff_groups',
            'home.can_manage_staff',
            'home.can_reset_staff_password',
            'home.can_assign_staff_groups',
            'home.view_staffgroup',
            'home.add_staffgroup',
            'home.change_staffgroup',
            'home.view_staffuser',
            'home.add_staffuser',
            'home.change_staffuser',
        ],
    },
    {
        'key': 'staff_editor',
        'label': 'Staff Editor',
        'description': 'Create and update staff users and groups without delete access.',
        'permissions': [
            'home.can_reset_staff_password',
            'home.can_assign_staff_groups',
            'home.view_staffgroup',
            'home.add_staffgroup',
            'home.change_staffgroup',
            'home.view_staffuser',
            'home.add_staffuser',
            'home.change_staffuser',
        ],
    },
    {
        'key': 'staff_viewer',
        'label': 'Staff Viewer',
        'description': 'Read-only access to staff users and groups.',
        'permissions': [
            'home.view_staffgroup',
            'home.view_staffuser',
        ],
    },
    {
        'key': 'staff_groups_full',
        'label': 'Staff Groups Admin',
        'description': 'Create and maintain staff groups and their permissions.',
        'permissions': [
            'home.can_manage_staff_groups',
            'home.view_staffgroup',
            'home.add_staffgroup',
            'home.change_staffgroup',
            'home.delete_staffgroup',
        ],
    },
    {
        'key': 'staff_groups_manager',
        'label': 'Staff Groups Manager',
        'description': 'Manage staff groups without delete access.',
        'permissions': [
            'home.can_manage_staff_groups',
            'home.view_staffgroup',
            'home.add_staffgroup',
            'home.change_staffgroup',
        ],
    },
    {
        'key': 'staff_groups_editor',
        'label': 'Staff Groups Editor',
        'description': 'Create and update staff groups.',
        'permissions': [
            'home.view_staffgroup',
            'home.add_staffgroup',
            'home.change_staffgroup',
        ],
    },
    {
        'key': 'staff_groups_viewer',
        'label': 'Staff Groups Viewer',
        'description': 'Read-only access to staff groups.',
        'permissions': [
            'home.view_staffgroup',
        ],
    },
    {
        'key': 'staff_users_full',
        'label': 'Staff Users Admin',
        'description': 'Manage staff users, passwords, and group assignment.',
        'permissions': [
            'home.can_manage_staff',
            'home.can_reset_staff_password',
            'home.can_assign_staff_groups',
            'home.view_staffuser',
            'home.add_staffuser',
            'home.change_staffuser',
            'home.delete_staffuser',
        ],
    },
    {
        'key': 'staff_users_manager',
        'label': 'Staff Users Manager',
        'description': 'Manage staff users, passwords, and assignments without delete access.',
        'permissions': [
            'home.can_manage_staff',
            'home.can_reset_staff_password',
            'home.can_assign_staff_groups',
            'home.view_staffuser',
            'home.add_staffuser',
            'home.change_staffuser',
        ],
    },
    {
        'key': 'staff_users_editor',
        'label': 'Staff Users Editor',
        'description': 'Create and update staff users.',
        'permissions': [
            'home.view_staffuser',
            'home.add_staffuser',
            'home.change_staffuser',
        ],
    },
    {
        'key': 'staff_users_viewer',
        'label': 'Staff Users Viewer',
        'description': 'Read-only access to staff users.',
        'permissions': [
            'home.view_staffuser',
        ],
    },
    {
        'key': 'books_admin',
        'label': 'Books Admin',
        'description': 'Full book-account and book-transaction administration.',
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
        'key': 'books_manager',
        'label': 'Books Manager',
        'description': 'Manage book accounts, book transactions, and cashbook/bankbook access.',
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
        'key': 'books_editor',
        'label': 'Books Editor',
        'description': 'Create and update book accounts and transactions without delete access.',
        'permissions': [
            'home.view_bookaccount',
            'home.add_bookaccount',
            'home.change_bookaccount',
            'home.view_booktransaction',
            'home.add_booktransaction',
            'home.change_booktransaction',
            'home.can_view_bank_books',
            'home.can_manage_books',
        ],
    },
    {
        'key': 'books_viewer',
        'label': 'Books Viewer',
        'description': 'Read-only access to book accounts, book transactions, and bank-book visibility.',
        'permissions': [
            'home.view_bookaccount',
            'home.view_booktransaction',
            'home.can_view_bank_books',
        ],
    },
    {
        'key': 'audit_admin',
        'label': 'Audit Admin',
        'description': 'Manage audit logs and export tenant audit reports.',
        'permissions': [
            'home.view_auditlog',
            'home.add_auditlog',
            'home.change_auditlog',
            'home.delete_auditlog',
            'home.can_view_audit_logs',
            'home.can_export_audit_logs',
        ],
    },
    {
        'key': 'audit_manager',
        'label': 'Audit Manager',
        'description': 'View audit logs and export tenant audit reports.',
        'permissions': [
            'home.view_auditlog',
            'home.can_view_audit_logs',
            'home.can_export_audit_logs',
        ],
    },
    {
        'key': 'audit_editor',
        'label': 'Audit Editor',
        'description': 'Create and update audit logs without delete access.',
        'permissions': [
            'home.view_auditlog',
            'home.add_auditlog',
            'home.change_auditlog',
            'home.can_view_audit_logs',
        ],
    },
    {
        'key': 'audit_viewer',
        'label': 'Audit Viewer',
        'description': 'Read-only access to tenant audit logs and audit reporting.',
        'permissions': [
            'home.view_auditlog',
            'home.can_view_audit_logs',
        ],
    },
]
def _staff_group_presets(request_or_user=None):
    allowed_permissions = {
        f'{permission.content_type.app_label}.{permission.codename}': permission
        for permission in _assignable_permissions(request_or_user)
    }
    permission_lookup = {
        f'{permission.content_type.app_label}.{permission.codename}': permission
        for permission in Permission.objects.select_related('content_type').all()
    }
    presets = []
    for preset in STAFF_GROUP_PRESET_DEFS:
        if not set(preset['permissions']).issubset(allowed_permissions):
            continue
        ids = []
        names = []
        for perm_name in preset['permissions']:
            permission = permission_lookup.get(perm_name)
            if permission:
                ids.append(permission.id)
                names.append(permission.name)
        presets.append({
            'key': preset['key'],
            'label': preset['label'],
            'description': preset['description'],
            'permission_ids': ids,
            'permission_names': names,
        })
    return presets
def _preset_reference_sections(request_or_user=None):
    sections = {}
    for preset in _staff_group_presets(request_or_user):
        label = preset['label']
        module_name = label.split(' ', 1)[0]
        sections.setdefault(module_name, []).append(preset)
    return sections
def _is_admin_like(user):
    return _has_tenant_any_perm(user, [
        'home.can_manage_staff',
        'home.can_manage_staff_groups',
        'home.can_manage_parties',
        'home.can_view_party_credentials',
        'home.can_approve_collection',
        'home.can_view_audit_logs',
    ])
def _sales_capabilities(user):
    can_add = _tenant_has_perm(user, 'home.add_sales')
    can_edit = _tenant_has_perm(user, 'home.change_sales') or _tenant_has_perm(user, 'home.can_manage_sales') or _tenant_has_perm(user, 'home.can_approve_sales')
    can_delete = _tenant_has_perm(user, 'home.delete_sales')
    can_message = _tenant_has_perm(user, 'home.can_manage_sales') or _tenant_has_perm(user, 'home.can_approve_sales')
    can_export = _tenant_has_perm(user, 'home.can_view_sales_reports') or _tenant_has_perm(user, 'home.can_view_audit_logs')
    can_filter_staff = _is_admin_like(user) or _tenant_has_perm(user, 'home.can_manage_sales')
    return {
        'can_add': can_add,
        'can_edit': can_edit,
        'can_delete': can_delete,
        'can_message': can_message,
        'can_export': can_export,
        'can_filter_staff': can_filter_staff,
    }
def _order_capabilities(user):
    can_add = _tenant_has_perm(user, 'home.add_order')
    can_edit = _tenant_has_perm(user, 'home.change_order') or _tenant_has_perm(user, 'home.can_manage_orders') or _tenant_has_perm(user, 'home.can_approve_orders')
    can_delete = _tenant_has_perm(user, 'home.delete_order')
    can_approve = _tenant_has_perm(user, 'home.can_approve_orders') or _tenant_has_perm(user, 'home.can_manage_orders')
    can_filter_staff = _is_admin_like(user) or _tenant_has_perm(user, 'home.can_manage_orders') or _tenant_has_perm(user, 'home.can_approve_orders')
    can_set_order_date = _tenant_has_perm(user, 'home.can_manage_orders') or _tenant_has_perm(user, 'home.can_approve_orders')
    return {
        'can_add': can_add,
        'can_edit': can_edit,
        'can_delete': can_delete,
        'can_approve': can_approve,
        'can_filter_staff': can_filter_staff,
        'can_set_order_date': can_set_order_date,
    }
def _collection_capabilities(user):
    can_add = _tenant_has_perm(user, 'home.add_collection')
    can_edit = _tenant_has_perm(user, 'home.change_collection') or _tenant_has_perm(user, 'home.can_approve_collection')
    can_delete = _tenant_has_perm(user, 'home.delete_collection')
    can_export = _tenant_has_perm(user, 'home.can_view_collection_reports') or _tenant_has_perm(user, 'home.can_view_audit_logs')
    can_filter_staff = _tenant_has_perm(user, 'home.can_approve_collection')
    return {
        'can_add': can_add,
        'can_edit': can_edit,
        'can_delete': can_delete,
        'can_export': can_export,
        'can_filter_staff': can_filter_staff,
    }
def _cash_counter_capabilities(user):
    can_add = _tenant_has_perm(user, 'home.add_cashcounter') or _tenant_has_perm(user, 'home.add_collectioncashcounter') or _tenant_has_perm(user, 'home.can_manage_cash_counter')
    can_edit = _tenant_has_perm(user, 'home.change_cashcounter') or _tenant_has_perm(user, 'home.change_collectioncashcounter') or _tenant_has_perm(user, 'home.can_manage_cash_counter')
    can_delete = _tenant_has_perm(user, 'home.delete_cashcounter') or _tenant_has_perm(user, 'home.delete_collectioncashcounter')
    can_export = _tenant_has_perm(user, 'home.can_view_cash_counter_reports') or _tenant_has_perm(user, 'home.can_view_audit_logs')
    can_filter_staff = _tenant_has_perm(user, 'home.can_manage_cash_counter')
    return {
        'can_add': can_add,
        'can_edit': can_edit,
        'can_delete': can_delete,
        'can_export': can_export,
        'can_filter_staff': can_filter_staff,
    }
def permission_required_any(*perm_names):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.is_authenticated and _has_tenant_any_perm(request, perm_names):
                return view_func(request, *args, **kwargs)
            if not request.user.is_authenticated:
                return redirect('/')
            message = 'Access denied. This URL is not enabled for the current tenant permissions.'
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'message': 'error', 'error': message}, status=403)
            return render(request, 'home/accessDenied.html', {
                'base_template': 'home/baseIndex.html',
                'access_denied_message': message,
            }, status=403)
        return wrapper
    return decorator
@ensure_csrf_cookie
def loginPage(request):
    # if not request.user.is_authenticated:
    return render(request, 'home/login.html')
    # else:
    #     return redirect('/admin_home/')
@permission_required_any('home.can_view_party_credentials', 'home.can_manage_staff')
def admin_home(request):
    try:
        val = tenant_qs(request, Validity).order_by('-expiryDate', '-id').first()
        if val and val.expiryDate and val.expiryDate < datetime.today().date():
            message = "Activation expired. Please contact ZivaInfotech for activation."
        elif val and val.expiryDate:
            message = "Your App License is Valid till {}".format(val.expiryDate.strftime('%d-%m-%Y'))
        else:
            message = "You are using a trial version."
    except Exception:
        message = "You are using a trial version."
    request.dashboard_license_message = message
    context = {
        'message': message,
    }
    return render(request, 'home/admin/index.html', context)
@permission_required_any('home.add_collection', 'home.can_manage_sales')
def collection_home(request):
    return render(request, 'home/collection/indexCollection.html')
@permission_required_any('home.can_manage_staff')
@is_activated()
def user_list(request):
    groups = tenant_qs(request, StaffGroup).filter(isDeleted__exact=False).order_by('name')
    party_groups = tenant_qs(request, PartyGroup).filter(isDeleted__exact=False).order_by('name')
    context = {
        'groups': groups,
        'party_groups': party_groups
    }
    return render(request, 'home/admin/userList.html', context)
@permission_required_any('home.can_manage_staff_groups')
@is_activated()
def staff_group_list(request):
    context = {
        'permission_catalog': _permission_catalog(request),
    }
    return render(request, 'home/admin/staffGroupList.html', context)
@permission_required_any('home.can_manage_staff_groups')
@is_activated()
def permission_reference(request):
    context = {
        'preset_sections': _preset_reference_sections(request),
    }
    return render(request, 'home/admin/permissionReference.html', context)
@permission_required_any('home.can_manage_staff_groups')
@is_activated()
def staff_group_create(request):
    context = {
        'page_mode': 'create',
        'page_title': 'Create Staff Group',
        'submit_label': 'Create Group',
        'group_obj': None,
        'permission_catalog': _permission_catalog(request),
        'permission_presets': _staff_group_presets(request),
        'selected_permission_ids': [],
        'selected_preset_keys': [],
    }
    return render(request, 'home/admin/staffGroupForm.html', context)
@permission_required_any('home.can_manage_staff_groups')
@is_activated()
def staff_group_edit(request, id):
    group = get_object_or_404(
        tenant_qs(request, StaffGroup).select_related('djangoGroup').filter(isDeleted=False),
        pk=id,
    )
    # Ensure hidden/internal model permissions never show in edit mode (and counts stay accurate).
    visible_permission_ids = {permission.id for perms in _permission_catalog(request).values() for permission in perms}
    selected_permission_ids = list(group.djangoGroup.permissions.values_list('id', flat=True)) if group.djangoGroup_id else []
    selected_permission_ids = [perm_id for perm_id in selected_permission_ids if perm_id in visible_permission_ids]
    selected_set = set(selected_permission_ids)
    presets = _staff_group_presets(request)
    selected_preset_keys = [
        preset['key'] for preset in presets
        if set(preset.get('permission_ids') or []).issubset(selected_set) and preset.get('permission_ids')
    ]
    context = {
        'page_mode': 'edit',
        'page_title': 'Edit Staff Group',
        'submit_label': 'Update Group',
        'group_obj': group,
        'permission_catalog': _permission_catalog(request),
        'permission_presets': presets,
        'selected_permission_ids': selected_permission_ids,
        'selected_preset_keys': selected_preset_keys,
    }
    return render(request, 'home/admin/staffGroupForm.html', context)
@permission_required_any('home.can_manage_staff_groups')
@is_activated()
def staff_group_detail(request, id):
    group = get_object_or_404(
        tenant_qs(request, StaffGroup).select_related('djangoGroup').filter(isDeleted=False),
        pk=id,
    )
    assigned_permissions = list(
        group.djangoGroup.permissions.select_related('content_type').order_by('content_type__model', 'name')
    ) if group.djangoGroup_id else []
    visible_permission_ids = {
        permission.id for permission in _assignable_permissions(request)
    }
    assigned_permissions = [permission for permission in assigned_permissions if permission.id in visible_permission_ids]
    permission_modules = {}
    for permission in assigned_permissions:
        module = permission.content_type.model.replace('_', ' ').title()
        if permission.codename.startswith('can_'):
            module = 'Custom ' + module
        permission_modules.setdefault(module, []).append(permission)
    staff_members = list(
        tenant_qs(request, StaffUser)
        .filter(staffGroup=group, isDeleted=False)
        .order_by('name')
    )
    context = {
        'group': group,
        'permission_modules': permission_modules,
        'staff_members': staff_members,
        'member_count': len(staff_members),
        'permission_count': len(assigned_permissions),
    }
    return render(request, 'home/admin/staffGroupDetail.html', context)
@permission_required_any('home.can_manage_parties', 'home.can_view_party_credentials')
@is_activated()
def party_group_list(request):
    can_view_credentials = request.user.has_perm('home.can_view_party_credentials')
    context = {
        'party_group_capabilities': {
            'can_add': request.user.has_perm('home.add_partygroup') or can_view_credentials,
            'can_edit': request.user.has_perm('home.change_partygroup') or can_view_credentials,
            'can_delete': request.user.has_perm('home.delete_partygroup') or can_view_credentials,
        },
    }
    return render(request, 'home/admin/partyGroupList.html', context)
@permission_required_any('home.view_bank', 'home.add_bank', 'home.change_bank', 'home.delete_bank', 'home.can_view_party_credentials')
@is_activated()
def bank_list(request):
    can_view_credentials = request.user.has_perm('home.can_view_party_credentials')
    context = {
        'bank_capabilities': {
            'can_add': request.user.has_perm('home.add_bank') or can_view_credentials,
            'can_edit': request.user.has_perm('home.change_bank') or can_view_credentials,
            'can_delete': request.user.has_perm('home.delete_bank') or can_view_credentials,
        },
        'can_view_bank_balances': request.user.has_perm('home.view_bank') or can_view_credentials,
    }
    return render(request, 'home/admin/BankList.html', context)
@permission_required_any('home.view_invoiceseries', 'home.add_invoiceseries', 'home.change_invoiceseries', 'home.delete_invoiceseries')
@is_activated()
def invoice_series_list(request):
    context = {
        'invoice_series_capabilities': {
            'can_add': request.user.has_perm('home.add_invoiceseries'),
            'can_edit': request.user.has_perm('home.change_invoiceseries'),
            'can_delete': request.user.has_perm('home.delete_invoiceseries'),
        },
    }
    return render(request, 'home/admin/invoiceSeriesList.html', context)
@permission_required_any('home.can_manage_parties', 'home.can_view_party_credentials')
@is_activated()
def party_list(request):
    party_groups = tenant_qs(request, PartyGroup).filter(isDeleted__exact=False).order_by('name')
    staffs = tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name')
    can_view_credentials = request.user.has_perm('home.can_view_party_credentials')
    can_add = request.user.has_perm('home.add_party') or can_view_credentials
    can_edit = request.user.has_perm('home.change_party') or can_view_credentials
    can_delete = request.user.has_perm('home.delete_party') or can_view_credentials
    context = {
        'party_groups': party_groups,
        'staffs': staffs,
        'party_capabilities': {
            'can_view_credentials': can_view_credentials,
            'can_add': can_add,
            'can_edit': can_edit,
            'can_delete': can_delete,
        },
    }
    return render(request, 'home/admin/PartyList.html', context)
def user_logout(request):
    # login_or_logout(request, 'Logout')
    logout(request)
    return redirect("homeApp:loginPage")
def _resolve_profile_instance(request):
    instance = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk).first()
    if instance is not None:
        return instance
    fallback_profile = getattr(request.user, 'staff_profile', None)
    if fallback_profile is not None:
        return fallback_profile
    return SimpleNamespace(
        photo=None,
        idProof=None,
        name=request.user.get_full_name() or request.user.username,
        user_ID=request.user,
        username=request.user.username,
        group=None,
        staffGroup=None,
        partyGroupID=None,
        isActive='Active' if request.user.is_active else 'Inactive',
        phone=None,
        email=getattr(request.user, 'email', ''),
        address=None,
        datetime=getattr(request.user, 'date_joined', None),
        lastUpdatedOn=getattr(request.user, 'last_login', None) or getattr(request.user, 'date_joined', None),
    )
def _profile_role_context(request, instance):
    tenant = get_current_tenant(request)
    membership = None
    if tenant is not None and request.user.is_authenticated:
        membership = TenantMembership.objects.filter(
            tenant=tenant,
            user=request.user,
        ).order_by('-isDefault', 'id').first()

    role_label = None
    identity_label = 'Staff Identity'
    if request.user.is_superuser:
        role_label = 'Project Owner'
        identity_label = 'Project Identity'
    elif membership and membership.role == TenantMembership.Role.OWNER:
        role_label = 'Tenant Owner'
        identity_label = 'Tenant Identity'
    elif membership and membership.role == TenantMembership.Role.ADMIN:
        role_label = 'Tenant Admin'
        identity_label = 'Tenant Identity'
    elif getattr(instance, 'staffGroup', None) and getattr(instance.staffGroup, 'name', None):
        role_label = instance.staffGroup.name
    elif getattr(instance, 'group', None):
        role_label = instance.group
    else:
        role_label = 'Workspace User'

    return {
        'profile_role_label': role_label,
        'profile_identity_label': identity_label,
    }
def my_profile(request):
    instance = _resolve_profile_instance(request)
    context = {
        'instance': instance,
        'base_template': 'home/admin/index.html',
    }
    context.update(_profile_role_context(request, instance))
    return render(request, 'home/profileCommon.html', context)
def my_profile_collection(request):
    instance = _resolve_profile_instance(request)
    context = {
        'instance': instance,
        'base_template': 'home/collection/indexCollection.html',
    }
    context.update(_profile_role_context(request, instance))
    return render(request, 'home/profileCommon.html', context)
def postLogin(request):
    if request.method == 'POST':
        username = request.POST.get('userName')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            try:
                tenant = get_current_tenant(request)
                qs = Validity.objects.all()
                if tenant is not None:
                    val = qs.filter(tenant=tenant).order_by('-expiryDate', '-id').first()
                    if val is None:
                        val = qs.filter(tenant__isnull=True).order_by('-expiryDate', '-id').first()
                else:
                    val = qs.filter(tenant__isnull=True).order_by('-expiryDate', '-id').first()
                if val and val.expiryDate and val.expiryDate < datetime.today().date():
                    return JsonResponse({'message': 'success', 'data': '/activate/', 'expired': True}, safe=False)
            except Exception:
                pass
            if _is_party_portal_user(request.user):
                _ensure_party_portal_permissions(request.user)
                return JsonResponse({'message': 'success', 'data': '/customer_home/'}, safe=False)
            if _user_has_portal_access(request.user):
                return JsonResponse({'message': 'success', 'data': '/home/'}, safe=False)
            logout(request)
        return JsonResponse({'message': 'fail'}, safe=False)
    return JsonResponse({'message': 'fail'}, safe=False)
#
@ensure_csrf_cookie
def homepage(request):
    if request.user.is_authenticated:
        if _is_party_portal_user(request.user):
            _ensure_party_portal_permissions(request.user)
            return redirect('homeApp:customer_home')
        if _user_has_portal_access(request.user):
            if request.user.has_perm('home.can_view_party_credentials') or request.user.has_perm('home.can_manage_staff'):
                try:
                    val = tenant_qs(request, Validity).order_by('-expiryDate', '-id').first()
                    if val and val.expiryDate and val.expiryDate < datetime.today().date():
                        request.dashboard_license_message = "Activation expired. Please contact ZivaInfotech for activation."
                    elif val and val.expiryDate:
                        request.dashboard_license_message = "Your App License is Valid till {}".format(
                            val.expiryDate.strftime('%d-%m-%Y')
                        )
                    else:
                        request.dashboard_license_message = "You are using a trial version."
                except Exception:
                    request.dashboard_license_message = "You are using a trial version."
            return render(request, 'home/baseIndex.html')
        logout(request)
    return render(request, 'home/login.html')
def _parse_customer_date(value, default=None):
    if not value:
        return default
    try:
        return datetime.strptime(value, '%d/%m/%Y').date()
    except Exception:
        pass
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except Exception:
        return default
def _customer_visible_party_ledger_qs(request, qs):
    """
    Customer portal should show only:
    - Approved Sales
    - Approved Payments (Collection / Counter Collection)
    """
    approved_collection_ids = tenant_qs(request, Collection).filter(
        isDeleted=False,
        isApproved=True,
    ).values('id')
    approved_counter_collection_ids = tenant_qs(request, CollectionCashCounter).filter(
        isDeleted=False,
        isApproved=True,
    ).values('id')
    return qs.filter(
        Q(source=PartyLedgerEntry.Source.SALES) |
        (Q(source=PartyLedgerEntry.Source.COLLECTION) & Q(source_object_id__in=approved_collection_ids)) |
        (Q(source=PartyLedgerEntry.Source.COUNTER_COLLECTION) & Q(source_object_id__in=approved_counter_collection_ids))
    )
@ensure_csrf_cookie
@permission_required_any('home.view_party')
def customer_home(request):
    party = tenant_qs(request, Party).filter(userID_id=request.user.pk, isDeleted=False).only(
        'id', 'name', 'phone', 'address', 'username', 'password', 'openingBalance'
    ).order_by('-id').first()
    if not party:
        return redirect('/')
    today = datetime.today().date()
    # Default: current month range (better than "today" for customers).
    start_default = today.replace(day=1)
    start_date = _parse_customer_date(request.GET.get('startDate'), start_default)
    end_date = _parse_customer_date(request.GET.get('endDate'), today)
    if end_date and start_date and end_date < start_date:
        start_date, end_date = end_date, start_date
    base_qs = tenant_qs(request, PartyLedgerEntry).filter(isDeleted=False, party=party)
    base_qs = _customer_visible_party_ledger_qs(request, base_qs)
    prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
        debit_total=Sum('debit'),
        credit_total=Sum('credit'),
    )
    opening = float(party.openingBalance or 0.0) + float(prior['debit_total'] or 0.0) - float(prior['credit_total'] or 0.0)
    range_qs = base_qs.filter(txDateTime__date__range=[start_date, end_date]).order_by('txDateTime', 'id')
    range_totals = range_qs.aggregate(debit_total=Sum('debit'), credit_total=Sum('credit'))
    range_debit = float(range_totals['debit_total'] or 0.0)
    range_credit = float(range_totals['credit_total'] or 0.0)
    closing = opening + range_debit - range_credit
    context = {
        'party': party,
        'startDate': start_date.strftime('%d/%m/%Y'),
        'endDate': end_date.strftime('%d/%m/%Y'),
        'opening': opening,
        'range_debit': range_debit,
        'range_credit': range_credit,
        'closing': closing,
    }
    try:
        request.session['cust_party_id'] = int(party.pk)
        request.session['cust_tenant_id'] = int(party.tenant_id) if party.tenant_id is not None else None
    except Exception:
        pass
    return render(request, 'home/customer.html', context)
@permission_required_any('home.view_party')
def customer_ledger_kpis(request):
    if request.method != 'GET':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    party = tenant_qs(request, Party).filter(userID_id=request.user.pk, isDeleted=False).only(
        'id', 'openingBalance'
    ).order_by('-id').first()
    if not party:
        return JsonResponse({'message': 'error', 'error': 'Party not found.'}, status=404)
    today = datetime.today().date()
    start_default = today.replace(day=1)
    start_date = _parse_customer_date(request.GET.get('startDate'), start_default)
    end_date = _parse_customer_date(request.GET.get('endDate'), today)
    if end_date and start_date and end_date < start_date:
        start_date, end_date = end_date, start_date
    base_qs = tenant_qs(request, PartyLedgerEntry).filter(isDeleted=False, party=party)
    base_qs = _customer_visible_party_ledger_qs(request, base_qs)
    prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
        debit_total=Sum('debit'),
        credit_total=Sum('credit'),
    )
    opening = float(party.openingBalance or 0.0) + float(prior['debit_total'] or 0.0) - float(prior['credit_total'] or 0.0)
    range_totals = base_qs.filter(txDateTime__date__range=[start_date, end_date]).aggregate(
        debit_total=Sum('debit'),
        credit_total=Sum('credit'),
    )
    range_debit = float(range_totals['debit_total'] or 0.0)
    range_credit = float(range_totals['credit_total'] or 0.0)
    closing = opening + range_debit - range_credit
    return JsonResponse({
        'message': 'success',
        'opening': opening,
        'debit': range_debit,
        'credit': range_credit,
        'closing': closing,
        'startDate': start_date.strftime('%d/%m/%Y') if start_date else '',
        'endDate': end_date.strftime('%d/%m/%Y') if end_date else '',
    })
@permission_required_any('home.view_party')
def customer_change_password(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    current_password = request.POST.get('current_password')
    new_password = request.POST.get('new_password')
    confirm_password = request.POST.get('confirm_password')
    if not current_password or not new_password or not confirm_password:
        return JsonResponse({'message': 'error', 'error': 'All password fields are required.'}, status=400)
    if not request.user.check_password(current_password):
        return JsonResponse({'message': 'error', 'error': 'Current password is incorrect.'}, status=400)
    if len(new_password) != 6 or not new_password.isdigit():
        return JsonResponse({'message': 'error', 'error': 'New password must be exactly 6 digits.'}, status=400)
    if new_password != confirm_password:
        return JsonResponse({'message': 'error', 'error': 'New password and confirm password do not match.'},
                            status=400)
    request.user.set_password(new_password)
    request.user.save(update_fields=['password'])
    update_session_auth_hash(request, request.user)
    party = tenant_qs(request, Party).filter(userID_id=request.user.pk, isDeleted=False).last()
    if party:
        party.password = new_password
        party.save(update_fields=['password'])
    return JsonResponse({'message': 'success', 'success': 'Password changed successfully.'}, status=200)
@permission_required_any('home.view_party')
def customer_active_collection_otp(request):
    if request.method != 'GET':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    # Only customer/party users should see OTP.
    party_id = request.session.get('cust_party_id')
    tenant_id = request.session.get('cust_tenant_id')
    if party_id:
        try:
            party_id = int(party_id)
        except Exception:
            party_id = None
    if tenant_id is not None:
        try:
            tenant_id = int(tenant_id)
        except Exception:
            tenant_id = None
    if not party_id:
        party = tenant_qs(request, Party).filter(userID_id=request.user.pk, isDeleted=False).only('id', 'tenant_id').order_by('-id').first()
        if not party:
            return JsonResponse({'message': 'error', 'error': 'Permission denied.'}, status=403)
        party_id = int(party.pk)
        tenant_id = int(party.tenant_id) if party.tenant_id is not None else None
        try:
            request.session['cust_party_id'] = party_id
            request.session['cust_tenant_id'] = tenant_id
        except Exception:
            pass
    payload = get_collection_otp_payload(tenant_id, party_id)
    if not payload:
        status_payload = get_collection_status_payload(tenant_id, party_id)
        if not status_payload:
            return JsonResponse({'message': 'success', 'active': False}, safe=False)
        status = str(status_payload.get('status') or '').strip()
        created_ts = status_payload.get('created_ts')
        ttl = int(status_payload.get('ttl') or 0)
        meta = status_payload.get('meta') if isinstance(status_payload.get('meta'), dict) else {}
        try:
            created_ts = int(created_ts)
        except Exception:
            created_ts = None
        if not status or created_ts is None or ttl <= 0:
            return JsonResponse({'message': 'success', 'active': False}, safe=False)
        now_ts = int(timezone.now().timestamp())
        expires_in = max(0, (created_ts + ttl) - now_ts)
        if expires_in <= 0:
            return JsonResponse({'message': 'success', 'active': False}, safe=False)
        return JsonResponse({
            'message': 'success',
            'active': False,
            'status': status,
            'status_expires_in': expires_in,
            'paymentMode': meta.get('paymentMode', ''),
            'amountPaid': meta.get('amountPaid', ''),
            'collectDate': meta.get('collectDate', ''),
            'paymentID': meta.get('paymentID', ''),
        }, safe=False)
    otp = str(payload.get('otp') or '').strip()
    created_ts = payload.get('created_ts')
    ttl = int(payload.get('ttl') or OTP_TTL_SECONDS)
    meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
    try:
        created_ts = int(created_ts)
    except Exception:
        created_ts = None
    if not otp or created_ts is None:
        return JsonResponse({'message': 'success', 'active': False}, safe=False)
    now_ts = int(timezone.now().timestamp())
    expires_in = max(0, (created_ts + ttl) - now_ts)
    if expires_in <= 0:
        return JsonResponse({'message': 'success', 'active': False}, safe=False)
    return JsonResponse({
        'message': 'success',
        'active': True,
        'otp': otp,
        'expires_in': expires_in,
        'paymentMode': meta.get('paymentMode', ''),
        'amountPaid': meta.get('amountPaid', ''),
        'collectDate': meta.get('collectDate', ''),
    }, safe=False)
@permission_required_any('home.add_collection')
def add_collection(request):
    instance = tenant_qs(request, Bank).filter(isDeleted__exact=False).order_by('name')
    staffs = _staff_users_with_permissions(
        request,
        'home.add_collection',
        'home.change_collection',
        'home.view_collection',
    )
    p_groups = tenant_qs(request, PartyGroup).filter(isDeleted__exact=False).order_by('name')
    context = {
        'instance': instance,
        'staffs': staffs,
        'p_groups': p_groups,
    }
    return render(request, 'home/collection/addCollection.html', context)
@permission_required_any('home.view_collection', 'home.add_collection', 'home.change_collection', 'home.can_approve_collection')
def my_collection(request):
    context = {
        'collection_capabilities': _collection_capabilities(request.user),
    }
    return render(request, 'home/collection/collectionListByStaff.html', context)
# admin
@is_activated()
@permission_required_any('home.can_approve_collection')
def collection_list(request):
    staffs = tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name')
    context = {
        'staffs': staffs
    }
    return render(request, 'home/admin/collectionListByAdmin.html', context)
@is_activated()
@permission_required_any('home.can_approve_collection')
def cheque_reminder_list(request):
    staffs = tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name')
    context = {
        'staffs': staffs
    }
    return render(request, 'home/admin/collectionListChequeReminder.html', context)
@permission_required_any('home.can_approve_collection')
def take_collection(request):
    instance = tenant_qs(request, Bank).filter(isDeleted__exact=False).order_by('name')
    context = {
        'instance': instance
    }
    return render(request, 'home/admin/takeCollection.html', context)
@permission_required_any('home.change_collection', 'home.can_approve_collection')
def edit_collection(request, id=None):
    obj = get_object_or_404(tenant_qs(request, Collection), pk=id)
    instance = tenant_qs(request, Bank).filter(isDeleted__exact=False).order_by('name')
    context = {
        'instance': instance,
        'obj': obj
    }
    return render(request, 'home/admin/editCollection.html', context)
def get_party_data(request):
    module_dir = os.path.dirname(__file__)  # get current directory
    file_path = os.path.join(module_dir, 'PartiesCSV.csv')
    with open(file_path, mode='r') as file:
        # reading the CSV file
        csvFile = csv.reader(file)
        # displaying the contents of the CSV file
        for lines in csvFile:
            try:
                partyGroup = tenant_qs(request, PartyGroup).get(name__iexact=lines[2])
                party = partyGroup.pk
            except:
                party = None
            try:
                user = tenant_qs(request, StaffUser).get(name__iexact=lines[3])
                usr = user.pk
            except:
                usr = None
            p = Party()
            assign_tenant(p, request)
            p.name = lines[0]
            p.phone = lines[1]
            p.partyGroupID_id = party
            p.assignTo_id = usr
            p.save()
    return HttpResponse('Ok')
def generate_collection_date(request):
    col = tenant_qs(request, Collection).all()
    for c in col:
        c.collectionDateTime = c.datetime
        c.save()
    return HttpResponse('Ok')
# ----------staff-------------------
@permission_required_any('home.view_attendance', 'home.add_sales', 'home.view_sales', 'home.can_manage_sales')
def staff_home(request):
    obj = get_object_or_404(tenant_qs(request, StaffUser), user_ID__pk=request.user.pk)
    context = {
        'obj': obj
    }
    return render(request, 'home/NormalStaff/indexStaff.html', context)
# ---------------------------attendance-------------------
@permission_required_any('home.add_attendance', 'home.view_attendance', 'home.change_attendance')
def add_attendance(request):
    if request.user.is_authenticated:
        try:
            instance = tenant_qs(request, Attendance).get(
                datetime__icontains=datetime.today().date(),
                staffID__user_ID__id=request.user.pk,
            )
        except:
            instance = Attendance()
            assign_tenant(instance, request)
            staff = get_object_or_404(tenant_qs(request, StaffUser), user_ID__id=request.user.pk)
            instance.staffID_id = staff.pk
            instance.save()
        context = {
            'obj': instance,
            'date': datetime.today().date()
        }
        return render(request, 'home/addTodayAttendance.html', context)
    else:
        return redirect('/')
@permission_required_any('home.view_attendance', 'home.add_attendance', 'home.change_attendance')
def attendance_report(request):
    if request.user.is_authenticated:
        return render(request, 'home/attendanceReportStaff.html')
    else:
        return redirect('/')
@permission_required_any('home.can_view_all_attendance', 'home.can_view_attendance_reports')
def attendance_report_admin(request):
    if request.user.is_authenticated:
        staffs = tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name')
        context = {
            'staffs': staffs
        }
        return render(request, 'home/attendanceReportAdmin.html', context)
    else:
        return redirect('/')
# ----------------------------- Sales ----------------------
@permission_required_any('home.add_sales')
def add_sales(request):
    return render(request, 'home/sales/addSales.html')
@is_activated()
@permission_required_any('home.can_manage_sales', 'home.view_sales', 'home.change_sales')
def sales_list(request):
    capabilities = _sales_capabilities(request.user)
    staffs = tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name') if capabilities['can_filter_staff'] else []
    context = {
        'staffs': staffs,
        'sales_capabilities': capabilities,
    }
    return render(request, 'home/sales/salesListByAdmin.html', context)
@permission_required_any('home.view_sales', 'home.add_sales', 'home.change_sales', 'home.can_manage_sales')
def my_sales_list(request):
    capabilities = _sales_capabilities(request.user)
    context = {
        'staffs': tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name') if capabilities['can_filter_staff'] else [],
        'sales_capabilities': capabilities,
    }
    return render(request, 'home/sales/salesListByStaff.html', context)
@permission_required_any('home.change_sales', 'home.can_manage_sales', 'home.can_approve_sales')
def edit_sales(request, id=None):
    obj = get_object_or_404(tenant_qs(request, Sales), pk=id)
    capabilities = _sales_capabilities(request.user)
    admin_like = capabilities['can_filter_staff'] or request.user.has_perm('home.can_view_party_credentials') or request.user.has_perm('home.can_approve_sales')
    if not admin_like:
        if not obj.createdBy_id or getattr(obj.createdBy, 'user_ID_id', None) != request.user.pk:
            return redirect('homeApp:my_sales_list')
    context = {
        'obj': obj,
        'invoice_display': obj.invoiceNumber,
        'sales_edit_readonly': bool(obj.isApproved and not admin_like),
        'sales_return_url': reverse('homeApp:sales_list') if admin_like else reverse('homeApp:my_sales_list'),
    }
    return render(request, 'home/sales/editSales.html', context)
# ----------------------------- Orders ----------------------
@permission_required_any('home.add_order')
def add_order(request):
    return render(request, 'home/orders/addOrder.html', {
        'order_capabilities': _order_capabilities(request.user),
    })
@is_activated()
@permission_required_any('home.can_manage_orders', 'home.view_order', 'home.change_order')
def orders_list(request):
    capabilities = _order_capabilities(request.user)
    staffs = tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name') if capabilities['can_filter_staff'] else []
    context = {
        'staffs': staffs,
        'order_capabilities': capabilities,
    }
    return render(request, 'home/orders/ordersListByAdmin.html', context)
@permission_required_any('home.view_order', 'home.add_order', 'home.change_order', 'home.can_manage_orders')
def my_orders_list(request):
    capabilities = _order_capabilities(request.user)
    context = {
        'staffs': tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name') if capabilities['can_filter_staff'] else [],
        'order_capabilities': capabilities,
    }
    return render(request, 'home/orders/ordersListByStaff.html', context)
@permission_required_any('home.change_order', 'home.can_manage_orders', 'home.can_approve_orders')
def edit_order(request, id=None):
    obj = get_object_or_404(tenant_qs(request, Order), pk=id)
    capabilities = _order_capabilities(request.user)
    admin_like = capabilities['can_filter_staff'] or request.user.has_perm('home.can_view_party_credentials') or request.user.has_perm('home.can_approve_orders') or request.user.has_perm('home.can_manage_orders')
    if not admin_like:
        if not obj.createdBy_id or getattr(obj.createdBy, 'user_ID_id', None) != request.user.pk:
            return redirect('homeApp:my_orders_list')
    attachments = tenant_qs(request, OrderAttachment).filter(isDeleted=False, order=obj).order_by('-id')
    context = {
        'obj': obj,
        'attachments': attachments,
        'order_capabilities': capabilities,
        'order_edit_readonly': bool(str(obj.status) == Order.Status.APPROVED and not admin_like),
        'order_return_url': reverse('homeApp:orders_list') if admin_like else reverse('homeApp:my_orders_list'),
    }
    return render(request, 'home/orders/editOrder.html', context)
@permission_required_any('home.view_order', 'home.add_order', 'home.change_order', 'home.can_manage_orders')
def order_detail(request, id=None):
    obj = get_object_or_404(tenant_qs(request, Order).select_related('partyID', 'createdBy', 'approvedBy'), pk=id)
    capabilities = _order_capabilities(request.user)
    admin_like = capabilities['can_filter_staff'] or request.user.has_perm('home.can_view_party_credentials') or request.user.has_perm('home.can_approve_orders') or request.user.has_perm('home.can_manage_orders')
    if not admin_like:
        if not obj.createdBy_id or getattr(obj.createdBy, 'user_ID_id', None) != request.user.pk:
            return redirect('homeApp:my_orders_list')
    attachments = tenant_qs(request, OrderAttachment).filter(isDeleted=False, order=obj).order_by('-id')
    context = {
        'obj': obj,
        'attachments': attachments,
        'order_capabilities': capabilities,
        'is_admin_like': admin_like,
        'can_approve_order': capabilities.get('can_approve', False) and str(obj.status) != Order.Status.APPROVED,
        'can_edit_order': capabilities.get('can_edit', False) and (admin_like or str(obj.status) != Order.Status.APPROVED),
        'order_return_url': reverse('homeApp:orders_list') if admin_like else reverse('homeApp:my_orders_list'),
    }
    return render(request, 'home/orders/orderDetail.html', context)
# -----------------------Message---------------------
@permission_required_any(
    'home.view_whatsappmessagestatus',
    'home.add_whatsappmessagestatus',
    'home.change_whatsappmessagestatus',
    'home.delete_whatsappmessagestatus',
)
def message_list(request):
    return render(request, 'home/messageList.html')
@permission_required_any('home.can_view_audit_logs', 'home.can_export_audit_logs')
def audit_log_report(request):
    tenant = get_current_tenant(request)
    audit_qs = AuditLog.objects.none()
    actors = []
    content_types = []
    if tenant is not None:
        tenant_ct = ContentType.objects.get_for_model(Tenant)
        audit_qs = AuditLog.objects.select_related('actor', 'content_type').filter(
            Q(snapshot__tenant=tenant.pk) |
            Q(content_type=tenant_ct, object_id=tenant.pk)
        )
        actors = (
            audit_qs.exclude(actor__isnull=True)
            .values('actor_id', 'actor__username')
            .order_by('actor__username')
            .distinct()
        )
        content_types = (
            audit_qs.values('content_type_id', 'content_type__model')
            .order_by('content_type__model')
            .distinct()
        )
    context = {
        'audit_actions': AuditLog.Action.choices,
        'audit_actors': actors,
        'audit_content_types': content_types,
        'audit_can_export': request.user.has_perm('home.can_export_audit_logs') or request.user.has_perm('home.can_view_party_credentials'),
    }
    return render(request, 'home/admin/auditLogReport.html', context)
# -----------------------CashCounter---------------------
@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter')
def cash_counter_home(request):
    return render(request, 'home/cashCounter/indexCashCounter.html')
@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter')
def cash_counter(request):
    banks = tenant_qs(request, Bank).filter(isDeleted__exact=False).order_by('name')
    context = {
        'banks': banks
    }
    return render(request, 'home/cashCounter/todaysCounter.html', context)
@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter', 'home.view_collectioncashcounter')
def my_cash_counter_list(request):
    return render(request, 'home/cashCounter/cashCounterListByStaff.html', {
        'cash_counter_capabilities': _cash_counter_capabilities(request.user),
    })
@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter')
def edit_cash_counter(request, id=None):
    obj = get_object_or_404(tenant_qs(request, CashCounter), pk=id)
    banks = tenant_qs(request, Bank).filter(isDeleted__exact=False).order_by('name')
    context = {
        'obj': obj,
        'invoice_display': obj.invoiceNumber or "",
        'banks': banks
    }
    return render(request, 'home/cashCounter/editCashCounter.html', context)
@permission_required_any('home.can_manage_cash_counter', 'home.view_collectioncashcounter', 'home.add_collectioncashcounter', 'home.change_collectioncashcounter')
def cash_counter_collection(request):
    instance = tenant_qs(request, Bank).filter(isDeleted__exact=False).order_by('name')
    staffs = _staff_users_with_permissions(
        request,
        'home.add_collection',
        'home.change_collection',
        'home.view_collection',
    )
    p_groups = tenant_qs(request, PartyGroup).filter(isDeleted__exact=False).order_by('name')
    context = {
        'instance': instance,
        'staffs': staffs,
        'p_groups': p_groups,
    }
    return render(request, 'home/cashCounter/addCollectionCashCounter.html', context)
@permission_required_any('home.change_collectioncashcounter', 'home.add_collectioncashcounter', 'home.view_collectioncashcounter', 'home.can_manage_cash_counter')
def edit_cash_counter_collection(request, id=None):
    obj = get_object_or_404(tenant_qs(request, CollectionCashCounter), pk=id)
    instance = tenant_qs(request, Bank).filter(isDeleted__exact=False).order_by('name')
    context = {
        'instance': instance,
        'obj': obj
    }
    return render(request, 'home/cashCounter/editCashCounterCollection.html', context)
def get_all_unsent_messages(request):
    msgs = (
        tenant_qs(request, WhatsappMessageStatus)
        .annotate(phone_length=Length('phone'))
        .filter(
            isDeleted=False,
            status='Fail',
            phone_length=10,
            datetime__gte=datetime.now() - timedelta(days=5)
        )
        .order_by('-datetime')[:100]
    )
    data = []
    for msg in msgs:
        data.append({
            'id': msg.id,
            'phone': msg.phone,
            'message': msg.message,
            'messageTo': msg.messageTo,
            'datetime': msg.datetime,
            'status': msg.status,
        })
    return JsonResponse(data, safe=False)
def update_sent_msg_status(request):
    if request.method == 'POST':
        id = request.POST.get("id")
        obj = get_object_or_404(tenant_qs(request, WhatsappMessageStatus), pk=int(id))
        obj.status = 'Success'
        obj.save()
        return JsonResponse({'message': 'success'}, safe=False)
    return JsonResponse({'message': 'error'}, safe=False)
def _parse_ddmmyyyy(value, default=None):
    if not value:
        return default
    try:
        return datetime.strptime(value, '%d/%m/%Y').date()
    except Exception:
        return default
@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter', 'home.can_manage_books')
def cashbook(request):
    tenant = get_current_tenant(request)
    if tenant:
        ensure_books_for_tenant(tenant)
    accounts = tenant_qs(request, BookAccount).filter(isDeleted=False, type=BookAccount.Type.CASH).order_by('-isDefault', 'name')
    account_id = request.GET.get('account')
    account = accounts.filter(pk=int(account_id)).first() if account_id and account_id.isdigit() else accounts.first()
    if account is None:
        return render(request, 'home/books/cashbook.html', {'accounts': [], 'entries': [], 'account': None})
    today = datetime.today().date()
    start_date = _parse_ddmmyyyy(request.GET.get('startDate'), today)
    end_date = _parse_ddmmyyyy(request.GET.get('endDate'), today)
    if end_date and start_date and end_date < start_date:
        start_date, end_date = end_date, start_date
    base_qs = tenant_qs(request, BookTransaction).filter(isDeleted=False, account=account)
    prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
        in_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.IN)),
        out_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.OUT)),
    )
    opening = (account.openingBalance or 0.0) + float(prior['in_total'] or 0.0) - float(prior['out_total'] or 0.0)
    qs = (
        base_qs
        .filter(txDateTime__date__range=[start_date, end_date])
        .select_related('party', 'account', 'account__bank')
        .order_by('txDateTime', 'id')
    )
    balance = opening
    entries = []
    range_in = 0.0
    range_out = 0.0
    for tx in qs:
        amount = float(tx.amount or 0.0)
        in_amt = amount if tx.direction == BookTransaction.Direction.IN else 0.0
        out_amt = amount if tx.direction == BookTransaction.Direction.OUT else 0.0
        balance += in_amt - out_amt
        range_in += in_amt
        range_out += out_amt
        entries.append({
            'tx': tx,
            'in': in_amt,
            'out': out_amt,
            'balance': balance,
        })
    can_view_credentials = request.user.has_perm('home.can_view_party_credentials')
    can_manage_cash_entries = request.user.has_perm('home.can_manage_cash_counter') or can_view_credentials or request.user.has_perm('home.can_manage_books')
    can_transfer = can_view_credentials or request.user.has_perm('home.can_manage_books')
    banks = tenant_qs(request, Bank).filter(isDeleted=False).order_by('name') if can_transfer else tenant_qs(request, Bank).none()
    context = {
        'accounts': accounts,
        'account': account,
        'startDate': start_date.strftime('%d/%m/%Y'),
        'endDate': end_date.strftime('%d/%m/%Y'),
        'opening': opening,
        'range_in': range_in,
        'range_out': range_out,
        'closing': balance,
        'entries': entries,
        'can_manage_books': can_manage_cash_entries,
        'can_transfer_books': can_transfer,
        'banks': banks,
    }
    return render(request, 'home/books/cashbook.html', context)
@permission_required_any('home.can_manage_books')
def bankbook(request):
    tenant = get_current_tenant(request)
    if tenant:
        ensure_books_for_tenant(tenant)
    banks = tenant_qs(request, Bank).filter(isDeleted=False).order_by('name')
    accounts = tenant_qs(request, BookAccount).filter(isDeleted=False, type=BookAccount.Type.BANK).select_related('bank').order_by('name')
    transfer_accounts = tenant_qs(request, BookAccount).filter(isDeleted=False).select_related('bank').order_by('type', 'name')
    bank_id = request.GET.get('bank')
    selected_bank = banks.filter(pk=int(bank_id)).first() if bank_id and bank_id.isdigit() else banks.first()
    account = accounts.filter(bank=selected_bank).first() if selected_bank else accounts.first()
    today = datetime.today().date()
    start_date = _parse_ddmmyyyy(request.GET.get('startDate'), today)
    end_date = _parse_ddmmyyyy(request.GET.get('endDate'), today)
    if end_date and start_date and end_date < start_date:
        start_date, end_date = end_date, start_date
    entries = []
    opening = 0.0
    range_in = 0.0
    range_out = 0.0
    closing = 0.0
    if account:
        base_qs = tenant_qs(request, BookTransaction).filter(isDeleted=False, account=account)
        prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
            in_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.IN)),
            out_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.OUT)),
        )
        opening = (account.openingBalance or 0.0) + float(prior['in_total'] or 0.0) - float(prior['out_total'] or 0.0)
        qs = (
            base_qs
            .filter(txDateTime__date__range=[start_date, end_date])
            .select_related('party', 'account', 'account__bank')
            .order_by('txDateTime', 'id')
        )
        balance = opening
        for tx in qs:
            amount = float(tx.amount or 0.0)
            in_amt = amount if tx.direction == BookTransaction.Direction.IN else 0.0
            out_amt = amount if tx.direction == BookTransaction.Direction.OUT else 0.0
            balance += in_amt - out_amt
            range_in += in_amt
            range_out += out_amt
            entries.append({
                'tx': tx,
                'in': in_amt,
                'out': out_amt,
                'balance': balance,
            })
        closing = balance
    context = {
        'banks': banks,
        'accounts': accounts,
        'transfer_accounts': transfer_accounts,
        'bank': selected_bank,
        'account': account,
        'startDate': start_date.strftime('%d/%m/%Y'),
        'endDate': end_date.strftime('%d/%m/%Y'),
        'opening': opening,
        'range_in': range_in,
        'range_out': range_out,
        'closing': closing,
        'entries': entries,
        'can_manage_books': request.user.has_perm('home.can_view_party_credentials') or request.user.has_perm('home.can_manage_books'),
    }
    return render(request, 'home/books/bankbook.html', context)
@permission_required_any('home.can_manage_parties', 'home.can_view_party_credentials', 'home.view_party')
def party_ledger(request, party_id):
    """
    Party ledger view (staff/admin can view any party; party user can view own).
    """
    is_party_user = request.user.has_perm('home.view_party') and not request.user.has_perm('home.can_manage_parties')
    if is_party_user:
        party = tenant_qs(request, Party).filter(userID_id=request.user.pk, isDeleted=False).only(
            'id', 'name', 'openingBalance'
        ).order_by('-id').first()
        if not party:
            return redirect('/')
    else:
        party = get_object_or_404(tenant_qs(request, Party), pk=party_id, isDeleted=False)
    today = datetime.today().date()
    start_date = _parse_ddmmyyyy(request.GET.get('startDate'), today)
    end_date = _parse_ddmmyyyy(request.GET.get('endDate'), today)
    if end_date and start_date and end_date < start_date:
        start_date, end_date = end_date, start_date
    base_qs = tenant_qs(request, PartyLedgerEntry).filter(isDeleted=False, party=party)
    prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
        debit_total=Sum('debit'),
        credit_total=Sum('credit'),
    )
    opening = float(party.openingBalance or 0.0) + float(prior['debit_total'] or 0.0) - float(prior['credit_total'] or 0.0)
    range_totals = base_qs.filter(txDateTime__date__range=[start_date, end_date]).aggregate(
        debit_total=Sum('debit'),
        credit_total=Sum('credit'),
    )
    range_debit = float(range_totals['debit_total'] or 0.0)
    range_credit = float(range_totals['credit_total'] or 0.0)
    closing = opening + range_debit - range_credit
    context = {
        'party': party,
        'startDate': start_date.strftime('%d/%m/%Y'),
        'endDate': end_date.strftime('%d/%m/%Y'),
        'opening': opening,
        'range_debit': range_debit,
        'range_credit': range_credit,
        'closing': closing,
        'is_party_user': is_party_user,
    }
    return render(request, 'home/party/partyLedger.html', context)
