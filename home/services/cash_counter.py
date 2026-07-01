from home.models import CashCounter, CollectionCashCounter
from home.services.collections import has_any_permission
from home.tenancy import tenant_qs


def can_manage_cash_counter_records(user):
    return user.has_perm('home.can_view_party_credentials')


def can_filter_all_cash_counter(user):
    return has_any_permission(
        user,
        'home.can_manage_cash_counter',
        'home.can_view_party_credentials',
        'home.can_approve_collection',
    )


def cash_counter_list_base_qs(request_or_user):
    return tenant_qs(request_or_user, CashCounter).select_related('partyID', 'bankID', 'createdBy').only(
        'id', 'counterID', 'invoiceNumber', 'amount', 'mixCashAmount', 'mixCardAmount', 'mode',
        'expenseType', 'remark', 'datetime', 'partyID__name', 'bankID__name',
        'bankID__accountNumber', 'createdBy__name'
    )


def cash_counter_collection_list_base_qs(request_or_user):
    return tenant_qs(request_or_user, CollectionCashCounter).select_related(
        'partyID', 'transferredPartyID', 'bankID', 'collectedBy', 'approvedBy'
    ).only(
        'id', 'paymentID', 'paidAmount', 'modeOfPayment', 'detail', 'remark', 'collectionAddress',
        'collectionDateTime', 'datetime', 'isApproved', 'chequeDate',
        'partyID__name', 'transferredPartyID__name', 'bankID__name', 'collectedBy__name', 'approvedBy__name'
    )
