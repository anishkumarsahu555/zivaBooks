import time
from utils.logger import logger
from home.tenancy import get_current_tenant
from .audit import clear_current_user, set_current_user


class AuditUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
        set_current_user(user)
        try:
            return self.get_response(request)
        finally:
            clear_current_user()


class RequestLoggingMiddleware:
    """Logs incoming HTTP request URL, HTTP method, user, tenant, response status, and duration."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start_time = time.monotonic()
        response = self.get_response(request)
        duration_ms = (time.monotonic() - start_time) * 1000

        path = request.get_full_path()
        # Skip static assets and favicon to keep application request logs focused
        if not (path.startswith('/static/') or path.startswith('/media/') or path == '/favicon.ico'):
            user = getattr(request, 'user', None)
            user_info = user.get_username() if user and user.is_authenticated else 'Anonymous'

            tenant = get_current_tenant(request)
            tenant_info = f"{getattr(tenant, 'name', 'N/A')} (ID: {getattr(tenant, 'pk', 'N/A')})" if tenant else 'No-Tenant'

            log_msg = "[HTTP %s] %s -> Status %s | User: %s | Tenant: %s | Duration: %.2fms"
            if response.status_code >= 500:
                logger.error(log_msg, request.method, path, response.status_code, user_info, tenant_info, duration_ms)
            elif response.status_code >= 400:
                logger.warning(log_msg, request.method, path, response.status_code, user_info, tenant_info, duration_ms)
            else:
                logger.info(log_msg, request.method, path, response.status_code, user_info, tenant_info, duration_ms)

        return response
