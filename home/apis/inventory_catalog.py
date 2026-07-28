from collections import defaultdict
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.http import JsonResponse

from home.cache_keys import inventory_list_cache_key, inventory_list_version_cache_key
from home.inventory import rebuild_inventory_stock
from home.models import InventoryBatch, InventoryCategory, InventoryItem, InventoryLedgerEntry, InventoryStock, InventoryTaxProfile, InventoryUnit
from home.services.collections import has_any_permission
from home.services.inventory_catalog import (
    apply_inventory_category_payload,
    apply_inventory_item_payload,
    apply_inventory_tax_profile_payload,
    apply_inventory_unit_payload,
)
from utils.logger import logger
from home.services.inventory_lines import safe_local_date
from home.tenancy import assign_tenant, get_current_tenant, tenant_qs


def _can_manage_inventory_records(user):
    return has_any_permission(user, 'home.can_manage_inventory', 'home.add_inventoryitem', 'home.change_inventoryitem', 'home.can_view_party_credentials')


def _get_inventory_list_cache_version(resource, request_or_user=None, *, tenant=None):
    version_key = inventory_list_version_cache_key(resource, request_or_user=request_or_user, tenant=tenant)
    version = cache.get(version_key)
    if version is None:
        version = 1
        cache.set(version_key, version, timeout=None)
    return version


def _bump_inventory_list_cache_version(resource, request_or_user=None, *, tenant=None):
    version_key = inventory_list_version_cache_key(resource, request_or_user=request_or_user, tenant=tenant)
    version = cache.get(version_key) or 1
    cache.set(version_key, version + 1, timeout=None)


def _catalog_cache_key(resource, *parts, request_or_user=None, tenant=None):
    version = _get_inventory_list_cache_version(resource, request_or_user=request_or_user, tenant=tenant)
    return inventory_list_cache_key(resource, f'v{version}', *parts, request_or_user=request_or_user, tenant=tenant)


