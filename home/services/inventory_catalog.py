from home.models import InventoryCategory, InventoryItem, InventoryTaxProfile, InventoryUnit
from home.services.inventory_lines import normalize_stock_bucket, safe_sales_float
from home.tenancy import tenant_qs


def parse_inventory_category(request, raw_category_id):
    raw = str(raw_category_id or '').strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError('Invalid category selection.')
    category = tenant_qs(request, InventoryCategory).filter(isDeleted=False, pk=int(raw)).first()
    if category is None:
        raise ValueError('Inventory category not found.')
    return category


def parse_inventory_tax_profile(request, raw_tax_profile_id):
    raw = str(raw_tax_profile_id or '').strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError('Invalid HSN / tax selection.')
    tax_profile = tenant_qs(request, InventoryTaxProfile).filter(isDeleted=False, pk=int(raw)).first()
    if tax_profile is None:
        raise ValueError('Inventory tax profile not found.')
    return tax_profile


def parse_inventory_unit(request, raw_unit_id):
    raw = str(raw_unit_id or '').strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError('Invalid unit selection.')
    unit = tenant_qs(request, InventoryUnit).filter(isDeleted=False, pk=int(raw)).first()
    if unit is None:
        raise ValueError('Inventory unit not found.')
    return unit


def apply_inventory_category_payload(request, category):
    category.name = (request.POST.get('name') or '').strip()
    if not category.name:
        raise ValueError('Category name is required.')
    category.code = (request.POST.get('code') or '').strip() or None
    category.description = (request.POST.get('description') or '').strip() or None
    category.isActive = str(request.POST.get('isActive') or '1').lower() not in {'0', 'false', 'no', 'off'}


def apply_inventory_tax_profile_payload(request, tax_profile):
    tax_profile.name = (request.POST.get('name') or '').strip()
    if not tax_profile.name:
        raise ValueError('Tax profile name is required.')
    tax_profile.hsnCode = (request.POST.get('hsnCode') or '').strip()
    if not tax_profile.hsnCode:
        raise ValueError('HSN code is required.')
    tax_profile.gstRate = safe_sales_float(request.POST.get('gstRate'))
    tax_profile.cessRate = safe_sales_float(request.POST.get('cessRate'))
    tax_profile.description = (request.POST.get('description') or '').strip() or None
    tax_profile.isActive = str(request.POST.get('isActive') or '1').lower() not in {'0', 'false', 'no', 'off'}


def apply_inventory_unit_payload(request, unit):
    unit.name = (request.POST.get('name') or '').strip()
    if not unit.name:
        raise ValueError('Unit name is required.')
    unit.symbol = (request.POST.get('symbol') or '').strip()
    if not unit.symbol:
        raise ValueError('Unit symbol is required.')
    unit.allowsDecimal = str(request.POST.get('allowsDecimal') or '').lower() in {'1', 'true', 'yes', 'on'}
    unit.description = (request.POST.get('description') or '').strip() or None
    unit.isActive = str(request.POST.get('isActive') or '1').lower() not in {'0', 'false', 'no', 'off'}


def apply_inventory_item_payload(request, item: InventoryItem):
    item.name = (request.POST.get('name') or '').strip()
    if not item.name:
        raise ValueError('Item name is required.')
    item.sku = (request.POST.get('sku') or '').strip() or None
    item.barcode = (request.POST.get('barcode') or '').strip() or None
    item.category = parse_inventory_category(request, request.POST.get('categoryId'))
    item.unit = parse_inventory_unit(request, request.POST.get('unitId'))
    item.taxProfile = parse_inventory_tax_profile(request, request.POST.get('taxProfileId'))
    item.categoryName = (request.POST.get('categoryName') or '').strip() or None
    if item.category_id:
        item.categoryName = item.category.name
    item.brandName = (request.POST.get('brandName') or '').strip() or None
    item.unitName = (request.POST.get('unitName') or '').strip() or 'pcs'
    if item.unit_id:
        item.unitName = item.unit.symbol
    item.hsnCode = (request.POST.get('hsnCode') or '').strip() or None
    item.description = (request.POST.get('description') or '').strip() or None
    item.defaultStockBucket = normalize_stock_bucket(request, request.POST.get('defaultStockBucket'), item)
    item.gstRate = safe_sales_float(request.POST.get('gstRate'))
    if item.taxProfile_id:
        item.hsnCode = item.taxProfile.hsnCode
        item.gstRate = item.taxProfile.gstRate
    item.defaultPurchasePrice = safe_sales_float(request.POST.get('defaultPurchasePrice'))
    item.defaultSalesPrice = safe_sales_float(request.POST.get('defaultSalesPrice'))
    item.mrp = safe_sales_float(request.POST.get('mrp'))
    item.minimumStockLevel = safe_sales_float(request.POST.get('minimumStockLevel'))
    item.reorderLevel = safe_sales_float(request.POST.get('reorderLevel'))
    item.requiresBatchTracking = str(request.POST.get('requiresBatchTracking') or '').lower() in {'1', 'true', 'yes', 'on'}
    item.isSaleBlocked = str(request.POST.get('isSaleBlocked') or '').lower() in {'1', 'true', 'yes', 'on'}
