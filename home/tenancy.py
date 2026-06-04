from django.db.models import Q

from .models import StaffUser, Tenant, TenantMembership


def get_current_tenant(request_or_user=None):
    if request_or_user is not None and hasattr(request_or_user, '_cached_current_tenant'):
        return request_or_user._cached_current_tenant
    user = getattr(request_or_user, 'user', request_or_user)
    if user is not None and hasattr(user, '_cached_current_tenant'):
        return user._cached_current_tenant
    if user is not None and getattr(user, 'is_authenticated', False):
        if getattr(user, 'is_superuser', False) and request_or_user is not None and hasattr(request_or_user, 'session'):
            tenant_id = request_or_user.session.get('superuser_current_tenant_id')
            if tenant_id:
                membership_tenant = Tenant.objects.filter(pk=tenant_id).first()
                if membership_tenant:
                    setattr(request_or_user, '_cached_current_tenant', membership_tenant)
                    setattr(user, '_cached_current_tenant', membership_tenant)
                    return membership_tenant
        membership = TenantMembership.objects.select_related('tenant').filter(
            user=user,
            isDefault=True,
        ).first()
        if membership:
            if request_or_user is not None:
                setattr(request_or_user, '_cached_current_tenant', membership.tenant)
            setattr(user, '_cached_current_tenant', membership.tenant)
            return membership.tenant
        membership = TenantMembership.objects.select_related('tenant').filter(user=user).first()
        if membership:
            if request_or_user is not None:
                setattr(request_or_user, '_cached_current_tenant', membership.tenant)
            setattr(user, '_cached_current_tenant', membership.tenant)
            return membership.tenant
    tenant = Tenant.objects.only('id', 'name', 'slug', 'enableAttendanceLocationMaps').order_by('id').first()
    if request_or_user is not None:
        setattr(request_or_user, '_cached_current_tenant', tenant)
    if user is not None:
        setattr(user, '_cached_current_tenant', tenant)
    return tenant


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


def tenant_object_or_superuser(request_or_user, model_or_qs, **filters):
    qs = model_or_qs._default_manager.all() if hasattr(model_or_qs, '_default_manager') else model_or_qs
    obj = tenant_qs(request_or_user, qs).filter(**filters).first()
    if obj is not None:
        return obj
    user = getattr(request_or_user, 'user', request_or_user)
    if not getattr(user, 'is_superuser', False):
        return None
    obj = qs.filter(**filters).first()
    tenant = getattr(obj, 'tenant', None)
    if obj is not None and tenant is not None:
        if request_or_user is not None:
            setattr(request_or_user, '_cached_current_tenant', tenant)
            if hasattr(request_or_user, 'session'):
                request_or_user.session['superuser_current_tenant_id'] = tenant.pk
        if user is not None:
            setattr(user, '_cached_current_tenant', tenant)
    return obj


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
    if hasattr(request, '_cached_staff_user'):
        return request._cached_staff_user
    staff_user = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk).first()
    request._cached_staff_user = staff_user
    return staff_user
