from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.models import ContentType
from stdimage import StdImageField


# Create your models here.


class TimeStampedModel(models.Model):
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    isDeleted = models.BooleanField(default=False)

    class Meta:
        abstract = True


class AuditedModel(models.Model):
    audit_logs = GenericRelation(
        'home.AuditLog',
        content_type_field='content_type',
        object_id_field='object_id',
        related_query_name='audited_object',
    )

    class Meta:
        abstract = True


class TenantAwareModel(TimeStampedModel, AuditedModel):
    tenant = models.ForeignKey('Tenant', blank=True, null=True, on_delete=models.CASCADE)

    class Meta:
        abstract = True


class TenantOwnedModel(TenantAwareModel, SoftDeleteModel):
    class Meta:
        abstract = True


class Tenant(TimeStampedModel, AuditedModel):
    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'Tenant List'
        indexes = [
            models.Index(fields=['isActive', 'name'], name='tenant_active_name_idx'),
        ]


class TenantMembership(TimeStampedModel, AuditedModel):
    class Role(models.TextChoices):
        OWNER = 'owner', 'Owner'
        ADMIN = 'admin', 'Admin'
        STAFF = 'staff', 'Staff'
        PARTY = 'party', 'Party'

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='tenant_memberships')
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)
    isDefault = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.user} @ {self.tenant} ({self.get_role_display()})'

    class Meta:
        verbose_name_plural = 'Tenant Membership List'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'user'], name='unique_tenant_user_membership'),
        ]
        indexes = [
            models.Index(fields=['user', 'isDefault'], name='tenantmember_user_default_idx'),
            models.Index(fields=['tenant', 'role'], name='tenantmember_tenant_role_idx'),
        ]


class AuditLog(TimeStampedModel):
    class Action(models.TextChoices):
        CREATE = 'create', 'Create'
        UPDATE = 'update', 'Update'
        DELETE = 'delete', 'Delete'

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name='audit_logs')
    object_id = models.PositiveBigIntegerField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='audit_logs',
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    object_repr = models.CharField(max_length=255)
    changes = models.JSONField(default=dict, blank=True)
    snapshot = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f'{self.content_type}#{self.object_id} {self.action}'

    class Meta:
        ordering = ['-datetime']
        verbose_name_plural = 'Audit Logs'
        indexes = [
            models.Index(fields=['content_type', 'object_id', '-datetime'], name='auditlog_object_dt_idx'),
            models.Index(fields=['actor', '-datetime'], name='auditlog_actor_dt_idx'),
            models.Index(fields=['action', '-datetime'], name='auditlog_action_dt_idx'),
        ]
        permissions = [
            ('can_view_audit_logs', 'Can view audit logs'),
            ('can_export_audit_logs', 'Can export audit logs'),
        ]


class StaffGroup(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='staff_groups')
    name = models.CharField(max_length=200, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    djangoGroup = models.OneToOneField(Group, blank=True, null=True, on_delete=models.SET_NULL, related_name='staff_group_profile')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'a) User Group List'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'name'], name='staffgroup_tenant_del_name_idx'),
        ]
        permissions = [
            ('can_manage_staff_groups', 'Can manage staff groups'),
            ('can_view_staff_group_permissions', 'Can view staff group permissions'),
        ]


class PartyGroup(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='party_groups')
    name = models.CharField(max_length=200, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'b) Party Group List'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'name'], name='partygroup_tenant_del_name_idx'),
        ]


class StaffUser(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='staff_users')
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
    username = models.CharField(max_length=200, blank=True, null=True, help_text='Legacy field. Use linked Django auth user for login.')
    userPassword = models.CharField(max_length=200, blank=True, null=True, help_text='Legacy field retained for compatibility. Use Django auth password hashing instead.')
    group = models.CharField(max_length=200, blank=True, null=True)
    staffGroup = models.ForeignKey(StaffGroup, blank=True, null=True, on_delete=models.SET_NULL, related_name='staff_members')
    partyGroupID = models.ForeignKey(PartyGroup, blank=True, null=True, on_delete=models.SET_NULL)
    user_ID = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='staff_profile',
    )
    isActive = models.CharField(max_length=200, blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'c) User List'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'name'], name='staffuser_tenant_del_name_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'group'], name='staffuser_tenant_del_group_idx'),
        ]
        permissions = [
            ('can_manage_staff', 'Can manage staff'),
            ('can_reset_staff_password', 'Can reset staff password'),
            ('can_assign_staff_groups', 'Can assign staff groups'),
        ]


