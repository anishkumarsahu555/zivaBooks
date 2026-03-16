from django.contrib import admin

# Register your models here.
from .models import *


class StaffGroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(StaffGroup, StaffGroupAdmin)


class PartyGroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(PartyGroup, PartyGroupAdmin)


class StaffUserAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'address', 'email', 'username', 'userPassword', 'group', 'partyGroupID',
                    'isActive', 'datetime',
                    'lastUpdatedOn']


admin.site.register(StaffUser, StaffUserAdmin)


class BankAdmin(admin.ModelAdmin):
    list_display = ['name', 'accountNumber', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(Bank, BankAdmin)


class PartyAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'address', 'partyGroupID', 'assignTo', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(Party, PartyAdmin)


class CollectionAdmin(admin.ModelAdmin):
    search_fields = ['partyID', 'modeOfPayment', 'paidAmount', 'collectedBy', 'remark']
    list_display = ['partyID', 'modeOfPayment', 'paidAmount', 'collectionDateTime', 'remark', 'collectionAddress',
                    'collectedBy',
                    'approvedOn', 'isTallied',
                    'latitude',
                    'longitude', 'isApproved', 'approvedBy', 'datetime']


admin.site.register(Collection, CollectionAdmin)


class GeolocationPackageAdmin(admin.ModelAdmin):
    list_display = ['name', 'balance', 'used', 'description', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(GeolocationPackage, GeolocationPackageAdmin)


class MessageAdmin(admin.ModelAdmin):
    list_display = ['instanceID', 'apiKey', 'balance', 'used', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(WhatsappMessage, MessageAdmin)


class AttendanceAdmin(admin.ModelAdmin):
    list_display = ['staffID', 'isLogIn', 'loginDateTime', 'login_remark', 'isLogOut', 'logoutDateTime',
                    'logout_remark', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(Attendance, AttendanceAdmin)


class MessageStatusAdmin(admin.ModelAdmin):
    list_display = ['messageTo', 'phone', 'status', 'message', 'datetime', 'lastUpdatedOn', 'isDeleted']


admin.site.register(WhatsappMessageStatus, MessageStatusAdmin)


class SalesAdmin(admin.ModelAdmin):
    list_display = ['partyID', 'paymentID', 'buildDate', 'invoiceNumber', 'amount', 'remark', 'createdBy', 'datetime',
                    'lastUpdatedOn', 'isDeleted']


admin.site.register(Sales, SalesAdmin)


class CashCounterAdmin(admin.ModelAdmin):
    list_display = ['counterID', 'partyID', 'invoiceNumber', 'mode', 'amount', 'mixCashAmount', 'mixCardAmount',
                    'remark', 'createdBy', 'entryDate', 'datetime',
                    'lastUpdatedOn', 'isDeleted']


admin.site.register(CashCounter, CashCounterAdmin)
