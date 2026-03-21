from django import forms
from django.contrib import admin
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission

# Register your models here.
from .models import *
from .tenancy import ensure_membership


User = get_user_model()


def _tenant_admin_permission_queryset():
    return Permission.objects.select_related('content_type').exclude(
        content_type__app_label__in=['admin', 'auth', 'contenttypes', 'sessions']
    ).order_by('content_type__app_label', 'content_type__model', 'name')


def _tenant_admin_group_name(tenant):
    return f'{tenant.slug}:Tenant Admin'


class TenantAdminForm(forms.ModelForm):
    tenant_admin_user = forms.ModelChoiceField(
        queryset=User.objects.order_by('username'),
        required=False,
        help_text='Select the auth user who should administer this tenant.',
        label='Tenant admin user',
    )
    admin_permissions = forms.ModelMultipleChoiceField(
        queryset=_tenant_admin_permission_queryset(),
        required=False,
        widget=FilteredSelectMultiple('Tenant modules and permissions', is_stacked=False),
        help_text='Selected permissions decide which modules this tenant admin can access.',
        label='Tenant admin permissions',
    )

    class Meta:
        model = Tenant
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['admin_permissions'].label_from_instance = self._permission_label
        if self.instance.pk and not self.is_bound:
            membership = self.instance.memberships.filter(
                role=TenantMembership.Role.ADMIN
            ).select_related('user').order_by('-isDefault', 'id').first()
            if membership:
                self.initial['tenant_admin_user'] = membership.user_id
            group = Group.objects.filter(name=_tenant_admin_group_name(self.instance)).first()
            if group:
                self.initial['admin_permissions'] = list(
                    group.permissions.filter(id__in=self.fields['admin_permissions'].queryset).values_list('id', flat=True)
                )

    @staticmethod
    def _permission_label(permission):
        module = permission.content_type.model.replace('_', ' ').title()
        return f'{module} | {permission.name}'


class StaffGroupAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'name', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(StaffGroup, StaffGroupAdmin)


class PartyGroupAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'name', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(PartyGroup, PartyGroupAdmin)


class StaffUserAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'name', 'phone', 'address', 'email', 'username', 'userPassword', 'group', 'partyGroupID',
                    'isActive', 'datetime',
                    'lastUpdatedOn']


admin.site.register(StaffUser, StaffUserAdmin)


class BankAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'name', 'accountNumber', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(Bank, BankAdmin)


class PartyAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'name', 'phone', 'address', 'partyGroupID', 'assignTo', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(Party, PartyAdmin)


class OrderAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'orderNumber', 'partyID', 'status', 'createdBy', 'buildDate', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(Order, OrderAdmin)


class OrderAttachmentAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'order', 'label', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(OrderAttachment, OrderAttachmentAdmin)


class CollectionAdmin(admin.ModelAdmin):
    search_fields = ['partyID', 'modeOfPayment', 'paidAmount', 'collectedBy', 'remark']
    list_display = ['tenant', 'partyID', 'modeOfPayment', 'paidAmount', 'collectionDateTime', 'remark', 'collectionAddress',
                    'collectedBy',
                    'approvedOn', 'isTallied',
                    'latitude',
                    'longitude', 'isApproved', 'approvedBy', 'datetime']


admin.site.register(Collection, CollectionAdmin)


class GeolocationPackageAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'name', 'balance', 'used', 'description', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(GeolocationPackage, GeolocationPackageAdmin)


class MessageAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'instanceID', 'apiKey', 'balance', 'used', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(WhatsappMessage, MessageAdmin)


class AttendanceAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'staffID', 'isLogIn', 'loginDateTime', 'login_remark', 'isLogOut', 'logoutDateTime',
                    'logout_remark', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(Attendance, AttendanceAdmin)


class MessageStatusAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'messageTo', 'phone', 'status', 'message', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(WhatsappMessageStatus, MessageStatusAdmin)


class SalesAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'partyID', 'paymentID', 'buildDate', 'invoiceNumber', 'amount', 'remark', 'createdBy', 'datetime',
                    'lastUpdatedOn', 'isDeleted']


admin.site.register(Sales, SalesAdmin)


class CashCounterAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'counterID', 'partyID', 'invoiceNumber', 'mode', 'amount', 'mixCashAmount', 'mixCardAmount',
                    'remark', 'createdBy', 'entryDate', 'datetime',
                    'lastUpdatedOn', 'isDeleted']


class TenantAdmin(admin.ModelAdmin):
    form = TenantAdminForm
    list_display = ['name', 'slug', 'isActive', 'datetime', 'lastUpdatedOn']

    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'isActive'),
        }),
        ('Tenant Admin Access', {
            'fields': ('tenant_admin_user', 'admin_permissions'),
            'description': 'Assign the tenant admin and decide which modules this tenant can access.',
        }),
    )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)

        tenant = form.instance
        admin_user = form.cleaned_data.get('tenant_admin_user')
        permissions = form.cleaned_data.get('admin_permissions')

        group, _ = Group.objects.get_or_create(name=_tenant_admin_group_name(tenant))
        group.permissions.set(permissions or [])

        previous_admin_user_ids = list(
            tenant.memberships.filter(role=TenantMembership.Role.ADMIN).values_list('user_id', flat=True)
        )
        previous_admin_user_ids = [user_id for user_id in previous_admin_user_ids if user_id]
        if previous_admin_user_ids:
            for user in User.objects.filter(id__in=previous_admin_user_ids):
                if admin_user is None or user.pk != admin_user.pk:
                    user.groups.remove(group)

        if admin_user is not None:
            ensure_membership(admin_user, tenant, TenantMembership.Role.ADMIN)
            admin_user.groups.add(group)


admin.site.register(Tenant, TenantAdmin)


class TenantMembershipAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'user', 'role', 'isDefault', 'datetime', 'lastUpdatedOn']


admin.site.register(TenantMembership, TenantMembershipAdmin)


class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['datetime', 'content_type', 'object_id', 'action', 'actor', 'object_repr']
    search_fields = ['object_repr', 'object_id', 'action', 'actor__username']
    list_filter = ['action', 'content_type']
    readonly_fields = ['datetime', 'lastUpdatedOn', 'content_type', 'object_id', 'actor', 'action', 'object_repr', 'changes', 'snapshot']


admin.site.register(AuditLog, AuditLogAdmin)


admin.site.register(CashCounter, CashCounterAdmin)


class BookAccountAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'type', 'name', 'bank', 'openingBalance', 'openingDate', 'isDefault', 'datetime', 'isDeleted']
    list_filter = ['type', 'isDefault', 'isDeleted']
    search_fields = ['name', 'bank__name', 'bank__accountNumber']


class BookTransactionAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'account', 'direction', 'amount', 'txDateTime', 'source', 'component', 'party', 'transferId', 'isDeleted']
    list_filter = ['source', 'direction', 'isDeleted']
    search_fields = ['narration', 'transferId', 'party__name', 'account__name']


admin.site.register(BookAccount, BookAccountAdmin)
admin.site.register(BookTransaction, BookTransactionAdmin)
