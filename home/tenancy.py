from django.db.models import Q

from .models import StaffUser, Tenant, TenantMembership


def get_current_tenant(request_or_user=None):
    user = getattr(request_or_user, 'user', request_or_user)
    if user is not None and getattr(user, 'is_authenticated', False):
        membership = TenantMembership.objects.select_related('tenant').filter(
            user=user,
            isDefault=True,
        ).first()
        if membership:
            return membership.tenant
        membership = TenantMembership.objects.select_related('tenant').filter(user=user).first()
        if membership:
            return membership.tenant
    return Tenant.objects.order_by('id').first()


def tenant_qs(request_or_user, model_or_qs, include_unassigned=True):
    tenant = get_current_tenant(request_or_user)
    qs = model_or_qs._default_manager.all() if hasattr(model_or_qs, '_default_manager') else model_or_qs
    model = qs.model
    if not any(field.name == 'tenant' for field in model._meta.fields):
        return qs
    if tenant is None:
        return qs
    tenant_filter = Q(tenant=tenant)
    if include_unassigned:
        tenant_filter |= Q(tenant__isnull=True)
    return qs.filter(tenant_filter)


def assign_tenant(instance, request_or_user, force=False):
    if not hasattr(instance, 'tenant_id'):
        return instance
    if force or instance.tenant_id is None:
        tenant = get_current_tenant(request_or_user)
        if tenant is not None:
            instance.tenant = tenant
    return instance


def ensure_membership(user, tenant, role):
    if user is None or tenant is None:
        return None
    membership, created = TenantMembership.objects.get_or_create(
        user=user,
        tenant=tenant,
        defaults={'role': role, 'isDefault': not TenantMembership.objects.filter(user=user).exists()},
    )
    if not created and membership.role != role:
        membership.role = role
        membership.save(update_fields=['role', 'lastUpdatedOn'])
    return membership


def get_staff_user_for_request(request):
    return tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk).first()
