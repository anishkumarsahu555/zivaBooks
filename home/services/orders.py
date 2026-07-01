from django.utils import timezone
from django.utils.html import escape

from home.models import Order, OrderStatusEvent, StaffUser
from home.services.collections import has_any_permission
from home.tenancy import assign_tenant, tenant_qs


ADMIN_LIKE_PERMS = (
    'home.can_manage_staff',
    'home.can_manage_staff_groups',
    'home.can_manage_parties',
    'home.can_view_party_credentials',
    'home.can_approve_collection',
    'home.can_view_audit_logs',
)


def is_admin_like(user):
    return has_any_permission(user, ADMIN_LIKE_PERMS)


def order_public_id(obj):
    return getattr(obj, 'orderNumber', None) or f'ORD{str(getattr(obj, "pk", 0)).zfill(6)}'


def can_manage_orders(user):
    return has_any_permission(user, 'home.can_manage_orders', 'home.can_approve_orders')


def can_filter_all_orders(user):
    return is_admin_like(user) or can_manage_orders(user) or user.has_perm('home.can_view_party_credentials')


def can_add_order(user):
    return user.has_perm('home.add_order')


def can_edit_order(user):
    return user.has_perm('home.change_order') or can_manage_orders(user) or user.has_perm('home.can_view_party_credentials')


def can_delete_order(user):
    return user.has_perm('home.delete_order') or user.has_perm('home.can_view_party_credentials')


def can_approve_orders(user):
    return user.has_perm('home.can_approve_orders')


def can_set_order_date(user):
    return user.has_perm('home.can_manage_orders') or user.has_perm('home.can_approve_orders') or user.has_perm('home.can_view_party_credentials')


def format_order_number(pk):
    return 'O' + str(int(pk)).zfill(8)


def order_status_text(status):
    status = str(status or '')
    status_map = {
        Order.Status.DRAFT: 'Draft',
        Order.Status.SUBMITTED: 'Submitted',
        Order.Status.APPROVED: 'Confirmed',
        Order.Status.PACKED: 'Packed',
        Order.Status.OUT_FOR_DELIVERY: 'Out for Delivery',
        Order.Status.DELIVERED: 'Delivered',
        Order.Status.CANCELLED: 'Cancelled',
        Order.Status.RETURNED: 'Returned',
        Order.Status.FULFILLED: 'Delivered',
    }
    return status_map.get(status, status.replace('_', ' ').title() or 'Unknown')


def order_status_label(status):
    status = str(status or '')
    if status == Order.Status.APPROVED:
        return '<div class="ui tiny teal label">Confirmed</div>'
    if status == Order.Status.SUBMITTED:
        return '<div class="ui tiny orange label">Submitted</div>'
    if status == Order.Status.DRAFT:
        return '<div class="ui tiny grey label">Draft</div>'
    if status == Order.Status.PACKED:
        return '<div class="ui tiny violet label">Packed</div>'
    if status == Order.Status.OUT_FOR_DELIVERY:
        return '<div class="ui tiny blue label">Out for Delivery</div>'
    if status == Order.Status.DELIVERED:
        return '<div class="ui tiny green label">Delivered</div>'
    if status == Order.Status.CANCELLED:
        return '<div class="ui tiny red label">Cancelled</div>'
    if status == Order.Status.RETURNED:
        return '<div class="ui tiny pink label">Returned</div>'
    if status == Order.Status.FULFILLED:
        return '<div class="ui tiny green label">Delivered</div>'
    return '<div class="ui tiny label">{}</div>'.format(escape(order_status_text(status)))


def order_status_transition_map():
    return {
        Order.Status.DRAFT: [Order.Status.SUBMITTED, Order.Status.CANCELLED],
        Order.Status.SUBMITTED: [Order.Status.APPROVED, Order.Status.CANCELLED],
        Order.Status.APPROVED: [Order.Status.PACKED, Order.Status.CANCELLED],
        Order.Status.PACKED: [Order.Status.OUT_FOR_DELIVERY, Order.Status.CANCELLED],
        Order.Status.OUT_FOR_DELIVERY: [Order.Status.DELIVERED, Order.Status.RETURNED],
        Order.Status.DELIVERED: [Order.Status.RETURNED],
        Order.Status.CANCELLED: [],
        Order.Status.RETURNED: [],
        Order.Status.FULFILLED: [Order.Status.RETURNED],
    }


def normalize_order_status(status):
    if str(status) == Order.Status.FULFILLED:
        return Order.Status.DELIVERED
    return str(status or '')