@transaction.atomic
def add_inventory_item_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        item = InventoryItem()
        assign_tenant(item, request)
        apply_inventory_item_payload(request, item)
        item.save()
        rebuild_inventory_stock(item.tenant, [item.pk])
        return JsonResponse({'message': 'success', 'itemId': item.pk}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_item_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        item = tenant_qs(request, InventoryItem).get(pk=int(request.POST.get('itemId')), isDeleted=False)
        apply_inventory_item_payload(request, item)
        item.save()
        rebuild_inventory_stock(item.tenant, [item.pk])
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_item_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        item = tenant_qs(request, InventoryItem).get(pk=int(request.POST.get('itemId')), isDeleted=False)
        item.isDeleted = True
        item.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        InventoryStock.objects.filter(tenant=item.tenant, item=item).update(isDeleted=True)
        InventoryLedgerEntry.objects.filter(tenant=item.tenant, item=item).update(isDeleted=True)
        _bump_inventory_list_cache_version('items', tenant=item.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_items_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    tenant = get_current_tenant(request)
    cache_key = _catalog_cache_key('items', 'all', request_or_user=request, tenant=tenant)
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse({'message': 'success', 'data': cached}, safe=False)
    non_gst_enabled = bool(getattr(tenant, 'nonGstShadowEnabled', False))
    items = tenant_qs(request, InventoryItem).filter(isDeleted=False).order_by('name')
    payload = []
    stocks = defaultdict(lambda: {'gst': 0.0, 'non_gst': 0.0})
    for row in InventoryStock.objects.filter(tenant=tenant, isDeleted=False, item__isDeleted=False):
        stocks[row.item_id][row.stockBucket] = float(row.quantityOnHand or 0.0)
    today = safe_local_date()
    expiry_cutoff = today + timedelta(days=30)
    batch_summary = defaultdict(lambda: {'count': 0, 'expired': 0, 'expiring': 0, 'nearest': None})
    for batch in InventoryBatch.objects.filter(tenant=tenant, isDeleted=False, item__isDeleted=False, quantityOnHand__gt=0).only('item_id', 'expiryDate'):
        summary = batch_summary[batch.item_id]
        summary['count'] += 1
        if batch.expiryDate:
            if summary['nearest'] is None or batch.expiryDate < summary['nearest']:
                summary['nearest'] = batch.expiryDate
            if batch.expiryDate < today:
                summary['expired'] += 1
            elif batch.expiryDate <= expiry_cutoff:
                summary['expiring'] += 1
    for item in items:
        item_batch_summary = batch_summary[item.pk]
        payload.append({
            'ID': item.pk,
            'Name': item.name,
            'SKU': item.sku or '',
            'Barcode': item.barcode or '',
            'CategoryID': item.category_id,
            'CategoryName': item.categoryName or '',
            'BrandName': item.brandName or '',
            'UnitID': item.unit_id,
            'UnitName': item.unitName or 'pcs',
            'UnitAllowsDecimal': bool(getattr(item.unit, 'allowsDecimal', False)) if item.unit_id else True,
            'TaxProfileID': item.taxProfile_id,
            'HSN': item.hsnCode or '',
            'Description': item.description or '',
            'GSTRate': float(item.gstRate or 0.0),
            'DefaultStockBucket': item.defaultStockBucket if non_gst_enabled else InventoryItem.StockBucket.GST,
            'DefaultPurchasePrice': float(item.defaultPurchasePrice or 0.0),
            'DefaultSalesPrice': float(item.defaultSalesPrice or 0.0),
            'MRP': float(item.mrp or 0.0),
            'MinimumStockLevel': float(item.minimumStockLevel or 0.0),
            'ReorderLevel': float(item.reorderLevel or 0.0),
            'RequiresBatchTracking': bool(item.requiresBatchTracking),
            'IsSaleBlocked': bool(item.isSaleBlocked),
            'BatchCount': item_batch_summary['count'],
            'ExpiredBatchCount': item_batch_summary['expired'],
            'ExpiringBatchCount': item_batch_summary['expiring'],
            'NearestExpiryDate': item_batch_summary['nearest'].strftime('%d/%m/%Y') if item_batch_summary['nearest'] else '',
            'GSTStock': stocks[item.pk]['gst'],
            'NonGSTStock': stocks[item.pk]['non_gst'] if non_gst_enabled else 0.0,
            'IsLowStock': (stocks[item.pk]['gst'] + stocks[item.pk]['non_gst']) <= float(item.minimumStockLevel or 0.0),
        })
    cache.set(cache_key, payload, timeout=60 * 10)
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


@transaction.atomic
def add_inventory_category_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        category = InventoryCategory()
        assign_tenant(category, request)
        apply_inventory_category_payload(request, category)
        category.save()
        _bump_inventory_list_cache_version('categories', tenant=category.tenant)
        return JsonResponse({'message': 'success', 'categoryId': category.pk}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_category_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        category = tenant_qs(request, InventoryCategory).get(pk=int(request.POST.get('categoryId')), isDeleted=False)
        apply_inventory_category_payload(request, category)
        category.save()
        InventoryItem.objects.filter(tenant=category.tenant, category=category, isDeleted=False).update(categoryName=category.name)
        _bump_inventory_list_cache_version('categories', tenant=category.tenant)
        _bump_inventory_list_cache_version('items', tenant=category.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_category_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        category = tenant_qs(request, InventoryCategory).get(pk=int(request.POST.get('categoryId')), isDeleted=False)
        InventoryItem.objects.filter(tenant=category.tenant, category=category, isDeleted=False).update(category=None)
        category.isDeleted = True
        category.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        _bump_inventory_list_cache_version('categories', tenant=category.tenant)
        _bump_inventory_list_cache_version('items', tenant=category.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_categories_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    cache_key = _catalog_cache_key('categories', 'all', request_or_user=request)
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse({'message': 'success', 'data': cached}, safe=False)
    rows = tenant_qs(request, InventoryCategory).filter(isDeleted=False).order_by('name')
    payload = [{
        'ID': row.pk,
        'Name': row.name,
        'Code': row.code or '',
        'Description': row.description or '',
        'IsActive': bool(row.isActive),
        'ItemCount': row.items.filter(isDeleted=False).count(),
    } for row in rows]
    cache.set(cache_key, payload, timeout=60 * 30)
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


@transaction.atomic
def add_inventory_unit_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        unit = InventoryUnit()
        assign_tenant(unit, request)
        apply_inventory_unit_payload(request, unit)
        unit.save()
        _bump_inventory_list_cache_version('units', tenant=unit.tenant)
        return JsonResponse({'message': 'success', 'unitId': unit.pk}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_unit_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        unit = tenant_qs(request, InventoryUnit).get(pk=int(request.POST.get('unitId')), isDeleted=False)
        apply_inventory_unit_payload(request, unit)
        unit.save()
        InventoryItem.objects.filter(tenant=unit.tenant, unit=unit, isDeleted=False).update(unitName=unit.symbol)
        _bump_inventory_list_cache_version('units', tenant=unit.tenant)
        _bump_inventory_list_cache_version('items', tenant=unit.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_unit_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        unit = tenant_qs(request, InventoryUnit).get(pk=int(request.POST.get('unitId')), isDeleted=False)
        InventoryItem.objects.filter(tenant=unit.tenant, unit=unit, isDeleted=False).update(unit=None)
        unit.isDeleted = True
        unit.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        _bump_inventory_list_cache_version('units', tenant=unit.tenant)
        _bump_inventory_list_cache_version('items', tenant=unit.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_units_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    cache_key = _catalog_cache_key('units', 'all', request_or_user=request)
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse({'message': 'success', 'data': cached}, safe=False)
    rows = tenant_qs(request, InventoryUnit).filter(isDeleted=False).order_by('name')
    payload = [{
        'ID': row.pk,
        'Name': row.name,
        'Symbol': row.symbol,
        'AllowsDecimal': bool(row.allowsDecimal),
        'Description': row.description or '',
        'IsActive': bool(row.isActive),
        'ItemCount': row.items.filter(isDeleted=False).count(),
    } for row in rows]
    cache.set(cache_key, payload, timeout=60 * 30)
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


@transaction.atomic
def add_inventory_tax_profile_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        tax_profile = InventoryTaxProfile()
        assign_tenant(tax_profile, request)
        apply_inventory_tax_profile_payload(request, tax_profile)
        tax_profile.save()
        _bump_inventory_list_cache_version('tax_profiles', tenant=tax_profile.tenant)
        return JsonResponse({'message': 'success', 'taxProfileId': tax_profile.pk}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_inventory_tax_profile_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        tax_profile = tenant_qs(request, InventoryTaxProfile).get(pk=int(request.POST.get('taxProfileId')), isDeleted=False)
        apply_inventory_tax_profile_payload(request, tax_profile)
        tax_profile.save()
        InventoryItem.objects.filter(tenant=tax_profile.tenant, taxProfile=tax_profile, isDeleted=False).update(
            hsnCode=tax_profile.hsnCode,
            gstRate=tax_profile.gstRate,
        )
        _bump_inventory_list_cache_version('tax_profiles', tenant=tax_profile.tenant)
        _bump_inventory_list_cache_version('items', tenant=tax_profile.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_inventory_tax_profile_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_inventory_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        tax_profile = tenant_qs(request, InventoryTaxProfile).get(pk=int(request.POST.get('taxProfileId')), isDeleted=False)
        InventoryItem.objects.filter(tenant=tax_profile.tenant, taxProfile=tax_profile, isDeleted=False).update(taxProfile=None)
        tax_profile.isDeleted = True
        tax_profile.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        _bump_inventory_list_cache_version('tax_profiles', tenant=tax_profile.tenant)
        _bump_inventory_list_cache_version('items', tenant=tax_profile.tenant)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


def list_inventory_tax_profiles_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    cache_key = _catalog_cache_key('tax_profiles', 'all', request_or_user=request)
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse({'message': 'success', 'data': cached}, safe=False)
    rows = tenant_qs(request, InventoryTaxProfile).filter(isDeleted=False).order_by('name')
    payload = [{
        'ID': row.pk,
        'Name': row.name,
        'HSNCode': row.hsnCode,
        'GSTRate': float(row.gstRate or 0.0),
        'CessRate': float(row.cessRate or 0.0),
        'Description': row.description or '',
        'IsActive': bool(row.isActive),
        'ItemCount': row.items.filter(isDeleted=False).count(),
    } for row in rows]
    cache.set(cache_key, payload, timeout=60 * 30)
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)
