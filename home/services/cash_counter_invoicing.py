from utils.logger import logger
from home.models import CashCounter, InvoiceSeries, InventoryItem, Sales, SalesReturnVoucher


def apply_cash_counter_invoice_payload(request, counter: CashCounter, party, *, line_items=None) -> None:
    line_items = line_items if line_items is not None else (
        request_line_items_payload(request) if request_line_items_present(request) else []
    )
    billing_mode = str(counter.mode or '') in cash_counter_billing_modes()
    if billing_mode and advanced_invoicing_enabled(request):
        apply_cash_counter_buyer_payload(request, counter, party)
    else:
        clear_invoice_buyer_payload(counter, b2c_kind=CashCounter.InvoiceKind.B2C)

    if billing_mode and line_items:
        counter.taxableAmount, counter.gstAmount, computed_total = sales_totals_from_lines(line_items)
        counter.roundOffAmount, payable_total = sales_round_off_from_total(computed_total)
        if counter.mode != 'Mix':
            counter.amount = payable_total
        elif abs((safe_sales_float(counter.mixCashAmount) + safe_sales_float(counter.mixCardAmount)) - payable_total) > 0.01:
            err_msg = f'Mix cash counter total must match rounded payable amount of {payable_total:.2f}.'
            logger.error("[CashCounterInvoicing] %s", err_msg)
            raise ValueError(err_msg)
    else:
        base_amount = safe_sales_float(
            counter.amount if counter.mode != 'Mix' else (
                safe_sales_float(counter.mixCashAmount) + safe_sales_float(counter.mixCardAmount)
            )
        )
        counter.taxableAmount = round(base_amount, 2)
        counter.gstAmount = 0.0
        counter.roundOffAmount = 0.0

    if counter.invoiceKind == CashCounter.InvoiceKind.B2C:
        counter.buyerGstNumber = None
    logger.info("[CashCounterInvoicing] Applied cash counter payload for entry %s: Amount=%s", counter.pk, counter.amount)
from home.services.cash_counter import can_manage_cash_counter_records
from home.services.inventory_lines import (
    advanced_invoicing_enabled,
    request_line_items_payload,
    request_line_items_present,
    safe_sales_float,
    sales_round_off_from_total,
    sales_totals_from_lines,
)
from home.services.invoices import (
    apply_cash_counter_buyer_payload,
    clear_invoice_buyer_payload,
    gst_breakup_from_invoice_subject,
)
from home.tenancy import tenant_qs


def cash_counter_billing_modes() -> set[str]:
    return {'Cash', 'Credit', 'Card', 'Mix', 'Return'}


def cash_counter_return_source_modes() -> set[str]:
    return {'Cash', 'Credit', 'Card', 'Mix'}


def parse_return_source_ref(raw_value: str | None):
    raw = str(raw_value or '').strip()
    if not raw:
        return None, None
    if ':' in raw:
        source_type, raw_id = raw.split(':', 1)
        source_type = str(source_type or '').strip().lower()
        raw_id = str(raw_id or '').strip()
        if raw_id.isdigit() and source_type in {'sale', 'cash_counter'}:
            return source_type, int(raw_id)
        return None, None
    if raw.isdigit():
        return 'sale', int(raw)
    return None, None


def gst_breakup_from_cash_counter(counter: CashCounter, tenant) -> dict:
    return gst_breakup_from_invoice_subject(counter, tenant)


def cash_counter_invoice_action_buttons(item: CashCounter, user) -> str:
    actions = []
    if item.mode in cash_counter_billing_modes() and getattr(item, 'invoiceNumber', None):
        actions.append(
            '''<a href="/api/generate_cash_counter_invoice_pdf/?id={}" data-inverted="" data-tooltip="Download Invoice" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular teal icon button"><i class="file pdf outline icon"></i></a>'''.format(item.pk)
        )
    if can_manage_cash_counter_records(user):
        actions.append(
            '''<a href="/edit_cash_counter/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button green"><i class="pen icon"></i></a>'''.format(item.pk)
        )
        actions.append(
            '''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk)
        )
    elif not actions:
        return '<div class="ui tiny label">Denied</div>'
    return ''.join(actions)


def apply_cash_counter_invoice_payload(request, counter: CashCounter, party, *, line_items=None) -> None:
    line_items = line_items if line_items is not None else (
        request_line_items_payload(request) if request_line_items_present(request) else []
    )
    billing_mode = str(counter.mode or '') in cash_counter_billing_modes()
    if billing_mode and advanced_invoicing_enabled(request):
        apply_cash_counter_buyer_payload(request, counter, party)
    else:
        clear_invoice_buyer_payload(counter, b2c_kind=CashCounter.InvoiceKind.B2C)

    if billing_mode and line_items:
        counter.taxableAmount, counter.gstAmount, computed_total = sales_totals_from_lines(line_items)
        counter.roundOffAmount, payable_total = sales_round_off_from_total(computed_total)
        if counter.mode != 'Mix':
            counter.amount = payable_total
        elif abs((safe_sales_float(counter.mixCashAmount) + safe_sales_float(counter.mixCardAmount)) - payable_total) > 0.01:
            raise ValueError(f'Mix cash counter total must match rounded payable amount of {payable_total:.2f}.')
    else:
        base_amount = safe_sales_float(
            counter.amount if counter.mode != 'Mix' else (
                safe_sales_float(counter.mixCashAmount) + safe_sales_float(counter.mixCardAmount)
            )
        )
        counter.taxableAmount = round(base_amount, 2)
        counter.gstAmount = 0.0
        counter.roundOffAmount = 0.0

    if counter.invoiceKind == CashCounter.InvoiceKind.B2C:
        counter.buyerGstNumber = None


def sync_cash_counter_lines(request, counter: CashCounter):
    from home.services.inventory_mutations import sync_cash_counter_lines as sync_lines

    return sync_lines(request, counter)


def sync_cash_counter_return_voucher(request, counter: CashCounter):
    from home.services.inventory_mutations import sync_cash_counter_return_voucher as sync_return_voucher

    return sync_return_voucher(request, counter)
