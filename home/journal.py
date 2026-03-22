from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from .models import (
    Bank,
    BookAccount,
    CashCounter,
    Collection,
    CollectionCashCounter,
    JournalEntry,
    JournalLine,
    Party,
    Sales,
    Tenant,
)


@dataclass(frozen=True)
class _Line:
    account_type: str
    debit: float
    credit: float
    book_account: Optional[BookAccount] = None
    party: Optional[Party] = None
    note: Optional[str] = None
    component: Optional[str] = None


def _safe_float(value) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _content_type_for(instance) -> ContentType:
    return ContentType.objects.get_for_model(instance.__class__)


def _delete_journal_for_source(instance) -> None:
    if not getattr(instance, 'pk', None) or not getattr(instance, 'tenant_id', None):
        return
    ct = _content_type_for(instance)
    JournalLine.objects.filter(
        tenant_id=instance.tenant_id,
        isDeleted=False,
        journalEntry__source_content_type=ct,
        journalEntry__source_object_id=instance.pk,
    ).update(isDeleted=True)
    JournalEntry.objects.filter(
        tenant_id=instance.tenant_id,
        isDeleted=False,
        source_content_type=ct,
        source_object_id=instance.pk,
    ).update(isDeleted=True)


def _default_cash_account(tenant: Tenant) -> Optional[BookAccount]:
    return BookAccount.objects.filter(
        tenant=tenant,
        isDeleted=False,
        type=BookAccount.Type.CASH,
        isDefault=True,
    ).first()


def _bank_account(tenant: Tenant, bank: Optional[Bank]) -> Optional[BookAccount]:
    if bank is None:
        return None
    return BookAccount.objects.filter(
        tenant=tenant,
        isDeleted=False,
        type=BookAccount.Type.BANK,
        bank=bank,
    ).first()


def _create_journal_entry(instance, *, source: str, entry_datetime, reference: Optional[str], narration: str, created_by_id: Optional[int], lines: Iterable[_Line]) -> None:
    lines = [line for line in lines if _safe_float(line.debit) > 0 or _safe_float(line.credit) > 0]
    if not lines:
        return

    ct = _content_type_for(instance)
    entry = JournalEntry.objects.create(
        tenant_id=instance.tenant_id,
        entryDateTime=entry_datetime or timezone.now(),
        reference=reference,
        narration=narration,
        source=source,
        createdBy_id=created_by_id,
        source_content_type=ct,
        source_object_id=instance.pk,
    )

    for index, line in enumerate(lines, start=1):
        JournalLine.objects.create(
            tenant_id=instance.tenant_id,
            journalEntry=entry,
            lineNo=index,
            accountType=line.account_type,
            bookAccount=line.book_account,
            party=line.party,
            debit=_safe_float(line.debit),
            credit=_safe_float(line.credit),
            note=line.note,
            component=line.component,
        )


def _sync_collection(instance: Collection) -> None:
    if instance.isDeleted or not instance.isApproved or not instance.partyID_id:
        return
    amount = _safe_float(instance.paidAmount)
    if amount <= 0:
        return

    account = None
    mode = (instance.modeOfPayment or 'Cash').strip()
    if mode == 'Cash':
        account = _default_cash_account(instance.tenant)
    elif mode in {'Online', 'Cheque', 'Cheque CC'}:
        account = _bank_account(instance.tenant, instance.bankID)
    if account is None:
        return

    particulars = f'Collection ({mode})'
    if instance.remark:
        particulars = f'{particulars} - {instance.remark}'

    _create_journal_entry(
        instance,
        source=JournalEntry.Source.COLLECTION,
        entry_datetime=instance.collectionDateTime or instance.datetime,
        reference=instance.paymentID,
        narration=particulars,
        created_by_id=instance.collectedBy_id,
        lines=[
            _Line(JournalLine.AccountType.CASH if account.type == BookAccount.Type.CASH else JournalLine.AccountType.BANK, amount, 0.0, book_account=account, party=instance.partyID, note=particulars),
            _Line(JournalLine.AccountType.PARTY, 0.0, amount, party=instance.partyID, note=particulars),
        ],
    )


