from __future__ import annotations

from datetime import datetime
from typing import Optional

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from .models import (
    Bank,
    BookAccount,
    BookTransaction,
    CashCounter,
    Collection,
    CollectionCashCounter,
    Party,
    StaffUser,
    Tenant,
)


BANK_REQUIRED_PAYMENT_MODES = {'Online', 'Cheque', 'Cheque CC'}


def ensure_default_cash_account(tenant: Tenant, name: str = 'Main Cash') -> BookAccount:
    account = (
        BookAccount.objects
        .filter(tenant=tenant, isDeleted=False, type=BookAccount.Type.CASH, isDefault=True)
        .first()
    )
    if account:
        return account
    return BookAccount.objects.create(
        tenant=tenant,
        type=BookAccount.Type.CASH,
        name=name,
        isDefault=True,
    )


def ensure_bank_account(tenant: Tenant, bank: Bank) -> BookAccount:
    account = (
        BookAccount.objects
        .filter(tenant=tenant, isDeleted=False, type=BookAccount.Type.BANK, bank=bank)
        .first()
    )
    if account:
        return account
    display_name = bank.name or 'Bank'
    if bank.accountNumber:
        display_name = f'{display_name} - {bank.accountNumber}'
    return BookAccount.objects.create(
        tenant=tenant,
        type=BookAccount.Type.BANK,
        name=display_name,
        bank=bank,
        isDefault=False,
    )


def ensure_books_for_tenant(tenant: Tenant) -> None:
    ensure_default_cash_account(tenant)
    for bank in Bank.objects.filter(tenant=tenant, isDeleted=False):
        ensure_bank_account(tenant, bank)


def _content_type_for(instance) -> ContentType:
    return ContentType.objects.get_for_model(instance.__class__)


def _delete_book_transactions_for_source(instance) -> None:
    if instance.pk is None:
        return
    tenant_id = getattr(instance, 'tenant_id', None)
    if tenant_id is None:
        return
    ct = _content_type_for(instance)
    BookTransaction.objects.filter(
        tenant_id=tenant_id,
        isDeleted=False,
        source_content_type=ct,
        source_object_id=instance.pk,
    ).update(isDeleted=True)


def _upsert_book_transaction(
    *,
    tenant: Tenant,
    account: BookAccount,
    source_instance,
    source: str,
    component: Optional[str],
    direction: str,
    amount: float,
    tx_datetime: datetime,
    narration: str = '',
    party: Optional[Party] = None,
    created_by: Optional[StaffUser] = None,
) -> BookTransaction:
    ct = _content_type_for(source_instance) if source_instance is not None else None
    source_id = getattr(source_instance, 'pk', None) if source_instance is not None else None

    existing = None
    if ct and source_id:
        existing = BookTransaction.objects.filter(
            tenant=tenant,
            isDeleted=False,
            account=account,
            source_content_type=ct,
            source_object_id=source_id,
            component=component,
        ).first()

    if existing:
        existing.direction = direction
        existing.amount = float(amount or 0.0)
        existing.txDateTime = tx_datetime
        existing.narration = narration
        existing.party = party
        existing.createdBy = created_by
        existing.source = source
        existing.save()
        return existing

    return BookTransaction.objects.create(
        tenant=tenant,
        account=account,
        direction=direction,
        amount=float(amount or 0.0),
        txDateTime=tx_datetime,
        narration=narration,
        party=party,
        createdBy=created_by,
        source=source,
        component=component,
        source_content_type=ct,
        source_object_id=source_id,
    )


@transaction.atomic
def sync_collection_entry(entry: Collection) -> None:
    if entry.pk is None:
        return
    # Clear existing postings to avoid stale rows when modes change.
    _delete_book_transactions_for_source(entry)
    if entry.isDeleted or entry.modeOfPayment == 'Party':
        return

    tenant = entry.tenant
    if tenant is None:
        return

    ensure_books_for_tenant(tenant)

    tx_datetime = entry.collectionDateTime or entry.datetime
    party = entry.partyID
    created_by = entry.collectedBy
    narration = f'Collection {entry.paymentID or entry.pk} ({entry.modeOfPayment})'
    if entry.remark:
        narration = f'{narration} - {entry.remark}'

    if entry.modeOfPayment == 'Cash':
        account = ensure_default_cash_account(tenant)
    elif entry.modeOfPayment in BANK_REQUIRED_PAYMENT_MODES:
        if not entry.bankID_id:
            return
        account = ensure_bank_account(tenant, entry.bankID)
    else:
        return

    _upsert_book_transaction(
        tenant=tenant,
        account=account,
        source_instance=entry,
        source=BookTransaction.Source.COLLECTION,
        component=None,
        direction=BookTransaction.Direction.IN,
        amount=entry.paidAmount,
        tx_datetime=tx_datetime,
        narration=narration,
        party=party,
        created_by=created_by,
    )


@transaction.atomic
def sync_counter_collection_entry(entry: CollectionCashCounter) -> None:
    if entry.pk is None:
        return
    _delete_book_transactions_for_source(entry)
    if entry.isDeleted or entry.modeOfPayment == 'Party':
        return

    tenant = entry.tenant
    if tenant is None:
        return

    ensure_books_for_tenant(tenant)

    tx_datetime = entry.collectionDateTime or entry.datetime
    party = entry.partyID
    created_by = entry.collectedBy
    narration = f'Counter Collection {entry.paymentID or entry.pk} ({entry.modeOfPayment})'
    if entry.remark:
        narration = f'{narration} - {entry.remark}'

    if entry.modeOfPayment == 'Cash':
        account = ensure_default_cash_account(tenant)
    elif entry.modeOfPayment in BANK_REQUIRED_PAYMENT_MODES:
        if not entry.bankID_id:
            return
        account = ensure_bank_account(tenant, entry.bankID)
    else:
        return

    _upsert_book_transaction(
        tenant=tenant,
        account=account,
        source_instance=entry,
        source=BookTransaction.Source.COUNTER_COLLECTION,
        component=None,
        direction=BookTransaction.Direction.IN,
        amount=entry.paidAmount,
        tx_datetime=tx_datetime,
        narration=narration,
        party=party,
        created_by=created_by,
    )


