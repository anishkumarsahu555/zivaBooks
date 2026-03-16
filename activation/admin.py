from django.contrib import admin

# Register your models here.

from .models import *


class ValidityAdmin(admin.ModelAdmin):
    list_display = ['activationDate', 'expiryDate', 'activationType', 'datetime', 'lastUpdatedOn']


admin.site.register(Validity, ValidityAdmin)



class EcomValidityAdmin(admin.ModelAdmin):
    list_display = ['activationDate', 'expiryDate', 'activationType', 'datetime', 'lastUpdatedOn']


admin.site.register(EcomValidity, EcomValidityAdmin)
