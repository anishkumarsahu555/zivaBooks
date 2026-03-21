from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Type

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from home.models import (
    CashCounter,
    Collection,
    CollectionCashCounter,
    PartyLedgerEntry,
    Sales,
)


@dataclass(frozen=True)
class _Posting:
    party_id: int
    tx_datetime: timezone.datetime
    particulars: str
    debit: float
    credit: float
    source: str
    reference: Optional[str] = None
    component: Optional[str] = None
    transfer_id: Optional[str] = None
    created_by_id: Optional[int] = None


def _ct_for(obj) -> ContentType:
    return ContentType.objects.get_for_model(obj.__class__)


def _clear_for_source(obj) -> None:
    PartyLedgerEntry.objects.filter(
        tenant_id=getattr(obj, "tenant_id", None),
        isDeleted=False,
        source_content_type=_ct_for(obj),
        source_object_id=obj.pk,
    ).update(isDeleted=True)


def _safe_float(value) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _tx_dt_for(obj, fallback_field: str = "datetime"):
    for name in ("collectionDateTime", "entryDate", "approvedOn", fallback_field):
        if hasattr(obj, name):
            val = getattr(obj, name)
            if val:
                return val
    return timezone.now()


def _postings_for_collection(obj: Collection) -> Tuple[_Posting, ...]:
    if not obj.partyID_id:
        return tuple()
    if not getattr(obj, 'isApproved', False):
        return tuple()
    amount = _safe_float(obj.paidAmount)
    if amount <= 0:
        return tuple()
    mode = (obj.modeOfPayment or "Cash").strip()
    ref = obj.paymentID or None
    if obj.bankID_id and not ref:
        ref = f'Bank#{obj.bankID_id}'
    particulars = f'Collection ({mode})'
    if obj.remark:
        particulars += f' - {obj.remark}'
    return (
        _Posting(
            party_id=obj.partyID_id,
            tx_datetime=_tx_dt_for(obj),
            particulars=particulars,
            debit=0.0,
            credit=amount,
            source=PartyLedgerEntry.Source.COLLECTION,
            reference=ref,
            created_by_id=obj.collectedBy_id,
        ),
    )


def _postings_for_counter_collection(obj: CollectionCashCounter) -> Tuple[_Posting, ...]:
    if not obj.partyID_id:
        return tuple()
    if not getattr(obj, 'isApproved', False):
        return tuple()
    amount = _safe_float(obj.paidAmount)
    if amount <= 0:
        return tuple()
    mode = (obj.modeOfPayment or "Cash").strip()
    ref = obj.paymentID or None
    if obj.bankID_id and not ref:
        ref = f'Bank#{obj.bankID_id}'
    particulars = f'Counter Collection ({mode})'
    if obj.remark:
        particulars += f' - {obj.remark}'
    return (
        _Posting(
            party_id=obj.partyID_id,
            tx_datetime=_tx_dt_for(obj),
            particulars=particulars,
            debit=0.0,
            credit=amount,
            source=PartyLedgerEntry.Source.COUNTER_COLLECTION,
            reference=ref,
            created_by_id=obj.collectedBy_id,
        ),
    )


def _postings_for_sales(obj: Sales) -> Tuple[_Posting, ...]:
    if not obj.partyID_id or obj.isApproved is False:
        return tuple()
    amount = _safe_float(obj.amount)
    if amount <= 0:
        return tuple()
    ref = obj.invoiceNumber or obj.paymentID or None
    particulars = 'Sale'
    if obj.remark:
        particulars += f' - {obj.remark}'
    return (
        _Posting(
            party_id=obj.partyID_id,
            tx_datetime=_tx_dt_for(obj, fallback_field="approvedOn"),
            particulars=particulars,
            debit=amount,
            credit=0.0,
            source=PartyLedgerEntry.Source.SALES,
            reference=ref,
            created_by_id=obj.createdBy_id,
        ),
    )


