from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import Group
from django.db import models
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from stdimage import StdImageField
from django.db.models import Q


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
    legalName = models.CharField(max_length=255, blank=True, null=True)
    gstNumber = models.CharField(max_length=20, blank=True, null=True)
    billingAddress = models.TextField(blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    isActive = models.BooleanField(default=True)
    inventoryEnabled = models.BooleanField(
        default=True,
        help_text='Enable inventory masters, purchases, stock control, and stock-backed billing for this tenant.',
    )
    advancedInvoicingEnabled = models.BooleanField(
        default=True,
        help_text='Enable advanced GST/B2B/B2C invoicing rules, buyer tax details, and invoice-series nature control.',
    )
    nonGstShadowEnabled = models.BooleanField(
        default=False,
        help_text='When disabled, Non-GST inventory and billing flows stay hidden and blocked for this tenant.',
    )
    nonGstShadowCodeHash = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text='Hashed 6-digit admin access code used to enable or disable Non-GST shadow mode.',
    )
    enableAttendanceLocationMaps = models.BooleanField(
        default=False,
        help_text='Enable reverse geolocation and map display on attendance records for this tenant.',
    )

    def set_non_gst_shadow_code(self, raw_code: str) -> None:
        self.nonGstShadowCodeHash = make_password(str(raw_code or '').strip())

    def check_non_gst_shadow_code(self, raw_code: str) -> bool:
        if not self.nonGstShadowCodeHash:
            return False
        return check_password(str(raw_code or '').strip(), self.nonGstShadowCodeHash)

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


class CredentialShareToken(TenantOwnedModel):
    class SubjectType(models.TextChoices):
        STAFF = 'staff', 'Staff'
        PARTY = 'party', 'Customer'

    class Purpose(models.TextChoices):
        INITIAL_LOGIN = 'initial_login', 'Initial Login'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='credential_share_tokens')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='credential_share_tokens')
    staffUser = models.ForeignKey(
        'StaffUser',
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name='credential_share_tokens',
    )
    party = models.ForeignKey(
        'Party',
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name='credential_share_tokens',
    )
    subjectType = models.CharField(max_length=20, choices=SubjectType.choices)
    purpose = models.CharField(max_length=30, choices=Purpose.choices, default=Purpose.INITIAL_LOGIN)
    tokenHash = models.CharField(max_length=64, unique=True)
    expiresAt = models.DateTimeField()
    usedAt = models.DateTimeField(blank=True, null=True)
    createdBy = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='generated_credential_share_tokens',
    )
    claimedFromIP = models.GenericIPAddressField(blank=True, null=True)

    def __str__(self):
        subject_label = self.staffUser.name if self.staffUser_id else (self.party.name if self.party_id else self.user.username)
        return f'{self.get_subjectType_display()} access link for {subject_label}'

    class Meta:
        verbose_name_plural = 'Credential Share Tokens'
        indexes = [
            models.Index(fields=['tenant', 'subjectType', 'expiresAt'], name='credtoken_tenant_subj_exp_idx'),
            models.Index(fields=['user', 'usedAt'], name='credtoken_user_used_idx'),
            models.Index(fields=['tokenHash'], name='credtoken_hash_idx'),
        ]


class AuditLog(TimeStampedModel):
    class Action(models.TextChoices):
        CREATE = 'create', 'Create'
        UPDATE = 'update', 'Update'
        DELETE = 'delete', 'Delete'
        APPROVE = 'approve', 'Approve'
        TRANSFER = 'transfer', 'Transfer'
        PASSWORD_CHANGE = 'password_change', 'Password Change'

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
    legalName = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=200, blank=True, null=True)
    gstNumber = models.CharField(max_length=20, blank=True, null=True)
    isB2B = models.BooleanField(default=False)
    address = models.TextField(blank=True, null=True)
    billingAddress = models.TextField(blank=True, null=True)
    openingBalance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    username = models.CharField(max_length=50, blank=True, null=True, help_text='Legacy field. Use linked Django auth user for login.')
    password = models.CharField(max_length=50, blank=True, null=True, help_text='Legacy field retained for compatibility. Use Django auth password hashing instead.')
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
    otpVerified = models.BooleanField(default=False)
    otpVerifiedOn = models.DateTimeField(blank=True, null=True)

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
    loginPhoto = StdImageField(upload_to='attendance/img', blank=True, variations={
        'thumbnail': (60, 60, True),
        'medium': (200, 200, True),
    }, delete_orphans=True)
    logoutPhoto = StdImageField(upload_to='attendance/img', blank=True, variations={
        'thumbnail': (60, 60, True),
        'medium': (200, 200, True),
    }, delete_orphans=True)
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


class StaffLocationLog(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='staff_location_logs')
    staff = models.ForeignKey(StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='location_logs')
    latitude = models.DecimalField(max_digits=10, decimal_places=7)
    longitude = models.DecimalField(max_digits=10, decimal_places=7)
    accuracy = models.FloatField(blank=True, null=True)
    speed = models.FloatField(blank=True, null=True)
    heading = models.FloatField(blank=True, null=True)
    battery_percent = models.FloatField(blank=True, null=True)
    is_mock_location = models.BooleanField(default=False)
    source = models.CharField(max_length=30, default='pwa')
    client_sample_id = models.CharField(max_length=80, blank=True, null=True)
    recorded_at = models.DateTimeField()

    def __str__(self):
        staff_name = self.staff.name if self.staff_id else 'Unknown staff'
        return f'{staff_name} @ {self.recorded_at}'

    class Meta:
        verbose_name_plural = 'i.1) Staff Location Logs'
        ordering = ['-recorded_at']
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'staff', 'recorded_at'], name='staffloc_tenant_staff_dt_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'recorded_at'], name='staffloc_tenant_dt_idx'),
            models.Index(fields=['tenant', 'staff', 'client_sample_id'], name='staffloc_client_sample_idx'),
        ]
        permissions = [
            ('can_view_staff_locations', 'Can view staff locations'),
            ('can_track_staff_location', 'Can track own staff location'),
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
        APPROVED = "approved", "Confirmed"
        PACKED = "packed", "Packed"
        OUT_FOR_DELIVERY = "out_for_delivery", "Out for Delivery"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        RETURNED = "returned", "Returned"
        FULFILLED = "fulfilled", "Delivered (Legacy)"

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
    assignedTo = models.ForeignKey(
        StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='orders_assigned'
    )
    linkedSales = models.ForeignKey(
        'Sales', blank=True, null=True, on_delete=models.SET_NULL, related_name='source_orders'
    )
    approvedOn = models.DateTimeField(blank=True, null=True)
    approvedBy = models.ForeignKey(
        StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='orders_approved'
    )
    packedOn = models.DateTimeField(blank=True, null=True)
    outForDeliveryOn = models.DateTimeField(blank=True, null=True)
    deliveredOn = models.DateTimeField(blank=True, null=True)
    deliveryOtpVerified = models.BooleanField(default=False)
    deliveryOtpVerifiedOn = models.DateTimeField(blank=True, null=True)
    deliveryOtpVerifiedBy = models.ForeignKey(
        StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='orders_delivery_otp_verified'
    )
    cancelledOn = models.DateTimeField(blank=True, null=True)
    returnedOn = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return self.orderNumber or f'Order#{self.pk}'

    class Meta:
        verbose_name_plural = 'Orders'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'buildDate'], name='order_tenant_del_build_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'status'], name='order_tenant_del_status_idx'),
            models.Index(fields=['tenant', 'partyID'], name='order_tenant_party_idx'),
            models.Index(fields=['tenant', 'createdBy'], name='order_tenant_staff_idx'),
            models.Index(fields=['tenant', 'assignedTo'], name='order_tenant_assigned_idx'),
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


