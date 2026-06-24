import csv
import os
from threading import Thread
from datetime import datetime, timedelta
from email.utils import formataddr, parseaddr
from functools import wraps
from types import SimpleNamespace
from django.conf import settings
from django.contrib.auth import logout, authenticate, login, update_session_auth_hash, get_user_model
from django.contrib.auth import password_validation
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.core.paginator import Paginator
from django.db.models import Q, Sum, F, FloatField, ExpressionWrapper, Window, Count
from django.db.models.functions import Length, TruncMonth
from django.http import Http404, JsonResponse, HttpResponse
from django.templatetags.static import static
from django.template.loader import render_to_string
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes
from django.utils import timezone
from weasyprint import CSS, HTML
# Create your views here.
from django.views.decorators.csrf import ensure_csrf_cookie
# import xlwt
from activation.models import *
from activation.views import is_activated
from .models import *
from .books import ensure_books_for_tenant
from .credential_links import credential_share_token_status, consume_credential_share_token
from .navigation import build_sidebar_sections, _has_any_perm as _has_tenant_any_perm
from .tenancy import assign_tenant, get_current_tenant, get_staff_user_for_request, tenant_object_or_superuser, tenant_qs
from .otp import get_collection_otp_payload, get_collection_status_payload, get_order_delivery_otp_payload, get_order_delivery_status_payload, OTP_TTL_SECONDS
from utils.logger import logger
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
def _order_tracking_status_text(status):
    mapping = {
        Order.Status.DRAFT: 'Draft',
        Order.Status.SUBMITTED: 'Submitted',
        Order.Status.APPROVED: 'Confirmed',
        Order.Status.PACKED: 'Packed',
        Order.Status.OUT_FOR_DELIVERY: 'Out for Delivery',
        Order.Status.DELIVERED: 'Delivered',
        Order.Status.CANCELLED: 'Cancelled',
        Order.Status.RETURNED: 'Returned',
        Order.Status.FULFILLED: 'Delivered',
    }
    return mapping.get(str(status or ''), str(status or '').replace('_', ' ').title())
def _order_allowed_next_statuses_view(order_obj):
    current = str(getattr(order_obj, 'status', '') or '')
    if current == Order.Status.FULFILLED:
        current = Order.Status.DELIVERED
    return {
        Order.Status.DRAFT: [Order.Status.SUBMITTED, Order.Status.CANCELLED],
        Order.Status.SUBMITTED: [Order.Status.APPROVED, Order.Status.CANCELLED],
        Order.Status.APPROVED: [Order.Status.PACKED, Order.Status.CANCELLED],
        Order.Status.PACKED: [Order.Status.OUT_FOR_DELIVERY, Order.Status.CANCELLED],
        Order.Status.OUT_FOR_DELIVERY: [Order.Status.DELIVERED, Order.Status.RETURNED],
        Order.Status.DELIVERED: [Order.Status.RETURNED],
        Order.Status.CANCELLED: [],
        Order.Status.RETURNED: [],
        Order.Status.FULFILLED: [Order.Status.RETURNED],
    }.get(current, [])
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


