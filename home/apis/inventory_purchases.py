from datetime import datetime

from django.db import transaction
from django.http import JsonResponse

from home.audit import snapshot_instance
from home.inventory import rebuild_inventory_stock, validate_non_negative_inventory
from home.models import PurchaseInvoice, PurchaseInvoiceRevision, StaffUser, TenantSequence
from home.services.collections import has_any_permission
from home.services.inventory_lines import assert_non_gst_enabled, request_line_items_payload
from home.services.inventory_mutations import (
    normalize_purchase_lines_for_treatment,
    purchase_totals_from_lines,
    sync_purchase_lines,
)
from home.services.sales import parse_sales_party
from home.services.sequences import next_tenant_sequence_value
from home.tenancy import assign_tenant, tenant_qs


def _purchase_public_id(obj):
    return getattr(obj, 'purchaseID', None) or f'PUR{str(getattr(obj, "pk", 0)).zfill(6)}'


def _can_manage_purchase_records(user):
    return has_any_permission(
        user,
        'home.add_purchaseinvoice',
        'home.change_purchaseinvoice',
        'home.delete_purchaseinvoice',
        'home.can_view_party_credentials',
    )


def _normalize_purchase_gst_treatment(request, raw_treatment: str | None) -> str:
    treatment = str(raw_treatment or PurchaseInvoice.GstTreatment.GST).strip()
    valid = {choice[0] for choice in PurchaseInvoice.GstTreatment.choices}
    if treatment not in valid:
        return PurchaseInvoice.GstTreatment.GST
    if treatment in {PurchaseInvoice.GstTreatment.NON_GST, PurchaseInvoice.GstTreatment.NON_GST_CLAIMABLE}:
        assert_non_gst_enabled(request, label='Non-GST purchase flow')
    return treatment


def _normalize_purchase_claim_status(treatment: str, raw_claim_status: str | None) -> str:
    status = str(raw_claim_status or PurchaseInvoice.ClaimStatus.NOT_APPLICABLE).strip()
    valid = {choice[0] for choice in PurchaseInvoice.ClaimStatus.choices}
    if status not in valid:
        status = PurchaseInvoice.ClaimStatus.NOT_APPLICABLE
    if treatment == PurchaseInvoice.GstTreatment.GST:
        return PurchaseInvoice.ClaimStatus.NOT_APPLICABLE
    return status


def _staff_for_request(request):
    return tenant_qs(request, StaffUser).get(user_ID_id=request.user.pk)


def list_purchase_invoices_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_purchase_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    rows = tenant_qs(request, PurchaseInvoice).filter(isDeleted=False).select_related('partyID', 'createdBy').order_by('-buildDate', '-id')[:100]
    payload = []
    for purchase in rows:
        payload.append({
            'ID': purchase.pk,
            'PurchaseID': _purchase_public_id(purchase),
            'InvoiceNumber': purchase.invoiceNumber,
            'InvoiceDate': purchase.buildDate.strftime('%d/%m/%Y') if purchase.buildDate else '',
            'Party': purchase.partyID.name if purchase.partyID_id else '-',
            'GSTTreatment': purchase.gstTreatment,
            'ClaimStatus': purchase.claimStatus,
            'TotalAmount': float(purchase.totalAmount or 0.0),
            'LineCount': purchase.lines.filter(isDeleted=False).count(),
            'RevisionCount': purchase.revisions.filter(isDeleted=False).count(),
        })
    return JsonResponse({'message': 'success', 'data': payload}, safe=False)