class OrderStatusEvent(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='order_status_events')
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='statusEvents')
    fromStatus = models.CharField(max_length=30, choices=Order.Status.choices, blank=True, null=True)
    toStatus = models.CharField(max_length=30, choices=Order.Status.choices)
    note = models.CharField(max_length=500, blank=True, null=True)
    actor = models.ForeignKey(
        StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='order_status_events'
    )

    class Meta:
        verbose_name_plural = 'Order Status Events'
        indexes = [
            models.Index(fields=['tenant', 'order', 'datetime'], name='ordstat_t_ord_dt_idx'),
        ]


class Sales(TenantOwnedModel):
    class InvoiceKind(models.TextChoices):
        B2C = 'b2c', 'B2C'
        B2B = 'b2b', 'B2B'

    class Mode(models.TextChoices):
        CASH = 'Cash', 'Cash'
        CREDIT = 'Credit', 'Credit'
        ONLINE = 'Online', 'Online'
        MIX = 'Mix', 'Mix'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='sales')
    partyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL)
    salesID = models.CharField(max_length=40, blank=True, null=True, db_index=True)
    paymentID = models.CharField(max_length=200, blank=True, null=True)
    invoiceSeries = models.ForeignKey('InvoiceSeries', blank=True, null=True, on_delete=models.SET_NULL, related_name='sales')
    invoiceSequenceNumber = models.PositiveIntegerField(blank=True, null=True)
    invoiceNumber = models.CharField(max_length=200, default='Cash')
    invoiceKind = models.CharField(max_length=10, choices=InvoiceKind.choices, default=InvoiceKind.B2C)
    buyerLegalName = models.CharField(max_length=255, blank=True, null=True)
    buyerGstNumber = models.CharField(max_length=20, blank=True, null=True)
    buyerBillingAddress = models.TextField(blank=True, null=True)
    placeOfSupply = models.CharField(max_length=120, blank=True, null=True)
    taxableAmount = models.FloatField(default=0.0)
    gstAmount = models.FloatField(default=0.0)
    roundOffAmount = models.FloatField(default=0.0)
    modeOfPayment = models.CharField(max_length=50, choices=Mode.choices, default=Mode.CASH)
    amount = models.FloatField(default=0.0)
    mixCashAmount = models.FloatField(default=0.0)
    mixOnlineAmount = models.FloatField(default=0.0)
    bankID = models.ForeignKey(Bank, blank=True, null=True, on_delete=models.SET_NULL, related_name='sales_entries')
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
            models.Index(fields=['tenant', 'salesID'], name='sales_tenant_salesid_idx'),
            models.Index(fields=['tenant', 'paymentID'], name='sales_tenant_payid_idx'),
        ]
        permissions = [
            ('can_approve_sales', 'Can approve sales'),
            ('can_manage_sales', 'Can manage sales'),
            ('can_view_sales_reports', 'Can view sales reports'),
        ]


class ProformaInvoice(TenantOwnedModel):
    class InvoiceKind(models.TextChoices):
        B2C = 'b2c', 'B2C'
        B2B = 'b2b', 'B2B'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SENT = 'sent', 'Sent'
        ACCEPTED = 'accepted', 'Accepted'
        CONVERTED = 'converted', 'Converted'
        CANCELLED = 'cancelled', 'Cancelled'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='proforma_invoices')
    partyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL, related_name='proforma_invoices')
    proformaID = models.CharField(max_length=40, blank=True, null=True, db_index=True)
    invoiceSeries = models.ForeignKey('InvoiceSeries', blank=True, null=True, on_delete=models.SET_NULL, related_name='proforma_invoices')
    invoiceSequenceNumber = models.PositiveIntegerField(blank=True, null=True)
    invoiceNumber = models.CharField(max_length=200, default='Proforma')
    invoiceKind = models.CharField(max_length=10, choices=InvoiceKind.choices, default=InvoiceKind.B2C)
    buyerLegalName = models.CharField(max_length=255, blank=True, null=True)
    buyerGstNumber = models.CharField(max_length=20, blank=True, null=True)
    buyerBillingAddress = models.TextField(blank=True, null=True)
    placeOfSupply = models.CharField(max_length=120, blank=True, null=True)
    taxableAmount = models.FloatField(default=0.0)
    gstAmount = models.FloatField(default=0.0)
    roundOffAmount = models.FloatField(default=0.0)
    amount = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)
    terms = models.TextField(blank=True, null=True)
    buildDate = models.DateField(blank=True, null=True)
    validUntil = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    convertedSales = models.ForeignKey(Sales, blank=True, null=True, on_delete=models.SET_NULL, related_name='source_proformas')
    convertedOn = models.DateTimeField(blank=True, null=True)
    createdBy = models.ForeignKey(StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='created_proforma_invoices')

    def __str__(self):
        return self.invoiceNumber or self.proformaID or f'Proforma {self.pk}'

    class Meta:
        verbose_name_plural = 'K1) Proforma Invoices'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'buildDate'], name='proforma_tenant_del_date_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'createdBy'], name='proforma_tenant_del_staff_idx'),
            models.Index(fields=['tenant', 'proformaID'], name='proforma_tenant_pid_idx'),
            models.Index(fields=['tenant', 'status'], name='proforma_tenant_status_idx'),
        ]
        permissions = [
            ('can_manage_proforma_invoice', 'Can manage proforma invoices'),
            ('can_convert_proforma_invoice', 'Can convert proforma invoices'),
            ('can_view_proforma_invoice_reports', 'Can view proforma invoice reports'),
        ]


