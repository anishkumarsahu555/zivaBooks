from datetime import datetime

from django.db import transaction
from django.db.models import Case, ExpressionWrapper, F, FloatField, Q, Sum, Value, When, Window
from django.http import JsonResponse
from django.utils.crypto import get_random_string
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from utils.logger import logger
from home.audit import log_audit_event, snapshot_instance
from home.books import ensure_books_for_tenant
from home.models import *
from home.services.books import can_access_cashbook, parse_books_date
from home.services.collections import has_any_permission as _has_any_permission
from home.services.reports import formatINR, format_money_compact as _format_money_compact
from home.tenancy import assign_tenant, get_current_tenant, tenant_qs


def get_bank_balances_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'forbidden'}, status=403)
    # Bank balances are sensitive; restrict to admin/owner-style access.
    if not request.user.has_perm('home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403)
    tenant = get_current_tenant(request)
    if tenant is not None:
        ensure_books_for_tenant(tenant)
    banks = list(
        tenant_qs(request, Bank)
        .filter(isDeleted=False)
        .values('id', 'name', 'accountNumber')
        .order_by('name')
    )
    totals_by_bank = {bank['id']: 0.0 for bank in banks}
    account_rows = list(
        tenant_qs(request, BookAccount)
        .filter(isDeleted=False, type=BookAccount.Type.BANK, bank__isnull=False)
        .values('id', 'bank_id', 'openingBalance')
    )
    tx_totals = {
        row['account']: row
        for row in (
            tenant_qs(request, BookTransaction)
            .filter(
                isDeleted=False,
                account_id__in=[row['id'] for row in account_rows],
            )
            .values('account')
            .annotate(
                in_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.IN)),
                out_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.OUT)),
            )
        )
    }
    for row in account_rows:
        account_total = float(row['openingBalance'] or 0.0)
        tx_row = tx_totals.get(row['id'])
        if tx_row:
            account_total += float(tx_row.get('in_total') or 0.0) - float(tx_row.get('out_total') or 0.0)
        totals_by_bank[row['bank_id']] = totals_by_bank.get(row['bank_id'], 0.0) + account_total
    data = []
    for bank in banks:
        data.append({
            'id': bank['id'],
            'name': bank['name'],
            'accountNumber': bank['accountNumber'] or '',
            'balance': totals_by_bank.get(bank['id'], 0.0),
        })
    return JsonResponse({'message': 'success', 'data': data}, safe=False)


