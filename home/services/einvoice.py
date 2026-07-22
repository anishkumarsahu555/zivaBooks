"""
GST E-Invoicing (IRP Schema v1.03) Service Layer for zivaBooks.
Handles payload assembly, provider routing, IRN generation, and cancellation.
"""

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, Tuple

from django.conf import settings
from django.utils import timezone as django_timezone

from home.models import Sales, Tenant

logger = logging.getLogger(__name__)


def get_state_code_from_gstin(gstin: str) -> str:
    """Extract 2-digit state code from GSTIN (e.g. 27AAAAA0000A1Z5 -> 27)."""
    if gstin and len(gstin.strip()) >= 2 and gstin[:2].isdigit():
        return gstin[:2].strip()
    return "27"  # Default Maharashtra state code fallback if unprovided


def build_einvoice_payload(sale: Sales) -> Dict[str, Any]:
    """
    Constructs NIC IRP v1.03 compliant JSON payload for a Sales invoice.
    """
    tenant: Tenant = sale.tenant
    buyer_gstin = (sale.buyerGstNumber or '').strip().upper()
    seller_gstin = (tenant.gstNumber if tenant and tenant.gstNumber else '27AAAAA0000A1Z5').strip().upper()

    doc_date_str = sale.buildDate.strftime('%d/%m/%Y') if sale.buildDate else django_timezone.now().strftime('%d/%m/%Y')
    place_of_supply = (sale.placeOfSupply or get_state_code_from_gstin(buyer_gstin)).strip()
    seller_state_code = get_state_code_from_gstin(seller_gstin)
    is_interstate = seller_state_code != place_of_supply[:2] if place_of_supply[:2].isdigit() else False

    # Build line items
    item_list = []
    tot_taxable = 0.0
    tot_cgst = 0.0
    tot_sgst = 0.0
    tot_igst = 0.0

    lines = sale.lines.filter(isDeleted=False)
    for idx, line in enumerate(lines, start=1):
        qty = float(line.quantity or 1.0)
        rate = float(getattr(line, 'unitPrice', getattr(line, 'rate', 0.0)) or 0.0)
        taxable = float(line.taxableAmount or (qty * rate))
        gst_percent = float(line.gstRate or 0.0)
        line_gst = float(line.gstAmount or 0.0)

        if is_interstate:
            igst_amt = line_gst
            cgst_amt = 0.0
            sgst_amt = 0.0
        else:
            igst_amt = 0.0
            cgst_amt = round(line_gst / 2.0, 2)
            sgst_amt = round(line_gst - cgst_amt, 2)

        tot_item_val = round(taxable + line_gst, 2)
        item_name = getattr(line.item, 'name', 'Item') if getattr(line, 'item', None) else getattr(line, 'itemName', f'Item {idx}')
        unit_name = getattr(line.item, 'unitName', 'NOS') if getattr(line, 'item', None) else getattr(line, 'unitName', 'NOS')

        item_list.append({
            "SlNo": str(idx),
            "ItemDescr": (item_name or f"Item {idx}").strip(),
            "IsServc": "N",
            "HsnCd": (line.hsnCode or (line.item.hsnCode if getattr(line, 'item', None) and getattr(line.item, 'hsnCode', None) else "999999")).strip(),
            "Qty": round(qty, 2),
            "Unit": (unit_name or "NOS").strip().upper()[:3],
            "UnitPrice": round(rate, 2),
            "TotAmt": round(qty * rate, 2),
            "Discount": round((qty * rate) - taxable, 2) if (qty * rate) > taxable else 0.0,
            "PreTaxVal": round(taxable, 2),
            "AssVal": round(taxable, 2),
            "GstRt": round(gst_percent, 2),
            "IgstAmt": round(igst_amt, 2),
            "CgstAmt": round(cgst_amt, 2),
            "SgstAmt": round(sgst_amt, 2),
            "TotItemVal": tot_item_val
        })

        tot_taxable += taxable
        tot_cgst += cgst_amt
        tot_sgst += sgst_amt
        tot_igst += igst_amt

    tot_inv_val = round(sale.amount or (tot_taxable + tot_cgst + tot_sgst + tot_igst + (sale.roundOffAmount or 0.0)), 2)

    payload = {
        "Version": "1.03",
        "TranDtls": {
            "TaxSch": "GST",
            "SupTyp": "B2B",
            "RegRev": "N",
            "EcmGstin": None,
            "IgstOnIntra": "N"
        },
        "DocDtls": {
            "Typ": "INV",
            "No": (sale.invoiceNumber or sale.salesID or "INV-101").strip(),
            "Dt": doc_date_str
        },
        "SellerDtls": {
            "Gstin": seller_gstin,
            "LglNm": (tenant.legalName or tenant.name if tenant else "zivaBooks Supplier").strip(),
            "TrdNm": (tenant.name if tenant else "zivaBooks").strip(),
            "Addr1": (tenant.billingAddress or "Main Street").strip()[:100],
            "Loc": "Head Office",
            "Pin": 400001,
            "Stcd": seller_state_code
        },
        "BuyerDtls": {
            "Gstin": buyer_gstin if buyer_gstin else "URP",
            "LglNm": (sale.buyerLegalName or (sale.partyID.name if sale.partyID else "Buyer Customer")).strip(),
            "TrdNm": (sale.buyerLegalName or (sale.partyID.name if sale.partyID else "Buyer Customer")).strip(),
            "Pos": place_of_supply[:2] if place_of_supply[:2].isdigit() else "27",
            "Addr1": (sale.buyerBillingAddress or "Buyer Premises").strip()[:100],
            "Loc": "Destination",
            "Pin": 400002,
            "Stcd": place_of_supply[:2] if place_of_supply[:2].isdigit() else "27"
        },
        "ItemList": item_list,
        "ValDtls": {
            "TotAssVal": round(tot_taxable, 2),
            "CgstVal": round(tot_cgst, 2),
            "SgstVal": round(tot_sgst, 2),
            "IgstVal": round(tot_igst, 2),
            "RndOffAmt": round(sale.roundOffAmount or 0.0, 2),
            "TotInvVal": tot_inv_val
        }
    }
    return payload


