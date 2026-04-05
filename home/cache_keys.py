from __future__ import annotations

from typing import Any

from home.tenancy import get_current_tenant


def tenant_cache_prefix(request_or_user: Any = None, *, tenant: Any = None) -> str:
    tenant_obj = tenant if tenant is not None else get_current_tenant(request_or_user)
    tenant_id = getattr(tenant_obj, "pk", tenant_obj)
    return f"tenant:{tenant_id if tenant_id is not None else 'none'}"


def tenant_cache_key(*parts: Any, request_or_user: Any = None, tenant: Any = None) -> str:
    segments = [tenant_cache_prefix(request_or_user, tenant=tenant)]
    segments.extend(str(part) for part in parts)
    return ":".join(segments)


def party_list_version_cache_key(request_or_user: Any = None, *, tenant: Any = None) -> str:
    return tenant_cache_key("party_list_cache_version", request_or_user=request_or_user, tenant=tenant)


def party_list_cache_key(*parts: Any, request_or_user: Any = None, tenant: Any = None) -> str:
    return tenant_cache_key("PartyList", *parts, request_or_user=request_or_user, tenant=tenant)


def inventory_list_version_cache_key(resource: str, request_or_user: Any = None, *, tenant: Any = None) -> str:
    return tenant_cache_key(f"inventory_{resource}_version", request_or_user=request_or_user, tenant=tenant)


def inventory_list_cache_key(resource: str, *parts: Any, request_or_user: Any = None, tenant: Any = None) -> str:
    return tenant_cache_key(f"Inventory{resource.title()}List", *parts, request_or_user=request_or_user, tenant=tenant)
