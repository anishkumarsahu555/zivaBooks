from __future__ import annotations

from datetime import date

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect

from home.tenancy import get_current_tenant

from .models import Validity


def _validity_for_request(request):
    if hasattr(request, "_cached_tenant_validity"):
        return request._cached_tenant_validity
    user = getattr(request, "user", None)
    tenant = get_current_tenant(request) if (user and user.is_authenticated) else None

    qs = Validity.objects.all()
    if tenant is not None:
        validity = qs.filter(tenant=tenant).order_by("-expiryDate", "-id").first()
        if validity is not None:
            request._cached_tenant_validity = validity
            return validity
    validity = qs.filter(tenant__isnull=True).order_by("-expiryDate", "-id").first()
    request._cached_tenant_validity = validity
    return validity


def _is_expired(validity) -> bool:
    if validity is None:
        return False
    expiry = getattr(validity, "expiryDate", None)
    if not expiry:
        return False
    return expiry < date.today()


class TenantActivationMiddleware:
    """
    When a tenant's Validity is expired, block every URL except this allowlist:
    - /home/, / (login), /postLogin/, /logout/, /activate/
    - static + media URLs
    - /admin/
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._is_allowlisted(request):
            return self.get_response(request)

        validity = _validity_for_request(request)
        if not _is_expired(validity):
            return self.get_response(request)

        if self._is_api_request(request):
            return JsonResponse(
                {"message": "license_expired", "redirect": "/activate/"},
                status=403,
            )
        return redirect("/activate/")

    def _is_allowlisted(self, request) -> bool:
        path = request.path or "/"

        static_url = getattr(settings, "STATIC_URL", "/static/") or "/static/"
        media_url = getattr(settings, "MEDIA_URL", "/media/") or "/media/"
        if path.startswith(static_url) or path.startswith(media_url):
            return True

        if path.startswith("/activate/"):
            return True

        if path.startswith("/admin/"):
            return True

        return path in {"/", "/home/", "/postLogin/", "/logout/"}

    def _is_api_request(self, request) -> bool:
        path = request.path or ""
        if "/api/" in path:
            return True
        accept = (request.headers.get("Accept") or "").lower()
        if "application/json" in accept:
            return True
        if (request.headers.get("X-Requested-With") or "") == "XMLHttpRequest":
            return True
        return False
