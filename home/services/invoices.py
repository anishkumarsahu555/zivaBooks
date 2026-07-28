from utils.logger import logger
from home.models import CashCounter, InvoiceSeries, Party, ProformaInvoice, Sales
from home.services.inventory_lines import (
    advanced_invoicing_enabled,
    request_line_items_payload,
    request_line_items_present,
    safe_sales_float,
    sales_round_off_from_total,
    sales_totals_from_lines,
)


UNION_TERRITORY_GST_STATE_CODES = {'04', '26', '31', '35', '38', '97'}
GST_STATE_CODE_NAME_MAP = {
    '01': 'jammu and kashmir',
    '02': 'himachal pradesh',
    '03': 'punjab',
    '04': 'chandigarh',
    '05': 'uttarakhand',
    '06': 'haryana',
    '07': 'delhi',
    '08': 'rajasthan',
    '09': 'uttar pradesh',
    '10': 'bihar',
    '11': 'sikkim',
    '12': 'arunachal pradesh',
    '13': 'nagaland',
    '14': 'manipur',
    '15': 'mizoram',
    '16': 'tripura',
    '17': 'meghalaya',
    '18': 'assam',
    '19': 'west bengal',
    '20': 'jharkhand',
    '21': 'odisha',
    '22': 'chhattisgarh',
    '23': 'madhya pradesh',
    '24': 'gujarat',
    '26': 'dadra and nagar haveli and daman and diu',
    '27': 'maharashtra',
    '29': 'karnataka',
    '30': 'goa',
    '31': 'lakshadweep',
    '32': 'kerala',
    '33': 'tamil nadu',
    '34': 'puducherry',
    '35': 'andaman and nicobar islands',
    '36': 'telangana',
    '37': 'andhra pradesh',
    '38': 'ladakh',
    '97': 'other territory',
}


def normalize_gst_number(raw_value) -> str:
    return str(raw_value or '').strip().upper()


def gst_state_code_from_number(gst_number: str | None) -> str:
    gst_number = normalize_gst_number(gst_number)
    if len(gst_number) >= 2 and gst_number[:2].isdigit():
        return gst_number[:2]
    return ''


def is_utgst_state_code(state_code: str | None) -> bool:
    return str(state_code or '').zfill(2) in UNION_TERRITORY_GST_STATE_CODES


def normalize_place_of_supply_value(value: str | None) -> str:
    normalized = str(value or '').strip().lower()
    normalized = normalized.replace('&', 'and')
    normalized = ' '.join(normalized.replace('-', ' ').replace(',', ' ').split())
    return normalized


def gst_state_code_from_place_of_supply(place_of_supply: str | None) -> str:
    normalized = normalize_place_of_supply_value(place_of_supply)
    if not normalized:
        return ''
    if len(normalized) >= 2 and normalized[:2].isdigit():
        return normalized[:2]
    for code, state_name in GST_STATE_CODE_NAME_MAP.items():
        if normalized == state_name or normalized.startswith(state_name + ' ') or state_name in normalized:
            return code
    return ''


def gst_breakup_from_invoice_subject(subject, tenant) -> dict:
    seller_state_code = gst_state_code_from_number(getattr(tenant, 'gstNumber', None))
    buyer_state_code = gst_state_code_from_number(getattr(subject, 'buyerGstNumber', None))
    place_of_supply_state_code = gst_state_code_from_place_of_supply(getattr(subject, 'placeOfSupply', None))
    effective_buyer_state_code = buyer_state_code or place_of_supply_state_code
    taxable_total = round(float(subject.taxableAmount or 0.0), 2)
    gst_total = round(float(subject.gstAmount or 0.0), 2)
    is_interstate = bool(seller_state_code and effective_buyer_state_code and seller_state_code != effective_buyer_state_code)
    breakdown = {
        'seller_state_code': seller_state_code,
        'buyer_state_code': buyer_state_code,
        'place_of_supply_state_code': place_of_supply_state_code,
        'effective_buyer_state_code': effective_buyer_state_code,
        'tax_mode_label': 'IGST' if is_interstate else ('CGST + UTGST' if is_utgst_state_code(seller_state_code) else 'CGST + SGST'),
        'cgst_total': 0.0,
        'sgst_total': 0.0,
        'utgst_total': 0.0,
        'igst_total': 0.0,
        'taxable_total': taxable_total,
        'gst_total': gst_total,
    }
    if is_interstate:
        breakdown['igst_total'] = gst_total
        return breakdown
    half_tax = round(gst_total / 2.0, 2)
    breakdown['cgst_total'] = half_tax
    other_half = round(gst_total - half_tax, 2)
    if is_utgst_state_code(seller_state_code):
        breakdown['utgst_total'] = other_half
    else:
        breakdown['sgst_total'] = other_half
    return breakdown