class InventoryItem(TenantOwnedModel):
    class StockBucket(models.TextChoices):
        GST = 'gst', 'GST'
        NON_GST = 'non_gst', 'Non GST'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_items')
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=80, blank=True, null=True)
    barcode = models.CharField(max_length=80, blank=True, null=True)
    category = models.ForeignKey(
        'InventoryCategory',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='items',
    )
    unit = models.ForeignKey(
        'InventoryUnit',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='items',
    )
    taxProfile = models.ForeignKey(
        'InventoryTaxProfile',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='items',
    )
    categoryName = models.CharField(max_length=120, blank=True, null=True)
    brandName = models.CharField(max_length=120, blank=True, null=True)
    unitName = models.CharField(max_length=40, blank=True, null=True, default='pcs')
    hsnCode = models.CharField(max_length=40, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    defaultStockBucket = models.CharField(max_length=20, choices=StockBucket.choices, default=StockBucket.GST)
    gstRate = models.FloatField(default=0.0)
    defaultPurchasePrice = models.FloatField(default=0.0)
    defaultSalesPrice = models.FloatField(default=0.0)
    mrp = models.FloatField(default=0.0)
    minimumStockLevel = models.FloatField(default=0.0)
    reorderLevel = models.FloatField(default=0.0)
    requiresBatchTracking = models.BooleanField(default=False)
    isActive = models.BooleanField(default=True)
    isSaleBlocked = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'Inventory Items'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'name'], name='invitem_tenant_del_name_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'sku'], name='invitem_tenant_del_sku_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'barcode'], name='invitem_tenant_barcode_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'sku'],
                condition=Q(isDeleted=False) & ~Q(sku__isnull=True),
                name='uniq_invsku_tenant',
            ),
        ]
        permissions = [
            ('can_manage_inventory', 'Can manage inventory'),
            ('can_view_inventory_reports', 'Can view inventory reports'),
        ]


class InventoryCategory(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_categories')
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=40, blank=True, null=True)
    description = models.CharField(max_length=300, blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'Inventory Categories'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'name'], name='invcat_tenant_name_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'name'],
                condition=Q(isDeleted=False),
                name='uniq_invcat_name_tenant',
            ),
        ]


class InventoryUnit(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_units')
    name = models.CharField(max_length=120)
    symbol = models.CharField(max_length=20)
    allowsDecimal = models.BooleanField(default=False)
    description = models.CharField(max_length=300, blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.symbol or self.name

    class Meta:
        verbose_name_plural = 'Inventory Units'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'name'], name='invunit_tenant_name_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'symbol'], name='invunit_tenant_symbol_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'name'],
                condition=Q(isDeleted=False),
                name='uniq_invunit_name_tenant',
            ),
            models.UniqueConstraint(
                fields=['tenant', 'symbol'],
                condition=Q(isDeleted=False),
                name='uniq_invunit_symbol_tenant',
            ),
        ]


class InventoryTaxProfile(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_tax_profiles')
    name = models.CharField(max_length=120)
    hsnCode = models.CharField(max_length=40)
    gstRate = models.FloatField(default=0.0)
    cessRate = models.FloatField(default=0.0)
    description = models.CharField(max_length=300, blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'Inventory Tax Profiles'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'name'], name='invtax_tenant_name_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'hsnCode'], name='invtax_tenant_hsn_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'name'],
                condition=Q(isDeleted=False),
                name='uniq_invtax_name_tenant',
            ),
        ]


class InventoryOpeningVoucher(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_opening_vouchers')
    voucherNumber = models.CharField(max_length=40, blank=True, null=True)
    entryDate = models.DateField(blank=True, null=True)
    remark = models.CharField(max_length=500, blank=True, null=True)
    createdBy = models.ForeignKey(
        StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='inventory_opening_vouchers_created'
    )

    def __str__(self):
        return self.voucherNumber or f'Opening#{self.pk}'

    class Meta:
        verbose_name_plural = 'Inventory Opening Vouchers'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'entryDate'], name='invopen_tenant_date_idx'),
        ]


class InventoryWarehouse(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_warehouses')
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=40, blank=True, null=True)
    address = models.CharField(max_length=300, blank=True, null=True)
    isDefault = models.BooleanField(default=False)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'Inventory Warehouses'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'name'], name='invwh_tenant_del_name_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'isDefault'], name='invwh_tenant_default_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'code'],
                condition=Q(isDeleted=False) & ~Q(code__isnull=True),
                name='uniq_invwh_code_tenant',
            ),
        ]


class InventoryOpeningLine(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_opening_lines')
    voucher = models.ForeignKey(InventoryOpeningVoucher, on_delete=models.CASCADE, related_name='lines')
    warehouse = models.ForeignKey(
        InventoryWarehouse,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='opening_lines',
    )
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='opening_lines')
    batch = models.ForeignKey('InventoryBatch', blank=True, null=True, on_delete=models.SET_NULL, related_name='opening_lines')
    stockBucket = models.CharField(max_length=20, choices=InventoryItem.StockBucket.choices)
    quantity = models.FloatField(default=0.0)
    unitCost = models.FloatField(default=0.0)
    lineTotal = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'Inventory Opening Lines'
        indexes = [
            models.Index(fields=['tenant', 'voucher'], name='invopenline_tenant_voucher_idx'),
            models.Index(fields=['tenant', 'item', 'stockBucket'], name='invopenline_tenant_item_idx'),
        ]


