from django import forms
from django.contrib import admin
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.utils.html import format_html, format_html_join

# Register your models here.
from .models import *
from .permission_bundles import (
    tenant_permission_bundle_definitions,
    get_bundle_keys_for_license_plan,
    get_permissions_for_license_plan,
)
from .tenancy import ensure_membership


User = get_user_model()


def _tenant_admin_permission_queryset():
    return Permission.objects.select_related('content_type').exclude(
        content_type__app_label__in=['admin', 'auth', 'contenttypes', 'sessions']
    ).order_by('content_type__app_label', 'content_type__model', 'name')


def _tenant_admin_group_name(tenant):
    return f'{tenant.slug}:Tenant Admin'


def _tenant_bundle_choices():
    return [(bundle['key'], bundle['label']) for bundle in tenant_permission_bundle_definitions()]


def _tenant_bundle_permissions():
    return {bundle['key']: bundle['permissions'] for bundle in tenant_permission_bundle_definitions()}


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
    preset_bundles = forms.MultipleChoiceField(
        choices=_tenant_bundle_choices(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='Module presets auto-sync with the selected License Plan, or select manually to fine-tune.',
        label='Tenant admin permission presets',
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
                selected_permission_ids = list(
                    group.permissions.filter(id__in=self.fields['admin_permissions'].queryset).values_list('id', flat=True)
                )
                self.initial['admin_permissions'] = selected_permission_ids
                selected_perm_names = {
                    f'{permission.content_type.app_label}.{permission.codename}'
                    for permission in group.permissions.select_related('content_type')
                }
                presets = [
                    bundle['key']
                    for bundle in tenant_permission_bundle_definitions()
                    if set(bundle['permissions']).issubset(selected_perm_names)
                ]
                if not presets and self.instance.license_plan:
                    presets = get_bundle_keys_for_license_plan(self.instance.license_plan)
                self.initial['preset_bundles'] = presets
            elif self.instance.license_plan:
                self.initial['preset_bundles'] = get_bundle_keys_for_license_plan(self.instance.license_plan)

    def clean_admin_permissions(self):
        permissions = list(self.cleaned_data.get('admin_permissions') or [])
        selected_bundle_keys = list(self.cleaned_data.get('preset_bundles') or [])

        # Auto-derive preset bundles from license_plan if none were manually specified
        license_plan = self.cleaned_data.get('license_plan') or getattr(self.instance, 'license_plan', None)
        if not selected_bundle_keys and license_plan:
            selected_bundle_keys = get_bundle_keys_for_license_plan(license_plan)

        bundle_permission_map = _tenant_bundle_permissions()
        extra_perm_names = set()
        for bundle_key in selected_bundle_keys:
            extra_perm_names.update(bundle_permission_map.get(bundle_key, []))

        if extra_perm_names:
            bundle_permissions = list(
                Permission.objects.select_related('content_type').filter(
                    content_type__app_label='home',
                    codename__in=[name.split('.', 1)[1] for name in extra_perm_names if name.startswith('home.')],
                )
            )
            existing_ids = {permission.pk for permission in permissions}
            for permission in bundle_permissions:
                if permission.pk not in existing_ids:
                    permissions.append(permission)
        return permissions

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


class CredentialShareTokenAdmin(admin.ModelAdmin):
    list_display = [
        'tenant',
        'subjectType',
        'user',
        'staffUser',
        'party',
        'expiresAt',
        'usedAt',
        'createdBy',
        'isDeleted',
    ]
    list_filter = ['tenant', 'subjectType', 'purpose', 'isDeleted', 'usedAt', 'expiresAt']
    search_fields = ['user__username', 'staffUser__name', 'party__name', 'tokenHash']
    readonly_fields = ['tokenHash', 'usedAt', 'claimedFromIP', 'datetime', 'lastUpdatedOn']


admin.site.register(CredentialShareToken, CredentialShareTokenAdmin)


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
    list_display = [
        'tenant',
        'staffID',
        'isLogIn',
        'loginDateTime',
        'isLogOut',
        'logoutDateTime',
        'login_photo_preview',
        'logout_photo_preview',
        'datetime',
        'isDeleted',
    ]
    list_filter = ['tenant', 'isLogIn', 'isLogOut', 'isDeleted', 'datetime']
    search_fields = ['staffID__name', 'staffID__phone', 'login_remark', 'logout_remark', 'login_location', 'logout_location']
    date_hierarchy = 'datetime'
    readonly_fields = [
        'datetime',
        'lastUpdatedOn',
        'login_photo_preview_large',
        'logout_photo_preview_large',
    ]
    fieldsets = (
        (None, {
            'fields': ('tenant', 'staffID', 'registerType', 'isDeleted'),
        }),
        ('Login', {
            'fields': (
                'isLogIn',
                'loginDateTime',
                'login_latitude',
                'login_longitude',
                'login_location',
                'login_remark',
                'loginPhoto',
                'login_photo_preview_large',
            ),
        }),
        ('Logout', {
            'fields': (
                'isLogOut',
                'logoutDateTime',
                'logout_latitude',
                'logout_longitude',
                'logout_location',
                'logout_remark',
                'logoutPhoto',
                'logout_photo_preview_large',
            ),
        }),
        ('Audit', {
            'fields': ('datetime', 'lastUpdatedOn'),
        }),
    )

    @staticmethod
    def _preview(image_field, width=44, height=44):
        if not image_field:
            return '-'
        image_url = getattr(getattr(image_field, 'thumbnail', None), 'url', None) or getattr(image_field, 'url', None)
        if not image_url:
            return '-'
        return format_html(
            '<img src="{}" style="width:{}px;height:{}px;object-fit:cover;border-radius:10px;border:1px solid rgba(15,23,42,.08);" />',
            image_url,
            width,
            height,
        )

    def login_photo_preview(self, obj):
        return self._preview(obj.loginPhoto)

    login_photo_preview.short_description = 'Login Photo'

    def logout_photo_preview(self, obj):
        return self._preview(obj.logoutPhoto)

    logout_photo_preview.short_description = 'Logout Photo'

    def login_photo_preview_large(self, obj):
        return self._preview(obj.loginPhoto, width=160, height=160)

    login_photo_preview_large.short_description = 'Login Photo Preview'

    def logout_photo_preview_large(self, obj):
        return self._preview(obj.logoutPhoto, width=160, height=160)

    logout_photo_preview_large.short_description = 'Logout Photo Preview'


admin.site.register(Attendance, AttendanceAdmin)


class MessageStatusAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'messageTo', 'phone', 'status', 'message', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(WhatsappMessageStatus, MessageStatusAdmin)


class SalesAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'partyID', 'paymentID', 'buildDate', 'invoiceSeries', 'invoiceSequenceNumber', 'invoiceNumber', 'amount', 'remark', 'createdBy', 'datetime',
                    'lastUpdatedOn', 'isDeleted']


admin.site.register(Sales, SalesAdmin)


class CashCounterAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'counterID', 'partyID', 'invoiceSeries', 'invoiceSequenceNumber', 'invoiceNumber', 'mode', 'amount', 'mixCashAmount', 'mixCardAmount',
                    'remark', 'createdBy', 'entryDate', 'datetime',
                    'lastUpdatedOn', 'isDeleted']


