from django.shortcuts import render, redirect, get_object_or_404

from home.views import permission_required_any
from home.tenancy import get_current_tenant, tenant_qs
from home.models import Bank, PurchaseInvoice


def _render_inventory_page(request, page_key, page_title):
    tenant = get_current_tenant(request)
    if tenant is not None and not getattr(tenant, 'inventoryEnabled', True):
        return redirect('homeApp:admin_home')
    return render(request, 'home/inventory/index.html', {
        'inventory_page': page_key,
        'inventory_page_title': page_title,
        'inventory_enabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advanced_invoicing_enabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'can_manage_non_gst_shadow': request.user.is_superuser or request.user.has_perm('home.can_view_party_credentials'),
    })


@permission_required_any('home.add_inventoryitem', 'home.change_inventoryitem', 'home.view_inventoryitem', 'home.can_manage_inventory', 'home.can_view_party_credentials')
def inventory_list(request):
    return _render_inventory_page(request, 'items', 'Items')


@permission_required_any('home.add_inventoryitem', 'home.change_inventoryitem', 'home.view_inventoryitem', 'home.can_manage_inventory', 'home.can_view_party_credentials')
def inventory_items_page(request):
    return _render_inventory_page(request, 'items', 'Items')


@permission_required_any('home.add_inventoryitem', 'home.change_inventoryitem', 'home.view_inventoryitem', 'home.can_manage_inventory', 'home.can_view_party_credentials')
def inventory_masters_page(request):
    return _render_inventory_page(request, 'masters', 'Masters')


@permission_required_any('home.add_inventoryitem', 'home.change_inventoryitem', 'home.view_inventoryitem', 'home.can_manage_inventory', 'home.can_view_party_credentials')
def inventory_stock_page(request):
    return _render_inventory_page(request, 'stock', 'Stock')


@permission_required_any('home.add_inventoryitem', 'home.change_inventoryitem', 'home.view_inventoryitem', 'home.can_manage_inventory', 'home.can_view_party_credentials')
def inventory_vouchers_page(request):
    return _render_inventory_page(request, 'vouchers', 'Adjustments')


@permission_required_any('home.add_purchaseinvoice', 'home.change_purchaseinvoice', 'home.view_purchaseinvoice', 'home.can_view_party_credentials')
def purchase_list(request):
    tenant = get_current_tenant(request)
    if tenant is not None and not getattr(tenant, 'inventoryEnabled', True):
        return redirect('homeApp:admin_home')
    context = {
        'banks': tenant_qs(request, Bank).filter(isDeleted=False).order_by('name'),
        'inventory_enabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advanced_invoicing_enabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'can_manage_non_gst_shadow': request.user.is_superuser or request.user.has_perm('home.can_view_party_credentials'),
    }
    return render(request, 'home/inventory/purchase.html', context)


@permission_required_any('home.add_purchaseinvoice', 'home.change_purchaseinvoice', 'home.view_purchaseinvoice', 'home.can_view_party_credentials')
def purchase_detail(request, id):
    tenant = get_current_tenant(request)
    if tenant is not None and not getattr(tenant, 'inventoryEnabled', True):
        return redirect('homeApp:admin_home')
    purchase = get_object_or_404(
        tenant_qs(request, PurchaseInvoice).select_related('partyID', 'createdBy'),
        pk=id,
        isDeleted=False,
    )
    context = {
        'purchase': purchase,
        'purchase_lines': purchase.lines.filter(isDeleted=False).select_related('item').order_by('id'),
        'purchase_revisions': purchase.revisions.filter(isDeleted=False).select_related('changedBy').order_by('-datetime'),
        'inventory_enabled': bool(getattr(tenant, 'inventoryEnabled', True)),
        'advanced_invoicing_enabled': bool(getattr(tenant, 'advancedInvoicingEnabled', True)),
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
    }
    return render(request, 'home/inventory/purchase_detail.html', context)


@permission_required_any('home.can_view_party_credentials', 'home.can_manage_staff')
def non_gst_shadow_page(request):
    tenant = get_current_tenant(request)
    inventory_enabled = bool(getattr(tenant, 'inventoryEnabled', True))
    advanced_invoicing_enabled = bool(getattr(tenant, 'advancedInvoicingEnabled', True))
    if tenant is not None and not inventory_enabled and not advanced_invoicing_enabled:
        return redirect('homeApp:admin_home')
    return render(request, 'home/inventory/non_gst_shadow.html', {
        'tenant': tenant,
        'inventory_enabled': inventory_enabled,
        'advanced_invoicing_enabled': advanced_invoicing_enabled,
        'non_gst_shadow_enabled': bool(getattr(tenant, 'nonGstShadowEnabled', False)),
        'non_gst_shadow_has_code': bool(getattr(tenant, 'nonGstShadowCodeHash', '')),
    })
