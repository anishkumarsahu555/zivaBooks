from datetime import date, timedelta

from django.db.models import F, Q
from django.urls import reverse

from .navigation import _effective_permission_names


def _has_any_perm(request_or_user, perm_names):
    effective_permissions = _effective_permission_names(request_or_user)
    return any(perm_name in effective_permissions for perm_name in perm_names)


def _action_alert(label, value, description, icon, tone, action_label, action_url):
    return {
        'label': label,
        'value': value,
        'description': description,
        'icon': icon,
        'tone': tone,
        'action_label': action_label,
        'action_url': action_url,
    }


def build_action_alerts(request):
    from .models import (
        Collection,
        CollectionCashCounter,
        InventoryBatch,
        InventoryStock,
        Order,
        WhatsappMessageStatus,
    )
    from .tenancy import tenant_qs

    today = date.today()
    alerts = []
    effective_permissions = _effective_permission_names(request)

    if _has_any_perm(request, ['home.can_manage_orders', 'home.can_approve_orders']):
        pending_orders = tenant_qs(request, Order).filter(
            isDeleted=False,
            status=Order.Status.SUBMITTED,
        ).count()
        alerts.append(_action_alert(
            'Pending Orders',
            pending_orders,
            'Submitted orders waiting for approval.',
            'clipboard check',
            'red' if pending_orders else 'green',
            'Review',
            f"{reverse('homeApp:orders_list')}?status={Order.Status.SUBMITTED}",
        ))

    if _has_any_perm(request, ['home.can_approve_collection']):
        pending_collections = tenant_qs(request, Collection).filter(
            isDeleted=False,
            isApproved=False,
        ).count()
        alerts.append(_action_alert(
            'Pending Collections',
            pending_collections,
            'Collection entries waiting for approval.',
            'hand holding usd',
            'red' if pending_collections else 'green',
            'Approve',
            f"{reverse('homeApp:collection_list')}?approval=pending",
        ))

    if _has_any_perm(request, ['home.view_collection', 'home.can_approve_collection', 'home.can_view_collection_reports']):
        cheque_filter = Q(modeOfPayment__iexact='Cheque') | Q(modeOfPayment__iexact='Cheque CC')
        overdue_collections = tenant_qs(request, Collection).filter(
            cheque_filter,
            isDeleted=False,
            chequeDate__lt=today,
        ).count()
        overdue_counter_collections = tenant_qs(request, CollectionCashCounter).filter(
            cheque_filter,
            isDeleted=False,
            chequeDate__lt=today,
        ).count()
        overdue_cheques = overdue_collections + overdue_counter_collections
        alerts.append(_action_alert(
            'Overdue Cheques',
            overdue_cheques,
            'Cheque payments past their reminder date.',
            'bell',
            'amber' if overdue_cheques else 'green',
            'Open List',
            f"{reverse('homeApp:cheque_reminder_list')}?due=overdue",
        ))

    if 'home.view_whatsappmessagestatus' in effective_permissions:
        failed_messages = tenant_qs(request, WhatsappMessageStatus).filter(
            isDeleted=False,
        ).exclude(status='Success').count()
        alerts.append(_action_alert(
            'Failed Messages',
            failed_messages,
            'Outbound messages that need retry or review.',
            'comment alternate outline',
            'red' if failed_messages else 'green',
            'Review',
            reverse('homeApp:report_failed_messages'),
        ))

    if _has_any_perm(request, [
        'home.can_manage_inventory',
        'home.can_view_inventory_reports',
        'home.view_inventoryitem',
        'home.add_inventoryitem',
        'home.change_inventoryitem',
        'home.can_view_party_credentials',
    ]):
        low_stock_rows = tenant_qs(request, InventoryStock).filter(
            isDeleted=False,
            item__isDeleted=False,
            item__isActive=True,
        ).filter(
            Q(item__reorderLevel__gt=0, quantityOnHand__lte=F('item__reorderLevel')) |
            Q(item__minimumStockLevel__gt=0, quantityOnHand__lte=F('item__minimumStockLevel'))
        ).values('item_id').distinct().count()
        expiring_batches = tenant_qs(request, InventoryBatch).filter(
            isDeleted=False,
            isActive=True,
            quantityOnHand__gt=0,
            expiryDate__isnull=False,
            expiryDate__gte=today,
            expiryDate__lte=today + timedelta(days=30),
        ).count()
        alerts.append(_action_alert(
            'Low Stock Items',
            low_stock_rows,
            'Items at or below configured stock thresholds.',
            'warehouse',
            'amber' if low_stock_rows else 'green',
            'Open Stock',
            f"{reverse('homeApp:inventory_items_page')}?filter=low_stock",
        ))
        alerts.append(_action_alert(
            'Expiring Batches',
            expiring_batches,
            'Active inventory batches expiring within 30 days.',
            'calendar times outline',
            'amber' if expiring_batches else 'green',
            'Open Stock',
            f"{reverse('homeApp:inventory_stock_page')}?filter=expiring_30",
        ))

    return alerts
