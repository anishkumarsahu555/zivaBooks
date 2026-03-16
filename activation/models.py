from django.db import models


# Create your models here.

class Validity(models.Model):
    activationDate = models.DateField(null=True)
    expiryDate = models.DateField(null=True)
    activationType = models.CharField(max_length=100, blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)

    def __str__(self):
        return str(self.activationDate) + '  -  ' + str(self.expiryDate)


class EcomValidity(models.Model):
    activationDate = models.DateField(null=True)
    expiryDate = models.DateField(null=True)
    activationType = models.CharField(max_length=100, blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)

    def __str__(self):
        return str(self.activationDate) + '  -  ' + str(self.expiryDate)
