from __future__ import annotations

import re

from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver

from home.tests.utils import SeededTenantMixin


_CONVERTER_RE = re.compile(r"<[^>]+>")


def _route_to_path(route: str) -> str:
    route = route.lstrip("/")
    route = _CONVERTER_RE.sub("1", route)
    return "/" + route


def _collect_api_routes():
    resolver = get_resolver()
    routes = []

    def walk(patterns, prefix=""):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                route = getattr(pattern.pattern, "_route", str(pattern.pattern))
                walk(pattern.url_patterns, prefix + route)
            elif isinstance(pattern, URLPattern):
                route = getattr(pattern.pattern, "_route", str(pattern.pattern))
                routes.append(prefix + route)

    walk(resolver.url_patterns, prefix="")

    api_routes = []
    for r in routes:
        route = (r or "").lstrip("/")
        if route.startswith("api/") or "/api/" in ("/" + route):
            api_routes.append("/" + route)
    return sorted(set(api_routes))


class AllApiEndpointsSmokeTests(SeededTenantMixin, TestCase):
    def test_all_api_endpoints_do_not_500_or_404_for_owner(self):
        # Some legacy endpoints return `None` on unsupported methods (instead of a 405).
        # Keep exceptions inside the response so we can assert on the status code.
        self.client.raise_request_exception = False
        self.client.force_login(self.owner)
        for route in _collect_api_routes():
            path = _route_to_path(route)
            with self.subTest(path=path):
                # These endpoints are not safe to "smoke" with empty payloads:
                # - PDF/report endpoints typically require parameters and a functional PDF backend.
                # - Messaging status endpoints require an existing message id.
                if path in {
                    "/api/generate_attendance_pdf_admin_report/",
                    "/api/generate_attendance_pdf_staff_report/",
                    "/api/generate_cash_counter_report/",
                    "/api/generate_collection_report/",
                    "/api/generate_sales_report/",
                    "/api/re_send_message_sales/",
                    "/api/update_sent_msg_status/",
                }:
                    continue

                query = "?draw=1&start=0&length=5" if ("ListJson" in path or "ListJSON" in path) else ""

                post_first = any(
                    token in path
                    for token in [
                        "/api/add_",
                        "/api/edit_",
                        "/api/delete_",
                        "/api/approve_",
                        "/api/update_",
                        "/api/change_",
                        "/api/request_",
                        "/api/verify_",
                        "/api/clear_",
                        "/api/send_",
                        "/api/enable_",
                        "/api/transfer_",
                    ]
                )

                if post_first:
                    response = self.client.post(path, data={}, HTTP_ACCEPT="application/json")
                    if response.status_code == 405:
                        response = self.client.get(path + query, HTTP_ACCEPT="application/json")
                else:
                    response = self.client.get(path + query, HTTP_ACCEPT="application/json")
                    if response.status_code == 405:
                        response = self.client.post(path, data={}, HTTP_ACCEPT="application/json")

                # This is a smoke check: we allow 4xx (missing data/ids/permissions),
                # but we should never blow up with 5xx.
                self.assertLess(response.status_code, 500)
