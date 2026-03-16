from django.contrib.auth.models import User
from django.db import models
from stdimage import StdImageField


# Create your models here.


class StaffGroup(models.Model):
    name = models.CharField(max_length=200, blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'a) User Group List'


class PartyGroup(models.Model):
    name = models.CharField(max_length=200, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'b) Party Group List'


class StaffUser(models.Model):
    name = models.CharField(max_length=200, blank=True, null=True)
    photo = StdImageField(upload_to='photo/img', blank=True, variations={
        'large': (600, 400),
        'thumbnail': (50, 50, True),
        'medium': (300, 200),
    }, delete_orphans=True)
    idProof = StdImageField(upload_to='photo/img', blank=True, variations={
        'large': (600, 400),
        'thumbnail': (50, 50, True),
        'medium': (300, 200),
    }, delete_orphans=True)
    phone = models.CharField(max_length=200, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    email = models.CharField(max_length=200, blank=True, null=True)
    username = models.CharField(max_length=200, blank=True, null=True)
    userPassword = models.CharField(max_length=200, blank=True, null=True)
    group = models.CharField(max_length=200, blank=True, null=True)
    partyGroupID = models.ForeignKey(PartyGroup, blank=True, null=True, on_delete=models.SET_NULL)
    user_ID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL)
    isActive = models.CharField(max_length=200, blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'c) User List'


class Bank(models.Model):
    name = models.CharField(max_length=200, blank=True, null=True)
    accountNumber = models.CharField(max_length=200, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'd) Bank List'


class Party(models.Model):
    name = models.CharField(max_length=200, blank=True, null=True)
    phone = models.CharField(max_length=200, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    username = models.CharField(max_length=15, blank=True, null=True)
    password = models.CharField(max_length=15, blank=True, null=True)
    userID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='party_user')
    partyGroupID = models.ForeignKey(PartyGroup, blank=True, null=True, on_delete=models.SET_NULL)
    assignTo = models.ForeignKey(StaffUser, blank=True, null=True, on_delete=models.SET_NULL)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'e) Party List'


class Collection(models.Model):
    partyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL)
    paymentID = models.CharField(max_length=200, blank=True, null=True)
    modeOfPayment = models.CharField(max_length=200, default='Cash')
    paidAmount = models.FloatField(default=0.0)
    bankID = models.ForeignKey(Bank, blank=True, null=True, on_delete=models.SET_NULL)
    detail = models.CharField(max_length=500, blank=True, null=True)
    remark = models.CharField(max_length=500, blank=True, null=True)
    collectionAddress = models.TextField(blank=True, null=True)
    collectedBy = models.ForeignKey(StaffUser, blank=True, null=True, on_delete=models.SET_NULL,
                                    related_name='CollectedBy')
    approvedOn = models.DateTimeField(blank=True, null=True)
    latitude = models.CharField(max_length=200, default='0.0')
    longitude = models.CharField(max_length=200, default='0.0')
    isApproved = models.BooleanField(default=False)
    isTallied = models.BooleanField(default=True)
    approvedBy = models.ForeignKey(StaffUser, blank=True, null=True, on_delete=models.SET_NULL,
                                   related_name='ApprovedBy')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    collectionDateTime = models.DateTimeField(blank=True, null=True)
    isDeleted = models.BooleanField(default=False)
    chequeDate = models.DateField(blank=True, null=True)
    transferredPartyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL,
                                           related_name="TransferredPartyID")

    def __str__(self):
        return str(self.partyID.name)

    class Meta:
        verbose_name_plural = 'f) Collection List'


class GeolocationPackage(models.Model):
    name = models.CharField(max_length=200, blank=True, null=True)
    balance = models.FloatField(default=0)
    used = models.FloatField(default=0)
    description = models.TextField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'g) Geolocation Package List'


class WhatsappMessage(models.Model):
    name = models.CharField(max_length=300, blank=True, null=True)
    instanceID = models.CharField(max_length=300, blank=True, null=True)
    apiKey = models.CharField(max_length=300, blank=True, null=True)
    balance = models.FloatField(default=0)
    used = models.FloatField(default=0)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = 'L) Whatsapp Message Package List'


class Attendance(models.Model):
    staffID = models.ForeignKey(StaffUser, blank=True, null=True, on_delete=models.SET_NULL,
                                related_name='staffID')
    registerType = models.CharField(max_length=200, blank=True, null=True)
    login_remark = models.TextField(blank=True, null=True)
    logout_remark = models.TextField(blank=True, null=True)
    other_remark = models.TextField(blank=True, null=True)
    login_latitude = models.CharField(max_length=200, default='0.0')
    logout_latitude = models.CharField(max_length=200, default='0.0')
    login_longitude = models.CharField(max_length=200, default='0.0')
    logout_longitude = models.CharField(max_length=200, default='0.0')
    isLogIn = models.BooleanField(default=False)
    isLogOut = models.BooleanField(default=False)
    loginDateTime = models.DateTimeField(blank=True, null=True)
    logoutDateTime = models.DateTimeField(blank=True, null=True)
    login_location = models.TextField(blank=True, null=True)
    logout_location = models.TextField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return str(self.staffID.name)

    class Meta:
        verbose_name_plural = 'i) Login/Logout List'


class WhatsappMessageStatus(models.Model):
    message = models.TextField(blank=True, null=True)
    messageTo = models.CharField(max_length=300, blank=True, null=True)
    phone = models.CharField(max_length=300, blank=True, null=True)
    status = models.CharField(max_length=300, blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return str(self.messageTo)

    class Meta:
        verbose_name_plural = 'J) Message Sent  List'


class Sales(models.Model):
    partyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL)
    paymentID = models.CharField(max_length=200, blank=True, null=True)
    invoiceNumber = models.CharField(max_length=200, default='Cash')
    amount = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)
    createdBy = models.ForeignKey(StaffUser, blank=True, null=True, on_delete=models.SET_NULL,
                                  related_name='createdByStaff')
    buildDate = models.DateField(blank=True, null=True)
    approvedOn = models.DateTimeField(blank=True, null=True)
    isApproved = models.BooleanField(default=False)
    approvedBy = models.ForeignKey(StaffUser, blank=True, null=True, on_delete=models.SET_NULL,
                                   related_name='SalesApprovedBy')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return str(self.partyID.name)

    class Meta:
        verbose_name_plural = 'K) Sales List'


class CashCounter(models.Model):
    partyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL)
    counterID = models.CharField(max_length=200, blank=True, null=True)
    invoiceNumber = models.CharField(max_length=200, blank=True, null=True)
    mode = models.CharField(max_length=200, blank=True, null=True)
    amount = models.FloatField(default=0.0)
    mixCashAmount = models.FloatField(default=0.0)
    mixCardAmount = models.FloatField(default=0.0)
    expenseType = models.CharField(max_length=500, blank=True, null=True, default='')
    bankID = models.ForeignKey(Bank, blank=True, null=True, on_delete=models.SET_NULL)
    remark = models.CharField(max_length=500, blank=True, null=True)
    createdBy = models.ForeignKey(StaffUser, blank=True, null=True, on_delete=models.SET_NULL,
                                  related_name='createdByStaffCashCounter')
    entryDate = models.DateTimeField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return self.mode

    class Meta:
        verbose_name_plural = 'L) Cash Counter List'