class InventoryStock(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_stocks')
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='stock_rows')
    stockBucket = models.CharField(max_length=20, choices=InventoryItem.StockBucket.choices)
    quantityOnHand = models.FloatField(default=0.0)

    def __str__(self):
        return f'{self.item} [{self.get_stockBucket_display()}] = {self.quantityOnHand}'

    class Meta:
        verbose_name_plural = 'Inventory Stock'
        indexes = [
            models.Index(fields=['tenant', 'item', 'stockBucket'], name='invstock_t_item_bkt_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'item', 'stockBucket'], name='uniq_invstock_bucket'),
        ]


class InventoryBatch(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_batches')
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='batches')
    warehouse = models.ForeignKey(
        InventoryWarehouse,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='inventory_batches',
    )
    stockBucket = models.CharField(max_length=20, choices=InventoryItem.StockBucket.choices)
    batchNumber = models.CharField(max_length=120, blank=True, null=True)
    manufactureDate = models.DateField(blank=True, null=True)
    expiryDate = models.DateField(blank=True, null=True)
    purchaseRate = models.FloatField(default=0.0)
    mrp = models.FloatField(default=0.0)
    quantityOnHand = models.FloatField(default=0.0)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        label = self.batchNumber or 'Unbatched'
        return f'{self.item} [{label}] = {self.quantityOnHand}'

    class Meta:
        verbose_name_plural = 'Inventory Batches'
        indexes = [
            models.Index(fields=['tenant', 'item', 'stockBucket'], name='invbatch_t_item_bkt_idx'),
            models.Index(fields=['tenant', 'warehouse', 'expiryDate'], name='invbatch_t_wh_exp_idx'),
            models.Index(fields=['tenant', 'item', 'expiryDate'], name='invbatch_t_item_exp_idx'),
        ]


class InventoryBatchStock(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_batch_stocks')
    batch = models.ForeignKey(InventoryBatch, on_delete=models.CASCADE, related_name='stock_rows')
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='batch_stock_rows')
    warehouse = models.ForeignKey(
        InventoryWarehouse,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='batch_stock_rows',
    )
    stockBucket = models.CharField(max_length=20, choices=InventoryItem.StockBucket.choices)
    quantityOnHand = models.FloatField(default=0.0)

    def __str__(self):
        warehouse = self.warehouse.name if self.warehouse_id else 'Default'
        return f'{self.batch} @ {warehouse} = {self.quantityOnHand}'

    class Meta:
        verbose_name_plural = 'Inventory Batch Stock'
        indexes = [
            models.Index(fields=['tenant', 'item', 'warehouse', 'stockBucket'], name='invbstock_t_item_wh_idx'),
            models.Index(fields=['tenant', 'batch', 'warehouse'], name='invbstock_t_batch_wh_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'batch', 'warehouse'], name='uniq_invbatch_stock_wh'),
        ]


class InventoryLedgerEntry(TenantOwnedModel):
    class SourceType(models.TextChoices):
        OPENING = 'opening', 'Opening Stock'
        PURCHASE = 'purchase', 'Purchase'
        SALES = 'sales', 'Sales'
        CASH_COUNTER = 'cash_counter', 'Cash Counter'
        ADJUSTMENT = 'adjustment', 'Stock Adjustment'
        TRANSFER_OUT = 'transfer_out', 'Stock Transfer Out'
        TRANSFER_IN = 'transfer_in', 'Stock Transfer In'
        PURCHASE_RETURN = 'purchase_return', 'Purchase Return'
        SALES_RETURN = 'sales_return', 'Sales Return'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_ledger_entries')
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='ledger_entries')
    warehouse = models.ForeignKey(
        InventoryWarehouse,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='ledger_entries',
    )
    batch = models.ForeignKey(
        InventoryBatch,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='ledger_entries',
    )
    stockBucket = models.CharField(max_length=20, choices=InventoryItem.StockBucket.choices)
    sourceType = models.CharField(max_length=30, choices=SourceType.choices)
    quantityDelta = models.FloatField(default=0.0)
    unitRate = models.FloatField(default=0.0)
    totalValue = models.FloatField(default=0.0)
    entryDate = models.DateField(blank=True, null=True)
    remark = models.CharField(max_length=500, blank=True, null=True)
    referenceNumber = models.CharField(max_length=120, blank=True, null=True)
    source_content_type = models.ForeignKey(
        ContentType,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='inventory_ledger_entries',
    )
    source_object_id = models.PositiveBigIntegerField(blank=True, null=True)
    source_object = GenericForeignKey('source_content_type', 'source_object_id')

    class Meta:
        verbose_name_plural = 'Inventory Ledger Entries'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'entryDate'], name='invled_tenant_date_idx'),
            models.Index(fields=['tenant', 'item', 'stockBucket'], name='invled_tenant_item_bucket_idx'),
            models.Index(fields=['tenant', 'warehouse', 'entryDate'], name='invled_tenant_wh_date_idx'),
            models.Index(fields=['source_content_type', 'source_object_id'], name='invled_source_idx'),
        ]


class PurchaseInvoice(TenantOwnedModel):
    class GstTreatment(models.TextChoices):
        GST = 'gst', 'GST Invoice'
        NON_GST = 'non_gst', 'Non GST Invoice'
        NON_GST_CLAIMABLE = 'non_gst_claimable', 'Non GST Claimable'

    class ClaimStatus(models.TextChoices):
        NOT_APPLICABLE = 'not_applicable', 'Not Applicable'
        PENDING = 'pending', 'Pending'
        CLAIMED = 'claimed', 'Claimed'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='purchase_invoices')
    partyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL, related_name='purchase_invoices')
    purchaseID = models.CharField(max_length=40, blank=True, null=True, db_index=True)
    invoiceNumber = models.CharField(max_length=200)
    buildDate = models.DateField(blank=True, null=True)
    gstTreatment = models.CharField(max_length=30, choices=GstTreatment.choices, default=GstTreatment.GST)
    claimStatus = models.CharField(max_length=20, choices=ClaimStatus.choices, default=ClaimStatus.NOT_APPLICABLE)
    subtotalAmount = models.FloatField(default=0.0)
    gstAmount = models.FloatField(default=0.0)
    totalAmount = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)
    createdBy = models.ForeignKey(
        StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='purchase_invoices_created'
    )

    def __str__(self):
        return self.invoiceNumber or f'Purchase#{self.pk}'

    class Meta:
        verbose_name_plural = 'Purchase Invoices'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'buildDate'], name='purchase_tenant_del_build_idx'),
            models.Index(fields=['tenant', 'partyID'], name='purchase_tenant_party_idx'),
            models.Index(fields=['tenant', 'purchaseID'], name='purchase_tenant_pid_idx'),
            models.Index(fields=['tenant', 'invoiceNumber'], name='purchase_tenant_inv_idx'),
        ]


class PurchaseInvoiceRevision(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='purchase_invoice_revisions')
    purchaseInvoice = models.ForeignKey(PurchaseInvoice, on_delete=models.CASCADE, related_name='revisions')
    previousGstTreatment = models.CharField(max_length=30, choices=PurchaseInvoice.GstTreatment.choices, blank=True, null=True)
    newGstTreatment = models.CharField(max_length=30, choices=PurchaseInvoice.GstTreatment.choices)
    previousClaimStatus = models.CharField(max_length=20, choices=PurchaseInvoice.ClaimStatus.choices, blank=True, null=True)
    newClaimStatus = models.CharField(max_length=20, choices=PurchaseInvoice.ClaimStatus.choices)
    reason = models.CharField(max_length=500, blank=True, null=True)
    snapshot = models.JSONField(default=dict, blank=True)
    changedBy = models.ForeignKey(
        StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='purchase_invoice_revisions_changed'
    )

    class Meta:
        verbose_name_plural = 'Purchase Invoice Revisions'
        indexes = [
            models.Index(fields=['tenant', 'purchaseInvoice', 'datetime'], name='pinvrev_tenant_purchase_dt_idx'),
        ]