def get_purchase_invoice_detail_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_purchase_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        purchase = tenant_qs(request, PurchaseInvoice).select_related('partyID', 'createdBy').get(pk=int(request.GET.get('purchaseId')), isDeleted=False)
        lines = [{
            'itemId': line.item_id,
            'itemName': line.item.name,
            'batchId': line.batch_id,
            'batchNumber': line.batch.batchNumber if line.batch_id else '',
            'manufactureDate': line.batch.manufactureDate.strftime('%d/%m/%Y') if line.batch_id and line.batch.manufactureDate else '',
            'expiryDate': line.batch.expiryDate.strftime('%d/%m/%Y') if line.batch_id and line.batch.expiryDate else '',
            'batchMrp': float(line.batch.mrp or 0.0) if line.batch_id else 0.0,
            'stockBucket': line.stockBucket,
            'quantity': float(line.quantity or 0.0),
            'unitCost': float(line.unitCost or 0.0),
            'taxableAmount': float(line.taxableAmount or 0.0),
            'gstRate': float(line.gstRate or 0.0),
            'gstAmount': float(line.gstAmount or 0.0),
            'lineTotal': float(line.lineTotal or 0.0),
            'remark': line.remark or '',
        } for line in purchase.lines.filter(isDeleted=False).select_related('item', 'batch').order_by('id')]
        revisions = [{
            'changedOn': revision.datetime.strftime('%d/%m/%Y %I:%M %p') if revision.datetime else '',
            'previousGstTreatment': revision.previousGstTreatment or '',
            'newGstTreatment': revision.newGstTreatment,
            'previousClaimStatus': revision.previousClaimStatus or '',
            'newClaimStatus': revision.newClaimStatus,
            'reason': revision.reason or '',
            'changedBy': revision.changedBy.name if revision.changedBy_id else '-',
        } for revision in purchase.revisions.filter(isDeleted=False).select_related('changedBy').order_by('-datetime')]
        return JsonResponse({'message': 'success', 'data': {
            'ID': purchase.pk,
            'PurchaseID': _purchase_public_id(purchase),
            'PartyID': purchase.partyID_id,
            'InvoiceNumber': purchase.invoiceNumber,
            'InvoiceDate': purchase.buildDate.strftime('%d/%m/%Y') if purchase.buildDate else '',
            'GSTTreatment': purchase.gstTreatment,
            'ClaimStatus': purchase.claimStatus,
            'Remark': purchase.remark or '',
            'Lines': lines,
            'Revisions': revisions,
        }}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def add_purchase_invoice_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_purchase_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        lines_payload = request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one purchase line is required.')
        purchase = PurchaseInvoice()
        assign_tenant(purchase, request)
        purchase.partyID = parse_sales_party(request, request.POST.get('party'))
        purchase.invoiceNumber = (request.POST.get('invoiceNumber') or '').strip()
        if not purchase.invoiceNumber:
            raise ValueError('Purchase invoice number is required.')
        purchase.buildDate = datetime.strptime(request.POST.get('invoiceDate'), '%d/%m/%Y').date()
        purchase.gstTreatment = _normalize_purchase_gst_treatment(request, request.POST.get('gstTreatment'))
        lines_payload = normalize_purchase_lines_for_treatment(purchase.gstTreatment, lines_payload)
        purchase.claimStatus = _normalize_purchase_claim_status(purchase.gstTreatment, request.POST.get('claimStatus'))
        purchase.remark = request.POST.get('remark')
        purchase.createdBy = _staff_for_request(request)
        purchase.subtotalAmount, purchase.gstAmount, purchase.totalAmount = purchase_totals_from_lines(lines_payload)
        purchase.save()
        purchase.purchaseID = next_tenant_sequence_value(request, TenantSequence.Scope.PURCHASE)
        purchase.save(update_fields=['purchaseID', 'lastUpdatedOn'])
        sync_purchase_lines(request, purchase, lines_payload)
        return JsonResponse({'message': 'success', 'purchaseId': purchase.pk}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def update_purchase_invoice_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_purchase_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        purchase = tenant_qs(request, PurchaseInvoice).get(pk=int(request.POST.get('purchaseId')))
        previous_treatment = purchase.gstTreatment
        previous_claim_status = purchase.claimStatus
        previous_snapshot = snapshot_instance(purchase)
        lines_payload = request_line_items_payload(request)
        if not lines_payload:
            raise ValueError('At least one purchase line is required.')
        purchase.partyID = parse_sales_party(request, request.POST.get('party'))
        purchase.invoiceNumber = (request.POST.get('invoiceNumber') or '').strip()
        purchase.buildDate = datetime.strptime(request.POST.get('invoiceDate'), '%d/%m/%Y').date()
        purchase.gstTreatment = _normalize_purchase_gst_treatment(request, request.POST.get('gstTreatment'))
        lines_payload = normalize_purchase_lines_for_treatment(purchase.gstTreatment, lines_payload)
        purchase.claimStatus = _normalize_purchase_claim_status(purchase.gstTreatment, request.POST.get('claimStatus'))
        purchase.remark = request.POST.get('remark')
        purchase.subtotalAmount, purchase.gstAmount, purchase.totalAmount = purchase_totals_from_lines(lines_payload)
        purchase.save()
        if previous_treatment != purchase.gstTreatment or previous_claim_status != purchase.claimStatus:
            PurchaseInvoiceRevision.objects.create(
                tenant=purchase.tenant,
                purchaseInvoice=purchase,
                previousGstTreatment=previous_treatment,
                newGstTreatment=purchase.gstTreatment,
                previousClaimStatus=previous_claim_status,
                newClaimStatus=purchase.claimStatus,
                reason=(request.POST.get('revisionReason') or '')[:500],
                snapshot=previous_snapshot,
                changedBy=_staff_for_request(request),
            )
        sync_purchase_lines(request, purchase, lines_payload)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)


@transaction.atomic
def delete_purchase_invoice_api(request):
    if request.method != 'POST':
        return JsonResponse({'message': 'error', 'error': 'Invalid request method.'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'message': 'unauthorized'}, status=401)
    if not _can_manage_purchase_records(request.user):
        return JsonResponse({'message': 'forbidden'}, status=403)
    try:
        purchase = tenant_qs(request, PurchaseInvoice).get(pk=int(request.POST.get('purchaseId')))
        item_ids = list(purchase.lines.filter(isDeleted=False).values_list('item_id', flat=True))
        purchase.isDeleted = True
        purchase.save(update_fields=['isDeleted', 'lastUpdatedOn'])
        rebuild_inventory_stock(purchase.tenant, item_ids)
        validate_non_negative_inventory(purchase.tenant, item_ids)
        return JsonResponse({'message': 'success'}, safe=False)
    except Exception as exc:
        return JsonResponse({'message': 'error', 'error': str(exc)}, safe=False)