class CollectionCashCounter(models.Model):
    partyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL)
    paymentID = models.CharField(max_length=200, blank=True, null=True)
    modeOfPayment = models.CharField(max_length=200, default='Cash')
    paidAmount = models.FloatField(default=0.0)
    bankID = models.ForeignKey(Bank, blank=True, null=True, on_delete=models.SET_NULL)
    detail = models.CharField(max_length=500, blank=True, null=True)
    remark = models.CharField(max_length=500, blank=True, null=True)
    collectionAddress = models.TextField(blank=True, null=True)
    collectedBy = models.ForeignKey(StaffUser, blank=True, null=True, on_delete=models.SET_NULL,
                                    related_name='CashCounterCollectedBy')
    approvedOn = models.DateTimeField(blank=True, null=True)
    latitude = models.CharField(max_length=200, default='0.0')
    longitude = models.CharField(max_length=200, default='0.0')
    isApproved = models.BooleanField(default=False)
    isTallied = models.BooleanField(default=True)
    approvedBy = models.ForeignKey(StaffUser, blank=True, null=True, on_delete=models.SET_NULL,
                                   related_name='CashCounterApprovedBy')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    collectionDateTime = models.DateTimeField(blank=True, null=True)
    isDeleted = models.BooleanField(default=False)
    chequeDate = models.DateField(blank=True, null=True)
    transferredPartyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL,
                                           related_name="CashCounterTransferredPartyID")

    def __str__(self):
        return str(self.partyID.name)

    class Meta:
        verbose_name_plural = 'f) Cash Counter Collection List'
