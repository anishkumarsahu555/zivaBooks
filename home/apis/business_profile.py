"""
Business Profile settings views and endpoints for Tenant profile configuration.
"""

import base64
from django.core.files.base import ContentFile
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from home.tenancy import get_current_tenant
from home.navigation import _has_any_perm, ADMIN_LIKE_PERMS


@require_http_methods(["GET"])
def business_profile_settings(request):
    """Renders business profile settings page."""
    if not request.user.is_authenticated:
        return redirect('homeApp:loginPage')
    if not _has_any_perm(request, ADMIN_LIKE_PERMS):
        return redirect('homeApp:homepage')

    tenant = get_current_tenant(request)
    context = {
        'tenant': tenant,
    }
    return render(request, 'home/settings/businessProfile.html', context)


@require_http_methods(["POST"])
def update_business_profile_api(request):
    """API endpoint to update Tenant business info and Logo."""
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _has_any_perm(request, ADMIN_LIKE_PERMS):
        return JsonResponse({'message': 'forbidden'}, status=403)

    tenant = get_current_tenant(request)
    if tenant is None:
        return JsonResponse({'message': 'error', 'error': 'Tenant not found.'}, status=400)

    try:
        tenant.legalName = request.POST.get('legalName', '').strip() or None
        tenant.gstNumber = request.POST.get('gstNumber', '').strip() or None
        tenant.billingAddress = request.POST.get('billingAddress', '').strip() or None
        tenant.phone = request.POST.get('phone', '').strip() or None

        # Check for cropped logo base64 string
        logo_data = request.POST.get('logo_data', '')
        if logo_data and logo_data.startswith('data:image'):
            fmt, imgstr = logo_data.split(';base64,')
            ext = fmt.split('/')[-1]
            data = ContentFile(base64.b64decode(imgstr), name=f"tenant_logo_{tenant.id}.{ext}")
            tenant.logo.save(f"logo_{tenant.id}.{ext}", data, save=False)
        elif 'logo_file' in request.FILES:
            tenant.logo = request.FILES['logo_file']

        tenant.save()
        return JsonResponse({'message': 'success'})
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)