def normalize_invoice_kind(raw_value: str | None, party: Party | None = None, *, series: InvoiceSeries | None = None, request=None) -> str:
    value = str(raw_value or '').strip().lower()
    valid = {choice[0] for choice in Sales.InvoiceKind.choices}
    series_nature = getattr(series, 'invoiceNature', InvoiceSeries.InvoiceNature.MIXED)
    advanced_enabled = advanced_invoicing_enabled(request) if request is not None else True
    if advanced_enabled and series is not None and getattr(series, 'lockInvoiceNature', False) and series_nature in valid:
        if value in valid and value != series_nature:
            raise ValueError(f'Invoice series {series.seriesCode} is locked for {series.get_invoiceNature_display()} invoices.')
        return series_nature
    if value in valid:
        return value
    if advanced_enabled and series_nature in valid:
        return series_nature
    if party and getattr(party, 'isB2B', False):
        return Sales.InvoiceKind.B2B
    return Sales.InvoiceKind.B2C


def apply_advanced_invoice_buyer_payload(request, obj, party: Party | None, *, b2c_kind):
    obj.invoiceKind = normalize_invoice_kind(
        request.POST.get('invoiceKind'),
        party,
        series=getattr(obj, 'invoiceSeries', None),
        request=request,
    )
    buyer_legal_name = (request.POST.get('buyerLegalName') or '').strip()
    if not buyer_legal_name and party:
        buyer_legal_name = (party.legalName or party.name or '').strip()
    obj.buyerLegalName = buyer_legal_name or None

    buyer_gst_number = normalize_gst_number(request.POST.get('buyerGstNumber'))
    if not buyer_gst_number and party:
        buyer_gst_number = normalize_gst_number(party.gstNumber)
    obj.buyerGstNumber = buyer_gst_number or None

    buyer_billing_address = (request.POST.get('buyerBillingAddress') or '').strip()
    if not buyer_billing_address and party:
        buyer_billing_address = (party.billingAddress or party.address or '').strip()
    obj.buyerBillingAddress = buyer_billing_address or None
    obj.placeOfSupply = (request.POST.get('placeOfSupply') or '').strip() or None

    if obj.invoiceKind == b2c_kind:
        obj.buyerGstNumber = None


def clear_invoice_buyer_payload(obj, *, b2c_kind):
    obj.invoiceKind = b2c_kind
    obj.buyerLegalName = None
    obj.buyerGstNumber = None
    obj.buyerBillingAddress = None
    obj.placeOfSupply = None


def apply_sales_invoice_payload(request, sale: Sales, party: Party | None, *, line_items=None) -> None:
    line_items = line_items if line_items is not None else (
        request_line_items_payload(request) if request_line_items_present(request) else []
    )
    if advanced_invoicing_enabled(request):
        apply_advanced_invoice_buyer_payload(request, sale, party, b2c_kind=Sales.InvoiceKind.B2C)
    else:
        clear_invoice_buyer_payload(sale, b2c_kind=Sales.InvoiceKind.B2C)

    if line_items:
        sale.taxableAmount, sale.gstAmount, computed_total = sales_totals_from_lines(line_items)
        sale.roundOffAmount, payable_total = sales_round_off_from_total(computed_total)
        if sale.modeOfPayment != Sales.Mode.MIX:
            sale.amount = payable_total
        elif abs(safe_sales_float(sale.amount) - payable_total) > 0.01:
            err_msg = f'Mix sales total must match rounded payable amount of {payable_total:.2f}.'
            logger.error("[Invoices] %s", err_msg)
            raise ValueError(err_msg)
        logger.info("[Invoices] Applied sales invoice payload: Taxable=%s, GST=%s, Payable=%s", sale.taxableAmount, sale.gstAmount, payable_total)
    else:
        sale.taxableAmount = round(float(sale.amount or 0.0), 2)
        sale.gstAmount = 0.0
        sale.roundOffAmount = 0.0

    if sale.invoiceKind == Sales.InvoiceKind.B2C:
        sale.buyerGstNumber = None


def apply_proforma_invoice_payload(request, proforma: ProformaInvoice, party: Party | None, *, line_items=None) -> None:
    line_items = line_items if line_items is not None else (
        request_line_items_payload(request) if request_line_items_present(request) else []
    )
    if advanced_invoicing_enabled(request):
        apply_advanced_invoice_buyer_payload(request, proforma, party, b2c_kind=ProformaInvoice.InvoiceKind.B2C)
    else:
        clear_invoice_buyer_payload(proforma, b2c_kind=ProformaInvoice.InvoiceKind.B2C)

    if line_items:
        proforma.taxableAmount, proforma.gstAmount, computed_total = sales_totals_from_lines(line_items)
        proforma.roundOffAmount, proforma.amount = sales_round_off_from_total(computed_total)
    else:
        proforma.amount = safe_sales_float(request.POST.get('amount'))
        if proforma.amount <= 0:
            raise ValueError('Amount must be greater than zero.')
        proforma.taxableAmount = round(float(proforma.amount or 0.0), 2)
        proforma.gstAmount = 0.0
        proforma.roundOffAmount = 0.0

    if proforma.invoiceKind == ProformaInvoice.InvoiceKind.B2C:
        proforma.buyerGstNumber = None


def apply_cash_counter_buyer_payload(request, counter: CashCounter, party: Party | None) -> None:
    apply_advanced_invoice_buyer_payload(request, counter, party, b2c_kind=CashCounter.InvoiceKind.B2C)
