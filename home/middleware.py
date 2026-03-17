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