class PurchaseLine(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='purchase_lines')
    purchaseInvoice = models.ForeignKey(PurchaseInvoice, on_delete=models.CASCADE, related_name='lines')
    warehouse = models.ForeignKey(
        InventoryWarehouse,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='purchase_lines',
    )
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='purchase_lines')
    batch = models.ForeignKey(InventoryBatch, blank=True, null=True, on_delete=models.SET_NULL, related_name='purchase_lines')
    stockBucket = models.CharField(max_length=20, choices=InventoryItem.StockBucket.choices)
    quantity = models.FloatField(default=0.0)
    unitCost = models.FloatField(default=0.0)
    taxableAmount = models.FloatField(default=0.0)
    gstRate = models.FloatField(default=0.0)
    gstAmount = models.FloatField(default=0.0)
    lineTotal = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'Purchase Lines'
        indexes = [
            models.Index(fields=['tenant', 'purchaseInvoice'], name='pline_tenant_purchase_idx'),
            models.Index(fields=['tenant', 'item', 'stockBucket'], name='pline_tenant_item_bucket_idx'),
        ]


class CashCounter(TenantOwnedModel):
    class InvoiceKind(models.TextChoices):
        B2C = 'b2c', 'B2C'
        B2B = 'b2b', 'B2B'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='cash_counters')
    partyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL)
    counterID = models.CharField(max_length=200, blank=True, null=True)
    invoiceSeries = models.ForeignKey('InvoiceSeries', blank=True, null=True, on_delete=models.SET_NULL, related_name='cash_counters')
    invoiceSequenceNumber = models.PositiveIntegerField(blank=True, null=True)
    invoiceNumber = models.CharField(max_length=200, blank=True, null=True)
    invoiceKind = models.CharField(max_length=10, choices=InvoiceKind.choices, default=InvoiceKind.B2C)
    buyerLegalName = models.CharField(max_length=255, blank=True, null=True)
    buyerGstNumber = models.CharField(max_length=20, blank=True, null=True)
    buyerBillingAddress = models.TextField(blank=True, null=True)
    placeOfSupply = models.CharField(max_length=120, blank=True, null=True)
    taxableAmount = models.FloatField(default=0.0)
    gstAmount = models.FloatField(default=0.0)
    roundOffAmount = models.FloatField(default=0.0)
    mode = models.CharField(max_length=200, blank=True, null=True)
    amount = models.FloatField(default=0.0)
    mixCashAmount = models.FloatField(default=0.0)
    mixCardAmount = models.FloatField(default=0.0)
    expenseType = models.CharField(max_length=500, blank=True, null=True, default='')
    bankID = models.ForeignKey(Bank, blank=True, null=True, on_delete=models.SET_NULL)
    linkedSalesReturnVoucher = models.ForeignKey(
        'SalesReturnVoucher',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='cash_counter_entries',
    )
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


class SalesLine(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='sales_lines')
    sales = models.ForeignKey(Sales, on_delete=models.CASCADE, related_name='lines')
    warehouse = models.ForeignKey(
        InventoryWarehouse,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='sales_lines',
    )
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='sales_lines')
    batch = models.ForeignKey(InventoryBatch, blank=True, null=True, on_delete=models.SET_NULL, related_name='sales_lines')
    hsnCode = models.CharField(max_length=40, blank=True, null=True)
    stockBucket = models.CharField(max_length=20, choices=InventoryItem.StockBucket.choices)
    quantity = models.FloatField(default=0.0)
    unitPrice = models.FloatField(default=0.0)
    discountPercent = models.FloatField(default=0.0)
    taxableAmount = models.FloatField(default=0.0)
    gstRate = models.FloatField(default=0.0)
    gstAmount = models.FloatField(default=0.0)
    lineTotal = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'Sales Lines'
        indexes = [
            models.Index(fields=['tenant', 'sales'], name='sline_tenant_sales_idx'),
            models.Index(fields=['tenant', 'item', 'stockBucket'], name='sline_tenant_item_bucket_idx'),
        ]


class ProformaInvoiceLine(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='proforma_invoice_lines')
    proformaInvoice = models.ForeignKey(ProformaInvoice, on_delete=models.CASCADE, related_name='lines')
    warehouse = models.ForeignKey(
        InventoryWarehouse,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='proforma_invoice_lines',
    )
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='proforma_invoice_lines')
    batch = models.ForeignKey(InventoryBatch, blank=True, null=True, on_delete=models.SET_NULL, related_name='proforma_invoice_lines')
    hsnCode = models.CharField(max_length=40, blank=True, null=True)
    stockBucket = models.CharField(max_length=20, choices=InventoryItem.StockBucket.choices)
    quantity = models.FloatField(default=0.0)
    unitPrice = models.FloatField(default=0.0)
    discountPercent = models.FloatField(default=0.0)
    taxableAmount = models.FloatField(default=0.0)
    gstRate = models.FloatField(default=0.0)
    gstAmount = models.FloatField(default=0.0)
    lineTotal = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'Proforma Invoice Lines'
        indexes = [
            models.Index(fields=['tenant', 'proformaInvoice'], name='piform_tenant_doc_idx'),
            models.Index(fields=['tenant', 'item', 'stockBucket'], name='piform_tenant_item_bkt_idx'),
        ]


class CashCounterLine(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='cash_counter_lines')
    cashCounter = models.ForeignKey(CashCounter, on_delete=models.CASCADE, related_name='lines')
    warehouse = models.ForeignKey(
        InventoryWarehouse,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='cash_counter_lines',
    )
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='cash_counter_lines')
    batch = models.ForeignKey(InventoryBatch, blank=True, null=True, on_delete=models.SET_NULL, related_name='cash_counter_lines')
    hsnCode = models.CharField(max_length=40, blank=True, null=True)
    stockBucket = models.CharField(max_length=20, choices=InventoryItem.StockBucket.choices)
    quantity = models.FloatField(default=0.0)
    unitPrice = models.FloatField(default=0.0)
    discountPercent = models.FloatField(default=0.0)
    taxableAmount = models.FloatField(default=0.0)
    gstRate = models.FloatField(default=0.0)
    gstAmount = models.FloatField(default=0.0)
    lineTotal = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'Cash Counter Lines'
        indexes = [
            models.Index(fields=['tenant', 'cashCounter'], name='ccline_tenant_counter_idx'),
            models.Index(fields=['tenant', 'item', 'stockBucket'], name='ccline_tenant_item_bucket_idx'),
        ]


class InventoryAdjustmentVoucher(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_adjustment_vouchers')
    voucherNumber = models.CharField(max_length=40, blank=True, null=True)
    entryDate = models.DateField(blank=True, null=True)
    remark = models.CharField(max_length=500, blank=True, null=True)
    createdBy = models.ForeignKey(
        StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='inventory_adjustment_vouchers_created'
    )

    class Meta:
        verbose_name_plural = 'Inventory Adjustment Vouchers'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'entryDate'], name='invadj_tenant_date_idx'),
        ]


