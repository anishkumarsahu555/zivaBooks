"""
API View endpoints for GST E-Invoicing & E-Way Bill workflows.
"""

import json
import logging
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import permission_required
from django.shortcuts import get_object_or_404
from home.tenancy import get_current_tenant

from home.models import Sales
from home.services.einvoice import (
    build_einvoice_payload,
    generate_irn_for_sale,
    cancel_irn_for_sale
)
from home.services.ewaybill import (
    generate_ewaybill_for_sale,
    cancel_ewaybill_for_sale
)

logger = logging.getLogger(__name__)


def _parse_request_data(request):
    """Helper to parse JSON or POST form body."""
    if request.content_type == 'application/json' and request.body:
        try:
            return json.loads(request.body)
        except Exception:
            return {}
    return request.POST


@require_http_methods(["POST"])
@permission_required('home.can_manage_sales', raise_exception=True)
def generate_einvoice_api(request):
    """API endpoint to generate E-Invoice IRN for a Sales Invoice."""
    data = _parse_request_data(request)
    sale_id = data.get('sale_id') or data.get('id')
    if not sale_id:
        return JsonResponse({'status': 'error', 'message': 'Missing sale_id parameter.'}, status=400)

    tenant = get_current_tenant(request)
    sale = get_object_or_404(Sales, id=sale_id, tenant=tenant, isDeleted=False)

    success, msg = generate_irn_for_sale(sale, user=request.user)
    if success:
        return JsonResponse({
            'status': 'success',
            'message': msg,
            'irn': sale.irn,
            'ack_no': sale.ackNo,
            'ack_date': sale.ackDate.strftime('%Y-%m-%d %H:%M:%S') if sale.ackDate else None,
            'qr_code': sale.signedQrCode,
            'einvoice_status': sale.einvoiceStatus
        })
    else:
        return JsonResponse({
            'status': 'error',
            'message': msg,
            'einvoice_status': sale.einvoiceStatus
        }, status=400)


@require_http_methods(["POST"])
@permission_required('home.can_manage_sales', raise_exception=True)
def cancel_einvoice_api(request):
    """API endpoint to cancel an E-Invoice IRN."""
    data = _parse_request_data(request)
    sale_id = data.get('sale_id') or data.get('id')
    reason = data.get('reason', '1')
    remarks = data.get('remarks', 'Cancelled')

    if not sale_id:
        return JsonResponse({'status': 'error', 'message': 'Missing sale_id parameter.'}, status=400)

    tenant = get_current_tenant(request)
    sale = get_object_or_404(Sales, id=sale_id, tenant=tenant, isDeleted=False)

    success, msg = cancel_irn_for_sale(sale, cancel_reason=reason, cancel_remarks=remarks, user=request.user)
    if success:
        return JsonResponse({
            'status': 'success',
            'message': msg,
            'einvoice_status': sale.einvoiceStatus
        })
    else:
        return JsonResponse({'status': 'error', 'message': msg}, status=400)


@require_http_methods(["GET"])
@permission_required('home.can_manage_sales', raise_exception=True)
def export_einvoice_schema_api(request, sale_id):
    """API endpoint to view/download standard IRP JSON Schema for a Sale."""
    tenant = get_current_tenant(request)
    sale = get_object_or_404(Sales, id=sale_id, tenant=tenant, isDeleted=False)
    payload = build_einvoice_payload(sale)

    response = HttpResponse(json.dumps(payload, indent=2), content_type='application/json')
    filename = f"einvoice_schema_{sale.invoiceNumber or sale.id}.json"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@require_http_methods(["POST"])
@permission_required('home.can_manage_sales', raise_exception=True)
def generate_ewaybill_api(request):
    """API endpoint to generate E-Way Bill for a Sales Invoice."""
    data = _parse_request_data(request)
    sale_id = data.get('sale_id') or data.get('id')
    if not sale_id:
        return JsonResponse({'status': 'error', 'message': 'Missing sale_id parameter.'}, status=400)

    tenant = get_current_tenant(request)
    sale = get_object_or_404(Sales, id=sale_id, tenant=tenant, isDeleted=False)

    transport_details = {
        'transportMode': data.get('transportMode', '1'),
        'transporterId': data.get('transporterId', ''),
        'transporterName': data.get('transporterName', ''),
        'vehicleNo': data.get('vehicleNo', ''),
        'vehicleType': data.get('vehicleType', 'R'),
        'distanceKm': data.get('distanceKm', 0)
    }

    success, msg = generate_ewaybill_for_sale(sale, transport_details=transport_details, user=request.user)
    if success:
        return JsonResponse({
            'status': 'success',
            'message': msg,
            'eway_bill_no': sale.ewayBillNo,
            'eway_bill_date': sale.ewayBillDate.strftime('%Y-%m-%d %H:%M:%S') if sale.ewayBillDate else None,
            'valid_upto': sale.validUpto.strftime('%Y-%m-%d %H:%M:%S') if sale.validUpto else None,
            'eway_bill_status': sale.ewayBillStatus
        })
    else:
        return JsonResponse({
            'status': 'error',
            'message': msg,
            'eway_bill_status': sale.ewayBillStatus
        }, status=400)


@require_http_methods(["POST"])
@permission_required('home.can_manage_sales', raise_exception=True)
def cancel_ewaybill_api(request):
    """API endpoint to cancel an E-Way Bill."""
    data = _parse_request_data(request)
    sale_id = data.get('sale_id') or data.get('id')
    reason = data.get('reason', '1')
    remarks = data.get('remarks', 'Cancelled')

    if not sale_id:
        return JsonResponse({'status': 'error', 'message': 'Missing sale_id parameter.'}, status=400)

    tenant = get_current_tenant(request)
    sale = get_object_or_404(Sales, id=sale_id, tenant=tenant, isDeleted=False)

    success, msg = cancel_ewaybill_for_sale(sale, cancel_reason=reason, cancel_remarks=remarks, user=request.user)
    if success:
        return JsonResponse({
            'status': 'success',
            'message': msg,
            'eway_bill_status': sale.ewayBillStatus
        })
    else:
        return JsonResponse({'status': 'error', 'message': msg}, status=400)