class Bank(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='banks')
    name = models.CharField(max_length=200, blank=True, null=True)
    accountNumber = models.CharField(max_length=200, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'd) Bank List'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'name'], name='bank_tenant_del_name_idx'),
        ]


class Party(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='parties')
    name = models.CharField(max_length=200, blank=True, null=True)
    phone = models.CharField(max_length=200, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    openingBalance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    username = models.CharField(max_length=15, blank=True, null=True, help_text='Legacy field. Use linked Django auth user for login.')
    password = models.CharField(max_length=15, blank=True, null=True, help_text='Legacy field retained for compatibility. Use Django auth password hashing instead.')
    userID = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='party_profile',
    )
    partyGroupID = models.ForeignKey(PartyGroup, blank=True, null=True, on_delete=models.SET_NULL)
    assignTo = models.ForeignKey(StaffUser, blank=True, null=True, on_delete=models.SET_NULL)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'e) Party List'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'name'], name='party_tenant_del_name_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'assignTo'], name='party_tenant_del_assign_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'partyGroupID'], name='party_tenant_del_group_idx'),
            models.Index(fields=['tenant', 'openingBalance'], name='party_tenant_openbal_idx'),
        ]
        permissions = [
            ('can_manage_parties', 'Can manage parties'),
            ('can_view_party_credentials', 'Can view party credentials'),
        ]


class Collection(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='collections')
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
    collectionDateTime = models.DateTimeField(blank=True, null=True)
    chequeDate = models.DateField(blank=True, null=True)
    transferredPartyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL,
                                           related_name="TransferredPartyID")

    def __str__(self):
        return str(self.partyID.name)

    class Meta:
        verbose_name_plural = 'f) Collection List'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'collectionDateTime'], name='collection_tenant_del_cdt_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'datetime'], name='collection_tenant_del_dt_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'collectedBy'], name='collect_tenant_staff_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'isApproved'], name='collection_tenant_del_appr_idx'),
            models.Index(fields=['tenant', 'paymentID'], name='collection_tenant_payid_idx'),
        ]
        permissions = [
            ('can_approve_collection', 'Can approve collection'),
            ('can_tally_collection', 'Can tally collection'),
            ('can_view_collection_reports', 'Can view collection reports'),
        ]


class GeolocationPackage(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='geolocation_packages')
    name = models.CharField(max_length=200, blank=True, null=True)
    balance = models.FloatField(default=0)
    used = models.FloatField(default=0)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'g) Geolocation Package List'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'datetime'], name='geopkg_tenant_del_dt_idx'),
        ]


class WhatsappMessage(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='whatsapp_messages')
    name = models.CharField(max_length=300, blank=True, null=True)
    instanceID = models.CharField(max_length=300, blank=True, null=True)
    apiKey = models.CharField(max_length=300, blank=True, null=True)
    balance = models.FloatField(default=0)
    used = models.FloatField(default=0)

    class Meta:
        verbose_name_plural = 'L) Whatsapp Message Package List'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'datetime'], name='wmsg_tenant_del_dt_idx'),
        ]


class Attendance(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='attendances')
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

    def __str__(self):
        return str(self.staffID.name)

    class Meta:
        verbose_name_plural = 'i) Login/Logout List'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'staffID', 'datetime'], name='attendance_tenant_staff_dt_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'loginDateTime'], name='attendance_tenant_login_idx'),
        ]
        permissions = [
            ('can_view_all_attendance', 'Can view all attendance'),
            ('can_view_attendance_reports', 'Can view attendance reports'),
        ]