@transaction.atomic
def add_book_manual_tx_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    account_id = request.POST.get('accountId')
    direction = request.POST.get('direction')
    amount = request.POST.get('amount')
    date = request.POST.get('date')
    narration = request.POST.get('narration', '')
    party_id = request.POST.get('partyId')
    if not account_id or not direction or not amount:
        return JsonResponse({'message': 'invalid'}, safe=False)
    try:
        account = tenant_qs(request, BookAccount).select_related('bank').get(pk=int(account_id), isDeleted=False)
    except Exception:
        return JsonResponse({'message': 'invalid_account'}, safe=False)
    if account.type == BookAccount.Type.BANK:
        if not request.user.has_perm('home.can_view_party_credentials'):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    elif not can_access_cashbook(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        amount_value = float(amount)
    except Exception:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    if amount_value <= 0:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    tx_date = parse_books_date(date) or datetime.today().date()
    tx_datetime = datetime.combine(tx_date, datetime.now().time())
    party = None
    if party_id and party_id.isdigit():
        party = tenant_qs(request, Party).filter(isDeleted=False, pk=int(party_id)).first()
    staff_user = tenant_qs(request, StaffUser).filter(isDeleted=False, user_ID_id=request.user.pk).first()
    tx = BookTransaction()
    assign_tenant(tx, request)
    tx.account = account
    tx.direction = direction
    tx.amount = amount_value
    tx.txDateTime = tx_datetime
    tx.narration = narration
    tx.party = party
    tx.createdBy = staff_user
    tx.source = BookTransaction.Source.MANUAL
    tx.save()
    return JsonResponse({'message': 'success'}, safe=False)


@transaction.atomic
def transfer_books_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not request.user.has_perm('home.can_view_party_credentials') and not request.user.has_perm('home.can_manage_books'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    from_account_id = request.POST.get('fromAccountId')
    to_account_id = request.POST.get('toAccountId')
    amount = request.POST.get('amount')
    date = request.POST.get('date')
    note = request.POST.get('note', '')
    if not from_account_id or not to_account_id or not amount:
        return JsonResponse({'message': 'invalid'}, safe=False)
    try:
        from_account = tenant_qs(request, BookAccount).get(pk=int(from_account_id), isDeleted=False)
        to_account = tenant_qs(request, BookAccount).get(pk=int(to_account_id), isDeleted=False)
    except Exception:
        return JsonResponse({'message': 'invalid_account'}, safe=False)
    if from_account.pk == to_account.pk:
        return JsonResponse({'message': 'same_account'}, safe=False)
    try:
        amount_value = float(amount)
    except Exception:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    if amount_value <= 0:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    tx_date = parse_books_date(date) or datetime.today().date()
    tx_datetime = datetime.combine(tx_date, datetime.now().time())
    staff_user = tenant_qs(request, StaffUser).filter(isDeleted=False, user_ID_id=request.user.pk).first()
    transfer_id = get_random_string(20)
    out_tx = BookTransaction()
    assign_tenant(out_tx, request)
    out_tx.account = from_account
    out_tx.direction = BookTransaction.Direction.OUT
    out_tx.amount = amount_value
    out_tx.txDateTime = tx_datetime
    out_tx.narration = note or 'Transfer'
    out_tx.createdBy = staff_user
    out_tx.source = BookTransaction.Source.TRANSFER
    out_tx.transferId = transfer_id
    out_tx.save()
    in_tx = BookTransaction()
    assign_tenant(in_tx, request)
    in_tx.account = to_account
    in_tx.direction = BookTransaction.Direction.IN
    in_tx.amount = amount_value
    in_tx.txDateTime = tx_datetime
    in_tx.narration = note or 'Transfer'
    in_tx.createdBy = staff_user
    in_tx.source = BookTransaction.Source.TRANSFER
    in_tx.transferId = transfer_id
    in_tx.save()
    log_audit_event(
        out_tx,
        AuditLog.Action.TRANSFER,
        actor=request.user,
        changes={
            'transferId': transfer_id,
            'fromAccount': str(from_account),
            'toAccount': str(to_account),
            'amount': amount_value,
            'note': note or 'Transfer',
        },
        snapshot=snapshot_instance(out_tx),
    )
    return JsonResponse({'message': 'success'}, safe=False)


@transaction.atomic
def transfer_cash_to_bank_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not request.user.has_perm('home.can_view_party_credentials') and not request.user.has_perm('home.can_manage_books'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    to_bank_id = request.POST.get('toBankId')
    if not to_bank_id:
        return JsonResponse({'message': 'invalid_bank'}, safe=False)
    try:
        bank = tenant_qs(request, Bank).get(pk=int(to_bank_id), isDeleted=False)
    except Exception:
        return JsonResponse({'message': 'invalid_bank'}, safe=False)
    tenant = get_current_tenant(request)
    to_account = (
        BookAccount.objects
        .filter(tenant=tenant, isDeleted=False, type=BookAccount.Type.BANK, bank=bank)
        .first()
    )
    if to_account is None:
        to_account = BookAccount.objects.create(
            tenant=tenant,
            type=BookAccount.Type.BANK,
            bank=bank,
            name=f'{bank.name} - {bank.accountNumber}' if bank.accountNumber else bank.name,
        )
    from_account_id = request.POST.get('fromAccountId')
    amount = request.POST.get('amount')
    date = request.POST.get('date')
    note = request.POST.get('note', '')
    if not from_account_id or not amount:
        return JsonResponse({'message': 'invalid'}, safe=False)
    try:
        from_account = tenant_qs(request, BookAccount).get(pk=int(from_account_id), isDeleted=False)
    except Exception:
        return JsonResponse({'message': 'invalid_account'}, safe=False)
    try:
        amount_value = float(amount)
    except Exception:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    if amount_value <= 0:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    tx_date = parse_books_date(date) or datetime.today().date()
    tx_datetime = datetime.combine(tx_date, datetime.now().time())
    staff_user = tenant_qs(request, StaffUser).filter(isDeleted=False, user_ID_id=request.user.pk).first()
    transfer_id = get_random_string(20)
    out_tx = BookTransaction()
    assign_tenant(out_tx, request)
    out_tx.account = from_account
    out_tx.direction = BookTransaction.Direction.OUT
    out_tx.amount = amount_value
    out_tx.txDateTime = tx_datetime
    out_tx.narration = note or 'Transfer'
    out_tx.createdBy = staff_user
    out_tx.source = BookTransaction.Source.TRANSFER
    out_tx.transferId = transfer_id
    out_tx.save()
    in_tx = BookTransaction()
    assign_tenant(in_tx, request)
    in_tx.account = to_account
    in_tx.direction = BookTransaction.Direction.IN
    in_tx.amount = amount_value
    in_tx.txDateTime = tx_datetime
    in_tx.narration = note or 'Transfer'
    in_tx.createdBy = staff_user
    in_tx.source = BookTransaction.Source.TRANSFER
    in_tx.transferId = transfer_id
    in_tx.save()
    log_audit_event(
        in_tx,
        AuditLog.Action.TRANSFER,
        actor=request.user,
        changes={
            'transferId': transfer_id,
            'fromAccount': str(from_account),
            'toAccount': str(to_account),
            'bank': bank.name,
            'amount': amount_value,
            'note': note or 'Transfer',
        },
        snapshot=snapshot_instance(in_tx),
    )
    return JsonResponse({'message': 'success'}, safe=False)


def get_book_tx_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    tx_id = request.GET.get('id')
    if not tx_id or not tx_id.isdigit():
        return JsonResponse({'message': 'invalid'}, safe=False)
    tx = tenant_qs(request, BookTransaction).select_related('account', 'account__bank').filter(isDeleted=False, pk=int(tx_id)).first()
    if tx is None:
        return JsonResponse({'message': 'not_found'}, safe=False)
    if tx.account.type == BookAccount.Type.BANK and not request.user.has_perm('home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    return JsonResponse({
        'message': 'success',
        'data': {
            'id': tx.id,
            'accountId': tx.account_id,
            'accountType': tx.account.type,
            'direction': tx.direction,
            'amount': tx.amount,
            'date': tx.txDateTime.strftime('%d/%m/%Y'),
            'narration': tx.narration or '',
            'source': tx.source,
            'transferId': tx.transferId or '',
        },
    }, safe=False)


def get_book_tx_source_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    tx_id = request.GET.get('id')
    if not tx_id or not tx_id.isdigit():
        return JsonResponse({'message': 'invalid'}, safe=False)
    tx = tenant_qs(request, BookTransaction).filter(isDeleted=False, pk=int(tx_id)).first()
    if tx is None:
        return JsonResponse({'message': 'not_found'}, safe=False)

    title = "Transaction Source Document"
    details = []
    detail_url = ""

    if tx.source_content_type and tx.source_object_id:
        obj = tx.source_object
        if obj:
            model_name = tx.source_content_type.model
            if model_name == 'collection':
                title = f"Collection Details - {obj.paymentID or ''}"
                detail_url = f"/collection_list/?search={obj.paymentID or ''}"
                details = [
                    ("Party Name", obj.partyID.name if obj.partyID else "-"),
                    ("Payment ID", obj.paymentID or "-"),
                    ("Mode of Payment", obj.modeOfPayment),
                    ("Paid Amount", f"₹ {obj.paidAmount:.2f}"),
                    ("Collected By", obj.collectedBy.name if obj.collectedBy else "-"),
                    ("Collection Date/Time", obj.collectionDateTime.strftime('%d-%m-%Y %I:%M %p') if obj.collectionDateTime else "-"),
                    ("Bank", obj.bankID.name if obj.bankID else "-"),
                    ("Approved By", obj.approvedBy.name if obj.approvedBy else "-"),
                    ("Detail", obj.detail or "-"),
                    ("Remark", obj.remark or "-"),
                ]
            elif model_name == 'sales':
                title = f"Sales Invoice Details - {obj.invoiceNumber or ''}"
                detail_url = f"/sales_list/?search={obj.invoiceNumber or ''}"
                details = [
                    ("Party Name", obj.partyID.name if obj.partyID else "-"),
                    ("Invoice Number", obj.invoiceNumber or "-"),
                    ("Payment Mode", obj.modeOfPayment),
                    ("Total Amount", f"₹ {obj.amount:.2f}"),
                    ("Taxable Amount", f"₹ {obj.taxableAmount:.2f}"),
                    ("GST Amount", f"₹ {obj.gstAmount:.2f}"),
                    ("Created By", obj.createdBy.name if obj.createdBy else "-"),
                    ("Bank", obj.bankID.name if obj.bankID else "-"),
                    ("Remark", obj.remark or "-"),
                ]
            elif model_name == 'cashcounter':
                title = f"Counter Sales Details - {obj.invoiceNumber or ''}"
                detail_url = f"/my_cash_counter_list/?search={obj.invoiceNumber or ''}"
                details = [
                    ("Party Name", obj.partyID.name if obj.partyID else "-"),
                    ("Invoice Number", obj.invoiceNumber or "-"),
                    ("Payment Mode", obj.mode),
                    ("Amount", f"₹ {obj.amount:.2f}"),
                    ("Created By", obj.createdBy.name if obj.createdBy else "-"),
                    ("Bank", obj.bankID.name if obj.bankID else "-"),
                    ("Remark", obj.remark or "-"),
                ]
            elif model_name == 'cashcountercollection':
                title = f"Counter Collection Details - {obj.paymentID or ''}"
                detail_url = f"/my_cash_counter_list/?search={obj.paymentID or ''}"
                details = [
                    ("Party Name", obj.partyID.name if obj.partyID else "-"),
                    ("Payment ID", obj.paymentID or "-"),
                    ("Mode of Payment", obj.modeOfPayment),
                    ("Paid Amount", f"₹ {obj.paidAmount:.2f}"),
                    ("Collected By", obj.collectedBy.name if obj.collectedBy else "-"),
                    ("Remark", obj.remark or "-"),
                ]
    elif tx.source == BookTransaction.Source.TRANSFER and tx.transferId:
        title = "Inter-Account Transfer Details"
        other_tx = tenant_qs(request, BookTransaction).filter(isDeleted=False, transferId=tx.transferId).exclude(pk=tx.pk).first()
        if other_tx:
            details = [
                ("Transfer Date", tx.txDateTime.strftime('%d-%m-%Y %I:%M %p')),
                ("Amount Transferred", f"₹ {tx.amount:.2f}"),
                ("From Account", other_tx.account.name if tx.direction == BookTransaction.Direction.IN else tx.account.name),
                ("To Account", tx.account.name if tx.direction == BookTransaction.Direction.IN else other_tx.account.name),
                ("Narration / Remarks", tx.narration or "-"),
            ]

    if not details:
        details = [
            ("Transaction Date", tx.txDateTime.strftime('%d-%m-%Y %I:%M %p')),
            ("Amount", f"₹ {tx.amount:.2f}"),
            ("Type / Direction", tx.get_direction_display()),
            ("Source", tx.get_source_display()),
            ("Narration", tx.narration or "-"),
        ]

    return JsonResponse({'message': 'success', 'title': title, 'details': details, 'detail_url': detail_url}, safe=False)


def get_generic_object_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    model_name = request.GET.get('model', '').lower().replace('_', '').replace(' ', '')
    object_id = request.GET.get('id')
    if not model_name or not object_id or not object_id.isdigit():
        return JsonResponse({'message': 'invalid'}, safe=False)

    title = "Document Details"
    details = []
    detail_url = ""

    pk = int(object_id)
    if model_name == 'collection':
        obj = tenant_qs(request, Collection).filter(isDeleted=False, pk=pk).first()
        if obj:
            title = f"Collection Details - {obj.paymentID or ''}"
            detail_url = f"/collection_list/?search={obj.paymentID or ''}"
            details = [
                ("Party Name", obj.partyID.name if obj.partyID else "-"),
                ("Payment ID", obj.paymentID or "-"),
                ("Mode of Payment", obj.modeOfPayment),
                ("Paid Amount", f"₹ {obj.paidAmount:.2f}"),
                ("Collected By", obj.collectedBy.name if obj.collectedBy else "-"),
                ("Collection Date/Time", obj.collectionDateTime.strftime('%d-%m-%Y %I:%M %p') if obj.collectionDateTime else "-"),
                ("Bank", obj.bankID.name if obj.bankID else "-"),
                ("Approved By", obj.approvedBy.name if obj.approvedBy else "-"),
                ("Detail", obj.detail or "-"),
                ("Remark", obj.remark or "-"),
            ]
    elif model_name in ('sales', 'salesinvoice'):
        obj = tenant_qs(request, Sales).filter(isDeleted=False, pk=pk).first()
        if obj:
            title = f"Sales Invoice Details - {obj.invoiceNumber or ''}"
            detail_url = f"/sales_list/?search={obj.invoiceNumber or ''}"
            details = [
                ("Party Name", obj.partyID.name if obj.partyID else "-"),
                ("Invoice Number", obj.invoiceNumber or "-"),
                ("Payment Mode", obj.modeOfPayment),
                ("Total Amount", f"₹ {obj.amount:.2f}"),
                ("Taxable Amount", f"₹ {obj.taxableAmount:.2f}"),
                ("GST Amount", f"₹ {obj.gstAmount:.2f}"),
                ("Created By", obj.createdBy.name if obj.createdBy else "-"),
                ("Bank", obj.bankID.name if obj.bankID else "-"),
                ("Remark", obj.remark or "-"),
            ]
    elif model_name in ('cashcounter', 'cashcounteractivity'):
        obj = tenant_qs(request, CashCounter).filter(isDeleted=False, pk=pk).first()
        if obj:
            title = f"Counter Sales Details - {obj.invoiceNumber or ''}"
            detail_url = f"/my_cash_counter_list/?search={obj.invoiceNumber or ''}"
            details = [
                ("Party Name", obj.partyID.name if obj.partyID else "-"),
                ("Invoice Number", obj.invoiceNumber or "-"),
                ("Payment Mode", obj.mode),
                ("Amount", f"₹ {obj.amount:.2f}"),
                ("Created By", obj.createdBy.name if obj.createdBy else "-"),
                ("Bank", obj.bankID.name if obj.bankID else "-"),
                ("Remark", obj.remark or "-"),
            ]
    elif model_name in ('cashcountercollection', 'collectioncashcounter'):
        obj = tenant_qs(request, CollectionCashCounter).filter(isDeleted=False, pk=pk).first()
        if obj:
            title = f"Counter Collection Details - {obj.paymentID or ''}"
            detail_url = f"/my_cash_counter_list/?search={obj.paymentID or ''}"
            details = [
                ("Party Name", obj.partyID.name if obj.partyID else "-"),
                ("Payment ID", obj.paymentID or "-"),
                ("Mode of Payment", obj.modeOfPayment),
                ("Paid Amount", f"₹ {obj.paidAmount:.2f}"),
                ("Collected By", obj.collectedBy.name if obj.collectedBy else "-"),
                ("Remark", obj.remark or "-"),
            ]

    if not details:
        return JsonResponse({'message': 'not_found'}, safe=False)

    return JsonResponse({'message': 'success', 'title': title, 'details': details, 'detail_url': detail_url}, safe=False)


@transaction.atomic
def edit_book_tx_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    tx_id = request.POST.get('id')
    if not tx_id or not tx_id.isdigit():
        return JsonResponse({'message': 'invalid'}, safe=False)
    tx = tenant_qs(request, BookTransaction).select_related('account').filter(isDeleted=False, pk=int(tx_id)).first()
    if tx is None:
        return JsonResponse({'message': 'not_found'}, safe=False)
    if tx.source != BookTransaction.Source.MANUAL:
        return JsonResponse({'message': 'edit_source'}, safe=False)
    if tx.account.type == BookAccount.Type.BANK and not request.user.has_perm('home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    if tx.account.type == BookAccount.Type.CASH and not can_access_cashbook(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    direction = request.POST.get('direction')
    amount = request.POST.get('amount')
    date = request.POST.get('date')
    narration = request.POST.get('narration', '')
    if direction not in (BookTransaction.Direction.IN, BookTransaction.Direction.OUT):
        return JsonResponse({'message': 'invalid_direction'}, safe=False)
    try:
        amount_value = float(amount)
    except Exception:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    if amount_value <= 0:
        return JsonResponse({'message': 'invalid_amount'}, safe=False)
    tx_date = parse_books_date(date) or tx.txDateTime.date()
    tx.txDateTime = datetime.combine(tx_date, tx.txDateTime.time())
    tx.direction = direction
    tx.amount = amount_value
    tx.narration = narration
    tx.save()
    return JsonResponse({'message': 'success'}, safe=False)


@transaction.atomic
def delete_book_tx_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    tx_id = request.POST.get('id')
    if not tx_id or not tx_id.isdigit():
        return JsonResponse({'message': 'invalid'}, safe=False)
    tx = tenant_qs(request, BookTransaction).select_related('account').filter(isDeleted=False, pk=int(tx_id)).first()
    if tx is None:
        return JsonResponse({'message': 'not_found'}, safe=False)
    if tx.account.type == BookAccount.Type.BANK and not request.user.has_perm('home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    if tx.account.type == BookAccount.Type.CASH and not can_access_cashbook(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    if tx.source not in (BookTransaction.Source.MANUAL, BookTransaction.Source.TRANSFER):
        return JsonResponse({'message': 'delete_source'}, safe=False)
    if tx.source == BookTransaction.Source.TRANSFER and tx.transferId:
        tenant = get_current_tenant(request)
        BookTransaction.objects.filter(tenant=tenant, isDeleted=False, transferId=tx.transferId).update(isDeleted=True)
        return JsonResponse({'message': 'success'}, safe=False)
    tx.isDeleted = True
    tx.save()
    return JsonResponse({'message': 'success'}, safe=False)


class BookTransactionListJson(BaseDatatableView):
    """
    Server-side DataTables list for BookTransaction.
    Uses a fixed chronological ordering so the running balance remains meaningful.
    """
    order_columns = ['txDateTime', 'narration', 'party__name', 'amount', 'amount', 'balance', 'source', 'id']

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
        return super().dispatch(request, *args, **kwargs)

    def _parse_date(self, value):
        return parse_books_date(value)

    def _get_account(self):
        account_id = self.request.GET.get('accountId') or self.request.GET.get('account')
        if not account_id or not str(account_id).isdigit():
            return None
        return tenant_qs(self.request, BookAccount).filter(isDeleted=False, pk=int(account_id)).first()

    def _ensure_permitted(self, account):
        if account is None:
            return False
        if account.type == BookAccount.Type.BANK:
            return self.request.user.has_perm('home.can_view_party_credentials')
        return can_access_cashbook(self.request.user)

    def get_initial_queryset(self):
        account = self._get_account()
        if not self._ensure_permitted(account):
            return BookTransaction.objects.none()
        start_date = self._parse_date(self.request.GET.get('startDate')) or datetime.today().date()
        end_date = self._parse_date(self.request.GET.get('endDate')) or start_date
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        base_qs = tenant_qs(self.request, BookTransaction).filter(isDeleted=False, account=account)
        prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
            in_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.IN)),
            out_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.OUT)),
        )
        opening = float(account.openingBalance or 0.0) + float(prior['in_total'] or 0.0) - float(prior['out_total'] or 0.0)
        self._opening = opening
        qs = (
            base_qs
            .filter(txDateTime__date__range=[start_date, end_date])
            .select_related('party', 'account')
        )
        signed_amount = Case(
            When(direction=BookTransaction.Direction.IN, then=F('amount')),
            default=ExpressionWrapper(-F('amount'), output_field=FloatField()),
            output_field=FloatField(),
        )
        running_delta = Window(expression=Sum(signed_amount), order_by=[F('txDateTime').asc(), F('id').asc()])
        balance_expr = ExpressionWrapper(Value(opening, output_field=FloatField()) + running_delta, output_field=FloatField())
        qs = qs.annotate(
            in_amount=Case(
                When(direction=BookTransaction.Direction.IN, then=F('amount')),
                default=Value(0.0),
                output_field=FloatField(),
            ),
            out_amount=Case(
                When(direction=BookTransaction.Direction.OUT, then=F('amount')),
                default=Value(0.0),
                output_field=FloatField(),
            ),
            balance=balance_expr,
        )
        return qs

    def ordering(self, qs):
        return qs.order_by('txDateTime', 'id')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(narration__icontains=search)
                | Q(party__name__icontains=search)
                | Q(source__icontains=search)
                | Q(direction__icontains=search)
                | Q(amount__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        can_manage = _has_any_permission(self.request.user, 'home.can_manage_cash_counter', 'home.can_view_party_credentials', 'home.can_manage_books')
        for item in qs:
            if item.source == BookTransaction.Source.MANUAL and can_manage:
                action = f'''
                  <a href="javascript:void(0)" data-inverted="" data-tooltip="Edit" data-position="left center" data-variation="mini"
                     style="font-size:10px;" onclick="openEditTx({item.pk})" class="ui circular facebook icon button green">
                    <i class="pen icon"></i>
                  </a>
                  <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini"
                          style="font-size:10px;" onclick="deleteTx({item.pk})" class="ui circular youtube icon button">
                    <i class="trash alternate icon"></i>
                  </button>'''
            elif item.source == BookTransaction.Source.TRANSFER and can_manage:
                action = f'''
                  <button data-inverted="" data-tooltip="Delete Transfer" data-position="left center" data-variation="mini"
                          style="font-size:10px;" onclick="deleteTx({item.pk})" class="ui circular youtube icon button">
                    <i class="trash alternate icon"></i>
                  </button>'''
            else:
                action = '''<div class="ui tiny label">Auto</div>'''
            try:
                if item.party:
                    party_val = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('party', '{item.party.pk}')" style="cursor: pointer;">{escape(item.party.name)}</span>'''
                else:
                    party_val = '-'
            except:
                party_val = '-'
            if (item.source_content_type_id and item.source_object_id) or (item.source == BookTransaction.Source.TRANSFER and item.transferId):
                narration_val = f'''<span class="lookup-detail-link" onclick="showSourceTxLookup('{item.pk}')" style="cursor: pointer;"><i class="file alternate outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.narration or '-')}</span>'''
            else:
                narration_val = escape(item.narration or '-')

            json_data.append([
                escape(item.txDateTime.strftime('%d-%m-%Y %I:%M %p')),
                narration_val,
                party_val,
                f'₹ {formatINR(format(float(item.in_amount or 0.0), ".0f"))}' if float(item.in_amount or 0.0) else '-',
                f'₹ {formatINR(format(float(item.out_amount or 0.0), ".0f"))}' if float(item.out_amount or 0.0) else '-',
                f'₹ {_format_money_compact(item.balance)}',
                escape(item.get_source_display()),
                action,
            ])
        return json_data
