from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView
from weasyprint import CSS, HTML

from home.audit import log_audit_event, snapshot_instance
from home.models import *
from home.otp import (
    OTP_TTL_SECONDS,
    clear_order_delivery_otp,
    clear_order_delivery_status,
    get_order_delivery_otp_payload,
    set_order_delivery_otp,
    set_order_delivery_status,
)
from home.services.collections import has_any_permission as _has_any_permission
from home.services.reports import pdf_base_context as _pdf_base_context, xlsx_report_response as _xlsx_report_response
from home.services.sequences import next_tenant_sequence_value as _next_tenant_sequence_value
from home.services.orders import (
    can_add_order as _can_add_order,
    can_approve_orders as _can_approve_orders,
    can_delete_order_record as _can_delete_order_record,
    can_edit_order as _can_edit_order,
    can_edit_order_record as _can_edit_order_record,
    can_filter_all_orders as _can_filter_all_orders,
    can_set_order_date as _can_set_order_date,
    create_order_status_event as _create_order_status_event,
    link_sale_to_order as _link_sale_to_order,
    normalize_order_status as _normalize_order_status,
    order_allowed_next_statuses as _order_allowed_next_statuses,
    order_public_id as _order_public_id,
    order_status_label as _order_status_label,
    orders_list_base_qs as _orders_list_base_qs,
    set_order_tracking_timestamps as _set_order_tracking_timestamps,
)
from home.tenancy import assign_tenant, get_current_tenant, tenant_qs


def _sales_public_id(obj):
    return getattr(obj, 'salesID', None) or getattr(obj, 'paymentID', None) or f'SAL{str(getattr(obj, "pk", 0)).zfill(6)}'

def request_order_delivery_otp_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = int(request.POST.get('orderID') or 0)
        obj = tenant_qs(request, Order).select_related('partyID').filter(pk=order_id, isDeleted=False).first()
        if not obj:
            return JsonResponse({'message': 'error', 'error': 'Order not found.'}, status=404)
        if not (_can_approve_orders(request.user) or _can_edit_order_record(request.user, obj)):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        if not obj.partyID_id:
            return JsonResponse({'message': 'error', 'error': 'Party is required for delivery OTP.'}, status=400)
        otp = get_random_string(length=6, allowed_chars='0123456789')
        meta = {'orderID': obj.pk, 'status': Order.Status.DELIVERED, 'orderNumber': obj.orderNumber or str(obj.pk)}
        clear_order_delivery_status(obj.partyID.tenant_id, obj.partyID_id)
        set_order_delivery_otp(obj.partyID.tenant_id, obj.partyID_id, otp, ttl=OTP_TTL_SECONDS, meta=meta)
        return JsonResponse({'message': 'success', 'expires_in': OTP_TTL_SECONDS}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)


def verify_order_delivery_otp_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = int(request.POST.get('orderID') or 0)
        otp_code = (request.POST.get('otp') or '').strip()
        if not (otp_code.isdigit() and len(otp_code) == 6):
            return JsonResponse({'message': 'otp_invalid'}, safe=False)
        obj = tenant_qs(request, Order).select_related('partyID').filter(pk=order_id, isDeleted=False).first()
        if not obj:
            return JsonResponse({'message': 'error', 'error': 'Order not found.'}, status=404)
        if not (_can_approve_orders(request.user) or _can_edit_order_record(request.user, obj)):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        if not obj.partyID_id:
            return JsonResponse({'message': 'error', 'error': 'Party is required for delivery OTP.'}, status=400)
        payload = get_order_delivery_otp_payload(obj.partyID.tenant_id, obj.partyID_id) or {}
        if not payload or str(payload.get('otp')) != otp_code:
            return JsonResponse({'message': 'otp_invalid'}, safe=False)
        meta = payload.get('meta') or {}
        if str(meta.get('orderID') or '') not in {'', str(obj.pk)}:
            return JsonResponse({'message': 'otp_invalid'}, safe=False)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)