LOCATION_ASSIGNABLE_PERMISSION_CODENAMES = {
    'can_track_staff_location',
    'can_view_staff_locations',
}


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
            if getattr(user, 'is_authenticated', False) and user.has_perm('home.can_manage_staff_groups'):
                allowed_ids.update(
                    Permission.objects.filter(
                        content_type__app_label='home',
                        codename__in=LOCATION_ASSIGNABLE_PERMISSION_CODENAMES,
                    ).values_list('id', flat=True)
                )
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
        'key': 'staff_location_tracker',
        'label': 'Location Tracker',
        'description': 'Allow staff to open the location tracker and share their route while using the PWA.',
        'permissions': [
            'home.can_track_staff_location',
        ],
    },
    {
        'key': 'staff_location_viewer',
        'label': 'Staff Location Viewer',
        'description': 'Allow admins to open the staff route map and review daily staff paths.',
        'permissions': [
            'home.can_view_staff_locations',
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
def _proforma_capabilities(user):
    can_add = _tenant_has_perm(user, 'home.add_proformainvoice') or _tenant_has_perm(user, 'home.can_manage_proforma_invoice')
    can_edit = _tenant_has_perm(user, 'home.change_proformainvoice') or _tenant_has_perm(user, 'home.can_manage_proforma_invoice')
    can_delete = _tenant_has_perm(user, 'home.delete_proformainvoice') or _tenant_has_perm(user, 'home.can_manage_proforma_invoice')
    can_convert = _tenant_has_perm(user, 'home.can_convert_proforma_invoice')
    can_export = _tenant_has_perm(user, 'home.can_view_proforma_invoice_reports') or _tenant_has_perm(user, 'home.can_view_audit_logs')
    can_filter_staff = _is_admin_like(user) or _tenant_has_perm(user, 'home.can_manage_proforma_invoice') or _tenant_has_perm(user, 'home.can_view_proforma_invoice_reports')
    return {
        'can_add': can_add,
        'can_edit': can_edit,
        'can_delete': can_delete,
        'can_convert': can_convert,
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
            return custom_permission_denied(request, exception=None, message=message)
        return wrapper
    return decorator


def custom_permission_denied(request, exception=None, message=None):
    return render(
        request,
        '403.html',
        {
            'error_title': 'Access Denied',
            'error_code': '403',
            'error_message': message or 'You do not have permission to access this page.',
        },
        status=403,
    )


def custom_page_not_found(request, exception):
    return render(
        request,
        '404.html',
        {
            'error_title': 'Page Not Found',
            'error_code': '404',
            'error_message': 'The page you are looking for does not exist or may have moved.',
        },
        status=404,
    )


def custom_server_error(request):
    return render(
        request,
        '500.html',
        {
            'error_title': 'Server Error',
            'error_code': '500',
            'error_message': 'Something went wrong on the server. Please try again shortly.',
        },
        status=500,
    )


def preview_403(request):
    return custom_permission_denied(
        request,
        message='Preview of the custom 403 page for tenant or permission-denied states.',
    )


def preview_404(request):
    return custom_page_not_found(
        request,
        exception=Exception('Preview'),
    )


def preview_500(request):
    return custom_server_error(request)
@ensure_csrf_cookie
def loginPage(request):
    # if not request.user.is_authenticated:
    return render(request, 'home/login.html')


PASSWORD_RESET_SUCCESS_MESSAGE = 'If an account exists, reset instructions have been sent.'
PASSWORD_RESET_CACHE_TTL = 300
PASSWORD_RESET_CACHE_LIMIT = 3
password_reset_token_generator = PasswordResetTokenGenerator()


def _password_reset_client_ip(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _password_reset_rate_limited(request, identifier):
    normalized_identifier = (identifier or '').strip().lower()
    ip = _password_reset_client_ip(request)
    cache_key = 'password-reset:{}:{}'.format(ip, normalized_identifier)
    count = cache.get(cache_key, 0)
    if count >= PASSWORD_RESET_CACHE_LIMIT:
        return True
    cache.set(cache_key, count + 1, PASSWORD_RESET_CACHE_TTL)
    return False


def _find_password_reset_user(identifier):
    value = (identifier or '').strip()
    if not value:
        return None
    user_model = get_user_model()
    user = (
        user_model.objects
        .filter(username__iexact=value, is_active=True)
        .order_by('id')
        .first()
    )
    if user is not None:
        return user

    staff_queryset = (
        StaffUser.objects
        .select_related('user_ID')
        .filter(
            isDeleted=False,
            user_ID__isnull=False,
            user_ID__is_active=True,
        )
        .filter(Q(isActive__iexact='Active') | Q(isActive__isnull=True) | Q(isActive=''))
    )
    staff = staff_queryset.filter(username__iexact=value).order_by('-id').first()
    if staff is not None:
        logger.info('[Password Reset Email] Matched StaffUser %s for %s', staff.pk, value)
        return staff.user_ID
    return None


def _password_reset_recipient_email(user):
    if not user:
        return ''
    if getattr(user, 'email', ''):
        return user.email
    try:
        staff = getattr(user, 'staff_profile', None)
        if staff is not None and getattr(staff, 'email', ''):
            return staff.email
    except Exception:
        pass
    return ''


def _password_reset_from_email():
    configured_from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', '') or getattr(settings, 'EMAIL_HOST_USER', '')
    _display_name, email_address = parseaddr(configured_from_email)
    if not email_address:
        email_address = getattr(settings, 'EMAIL_HOST_USER', '') or configured_from_email
    return formataddr(('zivaBooks', email_address))


def _password_reset_url(request, user):
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = password_reset_token_generator.make_token(user)
    path = reverse('homeApp:reset_password', kwargs={'uidb64': uidb64, 'token': token})
    return request.build_absolute_uri(path)


def _build_password_reset_email_payload(request, user):
    recipient_email = _password_reset_recipient_email(user)
    if not user or not recipient_email:
        logger.warning('[Password Reset Email] Failed: user or email missing')
        return None
    reset_url = _password_reset_url(request, user)
    context = {
        'user': user,
        'reset_url': reset_url,
        'logo_url': request.build_absolute_uri(static('sw/images/icon-192.png')),
        'timeout_minutes': int(getattr(settings, 'PASSWORD_RESET_TIMEOUT', 3600) / 60),
    }
    subject = render_to_string('home/emails/password_reset_subject.txt', context).strip()
    body = render_to_string('home/emails/password_reset_email.txt', context)
    html_body = render_to_string('home/emails/password_reset_email.html', context)
    return {
        'subject': subject,
        'body': body,
        'html_body': html_body,
        'from_email': _password_reset_from_email(),
        'recipient_email': recipient_email,
    }


def _send_password_reset_email_payload(payload):
    message = EmailMultiAlternatives(
        payload['subject'],
        payload['body'],
        payload['from_email'],
        [payload['recipient_email']],
    )
    message.attach_alternative(payload['html_body'], 'text/html')
    sent_count = message.send(fail_silently=False)
    if sent_count:
        logger.info('[Password Reset Email] Success: reset email sent to %s', payload['recipient_email'])
    else:
        logger.warning('[Password Reset Email] Failed: send_mail returned 0 for %s', payload['recipient_email'])
    return bool(sent_count)


def _send_password_reset_email_payload_async(payload):
    recipient_email = payload['recipient_email']

    def worker():
        try:
            _send_password_reset_email_payload(payload)
        except Exception:
            logger.exception('[Password Reset Email] Failed for %s', recipient_email)

    Thread(target=worker, daemon=True).start()
    logger.info('[Password Reset Email] Queued: reset email send started for %s', recipient_email)
    return True


def _send_password_reset_email(request, user):
    payload = _build_password_reset_email_payload(request, user)
    if payload is None:
        return False
    if getattr(settings, 'PASSWORD_RESET_EMAIL_ASYNC', True):
        return _send_password_reset_email_payload_async(payload)
    return _send_password_reset_email_payload(payload)


def _password_reset_user_from_token(uidb64, token):
    user_model = get_user_model()
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = user_model.objects.get(pk=user_id, is_active=True)
    except (TypeError, ValueError, OverflowError, user_model.DoesNotExist, ValidationError):
        return None
    if not password_reset_token_generator.check_token(user, token):
        return None
    return user


def _sync_legacy_password_fields(user, raw_password):
    try:
        staff = getattr(user, 'staff_profile', None)
        if staff is not None:
            staff.userPassword = raw_password
            staff.save(update_fields=['userPassword'])
    except Exception:
        pass
    try:
        party = getattr(user, 'party_profile', None)
        if party is not None:
            party.password = raw_password
            party.save(update_fields=['password'])
    except Exception:
        pass


def _log_password_reset_completed(user, request):
    try:
        AuditLog.objects.create(
            content_type=ContentType.objects.get_for_model(user.__class__),
            object_id=user.pk,
            actor=None,
            action=AuditLog.Action.PASSWORD_CHANGE,
            object_repr=user.username,
            changes={'password': {'from': '***', 'to': '***'}, 'source': 'forgot_password'},
            snapshot={'username': user.username, 'ip': _password_reset_client_ip(request)},
        )
    except Exception:
        pass


def forgot_password(request):
    context = {
        'message': '',
        'submitted': False,
        'identifier': '',
    }
    if request.method == 'POST':
        identifier = (request.POST.get('identifier') or '').strip()
        context['identifier'] = identifier
        context['submitted'] = True
        context['message'] = PASSWORD_RESET_SUCCESS_MESSAGE
        if identifier and not _password_reset_rate_limited(request, identifier):
            user = _find_password_reset_user(identifier)
            if user is not None:
                try:
                    _send_password_reset_email(request, user)
                except Exception:
                    logger.exception('[Password Reset Email] Failed for %s', identifier)
            else:
                logger.info('[Password Reset Email] Skipped: no active user found for %s', identifier)
        elif identifier:
            logger.warning('[Password Reset Email] Skipped: rate limited for %s', identifier)
        return render(request, 'home/forgotPassword.html', context)
    return render(request, 'home/forgotPassword.html', context)


def reset_password(request, uidb64, token):
    user = _password_reset_user_from_token(uidb64, token)
    context = {
        'valid_link': user is not None,
        'errors': [],
        'success': False,
    }
    if user is None:
        return render(request, 'home/resetPassword.html', context)

    if request.method == 'POST':
        new_password = request.POST.get('new_password') or ''
        confirm_password = request.POST.get('confirm_password') or ''
        errors = []
        if new_password != confirm_password:
            errors.append('New password and confirm password do not match.')
        try:
            password_validation.validate_password(new_password, user=user)
        except ValidationError as exc:
            errors.extend(exc.messages)
        if errors:
            context['errors'] = errors
            return render(request, 'home/resetPassword.html', context)

        user.set_password(new_password)
        user.save(update_fields=['password'])
        _sync_legacy_password_fields(user, new_password)
        _log_password_reset_completed(user, request)
        context['success'] = True
        context['valid_link'] = False
        return render(request, 'home/resetPassword.html', context)

    return render(request, 'home/resetPassword.html', context)
    # else:
    #     return redirect('/admin_home/')


@ensure_csrf_cookie
def claim_access(request, token):
    share_token, status = credential_share_token_status(token)
    if share_token is None and status == 'missing':
        return render(request, 'home/claimAccess.html', {
            'claim_status': 'invalid',
            'claim_heading': 'Link unavailable',
            'claim_message': 'This access link is invalid or no longer available.',
        }, status=404)

    subject_name = ''
    username = ''
    subject_type = 'Account'
    expires_at = None
    if share_token is not None:
        username = share_token.user.username
        expires_at = share_token.expiresAt
        if share_token.staffUser_id:
            subject_name = share_token.staffUser.name
            subject_type = 'Staff'
        elif share_token.party_id:
            subject_name = share_token.party.name
            subject_type = 'Customer'
        else:
            subject_name = share_token.user.username

    if request.method == 'POST' and status == 'valid':
        new_password = (request.POST.get('new_password') or '').strip()
        confirm_password = (request.POST.get('confirm_password') or '').strip()
        error = None
        if not new_password or not confirm_password:
            error = 'Both password fields are required.'
        elif len(new_password) < 6:
            error = 'Password must be at least 6 characters.'
        elif new_password != confirm_password:
            error = 'Passwords do not match.'
        if error is None:
            claimed_ip = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR')
            if consume_credential_share_token(share_token, new_password, claimed_from_ip=claimed_ip):
                return render(request, 'home/claimAccess.html', {
                    'claim_status': 'success',
                    'claim_heading': 'Access activated',
                    'claim_message': 'Your password has been set successfully. You can now sign in.',
                    'username': username,
                    'subject_name': subject_name,
                    'subject_type': subject_type,
                })
            status = 'used'

        if error is not None:
            return render(request, 'home/claimAccess.html', {
                'claim_status': 'valid',
                'claim_heading': 'Set your password',
                'claim_message': 'This secure link can be used only once.',
                'username': username,
                'subject_name': subject_name,
                'subject_type': subject_type,
                'expires_at': expires_at,
                'form_error': error,
            }, status=400)

    if status == 'used':
        return render(request, 'home/claimAccess.html', {
            'claim_status': 'used',
            'claim_heading': 'Link already used',
            'claim_message': 'This access link has already been claimed. Ask your administrator for a fresh link if needed.',
        }, status=410)
    if status == 'expired':
        return render(request, 'home/claimAccess.html', {
            'claim_status': 'expired',
            'claim_heading': 'Link expired',
            'claim_message': 'This access link has expired. Ask your administrator to generate a new one.',
        }, status=410)

    return render(request, 'home/claimAccess.html', {
        'claim_status': 'valid',
        'claim_heading': 'Set your password',
        'claim_message': 'This secure link can be used only once.',
        'username': username,
        'subject_name': subject_name,
        'subject_type': subject_type,
        'expires_at': expires_at,
    })
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
    can_manage_parties = request.user.has_perm('home.can_manage_parties')
    can_add = request.user.has_perm('home.add_party') or can_manage_parties
    can_edit = request.user.has_perm('home.change_party') or can_manage_parties
    can_delete = request.user.has_perm('home.delete_party') or can_manage_parties
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


def save_recovery_email(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if request.method != 'POST':
        return JsonResponse({'message': 'method_not_allowed'}, status=405, safe=False)
    email = (request.POST.get('email') or '').strip()
    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({'message': 'invalid_email'}, status=400, safe=False)

    request.user.email = email
    request.user.save(update_fields=['email'])
    try:
        staff = getattr(request.user, 'staff_profile', None)
        if staff is not None:
            staff.email = email
            staff.save(update_fields=['email'])
    except Exception:
        pass
    return JsonResponse({'message': 'success', 'email': email}, safe=False)


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
            if _has_tenant_any_perm(request, ['home.can_view_party_credentials', 'home.can_manage_staff']):
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


def _customer_page_context(page_key, page_title, page_subtitle=''):
    return {
        'customer_page_key': page_key,
        'customer_page_title': page_title,
        'customer_page_subtitle': page_subtitle,
    }


def _get_customer_party(request, fields=None):
    party_fields = list(fields or [])
    for required in ['id', 'tenant_id', 'name', 'phone', 'openingBalance']:
        if required not in party_fields:
            party_fields.append(required)
    party = tenant_qs(request, Party).filter(userID_id=request.user.pk, isDeleted=False).only(
        *party_fields
    ).order_by('-id').first()
    if party:
        try:
            request.session['cust_party_id'] = int(party.pk)
            request.session['cust_tenant_id'] = int(party.tenant_id) if party.tenant_id is not None else None
        except Exception:
            pass
    return party


def _customer_date_range_from_request(request):
    today = datetime.today().date()
    start_default = today.replace(day=1)
    start_date = _parse_customer_date(request.GET.get('startDate'), start_default)
    end_date = _parse_customer_date(request.GET.get('endDate'), today)
    if end_date and start_date and end_date < start_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date, today


def _format_customer_datetime(value, fallback='-'):
    if not value:
        return fallback
    dt_value = value
    if timezone.is_naive(dt_value):
        dt_value = timezone.make_aware(dt_value)
    return timezone.localtime(dt_value).strftime('%d %b %Y, %I:%M %p')


def _format_customer_date_value(value, fallback='-'):
    if not value:
        return fallback
    return value.strftime('%d %b %Y')


def _customer_bottom_nav_items():
    return [
        {'key': 'dashboard', 'label': 'Home', 'icon': 'home', 'url': reverse('homeApp:customer_home')},
        {'key': 'ledger', 'label': 'Ledger', 'icon': 'book', 'url': reverse('homeApp:customer_ledger')},
        {'key': 'invoices', 'label': 'Invoices', 'icon': 'file pdf outline', 'url': reverse('homeApp:customer_invoices')},
        {'key': 'payments', 'label': 'Payments', 'icon': 'money bill alternate outline', 'url': reverse('homeApp:customer_payments')},
        {'key': 'orders', 'label': 'Orders', 'icon': 'shopping bag', 'url': reverse('homeApp:customer_orders')},
        {'key': 'profile', 'label': 'Profile', 'icon': 'user outline', 'url': reverse('homeApp:customer_profile')},
    ]


def _customer_dashboard_actions():
    return [
        {'label': 'Home', 'icon': 'home', 'url': reverse('homeApp:customer_home')},
        {'label': 'Ledger', 'icon': 'book', 'url': reverse('homeApp:customer_ledger')},
        {'label': 'Invoices', 'icon': 'file pdf outline', 'url': reverse('homeApp:customer_invoices')},
        {'label': 'Payments', 'icon': 'money bill alternate outline', 'url': reverse('homeApp:customer_payments')},
        {'label': 'Orders', 'icon': 'shopping bag', 'url': reverse('homeApp:customer_orders')},
        {'label': 'Profile', 'icon': 'user outline', 'url': reverse('homeApp:customer_profile')},
    ]


def _customer_shift_month(date_obj, delta):
    month_index = (date_obj.month - 1) + delta
    year = date_obj.year + (month_index // 12)
    month = (month_index % 12) + 1
    return date_obj.replace(year=year, month=month, day=1)


def _customer_querystring(request, **overrides):
    params = request.GET.copy()
    for key, value in overrides.items():
        if value in (None, ''):
            params.pop(key, None)
        else:
            params[key] = value
    return params.urlencode()


def _customer_fragment_requested(request):
    return str(request.GET.get('fragment') or '').strip() == '1'


def _customer_fragment_response(request, template_name, items, page_obj):
    html = render_to_string(template_name, {'items': items}, request=request)
    return JsonResponse({
        'message': 'success',
        'html': html,
        'has_next': bool(page_obj.has_next()),
        'next_page': page_obj.next_page_number() if page_obj.has_next() else None,
        'current_page': page_obj.number,
        'total_pages': page_obj.paginator.num_pages,
    })


def _customer_sales_invoice_item(sale):
    return {
        'kind': 'Sales',
        'id': sale.id,
        'invoice_no': sale.invoiceNumber or sale.salesID or f'SAL-{sale.pk}',
        'date_label': _format_customer_date_value(sale.buildDate),
        'amount': float(sale.amount or 0.0),
        'download_url': reverse('homeApp:customer_download_sales_invoice_pdf') + f'?id={sale.id}',
        'search_text': ' '.join([
            str(sale.invoiceNumber or ''),
            str(sale.salesID or ''),
            str(sale.paymentID or ''),
            str(sale.remark or ''),
        ]).strip().lower(),
    }


def _customer_cash_counter_invoice_item(counter):
    return {
        'kind': 'Cash Counter',
        'id': counter.id,
        'invoice_no': counter.invoiceNumber or counter.counterID or f'CC-{counter.pk}',
        'date_label': _format_customer_datetime(counter.entryDate),
        'amount': float(counter.amount or 0.0),
        'download_url': reverse('homeApp:customer_download_cash_counter_invoice_pdf') + f'?id={counter.id}',
        'search_text': ' '.join([
            str(counter.invoiceNumber or ''),
            str(counter.counterID or ''),
            str(counter.remark or ''),
            str(counter.mode or ''),
        ]).strip().lower(),
    }
def _customer_ledger_display_datetime(entry):
    dt_value = entry.txDateTime
    try:
        if (
            entry.source in (PartyLedgerEntry.Source.COLLECTION, PartyLedgerEntry.Source.COUNTER_COLLECTION)
            and entry.txDateTime
            and entry.txDateTime.time().strftime('%H:%M:%S') == '00:00:00'
            and getattr(entry, 'datetime', None)
        ):
            dt_value = entry.datetime
    except Exception:
        dt_value = entry.txDateTime
    return dt_value


def _customer_ledger_item(entry, opening):
    running_total = float(getattr(entry, 'running_total', 0.0) or 0.0)
    balance = opening + running_total
    debit = float(entry.debit or 0.0)
    credit = float(entry.credit or 0.0)
    reference = str(entry.reference or '').strip()
    particulars = str(entry.particulars or '').strip()
    source_label = entry.get_source_display() if hasattr(entry, 'get_source_display') else str(entry.source or '').replace('_', ' ').title()
    title = reference or source_label or f'Entry #{entry.pk}'
    detail = particulars if particulars and particulars != title else ''
    return {
        'id': entry.id,
        'title': title,
        'subtitle': source_label,
        'detail': detail,
        'date_label': _format_customer_datetime(_customer_ledger_display_datetime(entry)),
        'debit': debit,
        'credit': credit,
        'balance': balance,
    }


def _customer_visible_party_ledger_qs(request, qs):
    """
    Customer portal should show only:
    - Approved Sales
    - Customer-facing Cash Counter ledger entries
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
        Q(source=PartyLedgerEntry.Source.CASH_COUNTER) |
        (Q(source=PartyLedgerEntry.Source.COLLECTION) & Q(source_object_id__in=approved_collection_ids)) |
        (Q(source=PartyLedgerEntry.Source.COUNTER_COLLECTION) & Q(source_object_id__in=approved_counter_collection_ids))
    )
@ensure_csrf_cookie
@permission_required_any('home.view_party')
def customer_home(request):
    party = _get_customer_party(
        request,
        fields=['address', 'billingAddress', 'legalName', 'gstNumber', 'isB2B', 'username', 'password'],
    )
    if not party:
        return redirect('/')
    start_date, end_date, today = _customer_date_range_from_request(request)
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
    order_qs = tenant_qs(request, Order).filter(isDeleted=False, partyID=party)
    payment_qs = tenant_qs(request, Collection).filter(isDeleted=False, isApproved=True, partyID=party)
    counter_payment_qs = tenant_qs(request, CollectionCashCounter).filter(isDeleted=False, isApproved=True, partyID=party)
    sales_qs = tenant_qs(request, Sales).filter(
        isDeleted=False,
        isApproved=True,
        partyID=party,
    )
    cash_invoice_qs = tenant_qs(request, CashCounter).filter(
        isDeleted=False,
        partyID=party,
    ).exclude(invoiceNumber__isnull=True).exclude(invoiceNumber__exact='')
    current_month_orders = order_qs.filter(buildDate__range=[start_date, end_date]).count()
    current_month_purchases = sales_qs.filter(buildDate__range=[start_date, end_date]).aggregate(total=Sum('amount'))
    last_payment = payment_qs.order_by('-collectionDateTime', '-id').first()
    if not last_payment:
        last_payment = counter_payment_qs.order_by('-collectionDateTime', '-id').first()
    trend_start = _customer_shift_month(today.replace(day=1), -5)
    trend_months = [_customer_shift_month(trend_start, index) for index in range(6)]
    trend_labels = [month.strftime('%b') for month in trend_months]

    sales_month_map = {
        row['month'].date().replace(day=1) if hasattr(row['month'], 'date') else row['month'].replace(day=1): float(row['total'] or 0.0)
        for row in sales_qs.filter(buildDate__gte=trend_start, buildDate__lte=end_date).annotate(
            month=TruncMonth('buildDate')
        ).values('month').annotate(total=Sum('amount')).order_by('month')
    }
    payment_month_map = {}
    for row in payment_qs.filter(
        collectionDateTime__date__gte=trend_start,
        collectionDateTime__date__lte=end_date,
    ).annotate(
        month=TruncMonth('collectionDateTime')
    ).values('month').annotate(total=Sum('paidAmount')).order_by('month'):
        month_key = row['month'].date().replace(day=1) if hasattr(row['month'], 'date') else row['month'].replace(day=1)
        payment_month_map[month_key] = payment_month_map.get(month_key, 0.0) + float(row['total'] or 0.0)
    for row in counter_payment_qs.filter(
        collectionDateTime__date__gte=trend_start,
        collectionDateTime__date__lte=end_date,
    ).annotate(
        month=TruncMonth('collectionDateTime')
    ).values('month').annotate(total=Sum('paidAmount')).order_by('month'):
        month_key = row['month'].date().replace(day=1) if hasattr(row['month'], 'date') else row['month'].replace(day=1)
        payment_month_map[month_key] = payment_month_map.get(month_key, 0.0) + float(row['total'] or 0.0)
    order_month_map = {
        row['month'].date().replace(day=1) if hasattr(row['month'], 'date') else row['month'].replace(day=1): int(row['total'] or 0)
        for row in order_qs.filter(buildDate__gte=trend_start, buildDate__lte=end_date).annotate(
            month=TruncMonth('buildDate')
        ).values('month').annotate(total=Count('id')).order_by('month')
    }
    trend_purchases = [round(sales_month_map.get(month, 0.0), 2) for month in trend_months]
    trend_payments = [round(payment_month_map.get(month, 0.0), 2) for month in trend_months]
    trend_orders = [int(order_month_map.get(month, 0)) for month in trend_months]

    status_label_map = {
        Order.Status.DRAFT: 'Draft',
        Order.Status.SUBMITTED: 'Submitted',
        Order.Status.APPROVED: 'Confirmed',
        Order.Status.PACKED: 'Packed',
        Order.Status.OUT_FOR_DELIVERY: 'Out for Delivery',
        Order.Status.DELIVERED: 'Delivered',
        Order.Status.RETURNED: 'Returned',
        Order.Status.FULFILLED: 'Delivered',
        Order.Status.CANCELLED: 'Cancelled',
    }
    raw_status_counts = order_qs.values('status').annotate(total=Count('id'))
    status_counts = {}
    for row in raw_status_counts:
        label = status_label_map.get(row['status'], _order_tracking_status_text(row['status']))
        status_counts[label] = status_counts.get(label, 0) + int(row['total'] or 0)
    status_labels = [label for label, total in status_counts.items() if total > 0]
    status_values = [status_counts[label] for label in status_labels]
    if not status_labels:
        status_labels = ['No Orders']
        status_values = [1]

    cash_invoice_count = cash_invoice_qs.filter(mode__in=['Cash', 'Credit', 'Card', 'Mix', 'Return']).count()
    activity_labels = ['Sales Invoices', 'Cash Counter', 'Payments', 'Orders']
    activity_values = [
        sales_qs.count(),
        cash_invoice_count,
        payment_qs.count() + counter_payment_qs.count(),
        order_qs.count(),
    ]
    context = {
        'party': party,
        'startDate': start_date.strftime('%d/%m/%Y'),
        'endDate': end_date.strftime('%d/%m/%Y'),
        'opening': opening,
        'range_debit': range_debit,
        'range_credit': range_credit,
        'closing': closing,
        'dashboard_cards': [
            {
                'label': 'Outstanding',
                'value': f'₹ {closing:,.2f}',
                'hint': 'Live closing balance for the selected range.',
                'tone': 'amber',
            },
            {
                'label': 'This Period Purchase',
                'value': f'₹ {float(current_month_purchases.get("total") or 0.0):,.2f}',
                'hint': f'{start_date.strftime("%d %b")} to {end_date.strftime("%d %b")}',
                'tone': 'blue',
            },
            {
                'label': 'Payments Received',
                'value': f'₹ {range_credit:,.2f}',
                'hint': 'Approved customer payments only.',
                'tone': 'green',
            },
            {
                'label': 'Orders',
                'value': str(current_month_orders),
                'hint': 'Orders in the current filtered range.',
                'tone': 'slate',
            },
        ],
        'quick_links': _customer_dashboard_actions(),
        'recent_summary': [
            {'label': 'Customer', 'value': party.legalName or party.name or '-'},
            {'label': 'Phone', 'value': party.phone or '-'},
            {'label': 'Last Payment', 'value': _format_customer_datetime(getattr(last_payment, 'collectionDateTime', None))},
            {'label': 'Profile Type', 'value': 'B2B Customer' if party.isB2B else 'Retail Customer'},
        ],
        'last_payment_label': _format_customer_datetime(getattr(last_payment, 'collectionDateTime', None)),
        'profile_type_label': 'B2B Customer' if party.isB2B else 'Retail Customer',
        'dashboard_charts': {
            'trend': {
                'labels': trend_labels,
                'purchases': trend_purchases,
                'payments': trend_payments,
            },
            'orders': {
                'labels': trend_labels,
                'values': trend_orders,
            },
            'status': {
                'labels': status_labels,
                'values': status_values,
            },
            'activity': {
                'labels': activity_labels,
                'values': activity_values,
            },
        },
    }
    context.update(_customer_page_context('dashboard', 'Customer Dashboard', 'Live account view built for mobile use.'))
    context['customer_bottom_nav_items'] = _customer_bottom_nav_items()
    return render(request, 'home/customer/dashboard.html', context)


@ensure_csrf_cookie
@permission_required_any('home.view_party')
def customer_ledger(request):
    party = _get_customer_party(request, fields=['address', 'billingAddress', 'legalName'])
    if not party:
        return redirect('/')
    start_date, end_date, _today = _customer_date_range_from_request(request)
    search = str(request.GET.get('q') or '').strip()
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
    ledger_qs = base_qs.filter(txDateTime__date__range=[start_date, end_date])
    if search:
        ledger_qs = ledger_qs.filter(
            Q(particulars__icontains=search) |
            Q(reference__icontains=search) |
            Q(source__icontains=search)
        )
    ledger_qs = ledger_qs.annotate(
        signed_amount=ExpressionWrapper(F('debit') - F('credit'), output_field=FloatField()),
    ).annotate(
        running_total=Window(
            expression=Sum('signed_amount'),
            order_by=[F('txDateTime').asc(), F('id').asc()],
        ),
    ).order_by('-txDateTime', '-id')
    paginator = Paginator(ledger_qs, 12)
    page_obj = paginator.get_page(request.GET.get('page') or 1)
    ledger_entries = [
        _customer_ledger_item(entry, opening)
        for entry in page_obj.object_list
    ]
    context = {
        'party': party,
        'startDate': start_date.strftime('%d/%m/%Y'),
        'endDate': end_date.strftime('%d/%m/%Y'),
        'opening': opening,
        'range_debit': range_debit,
        'range_credit': range_credit,
        'closing': closing,
        'page_obj': page_obj,
        'ledger_entries': ledger_entries,
        'search_query': search,
        'pagination_base_query': _customer_querystring(request, page=None),
        'customer_bottom_nav_items': _customer_bottom_nav_items(),
    }
    if _customer_fragment_requested(request):
        return _customer_fragment_response(request, 'home/customer/_ledger_items.html', ledger_entries, page_obj)
    context.update(_customer_page_context('ledger', 'Ledger', 'Filter the statement range and review the account flow.'))
    return render(request, 'home/customer/ledger.html', context)


@permission_required_any('home.view_party')
def customer_payments(request):
    party = _get_customer_party(request, fields=['address'])
    if not party:
        return redirect('/')
    search = str(request.GET.get('q') or '').strip()

    payments_qs = tenant_qs(request, PartyLedgerEntry).filter(
        isDeleted=False,
        party=party,
        source__in=[
            PartyLedgerEntry.Source.COLLECTION,
            PartyLedgerEntry.Source.COUNTER_COLLECTION,
        ],
    ).order_by('-txDateTime', '-id')
    if search:
        payments_qs = payments_qs.filter(
            Q(reference__icontains=search) |
            Q(particulars__icontains=search)
        )

    paginator = Paginator(payments_qs, 12)
    page_obj = paginator.get_page(request.GET.get('page') or 1)
    page_entries = list(page_obj.object_list)
    collection_ids = [item.source_object_id for item in page_entries if item.source == PartyLedgerEntry.Source.COLLECTION and item.source_object_id]
    counter_ids = [item.source_object_id for item in page_entries if item.source == PartyLedgerEntry.Source.COUNTER_COLLECTION and item.source_object_id]
    collections_map = {
        item.id: item
        for item in tenant_qs(request, Collection).filter(
            isDeleted=False,
            isApproved=True,
            partyID=party,
            id__in=collection_ids,
        )
    }
    counter_map = {
        item.id: item
        for item in tenant_qs(request, CollectionCashCounter).filter(
            isDeleted=False,
            isApproved=True,
            partyID=party,
            id__in=counter_ids,
        )
    }
    payments = []
    for entry in page_entries:
        if entry.source == PartyLedgerEntry.Source.COLLECTION:
            item = collections_map.get(entry.source_object_id)
            if not item:
                continue
            payments.append({
                'kind': 'Field Collection',
                'payment_id': item.paymentID or entry.reference or f'COL-{item.pk}',
                'mode': item.modeOfPayment or '-',
                'amount': float(item.paidAmount or entry.credit or 0.0),
                'datetime_label': _format_customer_datetime(item.collectionDateTime or item.datetime),
                'verified': bool(item.otpVerified),
                'detail': item.detail or item.remark or entry.particulars or '',
            })
        else:
            item = counter_map.get(entry.source_object_id)
            if not item:
                continue
            payments.append({
                'kind': 'Counter Collection',
                'payment_id': item.paymentID or entry.reference or f'CC-{item.pk}',
                'mode': item.modeOfPayment or '-',
                'amount': float(item.paidAmount or entry.credit or 0.0),
                'datetime_label': _format_customer_datetime(item.collectionDateTime or item.datetime),
                'verified': True,
                'detail': item.detail or item.remark or entry.particulars or '',
            })
    context = {
        'party': party,
        'page_obj': page_obj,
        'payments': payments,
        'search_query': search,
        'pagination_base_query': _customer_querystring(request, page=None),
        'customer_bottom_nav_items': _customer_bottom_nav_items(),
    }
    if _customer_fragment_requested(request):
        return _customer_fragment_response(request, 'home/customer/_payments_items.html', payments, page_obj)
    context.update(_customer_page_context('payments', 'Payments', 'Approved payments and collection references.'))
    return render(request, 'home/customer/payments.html', context)


@permission_required_any('home.view_party')
def customer_orders(request):
    party = _get_customer_party(request)
    if not party:
        return redirect('/')
    order_qs = tenant_qs(request, Order).filter(
        isDeleted=False,
        partyID=party,
        status__in=[Order.Status.DELIVERED, Order.Status.RETURNED, Order.Status.FULFILLED],
    ).order_by('-buildDate', '-id')
    search = str(request.GET.get('q') or '').strip()
    if search:
        order_qs = order_qs.filter(
            Q(orderNumber__icontains=search) |
            Q(orderText__icontains=search) |
            Q(remark__icontains=search) |
            Q(linkedSales__salesID__icontains=search) |
            Q(linkedSales__invoiceNumber__icontains=search)
        )
    paginator = Paginator(order_qs, 12)
    page_obj = paginator.get_page(request.GET.get('page') or 1)
    orders = []
    for order in page_obj.object_list:
        orders.append({
            'id': order.id,
            'number': order.orderNumber or f'Order #{order.pk}',
            'date_label': _format_customer_date_value(order.buildDate),
            'status': _order_tracking_status_text(order.status),
            'status_key': str(order.status or ''),
            'remark': order.remark or '',
            'linked_sales_id': getattr(order.linkedSales, 'salesID', '') if getattr(order, 'linkedSales', None) else '',
            'delivery_verified': bool(order.deliveryOtpVerified),
            'detail_url': reverse('homeApp:customer_order_detail', args=[order.id]),
        })
    context = {
        'party': party,
        'page_obj': page_obj,
        'orders': orders,
        'search_query': search,
        'pagination_base_query': _customer_querystring(request, page=None),
        'customer_bottom_nav_items': _customer_bottom_nav_items(),
    }
    if _customer_fragment_requested(request):
        return _customer_fragment_response(request, 'home/customer/_orders_items.html', orders, page_obj)
    context.update(_customer_page_context('orders', 'Orders', 'Track order progress from confirmation to delivery.'))
    return render(request, 'home/customer/orders.html', context)


@permission_required_any('home.view_party')
def customer_invoices(request):
    party = _get_customer_party(request)
    if not party:
        return redirect('/')
    search = str(request.GET.get('q') or '').strip()
    sales_qs = tenant_qs(request, Sales).filter(
        isDeleted=False,
        isApproved=True,
        partyID=party,
    ).order_by('-buildDate', '-id')
    if search:
        sales_qs = sales_qs.filter(
            Q(invoiceNumber__icontains=search) |
            Q(salesID__icontains=search) |
            Q(paymentID__icontains=search) |
            Q(remark__icontains=search)
        )
    sales_rows = [
        _customer_sales_invoice_item(item)
        for item in sales_qs
    ]
    cash_qs = tenant_qs(request, CashCounter).filter(
        isDeleted=False,
        partyID=party,
    ).exclude(invoiceNumber__isnull=True).exclude(invoiceNumber__exact='').order_by('-entryDate', '-id')
    if search:
        cash_qs = cash_qs.filter(
            Q(invoiceNumber__icontains=search) |
            Q(counterID__icontains=search) |
            Q(remark__icontains=search) |
            Q(mode__icontains=search)
        )
    cash_rows = [
        _customer_cash_counter_invoice_item(item)
        for item in cash_qs
        if str(item.mode or '') in {'Cash', 'Credit', 'Card', 'Mix', 'Return'}
    ]
    invoice_rows = sales_rows + cash_rows
    invoice_rows.sort(key=lambda item: (item.get('date_label') or '', item.get('id') or 0), reverse=True)
    paginator = Paginator(invoice_rows, 12)
    page_obj = paginator.get_page(request.GET.get('page') or 1)
    invoices = list(page_obj.object_list)
    context = {
        'party': party,
        'page_obj': page_obj,
        'invoices': invoices,
        'search_query': search,
        'pagination_base_query': _customer_querystring(request, page=None),
        'customer_bottom_nav_items': _customer_bottom_nav_items(),
    }
    if _customer_fragment_requested(request):
        return _customer_fragment_response(request, 'home/customer/_invoice_items.html', invoices, page_obj)
    context.update(_customer_page_context('invoices', 'Invoices', 'Download approved sales and cash counter invoices.'))
    return render(request, 'home/customer/invoices.html', context)


@permission_required_any('home.view_party')
def customer_download_sales_invoice_pdf(request):
    sale_id = request.GET.get('id')
    if not sale_id or not str(sale_id).isdigit():
        return JsonResponse({'message': 'error', 'error': 'Sale id is required.'}, status=400)
    party = _get_customer_party(request)
    if not party:
        return JsonResponse({'message': 'forbidden'}, status=403)
    sale = get_object_or_404(
        tenant_qs(request, Sales).select_related('partyID', 'createdBy', 'approvedBy', 'bankID', 'tenant'),
        pk=int(sale_id),
        isDeleted=False,
        isApproved=True,
        partyID=party,
    )
    from .apiView import _gst_breakup_from_sale, _pdf_base_context
    tenant = get_current_tenant(request)
    lines = list(sale.lines.filter(isDeleted=False).select_related('item').order_by('id'))
    gst_breakup = _gst_breakup_from_sale(sale, tenant)
    total_before_round_off = round(float(gst_breakup['taxable_total'] or 0.0) + float(gst_breakup['gst_total'] or 0.0), 2)
    round_off_amount = round(float(getattr(sale, 'roundOffAmount', 0.0) or 0.0), 2)
    context = {
        'sale': sale,
        'lines': lines,
        'seller_name': getattr(tenant, 'legalName', None) or getattr(tenant, 'name', None) or 'zivabooks.in',
        'seller_gst_number': getattr(tenant, 'gstNumber', None) or '',
        'seller_address': getattr(tenant, 'billingAddress', None) or '',
        'seller_phone': getattr(tenant, 'phone', None) or '',
        'buyer_name': sale.buyerLegalName or getattr(getattr(sale, 'partyID', None), 'legalName', None) or getattr(getattr(sale, 'partyID', None), 'name', None) or '-',
        'buyer_gst_number': sale.buyerGstNumber or '',
        'buyer_address': sale.buyerBillingAddress or getattr(getattr(sale, 'partyID', None), 'billingAddress', None) or getattr(getattr(sale, 'partyID', None), 'address', None) or '',
        'gst_total': gst_breakup['gst_total'],
        'taxable_total': gst_breakup['taxable_total'],
        'total_before_round_off': total_before_round_off,
        'round_off_amount': round_off_amount,
        'grand_total': float(sale.amount or 0.0),
        'tax_mode_label': gst_breakup['tax_mode_label'],
        'seller_state_code': gst_breakup['seller_state_code'],
        'buyer_state_code': gst_breakup['buyer_state_code'],
        'place_of_supply_state_code': gst_breakup['place_of_supply_state_code'],
        'effective_buyer_state_code': gst_breakup['effective_buyer_state_code'],
        'cgst_total': gst_breakup['cgst_total'],
        'sgst_total': gst_breakup['sgst_total'],
        'utgst_total': gst_breakup['utgst_total'],
        'igst_total': gst_breakup['igst_total'],
    }
    context.update(_pdf_base_context(request, [
        f'Invoice: {sale.invoiceNumber or sale.pk}',
        f'Buyer: {context["buyer_name"]}',
        f'Kind: {sale.get_invoiceKind_display()}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename=sales-invoice-{sale.invoiceNumber or sale.pk}.pdf'
    html = render_to_string("home/report/salesInvoicePDF.html", context)
    base_url = request.build_absolute_uri('/')
    HTML(string=html, base_url=base_url).write_pdf(response, stylesheets=[CSS(string='@page { size: A4; margin: 0.9cm; }')])
    return response


@permission_required_any('home.view_party')
def customer_download_cash_counter_invoice_pdf(request):
    counter_id = request.GET.get('id')
    if not counter_id or not str(counter_id).isdigit():
        return JsonResponse({'message': 'error', 'error': 'Cash counter id is required.'}, status=400)
    party = _get_customer_party(request)
    if not party:
        return JsonResponse({'message': 'forbidden'}, status=403)
    counter = get_object_or_404(
        tenant_qs(request, CashCounter).select_related('partyID', 'createdBy', 'bankID', 'tenant'),
        pk=int(counter_id),
        isDeleted=False,
        partyID=party,
    )
    if str(counter.mode or '') not in {'Cash', 'Credit', 'Card', 'Mix', 'Return'} or not getattr(counter, 'invoiceNumber', None):
        return JsonResponse({'message': 'error', 'error': 'Invoice download is only available for billing entries.'}, status=400)
    from .apiView import _gst_breakup_from_cash_counter, _pdf_base_context
    tenant = get_current_tenant(request)
    lines = list(counter.lines.filter(isDeleted=False).select_related('item').order_by('id'))
    gst_breakup = _gst_breakup_from_cash_counter(counter, tenant)
    total_before_round_off = round(float(gst_breakup['taxable_total'] or 0.0) + float(gst_breakup['gst_total'] or 0.0), 2)
    round_off_amount = round(float(getattr(counter, 'roundOffAmount', 0.0) or 0.0), 2)
    grand_total = round(float(counter.amount or 0.0), 2)
    if counter.mode == 'Mix':
        grand_total = round(float(counter.mixCashAmount or 0.0) + float(counter.mixCardAmount or 0.0), 2)
    context = {
        'counter': counter,
        'lines': lines,
        'seller_name': getattr(tenant, 'legalName', None) or getattr(tenant, 'name', None) or 'zivabooks.in',
        'seller_gst_number': getattr(tenant, 'gstNumber', None) or '',
        'seller_address': getattr(tenant, 'billingAddress', None) or '',
        'seller_phone': getattr(tenant, 'phone', None) or '',
        'buyer_name': counter.buyerLegalName or getattr(getattr(counter, 'partyID', None), 'legalName', None) or getattr(getattr(counter, 'partyID', None), 'name', None) or '-',
        'buyer_gst_number': counter.buyerGstNumber or '',
        'buyer_address': counter.buyerBillingAddress or getattr(getattr(counter, 'partyID', None), 'billingAddress', None) or getattr(getattr(counter, 'partyID', None), 'address', None) or '',
        'gst_total': gst_breakup['gst_total'],
        'taxable_total': gst_breakup['taxable_total'],
        'total_before_round_off': total_before_round_off,
        'round_off_amount': round_off_amount,
        'grand_total': grand_total,
        'tax_mode_label': gst_breakup['tax_mode_label'],
        'seller_state_code': gst_breakup['seller_state_code'],
        'buyer_state_code': gst_breakup['buyer_state_code'],
        'place_of_supply_state_code': gst_breakup['place_of_supply_state_code'],
        'effective_buyer_state_code': gst_breakup['effective_buyer_state_code'],
        'cgst_total': gst_breakup['cgst_total'],
        'sgst_total': gst_breakup['sgst_total'],
        'utgst_total': gst_breakup['utgst_total'],
        'igst_total': gst_breakup['igst_total'],
    }
    context.update(_pdf_base_context(request, [
        f'Invoice: {counter.invoiceNumber or counter.pk}',
        f'Buyer: {context["buyer_name"]}',
        f'Mode: {counter.mode or "-"}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename=cash-counter-invoice-{counter.invoiceNumber or counter.pk}.pdf'
    html = render_to_string("home/report/cashCounterInvoicePDF.html", context)
    base_url = request.build_absolute_uri('/')
    HTML(string=html, base_url=base_url).write_pdf(response, stylesheets=[CSS(string='@page { size: A4; margin: 0.9cm; }')])
    return response


@permission_required_any('home.view_party')
def customer_order_detail(request, id):
    party = _get_customer_party(request)
    if not party:
        return redirect('/')
    order = get_object_or_404(
        tenant_qs(request, Order).filter(isDeleted=False, partyID=party).select_related('linkedSales'),
        pk=id,
    )
    events = tenant_qs(request, OrderStatusEvent).filter(isDeleted=False, order=order).order_by('datetime', 'id')
    delivery_state = 'Verified' if order.deliveryOtpVerified else ('Pending' if str(order.status or '') in [Order.Status.OUT_FOR_DELIVERY, Order.Status.DELIVERED, Order.Status.FULFILLED] else 'Not Required')
    context = {
        'party': party,
        'order': order,
        'order_label': order.orderNumber or f'Order #{order.pk}',
        'order_status_text': _order_tracking_status_text(order.status),
        'order_summary_cards': [
            {'label': 'Current Status', 'value': _order_tracking_status_text(order.status)},
            {'label': 'Order Date', 'value': _format_customer_date_value(order.buildDate)},
            {'label': 'Linked Sale', 'value': getattr(order.linkedSales, 'salesID', '') or getattr(order.linkedSales, 'invoiceNumber', '') or '-'},
            {'label': 'Delivery OTP', 'value': delivery_state},
        ],
        'order_fact_rows': [
            {'icon': 'calendar alternate outline', 'label': 'Build Date', 'value': _format_customer_date_value(order.buildDate)},
            {'icon': 'file alternate outline', 'label': 'Sales Link', 'value': getattr(order.linkedSales, 'salesID', '') or getattr(order.linkedSales, 'invoiceNumber', '') or '-'},
            {'icon': 'clipboard outline', 'label': 'Order Notes', 'value': order.orderText or order.remark or '-'},
            {'icon': 'comment alternate outline', 'label': 'Remark', 'value': order.remark or '-'},
        ],
        'milestone_rows': [
            {'label': 'Confirmed', 'value': _format_customer_datetime(getattr(order, 'approvedOn', None))},
            {'label': 'Packed', 'value': _format_customer_datetime(getattr(order, 'packedOn', None))},
            {'label': 'Out for Delivery', 'value': _format_customer_datetime(getattr(order, 'outForDeliveryOn', None))},
            {'label': 'Delivered', 'value': _format_customer_datetime(getattr(order, 'deliveredOn', None))},
            {'label': 'OTP Verified On', 'value': _format_customer_datetime(getattr(order, 'deliveryOtpVerifiedOn', None))},
            {'label': 'Returned', 'value': _format_customer_datetime(getattr(order, 'returnedOn', None))},
        ],
        'timeline': [
            {
                'title': _order_tracking_status_text(event.toStatus),
                'timestamp': _format_customer_datetime(event.datetime),
                'note': event.note or '',
            }
            for event in events
        ],
        'customer_bottom_nav_items': _customer_bottom_nav_items(),
    }
    context.update(_customer_page_context('orders', 'Order Detail', 'Single order view with delivery status.'))
    return render(request, 'home/customer/order_detail.html', context)


@permission_required_any('home.view_party')
def customer_profile(request):
    party = _get_customer_party(
        request,
        fields=['address', 'billingAddress', 'legalName', 'gstNumber', 'isB2B', 'username'],
    )
    if not party:
        return redirect('/')
    context = {
        'party': party,
        'customer_bottom_nav_items': _customer_bottom_nav_items(),
        'profile_chips': [
            {'label': 'Customer Account', 'tone': 'accent'},
            {'label': 'B2B Customer' if party.isB2B else 'Retail Customer', 'tone': 'default'},
            {'label': party.phone or 'Phone not added', 'tone': 'warm'},
        ],
        'profile_identity': {
            'name': party.name or '-',
            'legal_name': party.legalName or party.name or '-',
            'login_id': party.username or request.user.username or '-',
            'phone': party.phone or '-',
        },
        'profile_contact_rows': [
            {'icon': 'phone', 'label': 'Phone', 'value': party.phone or '-'},
            {'icon': 'user outline', 'label': 'Login ID', 'value': party.username or request.user.username or '-'},
            {'icon': 'map marker alternate', 'label': 'Billing Address', 'value': party.billingAddress or party.address or '-'},
        ],
        'profile_business_rows': [
            {'icon': 'building outline', 'label': 'Legal Name', 'value': party.legalName or '-'},
            {'icon': 'id card outline', 'label': 'GST Number', 'value': party.gstNumber or '-'},
            {'icon': 'briefcase', 'label': 'Type', 'value': 'B2B Customer' if party.isB2B else 'Retail Customer'},
        ],
        'profile_meta_cards': [
            {'label': 'Portal Access', 'value': 'Active Customer Login'},
            {'label': 'Statement Access', 'value': 'Ledger, Payments, Orders'},
            {'label': 'Invoice Access', 'value': 'Approved Sales Only'},
            {'label': 'Security', 'value': '6-digit password enabled'},
        ]
    }
    context.update(_customer_page_context('profile', 'Profile', 'Business details linked to this customer account.'))
    return render(request, 'home/customer/profile.html', context)


@permission_required_any('home.view_party')
def customer_security(request):
    party = _get_customer_party(request)
    if not party:
        return redirect('/')
    context = {
        'party': party,
        'customer_bottom_nav_items': _customer_bottom_nav_items(),
    }
    context.update(_customer_page_context('security', 'Security', 'Update the 6-digit portal password for this account.'))
    return render(request, 'home/customer/security.html', context)


@permission_required_any('home.view_party')
def customer_ledger_kpis(request):
    if request.method != 'GET':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    party = _get_customer_party(request, fields=['openingBalance'])
    if not party:
        return JsonResponse({'message': 'error', 'error': 'Party not found.'}, status=404)
    start_date, end_date, _today = _customer_date_range_from_request(request)
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


@permission_required_any('home.view_party')
def customer_active_delivery_otp(request):
    if request.method != 'GET':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
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
    def _format_local_delivery_dt(raw_value):
        if not raw_value:
            return ''
        dt_value = raw_value
        if isinstance(raw_value, str):
            try:
                dt_value = datetime.fromisoformat(raw_value)
            except Exception:
                return ''
        if timezone.is_naive(dt_value):
            dt_value = timezone.make_aware(dt_value)
        return timezone.localtime(dt_value).strftime('%d %b %Y, %I:%M %p')
    def _normalize_delivery_dt(raw_value):
        if not raw_value:
            return None
        dt_value = raw_value
        if isinstance(raw_value, str):
            try:
                dt_value = datetime.fromisoformat(raw_value)
            except Exception:
                return None
        if timezone.is_naive(dt_value):
            dt_value = timezone.make_aware(dt_value)
        return dt_value
    status_payload = get_order_delivery_status_payload(tenant_id, party_id)
    if status_payload:
        status = str(status_payload.get('status') or '').strip()
        created_ts = status_payload.get('created_ts')
        ttl = int(status_payload.get('ttl') or 0)
        meta = status_payload.get('meta') if isinstance(status_payload.get('meta'), dict) else {}
        try:
            created_ts = int(created_ts)
        except Exception:
            created_ts = None
        if status and created_ts is not None and ttl > 0:
            now_ts = int(timezone.now().timestamp())
            expires_in = max(0, (created_ts + ttl) - now_ts)
            if expires_in > 0:
                return JsonResponse({
                    'message': 'success',
                    'active': False,
                    'status': status,
                    'status_expires_in': expires_in,
                    'orderID': meta.get('orderID', ''),
                    'orderNumber': meta.get('orderNumber', ''),
                    'verifiedOn': _format_local_delivery_dt(meta.get('verifiedOn')),
                }, safe=False)
    payload = get_order_delivery_otp_payload(tenant_id, party_id)
    if payload:
        otp = str(payload.get('otp') or '').strip()
        created_ts = payload.get('created_ts')
        ttl = int(payload.get('ttl') or OTP_TTL_SECONDS)
        meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
        try:
            created_ts = int(created_ts)
        except Exception:
            created_ts = None
        if otp and created_ts is not None:
            now_ts = int(timezone.now().timestamp())
            expires_in = max(0, (created_ts + ttl) - now_ts)
            if expires_in > 0:
                return JsonResponse({
                    'message': 'success',
                    'active': True,
                    'otp': otp,
                    'expires_in': expires_in,
                    'orderID': meta.get('orderID', ''),
                    'orderNumber': meta.get('orderNumber', ''),
                }, safe=False)
    return JsonResponse({'message': 'success', 'active': False}, safe=False)
@permission_required_any('home.add_collection', 'home.can_approve_collection')
def add_collection(request):
    capabilities = _collection_capabilities(request.user)
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
        'collection_capabilities': capabilities,
        'base_template': 'home/admin/index.html' if capabilities['can_filter_staff'] else 'home/collection/indexCollection.html',
        'collection_admin_mode': capabilities['can_filter_staff'],
        'collection_submit_url': reverse('homeApp:add_collection_by_admin_api') if capabilities['can_filter_staff'] else reverse('homeApp:add_collection_by_staff_api'),
    }
    return render(request, 'home/collection/addCollection.html', context)
@permission_required_any('home.view_collection', 'home.add_collection', 'home.change_collection', 'home.can_approve_collection')
def my_collection(request):
    return redirect('homeApp:collection_list')
# admin
@is_activated()
@permission_required_any('home.view_collection', 'home.add_collection', 'home.change_collection', 'home.can_approve_collection')
def collection_list(request):
    capabilities = _collection_capabilities(request.user)
    staffs = tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name') if capabilities['can_filter_staff'] else []
    context = {
        'staffs': staffs,
        'collection_capabilities': capabilities,
        'base_template': 'home/admin/index.html' if capabilities['can_filter_staff'] else 'home/collection/indexCollection.html',
        'collection_add_url': reverse('homeApp:add_collection'),
    }
    return render(request, 'home/collection/collectionList.html', context)
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
    return redirect('homeApp:add_collection')
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
        tenant = get_current_tenant(request)
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
            'date': datetime.today().date(),
            'showAttendanceLocationFeature': bool(getattr(tenant, 'enableAttendanceLocationMaps', False)),
        }
        return render(request, 'home/addTodayAttendance.html', context)
    else:
        return redirect('/')
def _attendance_report_capabilities(user):
    can_filter_staff = _tenant_has_perm(user, 'home.can_view_all_attendance') or _tenant_has_perm(user, 'home.can_view_attendance_reports')
    return {
        'can_filter_staff': can_filter_staff,
    }
@permission_required_any('home.view_attendance', 'home.add_attendance', 'home.change_attendance', 'home.can_view_all_attendance', 'home.can_view_attendance_reports')
def attendance_list(request):
    if request.user.is_authenticated:
        capabilities = _attendance_report_capabilities(request.user)
        context = {
            'attendance_capabilities': capabilities,
            'staffs': tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name') if capabilities['can_filter_staff'] else [],
        }
        return render(request, 'home/attendanceList.html', context)
    else:
        return redirect('/')
@permission_required_any('home.view_attendance', 'home.add_attendance', 'home.change_attendance')
def attendance_report(request):
    return redirect('homeApp:attendance_list')
@permission_required_any('home.can_view_all_attendance', 'home.can_view_attendance_reports')
def attendance_report_admin(request):
    return redirect('homeApp:attendance_list')


@permission_required_any('home.can_track_staff_location')
def staff_location_tracker(request):
    staff = get_object_or_404(tenant_qs(request, StaffUser), user_ID__pk=request.user.pk)
    return render(request, 'home/staffLocationTracker.html', {'staff': staff})


@permission_required_any(
    'home.can_view_staff_locations',
)
def staff_location_map(request):
    context = {
        'staffs': tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name'),
        'today': datetime.today().date(),
    }
    return render(request, 'home/staffLocationMap.html', context)
# ----------------------------- Sales ----------------------
@permission_required_any('home.add_sales')
def add_sales(request):
    tenant = get_current_tenant(request)
    source_order = None
    source_order_id = request.GET.get('order_id')
    if source_order_id:
        try:
            candidate = tenant_qs(request, Order).select_related('partyID').get(pk=int(source_order_id), isDeleted=False)
            if str(getattr(candidate, 'status', '') or '') in {
                Order.Status.APPROVED,
                Order.Status.PACKED,
                Order.Status.OUT_FOR_DELIVERY,
                Order.Status.DELIVERED,
                Order.Status.RETURNED,
                Order.Status.FULFILLED,
            }:
                source_order = candidate
        except Exception:
            source_order = None
    return render(request, 'home/sales/addSales.html', {
        'banks': tenant_qs(request, Bank).filter(isDeleted=False).order_by('name'),
        'inventory_enabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advanced_invoicing_enabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'seller_gst_number': getattr(tenant, 'gstNumber', '') or '',
        'sales_source_order': source_order,
    })
@is_activated()
@permission_required_any('home.view_sales', 'home.add_sales', 'home.change_sales', 'home.can_manage_sales')
def sales_list(request):
    capabilities = _sales_capabilities(request.user)
    staffs = tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name') if capabilities['can_filter_staff'] else []
    context = {
        'staffs': staffs,
        'sales_capabilities': capabilities,
    }
    return render(request, 'home/sales/salesList.html', context)
@permission_required_any('home.view_sales', 'home.add_sales', 'home.change_sales', 'home.can_manage_sales')
def my_sales_list(request):
    return redirect('homeApp:sales_list')
@permission_required_any('home.change_sales', 'home.can_manage_sales', 'home.can_approve_sales')
def edit_sales(request, id=None):
    obj = get_object_or_404(tenant_qs(request, Sales), pk=id)
    tenant = get_current_tenant(request)
    capabilities = _sales_capabilities(request.user)
    admin_like = capabilities['can_filter_staff'] or request.user.has_perm('home.can_view_party_credentials') or request.user.has_perm('home.can_approve_sales')
    if not admin_like:
        if not obj.createdBy_id or getattr(obj.createdBy, 'user_ID_id', None) != request.user.pk:
            return redirect('homeApp:sales_list')
    context = {
        'obj': obj,
        'invoice_display': obj.invoiceNumber,
        'sales_edit_readonly': bool(obj.isApproved and not admin_like),
        'sales_return_url': reverse('homeApp:sales_list'),
        'banks': tenant_qs(request, Bank).filter(isDeleted=False).order_by('name'),
        'sales_line_items': list(
            obj.lines.filter(isDeleted=False).select_related('item').values(
                'item_id', 'item__name', 'item__unitName', 'item__unit__allowsDecimal', 'stockBucket', 'quantity', 'unitPrice',
                'discountPercent', 'taxableAmount', 'gstRate', 'gstAmount', 'lineTotal', 'remark'
            )
        ),
        'inventory_enabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advanced_invoicing_enabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'seller_gst_number': getattr(tenant, 'gstNumber', '') or '',
    }
    return render(request, 'home/sales/editSales.html', context)


@permission_required_any('home.add_proformainvoice', 'home.can_manage_proforma_invoice')
def add_proforma_invoice(request):
    tenant = get_current_tenant(request)
    return render(request, 'home/proforma/addProforma.html', {
        'inventory_enabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advanced_invoicing_enabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'seller_gst_number': getattr(tenant, 'gstNumber', '') or '',
    })


@is_activated()
@permission_required_any(
    'home.view_proformainvoice',
    'home.add_proformainvoice',
    'home.change_proformainvoice',
    'home.can_manage_proforma_invoice',
    'home.can_convert_proforma_invoice',
    'home.can_view_proforma_invoice_reports',
)
def proforma_invoice_list(request):
    capabilities = _proforma_capabilities(request.user)
    staffs = tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name') if capabilities['can_filter_staff'] else []
    context = {
        'staffs': staffs,
        'proforma_capabilities': capabilities,
        'proforma_statuses': ProformaInvoice.Status.choices,
    }
    return render(request, 'home/proforma/proformaList.html', context)


@permission_required_any('home.change_proformainvoice', 'home.can_manage_proforma_invoice', 'home.can_convert_proforma_invoice')
def edit_proforma_invoice(request, id=None):
    obj = tenant_object_or_superuser(request, ProformaInvoice.objects.select_related('partyID', 'createdBy', 'tenant'), pk=id, isDeleted=False)
    if obj is None:
        raise Http404
    tenant = get_current_tenant(request)
    capabilities = _proforma_capabilities(request.user)
    admin_like = capabilities['can_filter_staff'] or capabilities['can_convert']
    if not admin_like:
        if not obj.createdBy_id or getattr(obj.createdBy, 'user_ID_id', None) != request.user.pk:
            return redirect('homeApp:proforma_invoice_list')
    can_manage_current = request.user.has_perm('home.can_manage_proforma_invoice')
    can_edit_current = bool(capabilities['can_edit'] and obj.status != ProformaInvoice.Status.CONVERTED and (can_manage_current or (obj.createdBy_id and getattr(obj.createdBy, 'user_ID_id', None) == request.user.pk)))
    context = {
        'obj': obj,
        'invoice_display': obj.invoiceNumber,
        'proforma_capabilities': capabilities,
        'proforma_edit_readonly': not can_edit_current,
        'proforma_can_edit_current': can_edit_current,
        'proforma_return_url': reverse('homeApp:proforma_invoice_list'),
        'proforma_statuses': ProformaInvoice.Status.choices,
        'proforma_line_items': list(
            obj.lines.filter(isDeleted=False).select_related('item').values(
                'item_id', 'item__name', 'item__unitName', 'item__unit__allowsDecimal', 'stockBucket', 'quantity', 'unitPrice',
                'discountPercent', 'taxableAmount', 'gstRate', 'gstAmount', 'lineTotal', 'remark'
            )
        ),
        'inventory_enabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advanced_invoicing_enabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'seller_gst_number': getattr(tenant, 'gstNumber', '') or '',
    }
    return render(request, 'home/proforma/editProforma.html', context)


@permission_required_any('home.add_inventoryitem', 'home.change_inventoryitem', 'home.view_inventoryitem', 'home.can_manage_inventory', 'home.can_view_party_credentials')
def inventory_list(request):
    return render(request, 'home/inventory/index.html')


@permission_required_any('home.add_purchaseinvoice', 'home.change_purchaseinvoice', 'home.view_purchaseinvoice', 'home.can_view_party_credentials')
def purchase_list(request):
    context = {
        'banks': tenant_qs(request, Bank).filter(isDeleted=False).order_by('name'),
    }
    return render(request, 'home/inventory/purchase.html', context)
# ----------------------------- Orders ----------------------
@permission_required_any('home.add_order')
def add_order(request):
    return render(request, 'home/orders/addOrder.html', {
        'order_capabilities': _order_capabilities(request.user),
        'staffs': tenant_qs(request, StaffUser).filter(isDeleted=False).order_by('name'),
    })
@is_activated()
@permission_required_any('home.view_order', 'home.add_order', 'home.change_order', 'home.can_manage_orders')
def orders_list(request):
    capabilities = _order_capabilities(request.user)
    staffs = tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name') if capabilities['can_filter_staff'] else []
    context = {
        'staffs': staffs,
        'order_capabilities': capabilities,
    }
    return render(request, 'home/orders/ordersList.html', context)
@permission_required_any('home.view_order', 'home.add_order', 'home.change_order', 'home.can_manage_orders')
def my_orders_list(request):
    return redirect('homeApp:orders_list')
@permission_required_any('home.change_order', 'home.can_manage_orders', 'home.can_approve_orders')
def edit_order(request, id=None):
    obj = get_object_or_404(tenant_qs(request, Order), pk=id)
    capabilities = _order_capabilities(request.user)
    admin_like = capabilities['can_filter_staff'] or request.user.has_perm('home.can_view_party_credentials') or request.user.has_perm('home.can_approve_orders') or request.user.has_perm('home.can_manage_orders')
    if not admin_like:
        if not obj.createdBy_id or getattr(obj.createdBy, 'user_ID_id', None) != request.user.pk:
            return redirect('homeApp:orders_list')
    attachments = tenant_qs(request, OrderAttachment).filter(isDeleted=False, order=obj).order_by('-id')
    context = {
        'obj': obj,
        'attachments': attachments,
        'staffs': tenant_qs(request, StaffUser).filter(isDeleted=False).order_by('name'),
        'order_capabilities': capabilities,
        'order_edit_readonly': bool(str(obj.status) not in {Order.Status.DRAFT, Order.Status.SUBMITTED} and not admin_like),
        'order_return_url': reverse('homeApp:orders_list'),
    }
    return render(request, 'home/orders/editOrder.html', context)
@permission_required_any('home.view_order', 'home.add_order', 'home.change_order', 'home.can_manage_orders')
def order_detail(request, id=None):
    obj = get_object_or_404(
        tenant_qs(request, Order).select_related('partyID', 'createdBy', 'approvedBy', 'assignedTo', 'linkedSales'),
        pk=id,
    )
    capabilities = _order_capabilities(request.user)
    admin_like = capabilities['can_filter_staff'] or request.user.has_perm('home.can_view_party_credentials') or request.user.has_perm('home.can_approve_orders') or request.user.has_perm('home.can_manage_orders')
    if not admin_like:
        if not obj.createdBy_id or getattr(obj.createdBy, 'user_ID_id', None) != request.user.pk:
            return redirect('homeApp:orders_list')
    attachments = tenant_qs(request, OrderAttachment).filter(isDeleted=False, order=obj).order_by('-id')
    status_events = tenant_qs(request, OrderStatusEvent).filter(isDeleted=False, order=obj).select_related('actor').order_by('-datetime')
    next_statuses = _order_allowed_next_statuses_view(obj)
    next_status_actions = [(status, _order_tracking_status_text(status)) for status in next_statuses]
    can_start_fulfillment = str(getattr(obj, 'status', '') or '') in {
        Order.Status.APPROVED,
        Order.Status.PACKED,
        Order.Status.OUT_FOR_DELIVERY,
        Order.Status.DELIVERED,
        Order.Status.RETURNED,
        Order.Status.FULFILLED,
    }
    context = {
        'obj': obj,
        'attachments': attachments,
        'status_events': status_events,
        'order_capabilities': capabilities,
        'is_admin_like': admin_like,
        'can_approve_order': capabilities.get('can_approve', False) and str(obj.status) != Order.Status.APPROVED,
        'can_edit_order': capabilities.get('can_edit', False) and str(obj.status) in {Order.Status.DRAFT, Order.Status.SUBMITTED},
        'can_transition_order': capabilities.get('can_approve', False) or capabilities.get('can_edit', False),
        'next_status_actions': next_status_actions,
        'can_link_sale': bool((capabilities.get('can_approve', False) or capabilities.get('can_edit', False)) and can_start_fulfillment),
        'order_return_url': reverse('homeApp:orders_list'),
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


def _business_filters_context(request):
    today = datetime.today().date().strftime('%d/%m/%Y')
    return {
        'staffs': tenant_qs(request, StaffUser).filter(isDeleted=False).order_by('name'),
        'parties': tenant_qs(request, Party).filter(isDeleted=False).order_by('name'),
        'defaultStartDate': today,
        'defaultEndDate': today,
    }


def _render_business_analytics(request, title, api_name, primary_title='Primary', secondary_title='Secondary'):
    context = _business_filters_context(request)
    context.update({
        'page_title': title,
        'api_url': reverse(api_name),
        'primary_title': primary_title,
        'secondary_title': secondary_title,
    })
    return render(request, 'home/business/analytics_board.html', context)


@is_activated()
@permission_required_any('home.can_view_party_credentials', 'home.can_view_sales_reports', 'home.can_view_collection_reports', 'home.can_view_cash_counter_reports')
def analytics_business_snapshot(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Business Snapshot',
        'api_url': reverse('homeApp:api_business_snapshot'),
        'primary_title': 'Top Sales Staff',
        'secondary_title': 'Top Collection Staff',
    })
    return render(request, 'home/business/business_snapshot.html', context)


@is_activated()
@permission_required_any('home.can_manage_sales', 'home.can_view_sales_reports', 'home.can_view_party_credentials')
def analytics_revenue_trend(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Revenue Trend',
        'api_url': reverse('homeApp:api_revenue_trend'),
        'export_actions': [
            {'label': 'PDF', 'url': reverse('homeApp:export_revenue_trend_pdf'), 'icon': 'file pdf outline', 'mode': 'range_group'},
            {'label': 'Excel', 'url': reverse('homeApp:export_revenue_trend_excel'), 'icon': 'file excel outline', 'mode': 'range_group'},
        ],
    })
    return render(request, 'home/business/revenue_trend.html', context)


@is_activated()
@permission_required_any('home.can_approve_collection', 'home.can_view_collection_reports', 'home.can_view_party_credentials')
def analytics_collection_trend(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Collection Trend',
        'api_url': reverse('homeApp:api_collection_trend'),
        'export_actions': [
            {'label': 'PDF', 'url': reverse('homeApp:export_collection_trend_pdf'), 'icon': 'file pdf outline', 'mode': 'range_group'},
            {'label': 'Excel', 'url': reverse('homeApp:export_collection_trend_excel'), 'icon': 'file excel outline', 'mode': 'range_group'},
        ],
    })
    return render(request, 'home/business/collection_trend.html', context)


@is_activated()
@permission_required_any('home.can_manage_parties', 'home.can_view_party_credentials')
def analytics_receivables(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Receivables',
        'api_url': reverse('homeApp:api_receivables'),
        'export_actions': [
            {'label': 'PDF', 'url': reverse('homeApp:export_receivables_pdf'), 'icon': 'file pdf outline', 'mode': 'range_only'},
            {'label': 'Excel', 'url': reverse('homeApp:export_receivables_excel'), 'icon': 'file excel outline', 'mode': 'range_only'},
        ],
    })
    return render(request, 'home/business/receivables.html', context)


@is_activated()
@permission_required_any('home.can_manage_staff', 'home.can_view_party_credentials')
def analytics_staff_productivity(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Staff Productivity',
        'api_url': reverse('homeApp:api_staff_productivity'),
        'export_actions': [
            {'label': 'PDF', 'url': reverse('homeApp:export_staff_productivity_pdf'), 'icon': 'file pdf outline', 'mode': 'range_only'},
            {'label': 'Excel', 'url': reverse('homeApp:export_staff_productivity_excel'), 'icon': 'file excel outline', 'mode': 'range_only'},
        ],
    })
    return render(request, 'home/business/staff_productivity.html', context)


@is_activated()
@permission_required_any('home.can_manage_books', 'home.can_view_party_credentials')
def analytics_cash_position(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Cash Position',
        'api_url': reverse('homeApp:api_cash_position'),
    })
    return render(request, 'home/business/cash_position.html', context)


@is_activated()
@permission_required_any('home.can_manage_orders', 'home.can_view_order_reports', 'home.can_view_party_credentials')
def analytics_order_pipeline(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Order Pipeline',
        'api_url': reverse('homeApp:api_order_pipeline'),
        'export_actions': [
            {'label': 'PDF', 'url': reverse('homeApp:export_order_pipeline_pdf'), 'icon': 'file pdf outline', 'mode': 'range_group'},
            {'label': 'Excel', 'url': reverse('homeApp:export_order_pipeline_excel'), 'icon': 'file excel outline', 'mode': 'range_group'},
        ],
    })
    return render(request, 'home/business/order_pipeline.html', context)


@is_activated()
@permission_required_any('home.can_view_party_credentials', 'home.can_approve_collection', 'home.can_approve_sales', 'home.can_approve_orders')
def analytics_exception_dashboard(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Exception Dashboard',
        'api_url': reverse('homeApp:api_exception_dashboard'),
    })
    return render(request, 'home/business/exceptions.html', context)


@is_activated()
@permission_required_any('home.can_view_party_credentials', 'home.can_view_sales_reports', 'home.can_view_collection_reports', 'home.can_view_cash_counter_reports')
def report_daily_summary(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Daily Summary Report',
        'api_url': reverse('homeApp:api_business_snapshot'),
        'primary_title': 'Top Sales Staff',
        'secondary_title': 'Top Collection Staff',
        'page_subtitle': 'Owner-facing daily summary with money, activity, and exceptions in one place.',
        'report_actions': [
            {'label': 'Sales List', 'url': reverse('homeApp:sales_list'), 'icon': 'shopping cart'},
            {'label': 'Collection List', 'url': reverse('homeApp:collection_list'), 'icon': 'hand holding usd'},
            {'label': 'Cash Counter', 'url': reverse('homeApp:my_cash_counter_list'), 'icon': 'calendar alternate'},
        ],
        'export_actions': [
            {'label': 'Daily Summary PDF', 'url': reverse('homeApp:generate_daily_summary_pdf'), 'icon': 'print', 'mode': 'single_day_all'},
            {'label': 'Daily Summary Excel', 'url': reverse('homeApp:generate_daily_summary_excel'), 'icon': 'file excel outline', 'mode': 'single_day_all'},
            {'label': 'Monthly Summary Excel', 'url': reverse('homeApp:generate_monthly_business_summary_excel'), 'icon': 'calendar alternate outline', 'mode': 'single_day_all'},
            {'label': 'Sales PDF', 'url': reverse('homeApp:generate_sales_report'), 'icon': 'file pdf outline', 'mode': 'single_day_all'},
            {'label': 'Sales Excel', 'url': reverse('homeApp:generate_sales_report_excel'), 'icon': 'file excel outline', 'mode': 'single_day_all'},
            {'label': 'Collection PDF', 'url': reverse('homeApp:generate_collection_report'), 'icon': 'file pdf outline', 'mode': 'single_day_all'},
            {'label': 'Collection Excel', 'url': reverse('homeApp:generate_collection_report_excel'), 'icon': 'file excel outline', 'mode': 'single_day_all'},
            {'label': 'Cash Counter PDF', 'url': reverse('homeApp:generate_cash_counter_report'), 'icon': 'file pdf outline', 'mode': 'single_day_all'},
            {'label': 'Cash Counter Excel', 'url': reverse('homeApp:generate_cash_counter_report_excel'), 'icon': 'file excel outline', 'mode': 'single_day_all'},
        ],
        'export_help': 'Quick PDFs use the selected end date. Keep this report on a single day when you need a print-ready daybook.',
    })
    return render(request, 'home/business/daily_summary.html', context)


@is_activated()
@permission_required_any('home.can_manage_staff', 'home.can_view_party_credentials')
def report_staff_performance(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Staff Performance Report',
        'api_url': reverse('homeApp:api_staff_productivity'),
        'page_subtitle': 'Compare staff output across sales, collections, orders, and attendance-backed efficiency.',
        'report_actions': [
            {'label': 'Attendance List', 'url': reverse('homeApp:attendance_list'), 'icon': 'calendar alternate'},
            {'label': 'Staff List', 'url': reverse('homeApp:user_list'), 'icon': 'users'},
        ],
        'export_actions': [
            {'label': 'PDF', 'url': reverse('homeApp:export_staff_productivity_pdf'), 'icon': 'file pdf outline', 'mode': 'range_only'},
            {'label': 'Excel', 'url': reverse('homeApp:export_staff_productivity_excel'), 'icon': 'file excel outline', 'mode': 'range_only'},
        ],
        'export_help': 'Use the selected date range to export a printable or spreadsheet-friendly staff performance summary.',
    })
    return render(request, 'home/business/staff_performance_report.html', context)


@is_activated()
@permission_required_any('home.can_manage_parties', 'home.can_view_party_credentials')
def report_customer_outstanding(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Customer Outstanding Report',
        'api_url': reverse('homeApp:api_receivables'),
        'page_subtitle': 'Outstanding balances, aging pressure, and assigned follow-up responsibility.',
        'report_actions': [
            {'label': 'Party List', 'url': reverse('homeApp:party_list'), 'icon': 'address book'},
            {'label': 'Customer Ledger', 'url': reverse('homeApp:report_party_ledger'), 'icon': 'book'},
        ],
        'export_actions': [
            {'label': 'PDF', 'url': reverse('homeApp:export_receivables_pdf'), 'icon': 'file pdf outline', 'mode': 'range_only'},
            {'label': 'Excel', 'url': reverse('homeApp:export_receivables_excel'), 'icon': 'file excel outline', 'mode': 'range_only'},
        ],
        'export_help': 'Exports use the selected end date as the receivables cut-off.',
    })
    return render(request, 'home/business/customer_outstanding_report.html', context)


@is_activated()
@permission_required_any('home.can_manage_parties', 'home.can_view_party_credentials', 'home.view_party')
def report_customer_ledger(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Customer Ledger Report',
        'page_subtitle': 'Select a customer, keep the date range tight, and either open the live ledger or export the PDF immediately.',
        'ledger_page_url_template': reverse('homeApp:party_ledger', args=[0]),
        'ledger_pdf_url_template': reverse('homeApp:generate_party_ledger_pdf', args=[0]),
        'ledger_excel_url_template': reverse('homeApp:generate_party_ledger_excel', args=[0]),
    })
    return render(request, 'home/business/customer_ledger_report.html', context)


@is_activated()
@permission_required_any('home.can_approve_collection', 'home.can_view_collection_reports', 'home.can_view_party_credentials')
def report_collection_business(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Collection Report',
        'page_subtitle': 'Open the detailed collection list to filter, review approvals, and export the report.',
        'open_url': reverse('homeApp:collection_list'),
        'download_label': 'Open Collection List',
        'filter_help': 'Pick one collection date and, if needed, narrow the export to a specific collector. Leave staff as All for a full-day report.',
        'show_staff_filter': True,
        'export_actions': [
            {
                'label': 'Export PDF',
                'url': reverse('homeApp:generate_collection_report'),
                'icon': 'file pdf outline',
                'mode': 'single_date_staff',
            },
            {
                'label': 'Export Excel',
                'url': reverse('homeApp:generate_collection_report_excel'),
                'icon': 'file excel outline',
                'mode': 'single_date_staff',
            },
        ],
        'secondary_actions': [
            {'label': 'Cheque Reminders', 'url': reverse('homeApp:cheque_reminder_list'), 'icon': 'bell'},
        ],
    })
    return render(request, 'home/business/attendance_report.html', context)


@is_activated()
@permission_required_any('home.can_manage_sales', 'home.can_view_sales_reports', 'home.can_view_party_credentials')
def report_sales_business(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Sales Report',
        'page_subtitle': 'Open the detailed sales list to filter invoices and export the report.',
        'open_url': reverse('homeApp:sales_list'),
        'download_label': 'Open Sales List',
        'filter_help': 'Pick one invoice date and optionally export only one staff member. Leave staff as All for the full sales daybook.',
        'show_staff_filter': True,
        'export_actions': [
            {
                'label': 'Export PDF',
                'url': reverse('homeApp:generate_sales_report'),
                'icon': 'file pdf outline',
                'mode': 'single_date_staff',
            },
            {
                'label': 'Export Excel',
                'url': reverse('homeApp:generate_sales_report_excel'),
                'icon': 'file excel outline',
                'mode': 'single_date_staff',
            },
        ],
        'secondary_actions': [
            {'label': 'Add Sale', 'url': reverse('homeApp:add_sales'), 'icon': 'plus circle'},
        ],
    })
    return render(request, 'home/business/link_report.html', context)


@is_activated()
@permission_required_any('home.can_manage_cash_counter', 'home.can_view_cash_counter_reports', 'home.can_view_party_credentials')
def report_cash_closing(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Cash Closing Report',
        'api_url': reverse('homeApp:api_cash_position'),
        'page_subtitle': 'Cash and bank movement summary with quick access to book-level detail.',
        'report_actions': [
            {'label': 'Cashbook', 'url': reverse('homeApp:cashbook'), 'icon': 'book'},
            {'label': 'Bankbook', 'url': reverse('homeApp:bankbook'), 'icon': 'university'},
            {'label': 'Cash Counter List', 'url': reverse('homeApp:my_cash_counter_list'), 'icon': 'calendar alternate'},
        ],
        'export_actions': [
            {'label': 'Cash Counter PDF', 'url': reverse('homeApp:generate_cash_counter_report'), 'icon': 'file pdf outline', 'mode': 'single_day_all'},
            {'label': 'Cash Counter Excel', 'url': reverse('homeApp:generate_cash_counter_report_excel'), 'icon': 'file excel outline', 'mode': 'single_day_all'},
            {'label': 'Book Statements', 'url': reverse('homeApp:report_bank_cash_movement'), 'icon': 'exchange', 'mode': 'link'},
        ],
        'export_help': 'Use the selected end date for cash-counter closing. For full book statements across a date range, open the book-statement hub.',
    })
    return render(request, 'home/business/cash_closing_report.html', context)


@is_activated()
@permission_required_any('home.can_manage_orders', 'home.can_view_order_reports', 'home.can_view_party_credentials')
def report_order_status(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Order Status Report',
        'api_url': reverse('homeApp:api_order_pipeline'),
        'primary_title': 'Status Split',
        'secondary_title': 'Top Staff',
        'page_subtitle': 'Track order status flow and jump into the detailed order screens for action.',
        'report_actions': [
            {'label': 'Orders List', 'url': reverse('homeApp:orders_list'), 'icon': 'clipboard list'},
            {'label': 'Add Order', 'url': reverse('homeApp:add_order'), 'icon': 'plus circle'},
        ],
        'export_actions': [
            {'label': 'PDF', 'url': reverse('homeApp:export_order_pipeline_pdf'), 'icon': 'file pdf outline', 'mode': 'range_group'},
            {'label': 'Excel', 'url': reverse('homeApp:export_order_pipeline_excel'), 'icon': 'file excel outline', 'mode': 'range_group'},
        ],
        'report_help': 'This report is best used as a live operational tracker. Open the orders list for row-level review and per-order PDF export.',
    })
    return render(request, 'home/business/order_status_report.html', context)


@is_activated()
@permission_required_any('home.can_view_all_attendance', 'home.can_view_attendance_reports', 'home.can_view_party_credentials')
def report_attendance_business(request):
    context = _business_filters_context(request)
    can_export_admin_attendance = request.user.has_perm('home.can_view_all_attendance') or request.user.has_perm('home.can_view_party_credentials')
    export_actions = []
    if can_export_admin_attendance:
        export_actions.append({
            'label': 'Export Admin PDF',
            'url': reverse('homeApp:generate_attendance_pdf_admin_report'),
            'icon': 'file pdf outline',
            'mode': 'range_staff',
        })
        export_actions.append({
            'label': 'Export Admin Excel',
            'url': reverse('homeApp:generate_attendance_excel_admin_report'),
            'icon': 'file excel outline',
            'mode': 'range_staff',
        })
        export_actions.append({
            'label': 'Monthly View PDF',
            'url': reverse('homeApp:generate_attendance_monthly_pdf_admin_report'),
            'icon': 'calendar',
            'mode': 'month_staff',
        })
        export_actions.append({
            'label': 'Monthly View Excel',
            'url': reverse('homeApp:generate_attendance_monthly_excel_admin_report'),
            'icon': 'file excel outline',
            'mode': 'month_staff',
        })
    export_actions.append({
        'label': 'Export My Attendance PDF',
        'url': reverse('homeApp:generate_attendance_pdf_staff_report'),
        'icon': 'user clock',
        'mode': 'range_only',
    })
    export_actions.append({
        'label': 'Export My Attendance Excel',
        'url': reverse('homeApp:generate_attendance_excel_staff_report'),
        'icon': 'file excel outline',
        'mode': 'range_only',
    })
    if not can_export_admin_attendance:
        export_actions.append({
            'label': 'Monthly View PDF',
            'url': reverse('homeApp:generate_attendance_monthly_pdf_staff_report'),
            'icon': 'calendar',
            'mode': 'month_only',
        })
        export_actions.append({
            'label': 'Monthly View Excel',
            'url': reverse('homeApp:generate_attendance_monthly_excel_staff_report'),
            'icon': 'file excel outline',
            'mode': 'month_only',
        })
    context.update({
        'page_title': 'Attendance Report',
        'page_subtitle': 'Open the attendance screen to filter records and export staff/admin attendance PDFs.',
        'open_url': reverse('homeApp:attendance_list'),
        'download_label': 'Open Attendance List',
        'filter_help': 'Use a date range for payroll or compliance checks. Admin export can target one staff member or All.',
        'show_end_date': True,
        'show_staff_filter': can_export_admin_attendance,
        'export_actions': export_actions,
    })
    return render(request, 'home/business/link_report.html', context)


@is_activated()
@permission_required_any('home.can_approve_collection', 'home.can_approve_sales', 'home.can_approve_orders', 'home.can_view_party_credentials')
def report_pending_approval(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Pending Approval Report',
        'api_url': reverse('homeApp:api_exception_dashboard'),
        'page_subtitle': 'Review all approval backlogs and route into the operational lists that need action.',
        'report_actions': [
            {'label': 'Sales List', 'url': reverse('homeApp:sales_list'), 'icon': 'shopping cart'},
            {'label': 'Collection List', 'url': reverse('homeApp:collection_list'), 'icon': 'hand holding usd'},
            {'label': 'Orders List', 'url': reverse('homeApp:orders_list'), 'icon': 'clipboard list'},
            {'label': 'Excel', 'url': reverse('homeApp:export_pending_approvals_excel'), 'icon': 'file excel outline'},
        ],
        'report_help': 'Use this screen to spot approval backlog and then jump into the exact operational queue that needs action.',
    })
    return render(request, 'home/business/pending_approval_report.html', context)


@is_activated()
@permission_required_any('home.can_manage_books', 'home.can_view_party_credentials')
def report_bank_cash_movement(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Bank/Cash Movement Report',
        'page_subtitle': 'Open the detailed cashbook and bankbook statements from a single report hub.',
        'cashbook_url': reverse('homeApp:cashbook'),
        'bankbook_url': reverse('homeApp:bankbook'),
        'cashbook_pdf_url': reverse('homeApp:generate_cashbook_pdf'),
        'cashbook_excel_url': reverse('homeApp:generate_cashbook_excel'),
        'bankbook_pdf_url': reverse('homeApp:generate_bankbook_pdf'),
        'bankbook_excel_url': reverse('homeApp:generate_bankbook_excel'),
        'cash_accounts': tenant_qs(request, BookAccount).filter(isDeleted=False, type=BookAccount.Type.CASH).order_by('-isDefault', 'name'),
        'bank_accounts': tenant_qs(request, BookAccount).filter(isDeleted=False, type=BookAccount.Type.BANK).select_related('bank').order_by('name'),
    })
    return render(request, 'home/business/bank_cash_movement_report.html', context)


@is_activated()
@permission_required_any('home.view_whatsappmessagestatus', 'home.add_whatsappmessagestatus', 'home.change_whatsappmessagestatus', 'home.delete_whatsappmessagestatus')
def report_failed_message(request):
    context = _business_filters_context(request)
    context.update({
        'page_title': 'Failed Message Report',
        'api_url': reverse('homeApp:api_failed_messages'),
        'page_subtitle': 'Review failed delivery attempts and then continue into the full message history screen.',
        'report_actions': [
            {'label': 'Message List', 'url': reverse('homeApp:message_list'), 'icon': 'comment dots outline'},
            {'label': 'Excel', 'url': reverse('homeApp:export_failed_messages_excel'), 'icon': 'file excel outline'},
        ],
    })
    return render(request, 'home/business/failed_message_report.html', context)
# -----------------------CashCounter---------------------
@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter')
def cash_counter_home(request):
    return render(request, 'home/cashCounter/indexCashCounter.html')
@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter')
def cash_counter(request):
    tenant = get_current_tenant(request)
    banks = tenant_qs(request, Bank).filter(isDeleted__exact=False).order_by('name')
    context = {
        'banks': banks,
        'inventory_enabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advanced_invoicing_enabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'seller_gst_number': getattr(tenant, 'gstNumber', '') or '',
    }
    return render(request, 'home/cashCounter/todaysCounter.html', context)
@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter', 'home.view_collectioncashcounter')
def my_cash_counter_list(request):
    return render(request, 'home/cashCounter/cashCounterListByStaff.html', {
        'cash_counter_capabilities': _cash_counter_capabilities(request.user),
    })
@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter')
def edit_cash_counter(request, id=None):
    obj = get_object_or_404(
        tenant_qs(request, CashCounter).select_related('linkedSalesReturnVoucher__sales', 'linkedSalesReturnVoucher__cashCounter'),
        pk=id,
    )
    tenant = get_current_tenant(request)
    banks = tenant_qs(request, Bank).filter(isDeleted__exact=False).order_by('name')
    context = {
        'obj': obj,
        'invoice_display': obj.invoiceNumber or "",
        'banks': banks,
        'cash_counter_line_items': list(
            obj.lines.filter(isDeleted=False).select_related('item').values(
                'item_id', 'item__name', 'item__unitName', 'item__unit__allowsDecimal', 'stockBucket', 'quantity',
                'unitPrice', 'hsnCode', 'discountPercent', 'taxableAmount', 'gstRate', 'gstAmount', 'lineTotal', 'remark'
            )
        ),
        'cash_counter_return_source_id': (
            f"sale:{obj.linkedSalesReturnVoucher.sales_id}"
            if getattr(getattr(obj, 'linkedSalesReturnVoucher', None), 'sales_id', None)
            else (
                f"cash_counter:{obj.linkedSalesReturnVoucher.cashCounter_id}"
                if getattr(getattr(obj, 'linkedSalesReturnVoucher', None), 'cashCounter_id', None)
                else None
            )
        ),
        'cash_counter_return_voucher_id': getattr(getattr(obj, 'linkedSalesReturnVoucher', None), 'pk', None),
        'inventory_enabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advanced_invoicing_enabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'seller_gst_number': getattr(tenant, 'gstNumber', '') or '',
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