def _postings_for_cash_counter(obj: CashCounter) -> Tuple[_Posting, ...]:
    if not obj.partyID_id:
        return tuple()

    mode = (obj.mode or "").strip()
    credit_modes = {"Collection", "Credit"}
    debit_modes = {"Advance"}

    if mode == "Mix":
        return tuple()

    amount = _safe_float(obj.amount)
    if amount <= 0:
        return tuple()

    invoice_ref = obj.invoiceNumber or 'No Invoice'
    counter_ref = obj.counterID or 'No Counter ID'
    particulars = f'Cash Counter - {invoice_ref} ({mode or "Entry"}) - {counter_ref}'
    if obj.remark:
        particulars += f' - {obj.remark}'

    if mode in credit_modes:
        debit, credit = 0.0, amount
    elif mode in debit_modes:
        debit, credit = amount, 0.0
    else:
        return tuple()

    return (
        _Posting(
            party_id=obj.partyID_id,
            tx_datetime=_tx_dt_for(obj, fallback_field="entryDate"),
            particulars=particulars,
            debit=debit,
            credit=credit,
            source=PartyLedgerEntry.Source.CASH_COUNTER,
            reference=obj.invoiceNumber or None,
            created_by_id=obj.createdBy_id,
        ),
    )


def _postings_for_object(obj) -> Tuple[_Posting, ...]:
    if isinstance(obj, Collection):
        return _postings_for_collection(obj)
    if isinstance(obj, CollectionCashCounter):
        return _postings_for_counter_collection(obj)
    if isinstance(obj, Sales):
        return _postings_for_sales(obj)
    if isinstance(obj, CashCounter):
        return _postings_for_cash_counter(obj)
    return tuple()


@transaction.atomic
def sync_party_ledger_entries_for_object(obj) -> None:
    """
    Idempotent sync for one source object.
    - Marks previous entries for that source as deleted (to handle edits/mode changes).
    - Reposts current entries when applicable.
    """
    if not getattr(obj, "pk", None) or not getattr(obj, "tenant_id", None):
        return

    _clear_for_source(obj)

    if getattr(obj, "isDeleted", False):
        return

    postings = _postings_for_object(obj)
    if not postings:
        return

    ct = _ct_for(obj)
    for posting in postings:
        PartyLedgerEntry.objects.create(
            tenant_id=obj.tenant_id,
            party_id=posting.party_id,
            txDateTime=posting.tx_datetime,
            particulars=posting.particulars,
            debit=posting.debit,
            credit=posting.credit,
            createdBy_id=posting.created_by_id,
            source=posting.source,
            reference=posting.reference,
            component=posting.component,
            transferId=posting.transfer_id,
            source_content_type=ct,
            source_object_id=obj.pk,
        )


@transaction.atomic
def rebuild_party_ledger_for_party(party_id: int, tenant_id: int) -> None:
    PartyLedgerEntry.objects.filter(tenant_id=tenant_id, party_id=party_id, isDeleted=False).update(isDeleted=True)

    for obj in Collection.objects.filter(tenant_id=tenant_id, isDeleted=False, partyID_id=party_id):
        sync_party_ledger_entries_for_object(obj)
    for obj in CollectionCashCounter.objects.filter(tenant_id=tenant_id, isDeleted=False, partyID_id=party_id):
        sync_party_ledger_entries_for_object(obj)
    for obj in Sales.objects.filter(tenant_id=tenant_id, isDeleted=False, partyID_id=party_id):
        sync_party_ledger_entries_for_object(obj)
    for obj in CashCounter.objects.filter(tenant_id=tenant_id, isDeleted=False, partyID_id=party_id):
        sync_party_ledger_entries_for_object(obj)


@transaction.atomic
def backfill_party_ledger(tenant_id: int | None = None) -> None:
    qs = PartyLedgerEntry.objects.filter(isDeleted=False)
    if tenant_id:
        qs = qs.filter(tenant_id=tenant_id)
    qs.update(isDeleted=True)

    sources = (
        list(Collection.objects.filter(isDeleted=False))
        + list(CollectionCashCounter.objects.filter(isDeleted=False))
        + list(Sales.objects.filter(isDeleted=False))
        + list(CashCounter.objects.filter(isDeleted=False))
    )
    if tenant_id:
        sources = [o for o in sources if o.tenant_id == tenant_id]
    for obj in sources:
        sync_party_ledger_entries_for_object(obj)
