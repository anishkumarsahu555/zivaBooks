import csv
import os
from datetime import datetime, timedelta
from functools import wraps

from django.contrib.auth import logout, authenticate, login, update_session_auth_hash
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.db.models import Q, Sum
from django.db.models.functions import Length
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
# Create your views here.
from django.views.decorators.csrf import csrf_exempt

# import xlwt
from activation.models import *
from activation.views import is_activated
from .models import *
from .books import ensure_books_for_tenant
from .navigation import build_sidebar_sections
from .tenancy import assign_tenant, get_current_tenant, get_staff_user_for_request, tenant_qs


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


def _user_has_portal_access(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    if build_sidebar_sections(user):
        return True
    return user.has_module_perms('home') or user.has_module_perms('activation')


def _permission_catalog():
    hidden_content_types = {
        ('activation', 'ecomvalidity'),
        ('activation', 'validity'),
        ('home', 'auditlog'),
        ('home', 'geolocationpackage'),
        ('home', 'tenant'),
        ('home', 'tenantmembership'),
        ('home', 'whatsappmessage'),
        ('home', 'whatsappmessagestatus'),
    }
    catalog = {}
    for permission in Permission.objects.select_related('content_type').exclude(
        content_type__app_label__in=['admin', 'auth', 'contenttypes', 'sessions']
    ).order_by('content_type__app_label', 'content_type__model', 'name'):
        if (permission.content_type.app_label, permission.content_type.model) in hidden_content_types:
            continue
        module = permission.content_type.model.replace('_', ' ').title()
        if permission.codename.startswith('can_'):
            module = 'Custom ' + module
        catalog.setdefault(module, []).append(permission)
    return catalog


STAFF_GROUP_PRESET_DEFS = [
    {
        'key': 'sales_full',
        'label': 'Sales FullList',
        'description': 'Full sales access with FullList, approve, delete, and reports.',
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
        'label': 'Sales Add + MyList',
        'description': 'Can add new sales and work only with MyList records.',
        'permissions': [
            'home.view_sales',
            'home.add_sales',
        ],
    },
    {
        'key': 'sales_add_edit_list',
        'label': 'Sales Add + Edit + MyList',
        'description': 'Can create, update, and view only MyList sales without delete or approval.',
        'permissions': [
            'home.view_sales',
            'home.add_sales',
            'home.change_sales',
        ],
    },
    {
        'key': 'sales_view_only',
        'label': 'Sales View MyList',
        'description': 'Read-only access to MyList sales.',
        'permissions': [
            'home.view_sales',
        ],
    },
    {
        'key': 'orders_full',
        'label': 'Orders FullList',
        'description': 'Full orders access with FullList, approve, delete, and reports.',
        'permissions': [
            'home.view_order',
            'home.add_order',
            'home.change_order',
            'home.delete_order',
            'home.can_manage_orders',
            'home.can_approve_orders',
            'home.can_view_order_reports',
        ],
    },
    {
        'key': 'orders_add_list',
        'label': 'Orders Add + MyList',
        'description': 'Can add new orders and work only with MyList records.',
        'permissions': [
            'home.view_order',
            'home.add_order',
        ],
    },
    {
        'key': 'orders_add_edit_list',
        'label': 'Orders Add + Edit + MyList',
        'description': 'Can create, update, and view only MyList orders before approval.',
        'permissions': [
            'home.view_order',
            'home.add_order',
            'home.change_order',
        ],
    },
    {
        'key': 'orders_view_only',
        'label': 'Orders View MyList',
        'description': 'Read-only access to MyList orders.',
        'permissions': [
            'home.view_order',
        ],
    },
    {
        'key': 'collection_full',
        'label': 'Collection FullList',
        'description': 'Full collection workflow with FullList, approval, tally, and reports.',
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
        'label': 'Collection Add + MyList',
        'description': 'Can create and view only MyList collections.',
        'permissions': [
            'home.view_collection',
            'home.add_collection',
        ],
    },
    {
        'key': 'collection_add_edit_list',
        'label': 'Collection Add + Edit + MyList',
        'description': 'Can create, update, and view only MyList collections.',
        'permissions': [
            'home.view_collection',
            'home.add_collection',
            'home.change_collection',
        ],
    },
    {
        'key': 'collection_view_only',
        'label': 'Collection View MyList',
        'description': 'Read-only access to MyList collections.',
        'permissions': [
            'home.view_collection',
        ],
    },
    {
        'key': 'cash_counter_full',
        'label': 'Cash Counter FullList',
        'description': 'Full cash counter workflow with FullList and counter collection access.',
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
        'label': 'Cash Counter Add + MyList',
        'description': 'Can create and view only MyList cash counter records.',
        'permissions': [
            'home.view_cashcounter',
            'home.add_cashcounter',
            'home.view_collectioncashcounter',
            'home.add_collectioncashcounter',
        ],
    },
    {
        'key': 'cash_counter_add_edit_list',
        'label': 'Cash Counter Add + Edit + MyList',
        'description': 'Can create, update, and view only MyList cash counter records.',
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
        'label': 'Cash Counter View MyList',
        'description': 'Read-only access to MyList cash counter records.',
        'permissions': [
            'home.view_cashcounter',
            'home.view_collectioncashcounter',
        ],
    },
    {
        'key': 'party_full',
        'label': 'Party FullList',
        'description': 'Full party + bank management with credentials access.',
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
        'key': 'party_manage',
        'label': 'Party Manage FullList',
        'description': 'Manage party + bank lists without credential visibility.',
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
        'label': 'Party List FullList',
        'description': 'View-only party, party group, and bank lists.',
        'permissions': [
            'home.can_manage_parties',
            'home.view_party',
            'home.view_partygroup',
            'home.view_bank',
        ],
    },
    {
        'key': 'attendance_full',
        'label': 'Attendance FullList',
        'description': 'Full attendance access with all-staff visibility and reports.',
        'permissions': [
            'home.view_attendance',
            'home.add_attendance',
            'home.change_attendance',
            'home.can_view_all_attendance',
            'home.can_view_attendance_reports',
        ],
    },
    {
        'key': 'attendance_add_edit_list',
        'label': 'Attendance Add + Edit + MyList',
        'description': 'Mark and update your own attendance records.',
        'permissions': [
            'home.view_attendance',
            'home.add_attendance',
            'home.change_attendance',
        ],
    },
    {
        'key': 'attendance_add_list',
        'label': 'Attendance Add + MyList',
        'description': 'Mark your attendance and view your own records.',
        'permissions': [
            'home.view_attendance',
            'home.add_attendance',
        ],
    },
    {
        'key': 'attendance_view_only',
        'label': 'Attendance View MyList',
        'description': 'Read-only access to your own attendance records.',
        'permissions': [
            'home.view_attendance',
        ],
    },
    {
        'key': 'staff_full',
        'label': 'Staff FullList',
        'description': 'Full staff and group administration.',
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
        'key': 'staff_groups_full',
        'label': 'Staff Groups FullList',
        'description': 'Create and maintain staff groups and permissions.',
        'permissions': [
            'home.can_manage_staff_groups',
            'home.view_staffgroup',
            'home.add_staffgroup',
            'home.change_staffgroup',
            'home.delete_staffgroup',
        ],
    },
    {
        'key': 'staff_users_full',
        'label': 'Staff Users FullList',
        'description': 'Manage staff users and their group assignment.',
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
]


def _staff_group_presets():
    permission_lookup = {
        f'{permission.content_type.app_label}.{permission.codename}': permission
        for permission in Permission.objects.select_related('content_type').all()
    }
    presets = []
    for preset in STAFF_GROUP_PRESET_DEFS:
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


def _preset_reference_sections():
    sections = {}
    for preset in _staff_group_presets():
        module_name = preset['label'].split(' ', 1)[0]
        sections.setdefault(module_name, []).append(preset)
    return sections


def _is_admin_like(user):
    return _has_any_permission(user, [
        'home.can_manage_staff',
        'home.can_manage_staff_groups',
        'home.can_manage_parties',
        'home.can_view_party_credentials',
        'home.can_approve_collection',
        'home.can_view_audit_logs',
    ])


def _sales_capabilities(user):
    can_add = user.has_perm('home.add_sales')
    can_edit = user.has_perm('home.change_sales') or user.has_perm('home.can_manage_sales') or user.has_perm('home.can_view_party_credentials')
    can_delete = user.has_perm('home.can_view_party_credentials')
    can_message = user.has_perm('home.can_manage_sales') or user.has_perm('home.can_approve_sales') or user.has_perm('home.can_view_party_credentials')
    can_export = user.has_perm('home.can_view_sales_reports') or user.has_perm('home.can_view_party_credentials') or user.has_perm('home.can_view_audit_logs')
    can_filter_staff = _is_admin_like(user) or user.has_perm('home.can_manage_sales')
    return {
        'can_add': can_add,
        'can_edit': can_edit,
        'can_delete': can_delete,
        'can_message': can_message,
        'can_export': can_export,
        'can_filter_staff': can_filter_staff,
    }


def _order_capabilities(user):
    can_add = user.has_perm('home.add_order')
    can_edit = user.has_perm('home.change_order') or user.has_perm('home.can_manage_orders') or user.has_perm('home.can_view_party_credentials')
    can_delete = user.has_perm('home.delete_order') or user.has_perm('home.can_view_party_credentials')
    can_approve = user.has_perm('home.can_approve_orders') or user.has_perm('home.can_manage_orders') or user.has_perm('home.can_view_party_credentials')
    can_filter_staff = _is_admin_like(user) or user.has_perm('home.can_manage_orders') or user.has_perm('home.can_view_party_credentials')
    can_set_order_date = user.has_perm('home.can_manage_orders') or user.has_perm('home.can_view_party_credentials') or user.has_perm('home.can_approve_orders')
    return {
        'can_add': can_add,
        'can_edit': can_edit,
        'can_delete': can_delete,
        'can_approve': can_approve,
        'can_filter_staff': can_filter_staff,
        'can_set_order_date': can_set_order_date,
    }


def _collection_capabilities(user):
    can_add = user.has_perm('home.add_collection')
    can_edit = user.has_perm('home.change_collection') or user.has_perm('home.can_approve_collection') or user.has_perm('home.can_view_party_credentials')
    can_delete = user.has_perm('home.can_view_party_credentials')
    can_export = user.has_perm('home.can_view_collection_reports') or user.has_perm('home.can_view_party_credentials') or user.has_perm('home.can_view_audit_logs')
    can_filter_staff = user.has_perm('home.can_approve_collection') or user.has_perm('home.can_view_party_credentials')
    return {
        'can_add': can_add,
        'can_edit': can_edit,
        'can_delete': can_delete,
        'can_export': can_export,
        'can_filter_staff': can_filter_staff,
    }


def _cash_counter_capabilities(user):
    can_add = user.has_perm('home.add_cashcounter') or user.has_perm('home.add_collectioncashcounter') or user.has_perm('home.can_manage_cash_counter')
    can_edit = user.has_perm('home.change_cashcounter') or user.has_perm('home.change_collectioncashcounter') or user.has_perm('home.can_manage_cash_counter') or user.has_perm('home.can_view_party_credentials')
    can_delete = user.has_perm('home.can_view_party_credentials')
    can_export = user.has_perm('home.can_view_cash_counter_reports') or user.has_perm('home.can_view_party_credentials') or user.has_perm('home.can_view_audit_logs')
    can_filter_staff = user.has_perm('home.can_manage_cash_counter') or user.has_perm('home.can_view_party_credentials') or user.has_perm('home.can_approve_collection')
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
            if request.user.is_authenticated and _has_any_permission(request.user, perm_names):
                return view_func(request, *args, **kwargs)
            return redirect('/')

        return wrapper

    return decorator


def loginPage(request):
    # if not request.user.is_authenticated:
    return render(request, 'home/login.html')
    # else:
    #     return redirect('/admin_home/')


@permission_required_any('home.can_view_party_credentials', 'home.can_manage_staff')
def admin_home(request):
    try:
        val = tenant_qs(request, Validity).last()
        message = "Your App License is Valid till {}".format(val.expiryDate.strftime('%d-%m-%Y'))
    except:
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
        'permission_catalog': _permission_catalog(),
    }
    return render(request, 'home/admin/staffGroupList.html', context)


@permission_required_any('home.can_manage_staff_groups')
@is_activated()
def permission_reference(request):
    context = {
        'preset_sections': _preset_reference_sections(),
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
        'permission_catalog': _permission_catalog(),
        'permission_presets': _staff_group_presets(),
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
    visible_permission_ids = {permission.id for perms in _permission_catalog().values() for permission in perms}
    selected_permission_ids = list(group.djangoGroup.permissions.values_list('id', flat=True)) if group.djangoGroup_id else []
    selected_permission_ids = [perm_id for perm_id in selected_permission_ids if perm_id in visible_permission_ids]
    selected_set = set(selected_permission_ids)
    presets = _staff_group_presets()
    selected_preset_keys = [
        preset['key'] for preset in presets
        if set(preset.get('permission_ids') or []).issubset(selected_set) and preset.get('permission_ids')
    ]
    context = {
        'page_mode': 'edit',
        'page_title': 'Edit Staff Group',
        'submit_label': 'Update Group',
        'group_obj': group,
        'permission_catalog': _permission_catalog(),
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
    hidden_content_types = {
        ('activation', 'ecomvalidity'),
        ('activation', 'validity'),
        ('home', 'auditlog'),
        ('home', 'geolocationpackage'),
        ('home', 'tenant'),
        ('home', 'tenantmembership'),
        ('home', 'whatsappmessage'),
        ('home', 'whatsappmessagestatus'),
    }
    assigned_permissions = [
        permission for permission in assigned_permissions
        if (permission.content_type.app_label, permission.content_type.model) not in hidden_content_types
    ]
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


@permission_required_any('home.can_manage_parties', 'home.can_view_party_credentials')
@is_activated()
def bank_list(request):
    can_view_credentials = request.user.has_perm('home.can_view_party_credentials')
    context = {
        'bank_capabilities': {
            'can_add': request.user.has_perm('home.add_bank') or can_view_credentials,
            'can_edit': request.user.has_perm('home.change_bank') or can_view_credentials,
            'can_delete': request.user.has_perm('home.delete_bank') or can_view_credentials,
        },
        'can_view_bank_balances': can_view_credentials,
    }
    return render(request, 'home/admin/BankList.html', context)


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


def my_profile(request):
    instance = get_object_or_404(tenant_qs(request, StaffUser), user_ID_id=request.user.pk)
    context = {
        'instance': instance
    }
    return render(request, 'home/admin/profileAdmin.html', context)


def my_profile_collection(request):
    instance = get_object_or_404(tenant_qs(request, StaffUser), user_ID_id=request.user.pk)
    context = {
        'instance': instance
    }
    return render(request, 'home/collection/profile.html', context)


@csrf_exempt
def postLogin(request):
    if request.method == 'POST':
        username = request.POST.get('userName')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            if _user_has_portal_access(request.user):
                return JsonResponse({'message': 'success', 'data': '/home/'}, safe=False)
            logout(request)
        return JsonResponse({'message': 'fail'}, safe=False)
    return JsonResponse({'message': 'fail'}, safe=False)


#
def homepage(request):
    if request.user.is_authenticated:
        if _user_has_portal_access(request.user):
            if request.user.has_perm('home.can_view_party_credentials') or request.user.has_perm('home.can_manage_staff'):
                try:
                    val = tenant_qs(request, Validity).last()
                    request.dashboard_license_message = "Your App License is Valid till {}".format(
                        val.expiryDate.strftime('%d-%m-%Y')
                    )
                except Exception:
                    request.dashboard_license_message = "You are using a trial version."
            return render(request, 'home/baseIndex.html')
        logout(request)
    return render(request, 'home/login.html')


@permission_required_any('home.view_party')
def customer_home(request):
    party = tenant_qs(request, Party).filter(userID_id=request.user.pk, isDeleted=False).only(
        'id', 'name', 'phone', 'address', 'username', 'password'
    ).order_by('-id').first()
    if not party:
        return redirect('/')

    msg_cache_key = f'customer_home_messages:{request.user.pk}:{party.phone}:{party.name or ""}'
    cached_messages = cache.get(msg_cache_key)
    if cached_messages is None:
        base_messages = tenant_qs(request, WhatsappMessageStatus).filter(
            isDeleted=False,
            phone=party.phone
        ).only('id', 'message', 'messageTo', 'phone', 'status', 'datetime').order_by('-datetime')
        if party.name:
            base_messages = base_messages.filter(messageTo__iexact=party.name)

        collection_messages = []
        sales_messages = []
        for msg in base_messages[:80]:
            text = (msg.message or "").lower()
            if len(collection_messages) < 10 and 'your payment has been' in text:
                collection_messages.append(msg)
            if len(sales_messages) < 10 and 'order has been dispatched' in text:
                sales_messages.append(msg)
            if len(collection_messages) >= 10 and len(sales_messages) >= 10:
                break

        cached_messages = {
            'collection_messages': collection_messages,
            'sales_messages': sales_messages,
        }
        cache.set(msg_cache_key, cached_messages, timeout=60)

    context = {
        'party': party,
        'collection_messages': cached_messages['collection_messages'],
        'sales_messages': cached_messages['sales_messages'],
    }
    return render(request, 'home/customer.html', context)


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
@permission_required_any('home.can_approve_collection', 'home.can_view_party_credentials')
def collection_list(request):
    staffs = tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name')
    context = {
        'staffs': staffs
    }
    return render(request, 'home/admin/collectionListByAdmin.html', context)


@is_activated()
@permission_required_any('home.can_approve_collection', 'home.can_view_party_credentials')
def cheque_reminder_list(request):
    staffs = tenant_qs(request, StaffUser).filter(isDeleted__exact=False).order_by('name')
    context = {
        'staffs': staffs
    }
    return render(request, 'home/admin/collectionListChequeReminder.html', context)


@permission_required_any('home.can_approve_collection', 'home.can_view_party_credentials')
def take_collection(request):
    instance = tenant_qs(request, Bank).filter(isDeleted__exact=False).order_by('name')
    context = {
        'instance': instance
    }
    return render(request, 'home/admin/takeCollection.html', context)


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


def attendance_report(request):
    if request.user.is_authenticated:
        return render(request, 'home/attendanceReportStaff.html')
    else:
        return redirect('/')


@permission_required_any('home.can_manage_staff', 'home.can_view_party_credentials', 'home.can_approve_collection')
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


@permission_required_any('home.change_sales', 'home.can_manage_sales', 'home.can_view_party_credentials')
def edit_sales(request, id=None):
    obj = get_object_or_404(tenant_qs(request, Sales), pk=id)
    capabilities = _sales_capabilities(request.user)
    admin_like = capabilities['can_filter_staff'] or request.user.has_perm('home.can_view_party_credentials') or request.user.has_perm('home.can_approve_sales')

    if not admin_like:
        if not obj.createdBy_id or getattr(obj.createdBy, 'user_ID_id', None) != request.user.pk:
            return redirect('homeApp:my_sales_list')
    invoice = obj.invoiceNumber
    splitVoice = invoice.split('/')

    context = {
        'obj': obj,
        'series': splitVoice[0],
        'number': splitVoice[1],
        'year': splitVoice[2],
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


@permission_required_any('home.change_order', 'home.can_manage_orders', 'home.can_view_party_credentials')
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
@permission_required_any('home.can_manage_sales', 'home.can_approve_collection', 'home.can_view_audit_logs')
def message_list(request):
    return render(request, 'home/messageList.html')


# -----------------------CashCounter---------------------
@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter')
def cash_counter_home(request):
    return render(request, 'home/cashCounter/indexCashCounter.html')


@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter', 'home.can_view_party_credentials', 'home.can_approve_collection')
def cash_counter(request):
    banks = tenant_qs(request, Bank).filter(isDeleted__exact=False).order_by('name')
    context = {
        'banks': banks
    }
    return render(request, 'home/cashCounter/todaysCounter.html', context)


@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter', 'home.view_collectioncashcounter', 'home.can_view_party_credentials', 'home.can_approve_collection')
def my_cash_counter_list(request):
    return render(request, 'home/cashCounter/cashCounterListByStaff.html', {
        'cash_counter_capabilities': _cash_counter_capabilities(request.user),
    })


@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter', 'home.can_view_party_credentials', 'home.can_approve_collection')
def edit_cash_counter(request, id=None):
    obj = get_object_or_404(tenant_qs(request, CashCounter), pk=id)
    banks = tenant_qs(request, Bank).filter(isDeleted__exact=False).order_by('name')

    invoice = obj.invoiceNumber
    try:
        splitVoice = invoice.split('/')
        context = {
            'obj': obj,
            'series': splitVoice[0],
            'number': splitVoice[1],
            'year': splitVoice[2],
            'banks': banks
        }

    except:
        context = {
            'obj': obj,
            'series': "",
            'number': "",
            'year': "",
            'banks': banks
        }

    return render(request, 'home/cashCounter/editCashCounter.html', context)


@permission_required_any('home.can_manage_cash_counter', 'home.view_collectioncashcounter', 'home.add_collectioncashcounter', 'home.change_collectioncashcounter', 'home.can_view_party_credentials', 'home.can_approve_collection')
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


@csrf_exempt
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


@permission_required_any('home.can_manage_cash_counter', 'home.view_cashcounter', 'home.can_view_party_credentials')
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


@permission_required_any('home.can_view_party_credentials')
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
