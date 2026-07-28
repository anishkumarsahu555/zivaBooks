from datetime import datetime

from django.db.models import Q, Sum

from utils.logger import logger
from home.models import Collection, CollectionCashCounter, Party, PartyLedgerEntry
from home.tenancy import tenant_qs


def parse_ddmmyyyy_api(value, default=None):
    if not value:
        return default
    try:
        return datetime.strptime(value, '%d/%m/%Y').date()
    except Exception:
        pass
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except Exception:
        return default


def customer_visible_party_ledger_qs_for_request(request, qs):
    approved_collection_ids = tenant_qs(request, Collection).filter(
        isDeleted=False,
        isApproved=True,
    ).values('id')
    approved_counter_collection_ids = tenant_qs(request, CollectionCashCounter).filter(
        isDeleted=False,
        isApproved=True,
    ).values('id')
    return qs.filter(
        Q(source=PartyLedgerEntry.Source.SALES) |
        Q(source=PartyLedgerEntry.Source.CASH_COUNTER) |
        (Q(source=PartyLedgerEntry.Source.COLLECTION) & Q(source_object_id__in=approved_collection_ids)) |
        (Q(source=PartyLedgerEntry.Source.COUNTER_COLLECTION) & Q(source_object_id__in=approved_counter_collection_ids))
    )


def party_ledger_export_party(request, party_id):
    if request.user.has_perm('home.can_manage_parties') or request.user.has_perm('home.can_view_party_credentials'):
        return tenant_qs(request, Party).filter(isDeleted=False, pk=int(party_id)).only(
            'id', 'name', 'address', 'openingBalance', 'tenant_id'
        ).first()
    return tenant_qs(request, Party).filter(isDeleted=False, userID_id=request.user.pk).only(
        'id', 'name', 'address', 'openingBalance', 'tenant_id'
    ).first()


def party_ledger_voucher_type(item, is_customer_view=False):
    if item.source == PartyLedgerEntry.Source.SALES:
        return 'Purchase' if is_customer_view else 'Sales'
    if item.source in (PartyLedgerEntry.Source.COLLECTION, PartyLedgerEntry.Source.COUNTER_COLLECTION):
        return 'Payment'
    if item.source == PartyLedgerEntry.Source.CASH_COUNTER:
        return 'Cash Counter'
    if item.source == PartyLedgerEntry.Source.TRANSFER:
        return 'Transfer'
    return 'Manual'


def party_ledger_voucher_number(item):
    if item.reference:
        return str(item.reference)
    if item.transferId:
        return str(item.transferId)
    if item.source_object_id:
        return str(item.source_object_id)
    return '-'


def customer_view_particulars(particulars):
    if particulars.startswith('Sale'):
        particulars = 'Purchase' + particulars[len('Sale'):]
    if particulars.startswith('Counter Collection'):
        particulars = 'Payment' + particulars[len('Counter Collection'):]
    if particulars.startswith('Collection'):
        particulars = 'Payment' + particulars[len('Collection'):]
    return particulars.replace('(Collection)', '(Payment)')


def strip_trailing_particular_chunks(particulars):
    if ' - ' not in particulars:
        return particulars
    parts = [part for part in particulars.split(' - ') if part]
    return parts[0] if parts else particulars


def party_ledger_statement_rows(request, party, start_date, end_date, is_customer_view=False):
    base_qs = tenant_qs(request, PartyLedgerEntry).filter(isDeleted=False, party=party)
    if is_customer_view:
        base_qs = customer_visible_party_ledger_qs_for_request(request, base_qs)
    prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
        debit_total=Sum('debit'),
        credit_total=Sum('credit'),
    )
    opening = float(party.openingBalance or 0.0) + float(prior['debit_total'] or 0.0) - float(prior['credit_total'] or 0.0)
    qs = base_qs.filter(txDateTime__date__range=[start_date, end_date]).order_by('txDateTime', 'id')
    balance = opening
    total_debit = 0.0
    total_credit = 0.0
    rows = []
    for item in qs:
        debit = float(item.debit or 0.0)
        credit = float(item.credit or 0.0)
        balance += debit - credit
        total_debit += debit
        total_credit += credit
        particulars = strip_trailing_particular_chunks((item.particulars or '').strip())
        if is_customer_view:
            particulars = customer_view_particulars(particulars)
        rows.append({
            'date': item.txDateTime.date(),
            'particulars': particulars or '-',
            'voucher_type': party_ledger_voucher_type(item, is_customer_view=is_customer_view),
            'voucher_no': party_ledger_voucher_number(item),
            'debit': debit,
            'credit': credit,
            'balance': balance,
        })
    return {
        'opening': opening,
        'rows': rows,
        'total_debit': total_debit,
        'total_credit': total_credit,
        'closing': balance,
    }