def clear_order_delivery_otp_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = int(request.POST.get('orderID') or 0)
        obj = tenant_qs(request, Order).select_related('partyID').filter(pk=order_id, isDeleted=False).first()
        if not obj:
            return JsonResponse({'message': 'error', 'error': 'Order not found.'}, status=404)
        if not (_can_approve_orders(request.user) or _can_edit_order_record(request.user, obj)):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        if not obj.partyID_id:
            return JsonResponse({'message': 'error', 'error': 'Party is required for delivery OTP.'}, status=400)
        clear_order_delivery_otp(obj.partyID.tenant_id, obj.partyID_id)
        clear_order_delivery_status(obj.partyID.tenant_id, obj.partyID_id)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, status=500)
@transaction.atomic
def add_order_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _can_add_order(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        build_date = request.POST.get('buildDate')
        party_id = request.POST.get('party')
        order_text = request.POST.get('orderText')
        remark = request.POST.get('remark')
        assigned_to = request.POST.get('assignedTo')
        lat = request.POST.get('lat')
        lng = request.POST.get('lng')
        loc = request.POST.get('loc')
        obj = Order()
        assign_tenant(obj, request)
        obj.orderText = order_text
        obj.remark = remark
        if _can_set_order_date(request.user) and build_date:
            obj.buildDate = datetime.strptime(build_date, '%d/%m/%Y').date()
        else:
            obj.buildDate = datetime.today().date()
        if party_id:
            obj.partyID_id = int(party_id)
        if lat:
            obj.latitude = lat
        if lng:
            obj.longitude = lng
        if loc and loc != 'N/A':
            obj.location = loc
        staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk, isDeleted=False).only('id').first()
        if staff:
            obj.createdBy_id = staff.pk
            obj.assignedTo_id = staff.pk
        if assigned_to:
            obj.assignedTo_id = int(assigned_to)
        # If staff enters text, consider it submitted; otherwise draft.
        obj.status = Order.Status.SUBMITTED if (order_text or '').strip() else Order.Status.DRAFT
        obj.save()
        # Auto-generate order number like Sales.paymentID.
        obj.orderNumber = _next_tenant_sequence_value(request, TenantSequence.Scope.ORDER)
        obj.save(update_fields=['orderNumber', 'lastUpdatedOn'])
        _create_order_status_event(request, obj, None, obj.status, 'Order created')
        for f in request.FILES.getlist('images'):
            att = OrderAttachment(order=obj)
            assign_tenant(att, request)
            att.image = f
            att.save()
        return JsonResponse({'message': 'success', 'order_id': obj.pk}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def update_order_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = request.POST.get('ID')
        obj = tenant_qs(request, Order).select_related('createdBy').get(pk=int(order_id))
        if not _can_edit_order_record(request.user, obj):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        build_date = request.POST.get('buildDate')
        party_id = request.POST.get('party')
        order_text = request.POST.get('orderText')
        remark = request.POST.get('remark')
        status = request.POST.get('status')
        previous_status = _normalize_order_status(obj.status)
        lat = request.POST.get('lat')
        lng = request.POST.get('lng')
        loc = request.POST.get('loc')
        assigned_to = request.POST.get('assignedTo')
        if order_text is not None:
            obj.orderText = order_text
        if remark is not None:
            obj.remark = remark
        if _can_set_order_date(request.user) and build_date:
            obj.buildDate = datetime.strptime(build_date, '%d/%m/%Y').date()
        if party_id:
            obj.partyID_id = int(party_id)
        if assigned_to:
            obj.assignedTo_id = int(assigned_to)
        elif assigned_to == '':
            obj.assignedTo = None
        if status in dict(Order.Status.choices):
            _set_order_tracking_timestamps(
                obj,
                status,
                tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk, isDeleted=False).only('id').first(),
            )
        if lat:
            obj.latitude = lat
        if lng:
            obj.longitude = lng
        if loc is not None and loc != 'N/A':
            obj.location = loc
        obj.save()
        if _normalize_order_status(obj.status) != previous_status:
            _create_order_status_event(request, obj, previous_status, obj.status, request.POST.get('statusNote', ''))
        for f in request.FILES.getlist('images'):
            att = OrderAttachment(order=obj)
            assign_tenant(att, request)
            att.image = f
            att.save()
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def delete_order(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = request.POST.get('userID')
        obj = tenant_qs(request, Order).get(pk=int(order_id))
        if not _can_delete_order_record(request.user, obj):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        obj.isDeleted = True
        obj.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def approve_order(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _can_approve_orders(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    try:
        order_id = request.POST.get('userID')
        obj = tenant_qs(request, Order).get(pk=int(order_id))
        staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk, isDeleted=False).only('id').first()
        previous_status = _normalize_order_status(obj.status)
        _set_order_tracking_timestamps(obj, Order.Status.APPROVED, staff)
        obj.save(update_fields=['status', 'approvedOn', 'approvedBy', 'lastUpdatedOn'])
        _create_order_status_event(request, obj, previous_status, obj.status, 'Order confirmed')
        log_audit_event(
            obj,
            AuditLog.Action.APPROVE,
            actor=request.user,
            changes={
                'status': {'from': previous_status, 'to': Order.Status.APPROVED},
                'approvedBy_id': {'from': None, 'to': getattr(staff, 'pk', None)},
                'approvedOn': {'from': None, 'to': obj.approvedOn.isoformat() if obj.approvedOn else None},
            },
            snapshot=snapshot_instance(obj),
        )
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def transition_order_status_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = request.POST.get('orderID')
        target_status = _normalize_order_status(request.POST.get('status'))
        note = request.POST.get('note', '')
        obj = tenant_qs(request, Order).get(pk=int(order_id), isDeleted=False)
        if not (_can_approve_orders(request.user) or _can_edit_order_record(request.user, obj)):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        current_status = _normalize_order_status(obj.status)
        if target_status not in _order_allowed_next_statuses(obj):
            return JsonResponse({'message': 'invalid_transition'}, status=400, safe=False)
        if target_status == Order.Status.DELIVERED and obj.partyID_id:
            otp_code = (request.POST.get('otpCode') or '').strip()
            if not (otp_code.isdigit() and len(otp_code) == 6):
                return JsonResponse({'message': 'otp_required'}, status=400, safe=False)
            payload = get_order_delivery_otp_payload(obj.partyID.tenant_id, obj.partyID_id) or {}
            if not payload or str(payload.get('otp')) != otp_code:
                return JsonResponse({'message': 'otp_invalid'}, status=400, safe=False)
        staff = tenant_qs(request, StaffUser).filter(user_ID_id=request.user.pk, isDeleted=False).only('id').first()
        _set_order_tracking_timestamps(obj, target_status, staff)
        update_fields = ['status', 'lastUpdatedOn']
        if target_status == Order.Status.APPROVED:
            update_fields.extend(['approvedOn', 'approvedBy'])
        elif target_status == Order.Status.PACKED:
            update_fields.append('packedOn')
        elif target_status == Order.Status.OUT_FOR_DELIVERY:
            update_fields.append('outForDeliveryOn')
        elif target_status == Order.Status.DELIVERED:
            obj.deliveryOtpVerified = bool(obj.partyID_id)
            obj.deliveryOtpVerifiedOn = timezone.now() if obj.partyID_id else None
            obj.deliveryOtpVerifiedBy = staff
            update_fields.extend(['deliveredOn', 'deliveryOtpVerified', 'deliveryOtpVerifiedOn', 'deliveryOtpVerifiedBy'])
        elif target_status == Order.Status.CANCELLED:
            update_fields.append('cancelledOn')
        elif target_status == Order.Status.RETURNED:
            update_fields.append('returnedOn')
        obj.save(update_fields=update_fields)
        if target_status == Order.Status.DELIVERED and obj.partyID_id:
            try:
                clear_order_delivery_otp(obj.partyID.tenant_id, obj.partyID_id)
                set_order_delivery_status(
                    obj.partyID.tenant_id,
                    obj.partyID_id,
                    'delivered',
                    meta={
                        'orderID': obj.pk,
                        'orderNumber': obj.orderNumber or str(obj.pk),
                        'verifiedOn': timezone.now().isoformat(),
                    },
                )
            except Exception:
                pass
        _create_order_status_event(request, obj, current_status, target_status, note)
        return JsonResponse({'message': 'success', 'statusLabel': _order_status_label(obj.status)}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
@transaction.atomic
def delete_order_attachment(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        att_id = request.POST.get('userID')
        att = tenant_qs(request, OrderAttachment).select_related('order').get(pk=int(att_id))
        if not _can_edit_order_record(request.user, att.order):
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
        att.isDeleted = True
        att.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception:
        return JsonResponse({'message': 'error'}, safe=False)
class OrdersByAdminListJson(BaseDatatableView):
    # Must match the number of columns returned in prepare_results.
    order_columns = [
        'orderNumber', 'partyID__name', 'buildDate', 'status',
        'approvedBy__name', 'approvedOn',
        'createdBy__name', 'lastUpdatedOn', 'datetime',
        'id', 'id'
    ]
    def get_initial_queryset(self):
        queryset = _orders_list_base_qs(self.request).filter(isDeleted=False)
        if not _can_filter_all_orders(self.request.user):
            queryset = queryset.filter(createdBy__user_ID_id=self.request.user.pk)
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            staffID = self.request.GET.get("staffID")
            status = _normalize_order_status(self.request.GET.get("status"))
            if startDateV and endDateV:
                sDate = datetime.strptime(startDateV, '%d/%m/%Y').date()
                eDate = datetime.strptime(endDateV, '%d/%m/%Y').date()
                queryset = queryset.filter(buildDate__range=[sDate, eDate])
            if status:
                queryset = queryset.filter(status=status)
            if _can_filter_all_orders(self.request.user) and staffID and staffID != 'All':
                queryset = queryset.filter(createdBy_id=int(staffID))
            return queryset
        except Exception:
            return queryset
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(orderNumber__icontains=search)
                | Q(partyID__name__icontains=search)
                | Q(status__icontains=search)
                | Q(remark__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        can_approve = _can_approve_orders(self.request.user)
        can_edit = _can_edit_order(self.request.user)
        for item in qs:
            normalized_status = _normalize_order_status(getattr(item, 'status', None))
            can_confirm = normalized_status == Order.Status.SUBMITTED
            editable_statuses = {Order.Status.DRAFT, Order.Status.SUBMITTED}
            action_parts = []
            if can_approve and can_confirm:
                action_parts.append(
                    '''<button data-inverted="" data-tooltip="Confirm" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="showApproveOrderModal('{}')" class="ui circular purple icon button"><i class="check circle icon"></i></button>'''.format(item.pk)
                )
            # Always provide a detail page.
            action_parts.append(
                '''<a href="/order_detail/{}/" data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular teal icon button"><i class="eye icon"></i></a>'''.format(item.pk)
            )
            # Allow editing for users with edit capability.
            if can_edit and normalized_status in editable_statuses:
                action_parts.append(
                    '''<a href="/edit_order/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular green icon button"><i class="pen icon"></i></a>'''.format(item.pk)
                )
            action_parts.append(
                '''<button data-inverted="" data-tooltip="Download PDF" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="downloadOrderPdf('{}')" class="ui circular blue icon button"><i class="file pdf outline icon"></i></button>'''.format(item.pk)
            )
            if _can_delete_order_record(self.request.user, item):
                action_parts.append(
                    '''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk)
                )
            action = ''.join(action_parts) or '<div class="ui tiny grey label">View Only</div>'
            try:
                party = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('party', '{item.partyID.pk}')" style="cursor: pointer;">{escape(item.partyID.name)}</span>'''
            except:
                party = '-'
            try:
                if item.createdBy:
                    created_by = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.createdBy.pk}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.createdBy.name)}</span>'''
                else:
                    created_by = '-'
            except:
                created_by = '-'
            try:
                if item.approvedBy:
                    approved_by = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('staff', '{item.approvedBy.pk}')" style="cursor: pointer;"><i class="user outline icon" style="margin-right: 4px; opacity: 0.8;"></i>{escape(item.approvedBy.name)}</span>'''
                else:
                    approved_by = '-'
            except:
                approved_by = '-'
            approved_on = item.approvedOn.strftime('%d-%m-%Y %I:%M %p') if getattr(item, 'approvedOn', None) else '-'
            status_label = _order_status_label(item.status)
            att_count = tenant_qs(self.request, OrderAttachment).filter(isDeleted=False, order_id=item.pk).count()
            json_data.append([
                escape(_order_public_id(item)),
                party,
                escape(item.buildDate.strftime('%d-%m-%Y') if item.buildDate else '-'),
                status_label,
                approved_by,
                escape(approved_on),
                created_by,
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(str(att_count)),
                action,
            ])
        return json_data
class OrdersByStaffListJson(BaseDatatableView):
    # Must match the number of columns returned in prepare_results.
    order_columns = [
        'orderNumber', 'partyID__name', 'buildDate', 'status',
        'lastUpdatedOn', 'datetime', 'id', 'id'
    ]
    def get_initial_queryset(self):
        queryset = _orders_list_base_qs(self.request).filter(isDeleted=False)
        if not _can_filter_all_orders(self.request.user):
            queryset = queryset.filter(createdBy__user_ID_id=self.request.user.pk)
        try:
            startDateV = self.request.GET.get("startDate")
            endDateV = self.request.GET.get("endDate")
            status = _normalize_order_status(self.request.GET.get("status"))
            if startDateV and endDateV:
                sDate = datetime.strptime(startDateV, '%d/%m/%Y').date()
                eDate = datetime.strptime(endDateV, '%d/%m/%Y').date()
                queryset = queryset.filter(buildDate__range=[sDate, eDate])
            if status:
                queryset = queryset.filter(status=status)
            return queryset
        except Exception:
            return queryset
    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(orderNumber__icontains=search)
                | Q(partyID__name__icontains=search)
                | Q(status__icontains=search)
                | Q(remark__icontains=search)
            )
        return qs
    def prepare_results(self, qs):
        json_data = []
        can_edit = _can_edit_order(self.request.user)
        for item in qs:
            normalized_status = _normalize_order_status(getattr(item, 'status', None))
            action_parts = []
            action_parts.append(
                '''<a href="/order_detail/{}/" data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular teal icon button"><i class="eye icon"></i></a>'''.format(item.pk)
            )
            # Staff can edit only before approval; the edit page locks after approval anyway.
            if can_edit and normalized_status in {Order.Status.DRAFT, Order.Status.SUBMITTED}:
                action_parts.append(
                    '''<a href="/edit_order/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular green icon button"><i class="pen icon"></i></a>'''.format(item.pk)
                )
            action_parts.append(
                '''<button data-inverted="" data-tooltip="Download PDF" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="downloadOrderPdf('{}')" class="ui circular blue icon button"><i class="file pdf outline icon"></i></button>'''.format(item.pk)
            )
            if _can_delete_order_record(self.request.user, item):
                action_parts.append(
                    '''<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick="delUser('{}')" class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'''.format(item.pk)
                )
            action = ''.join(action_parts) or '<div class="ui tiny grey label">View Only</div>'
            try:
                party = f'''<span class="lookup-detail-link" onclick="showGeneralLookup('party', '{item.partyID.pk}')" style="cursor: pointer;">{escape(item.partyID.name)}</span>'''
            except:
                party = '-'
            status_label = _order_status_label(item.status)
            att_count = tenant_qs(self.request, OrderAttachment).filter(isDeleted=False, order_id=item.pk).count()
            json_data.append([
                escape(_order_public_id(item)),
                party,
                escape(item.buildDate.strftime('%d-%m-%Y') if item.buildDate else '-'),
                status_label,
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p')),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                escape(str(att_count)),
                action,
            ])
        return json_data


def export_orders_list_excel(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    qs = _orders_list_base_qs(request).filter(isDeleted=False)
    if not _can_filter_all_orders(request.user):
        qs = qs.filter(createdBy__user_ID_id=request.user.pk)
        staff_name = 'My Orders'
    else:
        staff_id = request.GET.get('staffID')
        if staff_id and staff_id != 'All':
            qs = qs.filter(createdBy_id=int(staff_id))
            staff = tenant_qs(request, StaffUser).filter(pk=int(staff_id)).first()
            staff_name = staff.name if staff else 'Selected Staff'
        else:
            staff_name = 'All'
    try:
        start_date = datetime.strptime(request.GET.get('startDate'), '%d/%m/%Y').date()
        end_date = datetime.strptime(request.GET.get('endDate'), '%d/%m/%Y').date()
    except Exception:
        start_date = end_date = datetime.today().date()
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    qs = qs.filter(buildDate__range=[start_date, end_date])
    search = (request.GET.get('search') or '').strip()
    if search:
        qs = qs.filter(
            Q(orderNumber__icontains=search) | Q(partyID__name__icontains=search) |
            Q(status__icontains=search) | Q(remark__icontains=search)
        )
    rows = []
    for item in qs.order_by('-datetime'):
        att_count = tenant_qs(request, OrderAttachment).filter(isDeleted=False, order_id=item.pk).count()
        rows.append([
            item.orderNumber or str(item.pk),
            item.partyID.name if item.partyID_id else '-',
            item.buildDate.strftime('%d/%m/%Y') if item.buildDate else '-',
            item.get_status_display(),
            item.approvedBy.name if item.approvedBy_id else '-',
            item.approvedOn.strftime('%d/%m/%Y %I:%M %p') if item.approvedOn else '-',
            item.createdBy.name if item.createdBy_id else '-',
            item.lastUpdatedOn.strftime('%d/%m/%Y %I:%M %p') if item.lastUpdatedOn else '-',
            item.datetime.strftime('%d/%m/%Y %I:%M %p') if item.datetime else '-',
            att_count,
            item.remark or '-',
        ])
    return _xlsx_report_response('orders-list', [{
        'title': 'Orders',
        'report_title': 'Order List Export',
        'report_subtitle': 'Detailed filtered order rows',
        'metadata': [('Tenant', getattr(get_current_tenant(request), 'name', 'zivabooks.in')), ('From', start_date.strftime('%d/%m/%Y')), ('To', end_date.strftime('%d/%m/%Y')), ('Staff', staff_name), ('Search', search or '-')],
        'tables': [{'title': 'Orders', 'headers': ['Order No', 'Party', 'Date', 'Status', 'Approved By', 'Approved On', 'Created By', 'Updated', 'Created', 'Images', 'Remark'], 'rows': rows}],
    }])
def export_orders_list_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    qs = _orders_list_base_qs(request).filter(isDeleted=False)
    if not _can_filter_all_orders(request.user):
        qs = qs.filter(createdBy__user_ID_id=request.user.pk)
        staff_name = 'My Orders'
    else:
        staff_id = request.GET.get('staffID')
        if staff_id and staff_id != 'All':
            qs = qs.filter(createdBy_id=int(staff_id))
            staff = tenant_qs(request, StaffUser).filter(pk=int(staff_id)).first()
            staff_name = staff.name if staff else 'Selected Staff'
        else:
            staff_name = 'All'
    try:
        start_date = datetime.strptime(request.GET.get('startDate'), '%d/%m/%Y').date()
        end_date = datetime.strptime(request.GET.get('endDate'), '%d/%m/%Y').date()
    except Exception:
        start_date = end_date = datetime.today().date()
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    qs = qs.filter(buildDate__range=[start_date, end_date])
    search = (request.GET.get('search') or '').strip()
    if search:
        qs = qs.filter(
            Q(orderNumber__icontains=search) | Q(partyID__name__icontains=search) |
            Q(status__icontains=search) | Q(remark__icontains=search)
        )
    rows = []
    for item in qs.order_by('-datetime'):
        rows.append({
            'order_number': _order_public_id(item),
            'party_name': item.partyID.name if item.partyID_id else '-',
            'build_date': item.buildDate.strftime('%d/%m/%Y') if item.buildDate else '-',
            'status': item.get_status_display(),
            'approved_by': item.approvedBy.name if item.approvedBy_id else '-',
            'approved_on': item.approvedOn.strftime('%d/%m/%Y %I:%M %p') if item.approvedOn else '-',
            'created_by': item.createdBy.name if item.createdBy_id else '-',
            'updated_on': item.lastUpdatedOn.strftime('%d/%m/%Y %I:%M %p') if item.lastUpdatedOn else '-',
            'created_on': item.datetime.strftime('%d/%m/%Y %I:%M %p') if item.datetime else '-',
            'remark': item.remark or '-',
            'images_count': tenant_qs(request, OrderAttachment).filter(isDeleted=False, order_id=item.pk).count(),
        })
    context = {
        'start_date': start_date,
        'end_date': end_date,
        'staff_name': staff_name,
        'search': search or '-',
        'rows': rows,
        'total_orders': len(rows),
    }
    context.update(_pdf_base_context(request, [
        f'From: {start_date.strftime("%d/%m/%Y")}',
        f'To: {end_date.strftime("%d/%m/%Y")}',
        f'Staff: {staff_name}',
    ]))
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename=orders-list.pdf'
    html = render_to_string('home/report/ordersListPDF.html', context)
    HTML(string=html).write_pdf(response, stylesheets=[CSS(string='@page { size: A4 landscape; margin: 0.7cm; }')])
    return response
def generate_order_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    if not _has_any_permission(request.user, 'home.view_order', 'home.can_manage_orders', 'home.can_view_party_credentials'):
        return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    order_id = request.GET.get('id')
    if not order_id:
        return JsonResponse({'message': 'error'}, status=400, safe=False)
    order = get_object_or_404(
        tenant_qs(request, Order).select_related('partyID', 'createdBy', 'approvedBy'),
        pk=int(order_id),
    )
    # Staff can only export their own orders unless admin-like.
    if not _can_filter_all_orders(request.user):
        if getattr(order.createdBy, 'user_ID_id', None) != request.user.pk:
            return JsonResponse({'message': 'forbidden'}, status=403, safe=False)
    attachments = tenant_qs(request, OrderAttachment).filter(isDeleted=False, order=order).order_by('id')
    location = order.location or ('{},{}'.format(order.latitude, order.longitude) if order.latitude and order.longitude else '-')
    context = {
        'order': order,
        'attachments': attachments,
        'location': location,
    }
    context.update(_pdf_base_context(request, [
        f'Order: {order.orderNumber or order.pk}',
        f'Party: {getattr(getattr(order, "partyID", None), "name", "-")}',
        f'Status: {order.get_status_display()}',
    ]))
    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = f"attachment; filename=order-{order.orderNumber or order.pk}.pdf"
    html = render_to_string("home/orders/orderPDF.html", context)
    # base_url allows WeasyPrint to resolve media/static URLs.
    base_url = request.build_absolute_uri('/')
    HTML(string=html, base_url=base_url).write_pdf(response, stylesheets=[CSS(string='@page { size: A4; margin: 1cm; }')])
    return response
def list_order_sale_link_candidates_api(request):
    if request.method != 'GET':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = int(request.GET.get('orderID'))
        order_obj = tenant_qs(request, Order).select_related('partyID').get(pk=order_id, isDeleted=False)
        qs = tenant_qs(request, Sales).filter(isDeleted=False).select_related('partyID', 'createdBy').order_by('-buildDate', '-id')
        linked_elsewhere_ids = set(
            tenant_qs(request, Order)
            .filter(isDeleted=False, linkedSales__isnull=False)
            .exclude(pk=order_obj.pk)
            .values_list('linkedSales_id', flat=True)
        )
        if order_obj.partyID_id:
            qs = qs.filter(partyID_id=order_obj.partyID_id)
        rows = []
        for sale in qs[:40]:
            rows.append({
                'ID': sale.pk,
                'SalesID': _sales_public_id(sale),
                'InvoiceNumber': sale.invoiceNumber or '-',
                'Party': sale.partyID.name if sale.partyID_id else '-',
                'Amount': float(sale.amount or 0.0),
                'BuildDate': sale.buildDate.strftime('%d/%m/%Y') if sale.buildDate else '',
                'CreatedBy': sale.createdBy.name if getattr(sale, 'createdBy', None) else '-',
                'IsLinkedToThisOrder': bool(order_obj.linkedSales_id == sale.pk),
                'IsLinkedElsewhere': bool(sale.pk in linked_elsewhere_ids),
            })
        return JsonResponse({'message': 'success', 'data': rows}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
def link_order_to_sale_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error'}, status=405, safe=False)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401, safe=False)
    try:
        order_id = int(request.POST.get('orderID'))
        sale_id = int(request.POST.get('saleID'))
        order_obj = tenant_qs(request, Order).get(pk=order_id, isDeleted=False)
        sale_obj = tenant_qs(request, Sales).get(pk=sale_id, isDeleted=False)
        existing_order = tenant_qs(request, Order).filter(isDeleted=False, linkedSales=sale_obj).exclude(pk=order_obj.pk).first()
        if existing_order:
            return JsonResponse({'message': 'already_linked', 'error': f'Sale already linked to {existing_order.orderNumber or existing_order.pk}.'}, status=400, safe=False)
        _link_sale_to_order(request, order_obj, sale_obj, note='Existing sale linked to order')
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)

