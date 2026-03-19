from django.contrib import admin

# Register your models here.

from .models import *


class ValidityAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'activationDate', 'expiryDate', 'activationType', 'datetime', 'lastUpdatedOn']
    list_filter = ['tenant', 'activationType']
    search_fields = ['tenant__name', 'tenant__slug', 'activationType']
    raw_id_fields = ['tenant']


admin.site.register(Validity, ValidityAdmin)



class EcomValidityAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'activationDate', 'expiryDate', 'activationType', 'datetime', 'lastUpdatedOn']
    list_filter = ['tenant', 'activationType']
    search_fields = ['tenant__name', 'tenant__slug', 'activationType']
    raw_id_fields = ['tenant']


admin.site.register(EcomValidity, EcomValidityAdmin)
