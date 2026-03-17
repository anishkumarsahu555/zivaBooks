from django.contrib import admin

# Register your models here.
from .models import *


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
    list_display = ['name', 'slug', 'isActive', 'datetime', 'lastUpdatedOn']


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