@transaction.atomic
def sync_cash_counter_entry(entry: CashCounter) -> None:
    if entry.pk is None:
        return
    _delete_book_transactions_for_source(entry)
    if entry.isDeleted:
        return

    tenant = entry.tenant
    if tenant is None:
        return

    ensure_books_for_tenant(tenant)

    tx_datetime = entry.entryDate or entry.datetime
    created_by = entry.createdBy
    party = entry.partyID
    invoice_ref = entry.invoiceNumber or 'No Invoice'
    counter_ref = entry.counterID or 'No Counter ID'
    narration = f'Cash Counter - {invoice_ref} ({entry.mode}) - {counter_ref}'
    if entry.remark:
        narration = f'{narration} - {entry.remark}'

    cash_account = ensure_default_cash_account(tenant)

    # Cash Counter -> Books mapping:
    # Cash    -> cashbook IN
    # Credit  -> no cashbook/bankbook posting
    # Card    -> bankbook IN
    # Mix     -> cash part to cashbook IN, card part to bankbook IN
    # Return  -> cashbook OUT
    # Expense -> cashbook OUT
    # Advance/Collection continue to follow the existing legacy posting rules below.
    if entry.mode == 'Cash':
        _upsert_book_transaction(
            tenant=tenant,
            account=cash_account,
            source_instance=entry,
            source=BookTransaction.Source.CASH_COUNTER,
            component=None,
            direction=BookTransaction.Direction.IN,
            amount=entry.amount,
            tx_datetime=tx_datetime,
            narration=narration,
            party=party,
            created_by=created_by,
        )
        return

    if entry.mode == 'Credit':
        return

    if entry.mode == 'Advance':
        _upsert_book_transaction(
            tenant=tenant,
            account=cash_account,
            source_instance=entry,
            source=BookTransaction.Source.CASH_COUNTER,
            component=None,
            direction=BookTransaction.Direction.OUT,
            amount=entry.amount,
            tx_datetime=tx_datetime,
            narration=narration,
            party=party,
            created_by=created_by,
        )
        return

    if entry.mode == 'Collection':
        _upsert_book_transaction(
            tenant=tenant,
            account=cash_account,
            source_instance=entry,
            source=BookTransaction.Source.CASH_COUNTER,
            component=None,
            direction=BookTransaction.Direction.IN,
            amount=entry.amount,
            tx_datetime=tx_datetime,
            narration=narration,
            party=party,
            created_by=created_by,
        )
        return

    if entry.mode in {'Expense', 'Return'}:
        _upsert_book_transaction(
            tenant=tenant,
            account=cash_account,
            source_instance=entry,
            source=BookTransaction.Source.CASH_COUNTER,
            component=None,
            direction=BookTransaction.Direction.OUT,
            amount=entry.amount,
            tx_datetime=tx_datetime,
            narration=narration,
            party=party,
            created_by=created_by,
        )
        return

    if entry.mode == 'Card':
        if not entry.bankID_id:
            return
        bank_account = ensure_bank_account(tenant, entry.bankID)
        _upsert_book_transaction(
            tenant=tenant,
            account=bank_account,
            source_instance=entry,
            source=BookTransaction.Source.CASH_COUNTER,
            component=None,
            direction=BookTransaction.Direction.IN,
            amount=entry.amount,
            tx_datetime=tx_datetime,
            narration=narration,
            party=party,
            created_by=created_by,
        )
        return

    if entry.mode == 'Mix':
        if not entry.bankID_id:
            return
        bank_account = ensure_bank_account(tenant, entry.bankID)
        _upsert_book_transaction(
            tenant=tenant,
            account=cash_account,
            source_instance=entry,
            source=BookTransaction.Source.CASH_COUNTER,
            component='cash',
            direction=BookTransaction.Direction.IN,
            amount=entry.mixCashAmount,
            tx_datetime=tx_datetime,
            narration=narration,
            party=party,
            created_by=created_by,
        )
        _upsert_book_transaction(
            tenant=tenant,
            account=bank_account,
            source_instance=entry,
            source=BookTransaction.Source.CASH_COUNTER,
            component='bank',
            direction=BookTransaction.Direction.IN,
            amount=entry.mixCardAmount,
            tx_datetime=tx_datetime,
            narration=narration,
            party=party,
            created_by=created_by,
        )
        return

    # Other modes intentionally do not post to books.


def sync_book_transactions(instance) -> None:
    if isinstance(instance, Collection):
        sync_collection_entry(instance)
        return
    if isinstance(instance, CollectionCashCounter):
        sync_counter_collection_entry(instance)
        return
    if isinstance(instance, CashCounter):
        sync_cash_counter_entry(instance)
        return


@transaction.atomic
def backfill_books_for_tenant(tenant: Tenant) -> None:
    ensure_books_for_tenant(tenant)

    for entry in Collection.objects.filter(tenant=tenant):
        sync_collection_entry(entry)
    for entry in CollectionCashCounter.objects.filter(tenant=tenant):
        sync_counter_collection_entry(entry)
    for entry in CashCounter.objects.filter(tenant=tenant):
        sync_cash_counter_entry(entry)