class InventoryAdjustmentLine(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_adjustment_lines')
    voucher = models.ForeignKey(InventoryAdjustmentVoucher, on_delete=models.CASCADE, related_name='lines')
    warehouse = models.ForeignKey(InventoryWarehouse, blank=True, null=True, on_delete=models.SET_NULL, related_name='adjustment_lines')
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='adjustment_lines')
    batch = models.ForeignKey(InventoryBatch, blank=True, null=True, on_delete=models.SET_NULL, related_name='adjustment_lines')
    stockBucket = models.CharField(max_length=20, choices=InventoryItem.StockBucket.choices)
    quantityDelta = models.FloatField(default=0.0)
    unitCost = models.FloatField(default=0.0)
    lineTotal = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'Inventory Adjustment Lines'
        indexes = [
            models.Index(fields=['tenant', 'voucher'], name='invadjline_tenant_voucher_idx'),
        ]


class InventoryTransferVoucher(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_transfer_vouchers')
    voucherNumber = models.CharField(max_length=40, blank=True, null=True)
    entryDate = models.DateField(blank=True, null=True)
    fromWarehouse = models.ForeignKey(InventoryWarehouse, blank=True, null=True, on_delete=models.SET_NULL, related_name='transfer_vouchers_from')
    toWarehouse = models.ForeignKey(InventoryWarehouse, blank=True, null=True, on_delete=models.SET_NULL, related_name='transfer_vouchers_to')
    remark = models.CharField(max_length=500, blank=True, null=True)
    createdBy = models.ForeignKey(
        StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='inventory_transfer_vouchers_created'
    )

    class Meta:
        verbose_name_plural = 'Inventory Transfer Vouchers'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'entryDate'], name='invtrn_tenant_date_idx'),
        ]


class InventoryTransferLine(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='inventory_transfer_lines')
    voucher = models.ForeignKey(InventoryTransferVoucher, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='transfer_lines')
    batch = models.ForeignKey(InventoryBatch, blank=True, null=True, on_delete=models.SET_NULL, related_name='transfer_lines')
    stockBucket = models.CharField(max_length=20, choices=InventoryItem.StockBucket.choices)
    quantity = models.FloatField(default=0.0)
    unitCost = models.FloatField(default=0.0)
    lineTotal = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'Inventory Transfer Lines'
        indexes = [
            models.Index(fields=['tenant', 'voucher'], name='invtrnline_tenant_voucher_idx'),
        ]


class PurchaseReturnVoucher(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='purchase_return_vouchers')
    voucherNumber = models.CharField(max_length=40, blank=True, null=True)
    entryDate = models.DateField(blank=True, null=True)
    partyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL, related_name='purchase_return_vouchers')
    purchaseInvoice = models.ForeignKey(PurchaseInvoice, blank=True, null=True, on_delete=models.SET_NULL, related_name='return_vouchers')
    totalAmount = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)
    createdBy = models.ForeignKey(
        StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='purchase_return_vouchers_created'
    )

    class Meta:
        verbose_name_plural = 'Purchase Return Vouchers'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'entryDate'], name='prtn_tenant_date_idx'),
        ]


class PurchaseReturnLine(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='purchase_return_lines')
    voucher = models.ForeignKey(PurchaseReturnVoucher, on_delete=models.CASCADE, related_name='lines')
    warehouse = models.ForeignKey(InventoryWarehouse, blank=True, null=True, on_delete=models.SET_NULL, related_name='purchase_return_lines')
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='purchase_return_lines')
    batch = models.ForeignKey(InventoryBatch, blank=True, null=True, on_delete=models.SET_NULL, related_name='purchase_return_lines')
    stockBucket = models.CharField(max_length=20, choices=InventoryItem.StockBucket.choices)
    quantity = models.FloatField(default=0.0)
    unitCost = models.FloatField(default=0.0)
    lineTotal = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'Purchase Return Lines'
        indexes = [
            models.Index(fields=['tenant', 'voucher'], name='prtnline_tenant_voucher_idx'),
        ]


class SalesReturnVoucher(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='sales_return_vouchers')
    voucherNumber = models.CharField(max_length=40, blank=True, null=True)
    entryDate = models.DateField(blank=True, null=True)
    partyID = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL, related_name='sales_return_vouchers')
    sales = models.ForeignKey(Sales, blank=True, null=True, on_delete=models.SET_NULL, related_name='return_vouchers')
    cashCounter = models.ForeignKey(
        CashCounter,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='return_vouchers',
    )
    totalAmount = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)
    createdBy = models.ForeignKey(
        StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='sales_return_vouchers_created'
    )

    class Meta:
        verbose_name_plural = 'Sales Return Vouchers'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'entryDate'], name='srtn_tenant_date_idx'),
        ]


class SalesReturnLine(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='sales_return_lines')
    voucher = models.ForeignKey(SalesReturnVoucher, on_delete=models.CASCADE, related_name='lines')
    warehouse = models.ForeignKey(InventoryWarehouse, blank=True, null=True, on_delete=models.SET_NULL, related_name='sales_return_lines')
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name='sales_return_lines')
    batch = models.ForeignKey(InventoryBatch, blank=True, null=True, on_delete=models.SET_NULL, related_name='sales_return_lines')
    stockBucket = models.CharField(max_length=20, choices=InventoryItem.StockBucket.choices)
    quantity = models.FloatField(default=0.0)
    unitPrice = models.FloatField(default=0.0)
    lineTotal = models.FloatField(default=0.0)
    remark = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'Sales Return Lines'
        indexes = [
            models.Index(fields=['tenant', 'voucher'], name='srtnline_tenant_voucher_idx'),
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


class TenantSequence(TenantAwareModel):
    class Scope(models.TextChoices):
        COLLECTION_PAYMENT = 'collection_payment', 'Collection Payment'
        SALES = 'sales', 'Sales'
        SALES_PAYMENT = 'sales_payment', 'Sales Payment'
        ORDER = 'order', 'Order'
        PURCHASE = 'purchase', 'Purchase'
        CASH_COUNTER = 'cash_counter', 'Cash Counter'
        CASH_COUNTER_COLLECTION = 'cash_counter_collection', 'Cash Counter Collection'
        INVENTORY_OPENING = 'inventory_opening', 'Inventory Opening'
        INVENTORY_ADJUSTMENT = 'inventory_adjustment', 'Inventory Adjustment'
        INVENTORY_TRANSFER = 'inventory_transfer', 'Inventory Transfer'
        PURCHASE_RETURN = 'purchase_return', 'Purchase Return'
        SALES_RETURN = 'sales_return', 'Sales Return'
        PROFORMA_INVOICE = 'proforma_invoice', 'Proforma Invoice'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='sequences')
    scope = models.CharField(max_length=40, choices=Scope.choices)
    prefix = models.CharField(max_length=20)
    paddingLength = models.PositiveIntegerField(default=6)
    nextNumber = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f'{self.tenant} - {self.prefix}'

    class Meta:
        verbose_name_plural = 'Tenant Sequences'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'scope'], name='unique_tenant_sequence_scope'),
        ]
        indexes = [
            models.Index(fields=['tenant', 'scope'], name='tenantseq_tenant_scope_idx'),
        ]