class TenantAiUsageInline(admin.StackedInline):
    model = TenantAiUsage
    extra = 0
    can_delete = False


class TenantAdmin(admin.ModelAdmin):
    form = TenantAdminForm
    inlines = [TenantAiUsageInline]
    list_display = [
        'name',
        'slug',
        'license_plan',
        'legalName',
        'phone',
        'inventoryEnabled',
        'advancedInvoicingEnabled',
        'nonGstShadowEnabled',
        'isActive',
        'datetime',
    ]
    search_fields = ['name', 'slug', 'legalName', 'gstNumber', 'phone']
    list_filter = [
        'license_plan',
        'isActive',
        'inventoryEnabled',
        'advancedInvoicingEnabled',
        'nonGstShadowEnabled',
        'enableAttendanceLocationMaps',
    ]
    readonly_fields = ['datetime', 'lastUpdatedOn', 'membership_summary']

    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'license_plan', 'isActive'),
        }),
        ('Business Details', {
            'fields': ('legalName', 'gstNumber', 'phone', 'billingAddress'),
        }),
        ('Tenant Features & Plan Sync', {
            'fields': (
                'inventoryEnabled',
                'advancedInvoicingEnabled',
                'nonGstShadowEnabled',
                'enableAttendanceLocationMaps',
            ),
            'description': 'Feature flags automatically synchronize when a License Plan is assigned.',
        }),
        ('Tenant Admin Access', {
            'fields': ('tenant_admin_user', 'preset_bundles', 'admin_permissions'),
            'description': 'Assign the tenant admin, choose permission presets, then review the raw tenant admin permissions below.',
        }),
        ('Membership Snapshot', {
            'fields': ('membership_summary', 'datetime', 'lastUpdatedOn'),
        }),
    )

    def membership_summary(self, obj):
        memberships = obj.memberships.select_related('user').order_by('-isDefault', 'role', 'id')
        if not memberships.exists():
            return 'No memberships'
        rows = []
        for membership in memberships:
            default_badge = ' (Default)' if membership.isDefault else ''
            rows.append(f'{membership.user.username} - {membership.get_role_display()}{default_badge}')
        return format_html_join('\n', '{}<br>', ((row,) for row in rows))

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.license_plan:
            obj.apply_license_plan()

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


class InvoiceSeriesAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'module', 'seriesCode', 'startNumber', 'endNumber', 'nextNumber', 'paddingLength', 'isActive', 'isDefault']
    list_filter = ['module', 'isActive', 'isDefault']
    search_fields = ['tenant__name', 'seriesCode', 'name']


class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'reference', 'source', 'entryDateTime', 'createdBy', 'line_count', 'isDeleted']
    list_filter = ['source', 'isDeleted', 'tenant']
    search_fields = ['reference', 'narration', 'createdBy__name']
    readonly_fields = ['datetime', 'lastUpdatedOn']

    @staticmethod
    def line_count(obj):
        return obj.lines.filter(isDeleted=False).count()

    line_count.short_description = 'Lines'


class JournalLineAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'journal_reference', 'source', 'entryDateTime', 'lineNo', 'accountType', 'bookAccount', 'party', 'debit', 'credit', 'component', 'isDeleted']
    list_filter = ['accountType', 'isDeleted', 'tenant']
    search_fields = ['note', 'journalEntry__reference', 'party__name', 'bookAccount__name', 'journalEntry__narration']
    readonly_fields = ['datetime', 'lastUpdatedOn']

    @staticmethod
    def journal_reference(obj):
        return obj.journalEntry.reference or f'Journal#{obj.journalEntry_id}'

    journal_reference.short_description = 'Journal Ref'

    @staticmethod
    def source(obj):
        return obj.journalEntry.get_source_display()

    source.short_description = 'Source'

    @staticmethod
    def entryDateTime(obj):
        return obj.journalEntry.entryDateTime

    entryDateTime.short_description = 'Entry Date'


class TenantAiUsageAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'plan_name', 'max_queries_per_month', 'queries_used_this_month', 'last_reset_date']
    list_filter = ['plan_name', 'last_reset_date']
    search_fields = ['tenant__name', 'plan_name']


admin.site.register(BookAccount, BookAccountAdmin)
admin.site.register(BookTransaction, BookTransactionAdmin)
admin.site.register(InvoiceSeries, InvoiceSeriesAdmin)
admin.site.register(JournalEntry, JournalEntryAdmin)
admin.site.register(JournalLine, JournalLineAdmin)
admin.site.register(TenantAiUsage, TenantAiUsageAdmin)


class ZivaAiChatMessageAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'user', 'role', 'timestamp', 'message_snippet']
    list_filter = ['role', 'timestamp', 'tenant']
    search_fields = ['user__username', 'message']

    def message_snippet(self, obj):
        return obj.message[:50] + '...' if len(obj.message) > 50 else obj.message
    message_snippet.short_description = 'Message'

admin.site.register(ZivaAiChatMessage, ZivaAiChatMessageAdmin)


class LicenseInquiryAdmin(admin.ModelAdmin):
    list_display = [
        'full_name',
        'business_name',
        'email',
        'phone',
        'license_plan',
        'user_seats',
        'status',
        'datetime',
        'ip_address',
    ]
    list_filter = ['license_plan', 'status', 'datetime']
    search_fields = ['full_name', 'business_name', 'email', 'phone', 'message']
    readonly_fields = ['datetime', 'lastUpdatedOn', 'ip_address']
    date_hierarchy = 'datetime'
    list_editable = ['status']


admin.site.register(LicenseInquiry, LicenseInquiryAdmin)


class LicensePlanAdmin(admin.ModelAdmin):
    list_display = [
        'name',
        'plan_code',
        'price_per_year',
        'user_seats',
        'is_featured',
        'is_active',
        'display_order',
        'sales_billing_enabled',
        'gst_einvoicing_enabled',
        'inventory_enabled',
        'dual_otp_enabled',
        'gps_tracking_enabled',
        'ziva_ai_enabled',
    ]
    list_editable = ['price_per_year', 'user_seats', 'is_featured', 'is_active', 'display_order']
    list_filter = ['is_active', 'is_featured', 'gst_einvoicing_enabled', 'ziva_ai_enabled', 'gps_tracking_enabled', 'inventory_enabled']
    search_fields = ['name', 'plan_code', 'tagline']
    fieldsets = (
        ('General Plan Information', {
            'fields': ('plan_code', 'name', 'tagline', 'price_per_year', 'user_seats', 'is_featured', 'display_order', 'is_active')
        }),
        ('1. Sales & Invoicing Modules', {
            'fields': (
                'sales_billing_enabled',
                'proforma_estimates_enabled',
                'custom_invoice_series_enabled',
                'sales_returns_credit_notes_enabled',
                'thermal_printing_enabled',
                'pdf_excel_exports_enabled',
            )
        }),
        ('2. GST & Tax Compliance Modules', {
            'fields': (
                'gst_einvoicing_enabled',
                'eway_bill_enabled',
            )
        }),
        ('3. Inventory & FEFO Stock Modules', {
            'fields': (
                'inventory_enabled',
                'fefo_batch_tracking_enabled',
                'multi_warehouse_enabled',
                'stock_adjustment_vouchers_enabled',
                'zero_stock_alerts_enabled',
            )
        }),
        ('4. Counter POS & OTP Clearance Modules', {
            'fields': (
                'multi_counter_pos_enabled',
                'dual_otp_enabled',
            )
        }),
        ('5. Financial Accounting Modules', {
            'fields': (
                'cashbook_bankbook_enabled',
                'party_ledgers_enabled',
                'double_entry_journals_enabled',
            )
        }),
        ('6. Field Staff GPS & Attendance Modules', {
            'fields': (
                'gps_tracking_enabled',
                'route_history_map_enabled',
                'geofenced_attendance_enabled',
            )
        }),
        ('7. Ziva AI Assistant Modules', {
            'fields': (
                'ziva_ai_enabled',
                'ziva_ai_max_queries_per_month',
            )
        }),
        ('8. Security & Multi-Branch Modules', {
            'fields': (
                'multi_branch_isolation_enabled',
                'custom_shadow_mode_enabled',
                'role_based_permissions_enabled',
            )
        }),
        ('Custom Feature Bullet Points', {
            'fields': ('features_list',),
            'description': 'Enter human-readable feature bullets as a JSON array e.g. ["Sales System", "FEFO Stock Control"]'
        })
    )


admin.site.register(LicensePlan, LicensePlanAdmin)




