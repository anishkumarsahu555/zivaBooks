from home.models import CashCounter, InvoiceSeries, ProformaInvoice, Sales
from home.tenancy import tenant_qs


INVOICE_MODULE_MODEL_MAP = {
    InvoiceSeries.Module.SALES: Sales,
    InvoiceSeries.Module.CASH_COUNTER: CashCounter,
    InvoiceSeries.Module.PROFORMA: ProformaInvoice,
}


def invoice_series_queryset(request_or_user, module):
    return tenant_qs(request_or_user, InvoiceSeries).filter(
        isDeleted=False,
        module=module,
        isActive=True,
    ).order_by('-isDefault', 'seriesCode', 'id')


def invoice_model_for_module(module):
    return INVOICE_MODULE_MODEL_MAP[module]


def format_invoice_number(series, sequence_number):
    return f'{series.seriesCode}{str(sequence_number).zfill(series.paddingLength)}'


def available_invoice_numbers_for_series(series, limit=150, *, exclude_instance=None, from_next_only=False):
    model = invoice_model_for_module(series.module)
    used_numbers = set(
        model.objects.filter(
            tenant=series.tenant,
            isDeleted=False,
            invoiceSeries=series,
        ).exclude(
            pk=getattr(exclude_instance, 'pk', None)
        ).values_list('invoiceSequenceNumber', flat=True)
    )
    numbers = []
    candidate = max(series.nextNumber, series.startNumber) if from_next_only else series.startNumber
    while candidate <= series.endNumber and len(numbers) < limit:
        if candidate not in used_numbers:
            numbers.append(candidate)
        candidate += 1
    if not numbers:
        raise ValueError('Invoice series exhausted.')
    return numbers


def next_invoice_numbers_for_series(series, limit=150, *, exclude_instance=None):
    return available_invoice_numbers_for_series(series, limit=limit, exclude_instance=exclude_instance, from_next_only=True)


def next_invoice_number_for_series(series, *, exclude_instance=None):
    return next_invoice_numbers_for_series(series, limit=1, exclude_instance=exclude_instance)[0]


def reserve_invoice_number(request_or_user, module, series_id, *, exclude_instance=None, preferred_number=None):
    series = invoice_series_queryset(request_or_user, module).select_for_update().get(pk=int(series_id))
    available_numbers = available_invoice_numbers_for_series(series, limit=150, exclude_instance=exclude_instance)
    if preferred_number is not None:
        preferred_number = int(preferred_number)
        if preferred_number not in available_numbers:
            raise ValueError('Selected invoice number is no longer available.')
        sequence_number = preferred_number
    else:
        sequence_number = available_numbers[0]
    if series.nextNumber <= sequence_number:
        series.nextNumber = sequence_number + 1
        series.save(update_fields=['nextNumber', 'lastUpdatedOn'])
    return series, sequence_number, format_invoice_number(series, sequence_number)