class InvoiceSeries(TenantOwnedModel):
    class Module(models.TextChoices):
        SALES = 'sales', 'Sales'
        CASH_COUNTER = 'cash_counter', 'Cash Counter'
        PROFORMA = 'proforma', 'Proforma Invoice'

    class InvoiceNature(models.TextChoices):
        MIXED = 'mixed', 'Mixed / Manual'
        B2B = 'b2b', 'B2B'
        B2C = 'b2c', 'B2C'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='invoice_series')
    module = models.CharField(max_length=40, choices=Module.choices)
    name = models.CharField(max_length=200, blank=True, null=True)
    seriesCode = models.CharField(max_length=40)
    startNumber = models.PositiveIntegerField(default=1)
    endNumber = models.PositiveIntegerField(default=9999)
    nextNumber = models.PositiveIntegerField(default=1)
    paddingLength = models.PositiveIntegerField(default=4)
    isActive = models.BooleanField(default=True)
    isDefault = models.BooleanField(default=False)
    invoiceNature = models.CharField(max_length=10, choices=InvoiceNature.choices, default=InvoiceNature.MIXED)
    lockInvoiceNature = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.get_module_display()} - {self.seriesCode}'

    class Meta:
        verbose_name_plural = 'Invoice Series'
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'module', 'seriesCode'], name='unique_invoice_series_per_module'),
        ]
        indexes = [
            models.Index(fields=['tenant', 'module', 'isActive'], name='invser_tenant_active_idx'),
            models.Index(fields=['tenant', 'module', 'isDefault'], name='invser_tenant_default_idx'),
        ]


class BookAccount(TenantOwnedModel):
    class Type(models.TextChoices):
        CASH = 'cash', 'Cash'
        BANK = 'bank', 'Bank'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='book_accounts')
    type = models.CharField(max_length=10, choices=Type.choices)
    name = models.CharField(max_length=200)
    bank = models.ForeignKey(Bank, blank=True, null=True, on_delete=models.SET_NULL, related_name='book_accounts')
    openingBalance = models.FloatField(default=0.0)
    openingDate = models.DateField(blank=True, null=True)
    isDefault = models.BooleanField(default=False)

    def __str__(self):
        if self.type == self.Type.BANK and self.bank_id:
            return f'{self.name} ({self.bank.name})'
        return self.name

    class Meta:
        verbose_name_plural = 'Books: Accounts'
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'type'], name='bookacct_tenant_del_type_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'bank'], name='bookacct_tenant_del_bank_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'type', 'bank'],
                condition=Q(isDeleted=False, type='bank'),
                name='uniq_active_bank_book_account_per_bank',
            ),
            models.UniqueConstraint(
                fields=['tenant', 'type'],
                condition=Q(isDeleted=False, type='cash', isDefault=True),
                name='uniq_active_default_cash_book_account',
            ),
        ]
        permissions = [
            ('can_view_bank_books', 'Can view bank books'),
            ('can_manage_books', 'Can manage cashbook/bankbook'),
        ]


class BookTransaction(TenantOwnedModel):
    class Direction(models.TextChoices):
        IN = 'in', 'In'
        OUT = 'out', 'Out'

    class Source(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        COLLECTION = 'collection', 'Collection'
        SALES = 'sales', 'Sales'
        CASH_COUNTER = 'cash_counter', 'Cash Counter'
        COUNTER_COLLECTION = 'counter_collection', 'Counter Collection'
        TRANSFER = 'transfer', 'Transfer'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='book_transactions')
    account = models.ForeignKey(BookAccount, on_delete=models.CASCADE, related_name='transactions')
    direction = models.CharField(max_length=10, choices=Direction.choices)
    amount = models.FloatField(default=0.0)
    txDateTime = models.DateTimeField()
    narration = models.CharField(max_length=500, blank=True, null=True)
    party = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL, related_name='book_transactions')
    createdBy = models.ForeignKey(StaffUser, blank=True, null=True, on_delete=models.SET_NULL, related_name='book_transactions_created')
    source = models.CharField(max_length=30, choices=Source.choices, default=Source.MANUAL)
    component = models.CharField(max_length=30, blank=True, null=True, help_text='Disambiguates multi-part postings (e.g., Mix cash vs bank).')
    transferId = models.CharField(max_length=50, blank=True, null=True)

    source_content_type = models.ForeignKey(ContentType, blank=True, null=True, on_delete=models.SET_NULL, related_name='book_transactions')
    source_object_id = models.PositiveBigIntegerField(blank=True, null=True)
    source_object = GenericForeignKey('source_content_type', 'source_object_id')

    def __str__(self):
        return f'{self.account} {self.direction} {self.amount}'

    class Meta:
        verbose_name_plural = 'Books: Transactions'
        ordering = ['txDateTime', 'id']
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'account', 'txDateTime'], name='booktx_tenant_del_acct_dt_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'source', 'txDateTime'], name='booktx_tenant_del_src_dt_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'transferId'], name='booktx_tenant_del_transfer_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'account', 'source_content_type', 'source_object_id', 'component'],
                condition=Q(isDeleted=False),
                name='uniq_active_booktx_per_source_component',
            ),
        ]


class PartyLedgerEntry(TenantOwnedModel):
    """
    Party-centric ledger line (single-sided view).

    Convention:
    - `debit`  increases party balance (party owes more)
    - `credit` decreases party balance (party has paid / settlement)
    """

    class Source(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        COLLECTION = 'collection', 'Collection'
        COUNTER_COLLECTION = 'counter_collection', 'Counter Collection'
        SALES = 'sales', 'Sales'
        CASH_COUNTER = 'cash_counter', 'Cash Counter'
        TRANSFER = 'transfer', 'Transfer'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='party_ledger_entries')
    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name='ledger_entries')
    txDateTime = models.DateTimeField()
    particulars = models.CharField(max_length=500)
    debit = models.FloatField(default=0.0)
    credit = models.FloatField(default=0.0)
    createdBy = models.ForeignKey(
        StaffUser,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='party_ledger_entries_created',
    )
    source = models.CharField(max_length=30, choices=Source.choices, default=Source.MANUAL)
    reference = models.CharField(max_length=200, blank=True, null=True)
    component = models.CharField(max_length=30, blank=True, null=True)
    transferId = models.CharField(max_length=50, blank=True, null=True)

    source_content_type = models.ForeignKey(ContentType, blank=True, null=True, on_delete=models.SET_NULL, related_name='party_ledger_entries')
    source_object_id = models.PositiveBigIntegerField(blank=True, null=True)
    source_object = GenericForeignKey('source_content_type', 'source_object_id')

    def __str__(self):
        return f'{self.party} D{self.debit} C{self.credit}'

    class Meta:
        verbose_name_plural = 'Party Ledger: Entries'
        ordering = ['txDateTime', 'id']
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'party', 'txDateTime'], name='pldg_t_party_dt_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'source', 'txDateTime'], name='pldg_t_src_dt_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'transferId'], name='pldg_t_xfer_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'party', 'source_content_type', 'source_object_id', 'component'],
                condition=Q(isDeleted=False),
                name='uniq_active_party_ledger_per_source_component',
            ),
        ]


