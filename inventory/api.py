from django.http import JsonResponse

from home.apis import inventory_catalog
from home.apis import inventory_purchases
from home.apis import inventory_reports
from home.apis import inventory_returns
from home.apis import inventory_stock
from home.services.inventory_lines import inventory_feature_enabled


def _inventory_feature_guard(request):
    if inventory_feature_enabled(request):
        return None
    return JsonResponse(
        {
            'message': 'error',
            'error': 'Inventory is disabled for this tenant. Ask an admin to enable it from Billing > Non-GST Control.',
        },
        status=403,
    )


def _wrap_inventory_api(fn):
    def wrapped(request, *args, **kwargs):
        blocked = _inventory_feature_guard(request)
        if blocked is not None:
            return blocked
        return fn(request, *args, **kwargs)

    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


add_inventory_category_api = _wrap_inventory_api(inventory_catalog.add_inventory_category_api)
add_inventory_adjustment_voucher_api = _wrap_inventory_api(inventory_stock.add_inventory_adjustment_voucher_api)
add_inventory_item_api = _wrap_inventory_api(inventory_catalog.add_inventory_item_api)
add_inventory_tax_profile_api = _wrap_inventory_api(inventory_catalog.add_inventory_tax_profile_api)
add_inventory_unit_api = _wrap_inventory_api(inventory_catalog.add_inventory_unit_api)
add_inventory_transfer_voucher_api = _wrap_inventory_api(inventory_stock.add_inventory_transfer_voucher_api)
add_inventory_warehouse_api = _wrap_inventory_api(inventory_stock.add_inventory_warehouse_api)
add_inventory_opening_stock_api = _wrap_inventory_api(inventory_stock.add_inventory_opening_stock_api)
add_purchase_invoice_api = _wrap_inventory_api(inventory_purchases.add_purchase_invoice_api)
add_purchase_return_voucher_api = _wrap_inventory_api(inventory_returns.add_purchase_return_voucher_api)
add_sales_return_voucher_api = _wrap_inventory_api(inventory_returns.add_sales_return_voucher_api)
delete_inventory_adjustment_voucher_api = _wrap_inventory_api(inventory_stock.delete_inventory_adjustment_voucher_api)
delete_inventory_category_api = _wrap_inventory_api(inventory_catalog.delete_inventory_category_api)
delete_inventory_item_api = _wrap_inventory_api(inventory_catalog.delete_inventory_item_api)
delete_inventory_tax_profile_api = _wrap_inventory_api(inventory_catalog.delete_inventory_tax_profile_api)
delete_inventory_unit_api = _wrap_inventory_api(inventory_catalog.delete_inventory_unit_api)
delete_inventory_transfer_voucher_api = _wrap_inventory_api(inventory_stock.delete_inventory_transfer_voucher_api)
delete_inventory_warehouse_api = _wrap_inventory_api(inventory_stock.delete_inventory_warehouse_api)
delete_purchase_invoice_api = _wrap_inventory_api(inventory_purchases.delete_purchase_invoice_api)
delete_purchase_return_voucher_api = _wrap_inventory_api(inventory_returns.delete_purchase_return_voucher_api)
delete_sales_return_voucher_api = _wrap_inventory_api(inventory_returns.delete_sales_return_voucher_api)
deactivate_expired_zero_stock_batches_api = _wrap_inventory_api(inventory_stock.deactivate_expired_zero_stock_batches_api)
get_inventory_snapshot_api = _wrap_inventory_api(inventory_reports.get_inventory_snapshot_api)
get_inventory_adjustment_voucher_detail_api = _wrap_inventory_api(inventory_stock.get_inventory_adjustment_voucher_detail_api)
get_inventory_transfer_voucher_detail_api = _wrap_inventory_api(inventory_stock.get_inventory_transfer_voucher_detail_api)
get_purchase_invoice_detail_api = _wrap_inventory_api(inventory_purchases.get_purchase_invoice_detail_api)
get_purchase_return_voucher_detail_api = _wrap_inventory_api(inventory_returns.get_purchase_return_voucher_detail_api)
get_sales_return_voucher_detail_api = _wrap_inventory_api(inventory_returns.get_sales_return_voucher_detail_api)
list_inventory_adjustment_vouchers_api = _wrap_inventory_api(inventory_stock.list_inventory_adjustment_vouchers_api)
list_inventory_batch_ledger_api = _wrap_inventory_api(inventory_reports.list_inventory_batch_ledger_api)
list_inventory_batch_report_api = _wrap_inventory_api(inventory_reports.list_inventory_batch_report_api)
list_inventory_batches_api = _wrap_inventory_api(inventory_reports.list_inventory_batches_api)
list_inventory_categories_api = _wrap_inventory_api(inventory_catalog.list_inventory_categories_api)
list_inventory_ledger_entries_api = _wrap_inventory_api(inventory_reports.list_inventory_ledger_entries_api)
list_inventory_items_api = _wrap_inventory_api(inventory_catalog.list_inventory_items_api)
list_inventory_opening_vouchers_api = _wrap_inventory_api(inventory_stock.list_inventory_opening_vouchers_api)
list_inventory_tax_profiles_api = _wrap_inventory_api(inventory_catalog.list_inventory_tax_profiles_api)
list_inventory_units_api = _wrap_inventory_api(inventory_catalog.list_inventory_units_api)
list_inventory_transfer_vouchers_api = _wrap_inventory_api(inventory_stock.list_inventory_transfer_vouchers_api)
list_inventory_warehouses_api = _wrap_inventory_api(inventory_stock.list_inventory_warehouses_api)
list_purchase_invoices_api = _wrap_inventory_api(inventory_purchases.list_purchase_invoices_api)
list_purchase_return_vouchers_api = _wrap_inventory_api(inventory_returns.list_purchase_return_vouchers_api)
list_sales_return_vouchers_api = _wrap_inventory_api(inventory_returns.list_sales_return_vouchers_api)
update_inventory_adjustment_voucher_api = _wrap_inventory_api(inventory_stock.update_inventory_adjustment_voucher_api)
update_inventory_category_api = _wrap_inventory_api(inventory_catalog.update_inventory_category_api)
update_inventory_item_api = _wrap_inventory_api(inventory_catalog.update_inventory_item_api)
update_inventory_tax_profile_api = _wrap_inventory_api(inventory_catalog.update_inventory_tax_profile_api)
update_inventory_unit_api = _wrap_inventory_api(inventory_catalog.update_inventory_unit_api)
update_inventory_transfer_voucher_api = _wrap_inventory_api(inventory_stock.update_inventory_transfer_voucher_api)
update_inventory_warehouse_api = _wrap_inventory_api(inventory_stock.update_inventory_warehouse_api)
update_purchase_invoice_api = _wrap_inventory_api(inventory_purchases.update_purchase_invoice_api)
update_purchase_return_voucher_api = _wrap_inventory_api(inventory_returns.update_purchase_return_voucher_api)
update_sales_return_voucher_api = _wrap_inventory_api(inventory_returns.update_sales_return_voucher_api)