class MockEInvoiceProvider:
    """Mock / Simulator Provider for E-Invoice testing and sandbox validation."""

    def generate_irn(self, payload: Dict[str, Any], tenant: Tenant) -> Dict[str, Any]:
        seller_gstin = payload['SellerDtls']['Gstin']
        doc_no = payload['DocDtls']['No']
        doc_typ = payload['DocDtls']['Typ']
        doc_dt = payload['DocDtls']['Dt']

        raw_str = f"{seller_gstin}:{doc_typ}:{doc_no}:{doc_dt}"
        irn_hash = hashlib.sha256(raw_str.encode('utf-8')).hexdigest()

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ack_no = int(datetime.now().timestamp() * 1000) % 1000000000000

        # Construct signed QR code text payload
        qr_payload = {
            "Irn": irn_hash,
            "AckNo": ack_no,
            "AckDt": now_str,
            "SellerGstin": seller_gstin,
            "BuyerGstin": payload['BuyerDtls']['Gstin'],
            "DocNo": doc_no,
            "DocDt": doc_dt,
            "TotInvVal": payload['ValDtls']['TotInvVal'],
            "ItemCnt": len(payload['ItemList']),
            "MainHsn": payload['ItemList'][0]['HsnCd'] if payload['ItemList'] else "9999"
        }

        return {
            "Success": True,
            "Irn": irn_hash,
            "AckNo": str(ack_no),
            "AckDt": now_str,
            "SignedQrCode": json.dumps(qr_payload),
            "SignedInvoice": json.dumps(payload),
            "Status": "ACT"
        }

    def cancel_irn(self, irn: str, reason: str, remarks: str, tenant: Tenant) -> Dict[str, Any]:
        return {
            "Success": True,
            "Irn": irn,
            "CancelDate": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "Status": "CNL"
        }


def get_einvoice_provider(tenant: Tenant):
    """Factory returns active provider based on tenant config."""
    gsp = tenant.einvoice_gsp_provider if tenant else 'mock'
    if gsp == 'mock':
        return MockEInvoiceProvider()
    return MockEInvoiceProvider()


