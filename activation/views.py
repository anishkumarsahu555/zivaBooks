from functools import wraps

from django.shortcuts import render, redirect

# Create your views here.
from .models import *
from datetime import date

from home.tenancy import get_current_tenant
from home.models import Party, TenantMembership
from home.tenancy import tenant_qs


def _validity_for_request(request, model_cls):
    user = getattr(request, "user", None)
    tenant = get_current_tenant(request) if (user and user.is_authenticated) else None

    qs = model_cls.objects.all()
    if tenant is not None:
        validity = qs.filter(tenant=tenant).order_by("-expiryDate", "-id").first()
        if validity is not None:
            return validity
    return qs.filter(tenant__isnull=True).order_by("-expiryDate", "-id").first()


def _is_expired(validity) -> bool:
    if validity is None:
        return False
    expiry = getattr(validity, "expiryDate", None)
    if not expiry:
        return False
    return expiry < date.today()


def is_activated():
    def _is_activated(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            try:
                val = _validity_for_request(request, Validity)
                if _is_expired(val):
                    return redirect('/activate/')
                return view_func(request, *args, **kwargs)
            except Exception:
                return view_func(request, *args, **kwargs)

        return wrapper

    return _is_activated



def is_ecom_activated():
    def _is_ecom_activated(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            try:
                val = _validity_for_request(request, EcomValidity)
                if _is_expired(val):
                    return redirect('/activate/')
                return view_func(request, *args, **kwargs)
            except Exception:
                return view_func(request, *args, **kwargs)

        return wrapper

    return _is_ecom_activated


def activate(request):
    user = getattr(request, 'user', None)
    if user and user.is_authenticated and TenantMembership.objects.filter(
        user=user,
        role=TenantMembership.Role.PARTY,
    ).exists():
        party = tenant_qs(request, Party).filter(
            userID_id=user.pk,
            isDeleted=False,
        ).only('id', 'name').order_by('-id').first()
        return render(request, 'activation/customer_activate.html', {'party': party})
    return render(request, 'activation/activate.html')
