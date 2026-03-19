from __future__ import annotations

import base64
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from home.models import Party, StaffGroup, StaffUser, Tenant


User = get_user_model()

# 1x1 transparent PNG
_TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMA"
    "ASsJTYQAAAAASUVORK5CYII="
)


def tiny_png(name: str = "image.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name=name,
        content=base64.b64decode(_TINY_PNG_BASE64),
        content_type="image/png",
    )


class SeededTenantMixin:
    tenant: Tenant
    owner: User
    admin: User
    collector: User
    sales: User
    cash: User
    audit: User
    customer: User
    party: Party
    owner_staff: StaffUser

    @classmethod
    def setUpTestData(cls):
        stdout = StringIO()
        call_command("seed_mock_data", stdout=stdout)

        cls.tenant = Tenant.objects.get(slug="alpha-traders")
        cls.owner = User.objects.get(username="alpha.owner")

        tenant_key = cls.tenant.slug.replace("-", "")
        cls.admin = User.objects.get(username=f"{tenant_key}.admin")
        cls.collector = User.objects.get(username=f"{tenant_key}.collector")
        cls.sales = User.objects.get(username=f"{tenant_key}.sales")
        cls.cash = User.objects.get(username=f"{tenant_key}.cash")
        cls.audit = User.objects.get(username=f"{tenant_key}.audit")
        cls.customer = User.objects.get(username=f"{tenant_key}.customer")
        cls.party = Party.objects.get(tenant=cls.tenant, userID=cls.customer)
        cls.owner_staff = StaffUser.objects.get(tenant=cls.tenant, user_ID=cls.owner)

    @classmethod
    def any_staff_group(cls) -> StaffGroup:
        return StaffGroup.objects.filter(tenant=cls.tenant, isDeleted=False).order_by("id").first()