class JournalEntry(TenantOwnedModel):
    class Source(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        COLLECTION = 'collection', 'Collection'
        COUNTER_COLLECTION = 'counter_collection', 'Counter Collection'
        SALES = 'sales', 'Sales'
        CASH_COUNTER = 'cash_counter', 'Cash Counter'
        TRANSFER = 'transfer', 'Transfer'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='journal_entries')
    entryDateTime = models.DateTimeField()
    reference = models.CharField(max_length=200, blank=True, null=True)
    narration = models.CharField(max_length=500, blank=True, null=True)
    source = models.CharField(max_length=30, choices=Source.choices, default=Source.MANUAL)
    createdBy = models.ForeignKey(
        StaffUser,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='journal_entries_created',
    )
    source_content_type = models.ForeignKey(ContentType, blank=True, null=True, on_delete=models.SET_NULL, related_name='journal_entries')
    source_object_id = models.PositiveBigIntegerField(blank=True, null=True)
    source_object = GenericForeignKey('source_content_type', 'source_object_id')

    def __str__(self):
        source_label = self.get_source_display() if self.source else 'Journal'
        reference = self.reference or f'#{self.pk}'
        if self.entryDateTime:
            return f'{source_label} - {reference} - {self.entryDateTime.strftime("%d-%m-%Y %I:%M %p")}'
        return f'{source_label} - {reference}'

    class Meta:
        verbose_name_plural = 'Journal Entries'
        ordering = ['entryDateTime', 'id']
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'entryDateTime'], name='journal_tenant_dt_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'source'], name='journal_tenant_src_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'source_content_type', 'source_object_id'],
                condition=Q(isDeleted=False),
                name='uniq_active_journal_per_source',
            ),
        ]


class JournalLine(TenantOwnedModel):
    class AccountType(models.TextChoices):
        CASH = 'cash', 'Cash'
        BANK = 'bank', 'Bank'
        PARTY = 'party', 'Party'
        SALES = 'sales', 'Sales'
        EXPENSE = 'expense', 'Expense'
        ADJUSTMENT = 'adjustment', 'Adjustment'
        ADVANCE = 'advance', 'Advance'

    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE, related_name='journal_lines')
    journalEntry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name='lines')
    lineNo = models.PositiveIntegerField(default=1)
    accountType = models.CharField(max_length=20, choices=AccountType.choices)
    bookAccount = models.ForeignKey(BookAccount, blank=True, null=True, on_delete=models.SET_NULL, related_name='journal_lines')
    party = models.ForeignKey(Party, blank=True, null=True, on_delete=models.SET_NULL, related_name='journal_lines')
    debit = models.FloatField(default=0.0)
    credit = models.FloatField(default=0.0)
    note = models.CharField(max_length=255, blank=True, null=True)
    component = models.CharField(max_length=30, blank=True, null=True)

    def __str__(self):
        reference = getattr(self.journalEntry, 'reference', None) or f'Journal#{self.journalEntry_id}'
        return f'{reference} | {self.get_accountType_display()} | D{self.debit} C{self.credit}'

    class Meta:
        verbose_name_plural = 'Journal Lines'
        ordering = ['journalEntry_id', 'lineNo', 'id']
        indexes = [
            models.Index(fields=['tenant', 'isDeleted', 'journalEntry', 'lineNo'], name='jrnline_tenant_entry_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'accountType'], name='jrnline_tenant_account_idx'),
            models.Index(fields=['tenant', 'isDeleted', 'party'], name='jrnline_tenant_party_idx'),
        ]


class UserTwoFactorAuth(TenantOwnedModel):
    tenant = models.ForeignKey(Tenant, blank=True, null=True, on_delete=models.CASCADE)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='two_factor_auth')
    secret = models.CharField(max_length=64)
    isEnabled = models.BooleanField(default=False)
    confirmedAt = models.DateTimeField(blank=True, null=True)
    lastUsedStep = models.BigIntegerField(blank=True, null=True)

    class Meta:
        verbose_name_plural = 'User Two Factor Auth'
        indexes = [
            models.Index(fields=['tenant', 'isEnabled'], name='user2fa_tenant_enabled_idx'),
            models.Index(fields=['user', 'isEnabled'], name='user2fa_user_enabled_idx'),
        ]


class TenantAiUsage(TenantAwareModel):
    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name='ai_usage')
    plan_name = models.CharField(max_length=50, default='Free')
    max_queries_per_month = models.IntegerField(default=5)
    queries_used_this_month = models.IntegerField(default=0)
    last_reset_date = models.DateField(auto_now_add=True)

    def __str__(self):
        return f"{self.tenant.name if self.tenant else 'No Tenant'} - {self.plan_name} ({self.queries_used_this_month}/{self.max_queries_per_month})"

    def check_and_reset_usage(self):
        from datetime import date
        today = date.today()
        if not self.last_reset_date or self.last_reset_date.year != today.year or self.last_reset_date.month != today.month:
            self.queries_used_this_month = 0
            self.last_reset_date = today
            self.save()
            return True
        return False

    class Meta:
        verbose_name_plural = 'Tenant AI Usages'
        indexes = [
            models.Index(fields=['tenant', 'plan_name'], name='aiusage_tenant_plan_idx'),
        ]


class ZivaAiChatMessage(TenantOwnedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ziva_ai_chats')
    role = models.CharField(max_length=10, choices=[('user', 'User'), ('bot', 'Bot')])
    message = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    session_id = models.CharField(max_length=64, default='default', db_index=True)
    session_title = models.CharField(max_length=255, default='General Chat')

    def __str__(self):
        return f"{self.user} ({self.role}): {self.message[:30]}"

    class Meta:
        verbose_name_plural = 'Ziva AI Chat Messages'
        ordering = ['timestamp']
        indexes = [
            models.Index(fields=['tenant', 'user', 'session_id', 'timestamp'], name='aichat_tenant_user_sess_idx'),
        ]


