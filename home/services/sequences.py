from utils.logger import logger
from django.db import transaction

from home.models import TenantSequence
from home.tenancy import get_current_tenant


TENANT_SEQUENCE_DEFAULTS = {
    TenantSequence.Scope.COLLECTION_PAYMENT: {'prefix': 'COL', 'padding': 6},
    TenantSequence.Scope.SALES: {'prefix': 'SAL', 'padding': 6},
    TenantSequence.Scope.SALES_PAYMENT: {'prefix': 'SPY', 'padding': 6},
    TenantSequence.Scope.ORDER: {'prefix': 'ORD', 'padding': 6},
    TenantSequence.Scope.PURCHASE: {'prefix': 'PUR', 'padding': 6},
    TenantSequence.Scope.CASH_COUNTER: {'prefix': 'CC', 'padding': 6},
    TenantSequence.Scope.CASH_COUNTER_COLLECTION: {'prefix': 'CCP', 'padding': 6},
    TenantSequence.Scope.INVENTORY_OPENING: {'prefix': 'OPN', 'padding': 6},
    TenantSequence.Scope.INVENTORY_ADJUSTMENT: {'prefix': 'ADJ', 'padding': 6},
    TenantSequence.Scope.INVENTORY_TRANSFER: {'prefix': 'TRF', 'padding': 6},
    TenantSequence.Scope.PURCHASE_RETURN: {'prefix': 'PRN', 'padding': 6},
    TenantSequence.Scope.SALES_RETURN: {'prefix': 'SRN', 'padding': 6},
    TenantSequence.Scope.PROFORMA_INVOICE: {'prefix': 'PI', 'padding': 6},
}


def tenant_sequence_queryset(request_or_user, scope):
    tenant = get_current_tenant(request_or_user)
    return TenantSequence.objects.filter(tenant=tenant, scope=scope)


def next_tenant_sequence_value(request_or_user, scope):
    defaults = TENANT_SEQUENCE_DEFAULTS[scope]
    with transaction.atomic():
        sequence = tenant_sequence_queryset(request_or_user, scope).select_for_update().first()
        if sequence is None:
            sequence = TenantSequence(
                tenant=get_current_tenant(request_or_user),
                scope=scope,
                prefix=defaults['prefix'],
                paddingLength=defaults['padding'],
                nextNumber=1,
            )
            sequence.save()
        number = sequence.nextNumber
        sequence.nextNumber = number + 1
        sequence.save(update_fields=['nextNumber', 'lastUpdatedOn'])
    generated_val = f"{sequence.prefix}{str(number).zfill(sequence.paddingLength)}"
    logger.info("[Sequences] Generated sequence value '%s' for scope '%s'", generated_val, scope)
    return generated_val
