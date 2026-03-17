from django.db import models
from home.models import TenantAwareModel


# Create your models here.

class Validity(TenantAwareModel):
    tenant = models.ForeignKey('home.Tenant', blank=True, null=True, on_delete=models.CASCADE, related_name='validities')
    activationDate = models.DateField(null=True)
    expiryDate = models.DateField(null=True)
    activationType = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return str(self.activationDate) + '  -  ' + str(self.expiryDate)

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'expiryDate'], name='validity_tenant_expiry_idx'),
            models.Index(fields=['tenant', 'activationType'], name='validity_tenant_type_idx'),
        ]


class EcomValidity(TenantAwareModel):
    tenant = models.ForeignKey('home.Tenant', blank=True, null=True, on_delete=models.CASCADE, related_name='ecom_validities')
    activationDate = models.DateField(null=True)
    expiryDate = models.DateField(null=True)
    activationType = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return str(self.activationDate) + '  -  ' + str(self.expiryDate)

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'expiryDate'], name='evalidity_tenant_expiry_idx'),
            models.Index(fields=['tenant', 'activationType'], name='evalidity_tenant_type_idx'),
        ]