def _sync_counter_collection(instance: CollectionCashCounter) -> None:
    if instance.isDeleted or not instance.isApproved or not instance.partyID_id:
        return
    amount = _safe_float(instance.paidAmount)
    if amount <= 0:
        return

    account = None
    mode = (instance.modeOfPayment or 'Cash').strip()
    if mode == 'Cash':
        account = _default_cash_account(instance.tenant)
    elif mode in {'Online', 'Cheque', 'Cheque CC'}:
        account = _bank_account(instance.tenant, instance.bankID)
    if account is None:
        return

    particulars = f'Counter Collection ({mode})'
    if instance.remark:
        particulars = f'{particulars} - {instance.remark}'

    _create_journal_entry(
        instance,
        source=JournalEntry.Source.COUNTER_COLLECTION,
        entry_datetime=instance.collectionDateTime or instance.datetime,
        reference=instance.paymentID,
        narration=particulars,
        created_by_id=instance.collectedBy_id,
        lines=[
            _Line(JournalLine.AccountType.CASH if account.type == BookAccount.Type.CASH else JournalLine.AccountType.BANK, amount, 0.0, book_account=account, party=instance.partyID, note=particulars),
            _Line(JournalLine.AccountType.PARTY, 0.0, amount, party=instance.partyID, note=particulars),
        ],
    )


def _sync_sales(instance: Sales) -> None:
    if instance.isDeleted or not instance.isApproved:
        return
    amount = _safe_float(instance.amount)
    if amount <= 0:
        return

    mode = (instance.modeOfPayment or Sales.Mode.CASH).strip()
    narration = f'Sales - {instance.invoiceNumber or instance.paymentID or instance.pk} ({mode})'
    if instance.remark:
        narration = f'{narration} - {instance.remark}'

    lines = []
    if mode == Sales.Mode.CASH:
        account = _default_cash_account(instance.tenant)
        if account:
            lines.append(_Line(JournalLine.AccountType.CASH, amount, 0.0, book_account=account, party=instance.partyID, note=narration))
            lines.append(_Line(JournalLine.AccountType.SALES, 0.0, amount, note=narration))
    elif mode == Sales.Mode.ONLINE:
        account = _bank_account(instance.tenant, instance.bankID)
        if account:
            lines.append(_Line(JournalLine.AccountType.BANK, amount, 0.0, book_account=account, party=instance.partyID, note=narration))
            lines.append(_Line(JournalLine.AccountType.SALES, 0.0, amount, note=narration))
    elif mode == Sales.Mode.MIX:
        cash_amount = _safe_float(instance.mixCashAmount)
        online_amount = _safe_float(instance.mixOnlineAmount)
        cash_account = _default_cash_account(instance.tenant)
        bank_account = _bank_account(instance.tenant, instance.bankID)
        if cash_account and cash_amount > 0:
            lines.append(_Line(JournalLine.AccountType.CASH, cash_amount, 0.0, book_account=cash_account, party=instance.partyID, note=narration, component='cash'))
        if bank_account and online_amount > 0:
            lines.append(_Line(JournalLine.AccountType.BANK, online_amount, 0.0, book_account=bank_account, party=instance.partyID, note=narration, component='bank'))
        if lines:
            lines.append(_Line(JournalLine.AccountType.SALES, 0.0, cash_amount + online_amount, note=narration))
    elif mode == Sales.Mode.CREDIT:
        if instance.partyID_id:
            lines.append(_Line(JournalLine.AccountType.PARTY, amount, 0.0, party=instance.partyID, note=narration))
            lines.append(_Line(JournalLine.AccountType.SALES, 0.0, amount, note=narration))

    _create_journal_entry(
        instance,
        source=JournalEntry.Source.SALES,
        entry_datetime=instance.approvedOn or instance.datetime,
        reference=instance.invoiceNumber or instance.paymentID,
        narration=narration,
        created_by_id=instance.createdBy_id,
        lines=lines,
    )