class WhatsappMessageStatus(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='whatsapp_message_statuses')
    message = models.TextField(blank=True, null=True)
    messageTo = models.CharField(max_length=300, blank=True, null=True)
    phone = models.CharField(max_length=300, blank=True, null=True)
    status = models.CharField(max_length=300, blank=True, null=True)

    def __str__(self):
        return str(self.messageTo)

    class Meta:
        verbose_name_plural = 'J) Message Sent  List'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'messageTo'], name='msgstatus_tenant_del_to_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'datetime'], name='msgstatus_tenant_del_dt_idx'),
        ]


class Order(TenantOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        CANCELLED = "cancelled", "Cancelled"
        FULFILLED = "fulfilled", "Fulfilled"

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='orders')
    orderNumber = models.CharField(max_length=40, blank=True, null=True, db_index=True)
    partyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL, related_name='orders')
    buildDate = models.DateField(blank=True, null=True)

    orderText = models.TextField(blank=True, null=True)
    remark = models.CharField(max_length=500, blank=True, null=True)

    # Optional capture location.
    latitude = models.CharField(max_length=200, default='0.0', blank=True, null=True)
    longitude = models.CharField(max_length=200, default='0.0', blank=True, null=True)
    location = models.TextField(blank=True, null=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    createdBy = models.ForeignKey(
        StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='orders_created'
    )
    approvedOn = models.DateTimeField(blank=True, null=True)
    approvedBy = models.ForeignKey(
        StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='orders_approved'
    )

    def __str__(self):
        return self.orderNumber or f'Order#{self.pk}'

    class Meta:
        verbose_name_plural = 'Orders'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'buildDate'], name='order_tenant_del_build_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'status'], name='order_tenant_del_status_idx'),
            models.Index(fields=['tenant', 'partyID'], name='order_tenant_party_idx'),
            models.Index(fields=['tenant', 'createdBy'], name='order_tenant_staff_idx'),
        ]
        permissions = [
            ('can_approve_orders', 'Can approve orders'),
            ('can_manage_orders', 'Can manage orders'),
            ('can_view_order_reports', 'Can view order reports'),
        ]


class OrderAttachment(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='order_attachments')
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='attachments')
    label = models.CharField(max_length=120, blank=True, null=True)
    image = StdImageField(
        upload_to='orders/attachments/',
        blank=True,
        variations={
            'thumbnail': (240, 240, True),
            'medium': (900, 900),
        },
        delete_orphans=True,
    )

    class Meta:
        verbose_name_plural = 'Order Attachments'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'order'], name='orderatt_tenant_order_idx'),
        ]


class Sales(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='sales')
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

    def __str__(self):
        return str(self.partyID.name)

    class Meta:
        verbose_name_plural = 'K) Sales List'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'buildDate'], name='sales_tenant_del_build_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'createdBy'], name='sales_tenant_del_staff_idx'),
            models.Index(fields=['tenant', 'paymentID'], name='sales_tenant_payid_idx'),
        ]
        permissions = [
            ('can_approve_sales', 'Can approve sales'),
            ('can_manage_sales', 'Can manage sales'),
            ('can_view_sales_reports', 'Can view sales reports'),
        ]


class CashCounter(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='cash_counters')
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

    def __str__(self):
        return self.mode

    class Meta:
        verbose_name_plural = 'L) Cash Counter List'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'entryDate'], name='cashcounter_tenant_entry_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'createdBy'], name='cashcounter_tenant_staff_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'mode'], name='cashcounter_tenant_mode_idx'),
        ]
        permissions = [
            ('can_manage_cash_counter', 'Can manage cash counter'),
            ('can_view_cash_counter_reports', 'Can view cash counter reports'),
        ]


class CollectionCashCounter(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='cash_counter_collections')
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
    collectionDateTime = models.DateTimeField(blank=True, null=True)
    chequeDate = models.DateField(blank=True, null=True)
    transferredPartyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL,
                                           related_name="CashCounterTransferredPartyID")

    def __str__(self):
        return str(self.partyID.name)

    class Meta:
        verbose_name_plural = 'f) Cash Counter Collection List'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'collectionDateTime'], name='ccollection_tenant_cdt_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'datetime'], name='ccollection_tenant_dt_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'collectedBy'], name='ccollection_tenant_staff_idx'),
            models.Index(fields=['tenant', 'paymentID'], name='ccollection_tenant_payid_idx'),
        ]