def set_order_tracking_timestamps(order_obj, new_status, actor_staff=None):
    now_value = timezone.now()
    new_status = normalize_order_status(new_status)
    order_obj.status = new_status
    if new_status == Order.Status.APPROVED:
        order_obj.approvedOn = order_obj.approvedOn or now_value
        if actor_staff and not order_obj.approvedBy_id:
            order_obj.approvedBy = actor_staff
    elif new_status == Order.Status.PACKED:
        order_obj.packedOn = order_obj.packedOn or now_value
    elif new_status == Order.Status.OUT_FOR_DELIVERY:
        order_obj.outForDeliveryOn = order_obj.outForDeliveryOn or now_value
    elif new_status == Order.Status.DELIVERED:
        order_obj.deliveredOn = order_obj.deliveredOn or now_value
    elif new_status == Order.Status.CANCELLED:
        order_obj.cancelledOn = order_obj.cancelledOn or now_value
    elif new_status == Order.Status.RETURNED:
        order_obj.returnedOn = order_obj.returnedOn or now_value


def create_order_status_event(request, order_obj, from_status, to_status, note=''):
    event = OrderStatusEvent(order=order_obj)
    assign_tenant(event, request)
    event.fromStatus = normalize_order_status(from_status) or None
    event.toStatus = normalize_order_status(to_status)
    event.note = note or ''
    staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk, isDeleted=False).only('id').first()
    if staff:
        event.actor_id = staff.pk
    event.save()


def order_allowed_next_statuses(order_obj):
    current = normalize_order_status(getattr(order_obj, 'status', None))
    return order_status_transition_map().get(current, [])


def link_sale_to_order(request, order_obj, sale_obj, *, note=''):
    if not order_obj or not sale_obj:
        return
    previous_sale_id = order_obj.linkedSales_id
    order_obj.linkedSales = sale_obj
    update_fields = ['linkedSales', 'lastUpdatedOn']
    if normalize_order_status(order_obj.status) in {Order.Status.DRAFT, Order.Status.SUBMITTED}:
        actor_staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk, isDeleted=False).only('id').first()
        previous_status = normalize_order_status(order_obj.status)
        set_order_tracking_timestamps(order_obj, Order.Status.APPROVED, actor_staff)
        update_fields.extend(['status', 'approvedOn', 'approvedBy'])
        order_obj.save(update_fields=update_fields)
        create_order_status_event(request, order_obj, previous_status, order_obj.status, note or f'Sale {sale_obj.invoiceNumber or sale_obj.salesID or sale_obj.pk} linked')
    else:
        order_obj.save(update_fields=update_fields)
        if previous_sale_id != sale_obj.pk:
            create_order_status_event(request, order_obj, order_obj.status, order_obj.status, note or f'Sale {sale_obj.invoiceNumber or sale_obj.salesID or sale_obj.pk} linked')


def can_edit_order_record(user, order_obj):
    if not user.is_authenticated or order_obj is None:
        return False
    if not can_edit_order(user):
        return False
    admin_like = can_filter_all_orders(user)
    if str(order_obj.status) not in {Order.Status.DRAFT, Order.Status.SUBMITTED} and not admin_like:
        return False
    if admin_like:
        return True
    staff = tenant_qs(user, StaffUser).filter(user_ID_id=user.pk, isDeleted=False).only('id').first()
    if not staff:
        return False
    return order_obj.createdBy_id == staff.pk


def can_delete_order_record(user, order_obj):
    if not user.is_authenticated or order_obj is None:
        return False
    if not can_delete_order(user):
        return False
    admin_like = can_filter_all_orders(user)
    if str(order_obj.status) not in {Order.Status.DRAFT, Order.Status.SUBMITTED} and not admin_like:
        return False
    if admin_like:
        return True
    staff = tenant_qs(user, StaffUser).filter(user_ID_id=user.pk, isDeleted=False).only('id').first()
    if not staff:
        return False
    return order_obj.createdBy_id == staff.pk


def orders_list_base_qs(request_or_user):
    return tenant_qs(request_or_user, Order).select_related('partyID', 'createdBy', 'approvedBy').only(
        'id', 'orderNumber', 'buildDate', 'datetime', 'lastUpdatedOn', 'remark', 'approvedOn',
        'status', 'latitude', 'longitude', 'location',
        'partyID__name', 'createdBy__name', 'approvedBy__name'
    )