def _sync_cash_counter(instance: CashCounter) -> None:
    if instance.isDeleted:
        return
    amount = _safe_float(instance.amount)
    if amount <= 0 and (instance.mode or '').strip() != 'Mix':
        return

    mode = (instance.mode or '').strip()
    narration = f'Cash Counter - {instance.invoiceNumber or "No Invoice"} ({mode or "Entry"}) - {instance.counterID or "No Counter ID"}'
    if instance.remark:
        narration = f'{narration} - {instance.remark}'

    cash_account = _default_cash_account(instance.tenant)
    bank_account = _bank_account(instance.tenant, instance.bankID)
    lines = []

    if mode == 'Cash' and cash_account:
        lines = [
            _Line(JournalLine.AccountType.CASH, amount, 0.0, book_account=cash_account, party=instance.partyID, note=narration),
            _Line(JournalLine.AccountType.SALES, 0.0, amount, note=narration),
        ]
    elif mode == 'Card' and bank_account:
        lines = [
            _Line(JournalLine.AccountType.BANK, amount, 0.0, book_account=bank_account, party=instance.partyID, note=narration),
            _Line(JournalLine.AccountType.SALES, 0.0, amount, note=narration),
        ]
    elif mode == 'Mix':
        cash_amount = _safe_float(instance.mixCashAmount)
        bank_amount = _safe_float(instance.mixCardAmount)
        if cash_account and cash_amount > 0:
            lines.append(_Line(JournalLine.AccountType.CASH, cash_amount, 0.0, book_account=cash_account, party=instance.partyID, note=narration, component='cash'))
        if bank_account and bank_amount > 0:
            lines.append(_Line(JournalLine.AccountType.BANK, bank_amount, 0.0, book_account=bank_account, party=instance.partyID, note=narration, component='bank'))
        if lines:
            lines.append(_Line(JournalLine.AccountType.SALES, 0.0, cash_amount + bank_amount, note=narration))
    elif mode == 'Credit' and instance.partyID_id:
        lines = [
            _Line(JournalLine.AccountType.PARTY, amount, 0.0, party=instance.partyID, note=narration),
            _Line(JournalLine.AccountType.SALES, 0.0, amount, note=narration),
        ]
    elif mode == 'Advance' and cash_account and instance.partyID_id:
        lines = [
            _Line(JournalLine.AccountType.ADVANCE, amount, 0.0, party=instance.partyID, note=narration),
            _Line(JournalLine.AccountType.CASH, 0.0, amount, book_account=cash_account, party=instance.partyID, note=narration),
        ]
    elif mode == 'Collection' and cash_account and instance.partyID_id:
        lines = [
            _Line(JournalLine.AccountType.CASH, amount, 0.0, book_account=cash_account, party=instance.partyID, note=narration),
            _Line(JournalLine.AccountType.PARTY, 0.0, amount, party=instance.partyID, note=narration),
        ]
    elif mode == 'Expense' and cash_account:
        lines = [
            _Line(JournalLine.AccountType.EXPENSE, amount, 0.0, note=narration),
            _Line(JournalLine.AccountType.CASH, 0.0, amount, book_account=cash_account, party=instance.partyID, note=narration),
        ]
    elif mode == 'Return' and cash_account:
        lines = [
            _Line(JournalLine.AccountType.ADJUSTMENT, amount, 0.0, note=narration),
            _Line(JournalLine.AccountType.CASH, 0.0, amount, book_account=cash_account, party=instance.partyID, note=narration),
        ]

    _create_journal_entry(
        instance,
        source=JournalEntry.Source.CASH_COUNTER,
        entry_datetime=instance.entryDate or instance.datetime,
        reference=instance.invoiceNumber or instance.counterID,
        narration=narration,
        created_by_id=instance.createdBy_id,
        lines=lines,
    )


@transaction.atomic
def sync_journal_entries_for_object(instance) -> None:
    if not getattr(instance, 'pk', None) or not getattr(instance, 'tenant_id', None):
        return

    _delete_journal_for_source(instance)
    if getattr(instance, 'isDeleted', False):
        return

    if isinstance(instance, Collection):
        _sync_collection(instance)
        return
    if isinstance(instance, CollectionCashCounter):
        _sync_counter_collection(instance)
        return
    if isinstance(instance, Sales):
        _sync_sales(instance)
        return
    if isinstance(instance, CashCounter):
        _sync_cash_counter(instance)
        return
