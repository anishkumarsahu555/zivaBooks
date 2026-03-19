from __future__ import annotations

from typing import Any, Dict, Optional

from django.core.cache import cache
from django.utils import timezone

OTP_TTL_SECONDS = 300
STATUS_TTL_SECONDS = 45


def collection_otp_cache_key(tenant_id, party_id: int) -> str:
    return f'collection_otp:{tenant_id or "none"}:{party_id}'


def set_collection_otp(tenant_id, party_id: int, otp: str, ttl: int = OTP_TTL_SECONDS, meta: Optional[Dict[str, Any]] = None) -> None:
    meta = meta or {}
    cache.set(
        collection_otp_cache_key(tenant_id, party_id),
        {
            'otp': str(otp),
            'created_ts': int(timezone.now().timestamp()),
            'ttl': int(ttl),
            'meta': meta,
        },
        timeout=int(ttl),
    )


def get_collection_otp_payload(tenant_id, party_id: int) -> Optional[Dict[str, Any]]:
    payload = cache.get(collection_otp_cache_key(tenant_id, party_id))
    return payload if isinstance(payload, dict) else None


def clear_collection_otp(tenant_id, party_id: int) -> None:
    cache.delete(collection_otp_cache_key(tenant_id, party_id))


def collection_status_cache_key(tenant_id, party_id: int) -> str:
    return f'collection_otp_status:{tenant_id or "none"}:{party_id}'


def set_collection_status(tenant_id, party_id: int, status: str, meta: Optional[Dict[str, Any]] = None, ttl: int = STATUS_TTL_SECONDS) -> None:
    meta = meta or {}
    cache.set(
        collection_status_cache_key(tenant_id, party_id),
        {
            'status': str(status),
            'created_ts': int(timezone.now().timestamp()),
            'ttl': int(ttl),
            'meta': meta,
        },
        timeout=int(ttl),
    )


def get_collection_status_payload(tenant_id, party_id: int) -> Optional[Dict[str, Any]]:
    payload = cache.get(collection_status_cache_key(tenant_id, party_id))
    return payload if isinstance(payload, dict) else None


def clear_collection_status(tenant_id, party_id: int) -> None:
    cache.delete(collection_status_cache_key(tenant_id, party_id))
