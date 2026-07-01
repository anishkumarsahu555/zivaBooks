from datetime import datetime

from django.db.models import Q, Sum

from home.books import ensure_books_for_tenant
from home.models import Bank, BookAccount, BookTransaction
from home.services.collections import has_any_permission
from home.tenancy import get_current_tenant, tenant_qs


def parse_books_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%d/%m/%Y').date()
    except Exception:
        return None


def cashbook_export_account(request):
    account_id = request.GET.get('account') or request.GET.get('accountId')
    if account_id and str(account_id).isdigit():
        return tenant_qs(request, BookAccount).filter(
            isDeleted=False,
            pk=int(account_id),
            type=BookAccount.Type.CASH,
        ).first()
    tenant = get_current_tenant(request)
    if tenant:
        ensure_books_for_tenant(tenant)
    return tenant_qs(request, BookAccount).filter(
        isDeleted=False,
        type=BookAccount.Type.CASH,
    ).order_by('-isDefault', 'name').first()


def bankbook_export_account(request):
    bank_id = request.GET.get('bank')
    tenant = get_current_tenant(request)
    if tenant:
        ensure_books_for_tenant(tenant)
    accounts = tenant_qs(request, BookAccount).filter(
        isDeleted=False,
        type=BookAccount.Type.BANK,
    ).select_related('bank').order_by('name')
    if bank_id and str(bank_id).isdigit():
        selected_bank = tenant_qs(request, Bank).filter(isDeleted=False, pk=int(bank_id)).first()
        if selected_bank:
            return accounts.filter(bank=selected_bank).first()
    return accounts.first()


def can_access_cashbook(user):
    return has_any_permission(user, 'home.can_manage_cash_counter', 'home.can_view_party_credentials', 'home.can_manage_books')


def can_access_bankbook(user):
    return user.has_perm('home.can_view_party_credentials') or user.has_perm('home.can_manage_books')


def book_statement_rows(request, account, start_date, end_date, include_bank=False):
    base_qs = tenant_qs(request, BookTransaction).filter(isDeleted=False, account=account)
    prior = base_qs.filter(txDateTime__date__lt=start_date).aggregate(
        in_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.IN)),
        out_total=Sum('amount', filter=Q(direction=BookTransaction.Direction.OUT)),
    )
    opening = float(account.openingBalance or 0.0) + float(prior['in_total'] or 0.0) - float(prior['out_total'] or 0.0)
    related = ['party', 'account']
    if include_bank:
        related.append('account__bank')
    qs = (
        base_qs
        .filter(txDateTime__date__range=[start_date, end_date])
        .select_related(*related)
        .order_by('txDateTime', 'id')
    )
    balance = opening
    total_in = 0.0
    total_out = 0.0
    rows = []
    for item in qs:
        in_amt = float(item.amount or 0.0) if item.direction == BookTransaction.Direction.IN else 0.0
        out_amt = float(item.amount or 0.0) if item.direction == BookTransaction.Direction.OUT else 0.0
        balance += in_amt - out_amt
        total_in += in_amt
        total_out += out_amt
        rows.append({
            'date': item.txDateTime,
            'particulars': (item.narration or '-').strip() or '-',
            'party': item.party.name if item.party_id else '-',
            'receipt': in_amt,
            'payment': out_amt,
            'balance': balance,
            'source': item.get_source_display(),
        })
    return {
        'opening': opening,
        'rows': rows,
        'total_in': total_in,
        'total_out': total_out,
        'closing': balance,
    }


def cashbook_statement_rows(request, account, start_date, end_date):
    return book_statement_rows(request, account, start_date, end_date, include_bank=False)


def bankbook_statement_rows(request, account, start_date, end_date):
    return book_statement_rows(request, account, start_date, end_date, include_bank=True)
