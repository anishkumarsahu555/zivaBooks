from datetime import datetime

from django.utils import timezone

from home.models import Collection
from home.tenancy import tenant_qs


BANK_REQUIRED_COLLECTION_MODES = {'Online', 'Cheque', 'Cheque CC'}
CHEQUE_COLLECTION_MODES = {'Cheque', 'Cheque CC'}


def parse_party_id_value(raw):
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if '@' in value:
        right = value.split('@')[-1].strip()
        if right.isdigit():
            return int(right)
    if value.isdigit():
        return int(value)
    return None


def has_any_permission(user, *perm_names):
    if len(perm_names) == 1 and isinstance(perm_names[0], (list, tuple, set)):
        perm_names = tuple(perm_names[0])
    return any(user.has_perm(perm_name) for perm_name in perm_names)


def can_review_collections(user):
    return user.has_perm('home.can_approve_collection')


def can_auto_approve_collections(user):
    return user.has_perm('home.can_approve_collection')


def can_filter_all_collections(user):
    return can_review_collections(user)


def collection_verified_label(is_verified):
    if is_verified:
        return '<div class="ui tiny green label">Yes</div>'
    return '<div class="ui tiny grey label">No</div>'


def collection_list_base_qs(request_or_user):
    return tenant_qs(request_or_user, Collection).select_related(
        'partyID', 'transferredPartyID', 'bankID', 'collectedBy', 'approvedBy'
    ).only(
        'id', 'paymentID', 'paidAmount', 'modeOfPayment', 'detail', 'remark', 'collectionAddress',
        'collectionDateTime', 'datetime', 'isApproved', 'isTallied', 'chequeDate', 'otpVerified', 'otpVerifiedOn',
        'partyID__name', 'transferredPartyID__name', 'bankID__name',
        'collectedBy__name', 'approvedBy__name'
    )


def parse_collection_form_date(raw_date):
    if not raw_date:
        return None
    try:
        return datetime.strptime(raw_date, '%d/%m/%Y').date()
    except Exception:
        return None


def collection_datetime_with_preserved_time(raw_date, base_datetime):
    parsed_date = parse_collection_form_date(raw_date)
    if not parsed_date:
        return base_datetime
    base_dt = base_datetime or timezone.now()
    try:
        base_dt = timezone.localtime(base_dt)
    except Exception:
        pass
    try:
        return datetime.combine(parsed_date, base_dt.timetz().replace(microsecond=0))
    except Exception:
        return base_datetime


from utils.logger import logger


def collection_payment_validation_error(payment_mode, bank, cheque_date):
    if payment_mode in BANK_REQUIRED_COLLECTION_MODES and not bank:
        logger.error("[CollectionsService] Validation error: Bank required for payment mode %s", payment_mode)
        return 'bank_required'
    if payment_mode in CHEQUE_COLLECTION_MODES and not cheque_date:
        logger.error("[CollectionsService] Validation error: Cheque date required for payment mode %s", payment_mode)
        return 'cheque_date_required'
    return None
