from home.models import Bank, Party, Sales, StaffUser
from home.services.collections import has_any_permission
from home.services.orders import is_admin_like
from home.tenancy import tenant_qs


def can_manage_sales(user):
    return user.has_perm('home.can_manage_sales')


def can_approve_sales(user):
    return user.has_perm('home.can_approve_sales')


def has_sales_access(user):
    return has_any_permission(user, 'home.can_manage_sales', 'home.can_approve_sales', 'home.view_sales', 'home.add_sales', 'home.change_sales')


def has_return_source_access(user):
    return has_sales_access(user) or has_any_permission(user, 'home.can_manage_cash_counter', 'home.view_cashcounter', 'home.can_view_party_credentials')


def can_filter_all_sales(user):
    return is_admin_like(user) or can_manage_sales(user) or can_approve_sales(user)


def can_edit_sales(user):
    return user.has_perm('home.change_sales') or can_manage_sales(user) or can_approve_sales(user) or user.has_perm('home.can_view_party_credentials')


def can_delete_sales(user):
    return user.has_perm('home.can_view_party_credentials')


def can_edit_sales_record(user, sale_obj):
    if not user.is_authenticated:
        return False
    if not can_edit_sales(user):
        return False
    if sale_obj is None:
        return False
    admin_like = can_manage_sales(user) or can_approve_sales(user) or user.has_perm('home.can_view_party_credentials')
    if sale_obj.isApproved and not admin_like:
        return False
    if admin_like:
        return True
    staff = tenant_qs(user, StaffUser).filter(user_ID_id=user.pk, isDeleted=False).only('id').first()
    if not staff:
        return False
    return sale_obj.createdBy_id == staff.pk


def normalize_sales_mode(mode: str | None) -> str:
    value = str(mode or Sales.Mode.CASH).strip()
    aliases = {
        'Card': Sales.Mode.ONLINE,
        'Card/Online': Sales.Mode.ONLINE,
    }
    if value in aliases:
        return aliases[value]
    valid = {choice[0] for choice in Sales.Mode.choices}
    return value if value in valid else Sales.Mode.CASH


def sales_list_base_qs(request_or_user):
    return tenant_qs(request_or_user, Sales).select_related('partyID', 'createdBy', 'approvedBy').only(
        'id', 'paymentID', 'invoiceNumber', 'amount', 'buildDate', 'datetime', 'remark', 'isApproved',
        'partyID__name', 'createdBy__name', 'approvedBy__name',
    )


def parse_sales_party(request, party_value: str | None) -> Party:
    raw = str(party_value or '').strip()
    if not raw:
        raise ValueError('Party is required.')
    if '@' in raw:
        raw = raw.rsplit('@', 1)[-1].strip()
    if not raw.isdigit():
        raise ValueError('Invalid party selection.')
    return tenant_qs(request, Party).select_related().get(pk=int(raw))


def parse_sales_bank_id(request, bank_value: str | None) -> int | None:
    raw = str(bank_value or '').strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError('Invalid bank selection.')
    bank = tenant_qs(request, Bank).filter(isDeleted=False, pk=int(raw)).only('id').first()
    if not bank:
        raise ValueError('Selected bank was not found.')
    return bank.pk
