from django.http import JsonResponse
from django.views.decorators.cache import cache_page

from home.services.collections import has_any_permission
from home.services.dashboard_summaries import (
    admin_dashboard_summary,
    bank_dashboard_summary,
    orders_dashboard_summary,
    party_dashboard_summary,
    sales_dashboard_summary,
    staff_dashboard_summary,
)


@cache_page(60 * 5)
def get_admin_dashboard_report_api(request):
    return JsonResponse({'data': admin_dashboard_summary(request)}, safe=False)


@cache_page(60 * 5)
def get_staff_dashboard_report_api(request):
    return JsonResponse({'data': staff_dashboard_summary(request)}, safe=False)


@cache_page(60 * 5)
def get_party_dashboard_report_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'forbidden'}, status=403)
    if not has_any_permission(request.user, 'home.can_manage_parties', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403)
    return JsonResponse({'data': party_dashboard_summary(request)}, safe=False)


@cache_page(60 * 5)
def get_bank_dashboard_report_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'forbidden'}, status=403)
    if not has_any_permission(request.user, 'home.can_manage_parties', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403)
    return JsonResponse({'data': bank_dashboard_summary(request)}, safe=False)


@cache_page(60 * 5)
def get_sales_dashboard_report_api(request):
    return JsonResponse({'data': sales_dashboard_summary(request)}, safe=False)


def get_orders_dashboard_report_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'forbidden'}, status=403)
    return JsonResponse({'data': orders_dashboard_summary(request)}, safe=False)