__all__ = [
    'add_inventory_category_api',
    'add_inventory_adjustment_voucher_api',
    'add_inventory_item_api',
    'add_inventory_tax_profile_api',
    'add_inventory_unit_api',
    'add_inventory_transfer_voucher_api',
    'add_inventory_warehouse_api',
    'add_inventory_opening_stock_api',
    'add_purchase_invoice_api',
    'add_purchase_return_voucher_api',
    'add_sales_return_voucher_api',
    'delete_inventory_adjustment_voucher_api',
    'delete_inventory_category_api',
    'delete_inventory_item_api',
    'delete_inventory_tax_profile_api',
    'delete_inventory_unit_api',
    'delete_inventory_transfer_voucher_api',
    'delete_inventory_warehouse_api',
    'delete_purchase_invoice_api',
    'delete_purchase_return_voucher_api',
    'delete_sales_return_voucher_api',
    'deactivate_expired_zero_stock_batches_api',
    'get_inventory_snapshot_api',
    'get_inventory_adjustment_voucher_detail_api',
    'get_inventory_transfer_voucher_detail_api',
    'get_purchase_invoice_detail_api',
    'get_purchase_return_voucher_detail_api',
    'get_sales_return_voucher_detail_api',
    'list_inventory_adjustment_vouchers_api',
    'list_inventory_batch_ledger_api',
    'list_inventory_batch_report_api',
    'list_inventory_batches_api',
    'list_inventory_categories_api',
    'list_inventory_ledger_entries_api',
    'list_inventory_items_api',
    'list_inventory_opening_vouchers_api',
    'list_inventory_tax_profiles_api',
    'list_inventory_units_api',
    'list_inventory_transfer_vouchers_api',
    'list_inventory_warehouses_api',
    'list_purchase_invoices_api',
    'list_purchase_return_vouchers_api',
    'list_sales_return_vouchers_api',
    'update_inventory_adjustment_voucher_api',
    'update_inventory_category_api',
    'update_inventory_item_api',
    'update_inventory_tax_profile_api',
    'update_inventory_unit_api',
    'update_inventory_transfer_voucher_api',
    'update_inventory_warehouse_api',
    'update_purchase_invoice_api',
    'update_purchase_return_voucher_api',
    'update_sales_return_voucher_api',
]