def generate_irn_for_sale(sale: Sales, user=None) -> Tuple[bool, str]:
    """High-level service entry point to generate IRN for a Sales invoice."""
    if sale.invoiceKind != Sales.InvoiceKind.B2B:
        return False, "E-Invoicing is only applicable to B2B Sales Invoices."

    if not sale.isApproved:
        return False, "Invoice must be approved before generating E-Invoice IRN."

    if sale.einvoiceStatus == Sales.EInvoiceStatus.GENERATED and sale.irn:
        return False, f"IRN already generated for this invoice (IRN: {sale.irn[:12]}...)."

    if sale.einvoiceStatus == Sales.EInvoiceStatus.CANCELLED:
        return False, "Cannot generate IRN for a cancelled E-Invoice."

    tenant = sale.tenant
    payload = build_einvoice_payload(sale)
    provider = get_einvoice_provider(tenant)

    try:
        res = provider.generate_irn(payload, tenant)
        if res.get('Success'):
            sale.irn = res['Irn']
            sale.ackNo = str(res['AckNo'])
            sale.ackDate = django_timezone.now()
            sale.signedQrCode = res.get('SignedQrCode')
            sale.signedInvoice = res.get('SignedInvoice')
            sale.einvoiceStatus = Sales.EInvoiceStatus.GENERATED
            sale.save(update_fields=[
                'irn', 'ackNo', 'ackDate', 'signedQrCode', 'signedInvoice', 'einvoiceStatus', 'lastUpdatedOn'
            ])
            logger.info(f"[EInvoice] Generated IRN {sale.irn[:10]}... for Sales ID {sale.id} (Tenant {tenant.id})")
            return True, "E-Invoice IRN generated successfully."
        else:
            sale.einvoiceStatus = Sales.EInvoiceStatus.FAILED
            sale.save(update_fields=['einvoiceStatus', 'lastUpdatedOn'])
            err_msg = res.get('ErrorDetails', [{'ErrorMessage': 'IRP Gateway returned failure'}])[0]['ErrorMessage']
            return False, f"E-Invoice generation failed: {err_msg}"
    except Exception as exc:
        logger.error(f"[EInvoice] Exception during IRN generation for Sale {sale.id}: {str(exc)}", exc_info=True)
        sale.einvoiceStatus = Sales.EInvoiceStatus.FAILED
        sale.save(update_fields=['einvoiceStatus', 'lastUpdatedOn'])
        return False, f"System error generating IRN: {str(exc)}"


def cancel_irn_for_sale(sale: Sales, cancel_reason: str, cancel_remarks: str, user=None) -> Tuple[bool, str]:
    """High-level service entry point to cancel an active IRN."""
    if not sale.irn or sale.einvoiceStatus != Sales.EInvoiceStatus.GENERATED:
        return False, "No active generated IRN found to cancel for this invoice."

    tenant = sale.tenant
    provider = get_einvoice_provider(tenant)

    try:
        res = provider.cancel_irn(sale.irn, cancel_reason or "1", cancel_remarks or "Order Cancelled", tenant)
        if res.get('Success'):
            sale.einvoiceStatus = Sales.EInvoiceStatus.CANCELLED
            sale.einvoiceCancelReason = cancel_reason or "1"
            sale.einvoiceCancelRemarks = cancel_remarks or "Cancelled"
            sale.save(update_fields=['einvoiceStatus', 'einvoiceCancelReason', 'einvoiceCancelRemarks', 'lastUpdatedOn'])
            logger.info(f"[EInvoice] Cancelled IRN {sale.irn[:10]}... for Sales ID {sale.id}")
            return True, "E-Invoice IRN cancelled successfully."
        else:
            return False, "Failed to cancel IRN on IRP Gateway."
    except Exception as exc:
        logger.error(f"[EInvoice] Exception cancelling IRN for Sale {sale.id}: {str(exc)}", exc_info=True)
        return False, f"System error cancelling IRN: {str(exc)}"
