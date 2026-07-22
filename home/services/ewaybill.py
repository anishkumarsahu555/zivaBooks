"""
GST E-Way Bill (EWB) Service Layer for zivaBooks.
Handles transport details validation, EWB payload generation, and cancellation.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple

from django.utils import timezone as django_timezone

from home.models import Sales, Tenant
from home.services.einvoice import get_state_code_from_gstin

logger = logging.getLogger(__name__)


def build_ewaybill_payload(sale: Sales, transport_details: Dict[str, Any]) -> Dict[str, Any]:
    """Constructs GST E-Way Bill API Payload for a Sales Invoice."""
    tenant: Tenant = sale.tenant
    buyer_gstin = (sale.buyerGstNumber or '').strip().upper()
    seller_gstin = (tenant.gstNumber if tenant and tenant.gstNumber else '27AAAAA0000A1Z5').strip().upper()

    doc_date_str = sale.buildDate.strftime('%d/%m/%Y') if sale.buildDate else django_timezone.now().strftime('%d/%m/%Y')
    place_of_supply = (sale.placeOfSupply or get_state_code_from_gstin(buyer_gstin)).strip()
    seller_state = get_state_code_from_gstin(seller_gstin)

    mode = str(transport_details.get('transportMode', sale.transportMode or '1'))
    distance = int(transport_details.get('distanceKm', sale.distanceKm or 50))
    transporter_id = str(transport_details.get('transporterId', sale.transporterId or '')).strip()
    transporter_name = str(transport_details.get('transporterName', sale.transporterName or '')).strip()
    vehicle_no = str(transport_details.get('vehicleNo', sale.vehicleNo or '')).strip()
    vehicle_type = str(transport_details.get('vehicleType', sale.vehicleType or 'R')).strip()

    payload = {
        "supplyType": "O",
        "subSupplyType": "1",
        "docType": "INV",
        "docNo": (sale.invoiceNumber or sale.salesID or "INV-101").strip(),
        "docDate": doc_date_str,
        "fromGstin": seller_gstin,
        "fromTrdName": (tenant.name if tenant else "zivaBooks").strip(),
        "fromAddr1": (tenant.billingAddress or "Main Street").strip()[:100],
        "fromPlace": "Head Office",
        "fromPincode": 400001,
        "actFromStateCode": int(seller_state) if seller_state.isdigit() else 27,
        "fromStateCode": int(seller_state) if seller_state.isdigit() else 27,
        "toGstin": buyer_gstin if buyer_gstin else "URP",
        "toTrdName": (sale.buyerLegalName or (sale.partyID.name if sale.partyID else "Buyer")).strip(),
        "toAddr1": (sale.buyerBillingAddress or "Buyer Premises").strip()[:100],
        "toPlace": "Destination",
        "toPincode": 400002,
        "actToStateCode": int(place_of_supply[:2]) if place_of_supply[:2].isdigit() else 27,
        "toStateCode": int(place_of_supply[:2]) if place_of_supply[:2].isdigit() else 27,
        "totalValue": round(sale.taxableAmount or sale.amount, 2),
        "cgstValue": round(sale.gstAmount / 2.0, 2) if sale.placeOfSupply == seller_state else 0.0,
        "sgstValue": round(sale.gstAmount / 2.0, 2) if sale.placeOfSupply == seller_state else 0.0,
        "igstValue": round(sale.gstAmount, 2) if sale.placeOfSupply != seller_state else 0.0,
        "totInvValue": round(sale.amount, 2),
        "transMode": mode,
        "transDistance": str(distance),
        "transporterId": transporter_id,
        "transporterName": transporter_name,
        "vehicleNo": vehicle_no,
        "vehicleType": vehicle_type
    }
    return payload


class MockEWayBillProvider:
    """Mock / Simulator Provider for E-Way Bill testing."""

    def generate_ewaybill(self, payload: Dict[str, Any], tenant: Tenant) -> Dict[str, Any]:
        now = datetime.now()
        ewb_no = f"1410{int(now.timestamp())}"[:12]
        valid_until = now + timedelta(days=2)

        return {
            "Success": True,
            "ewayBillNo": ewb_no,
            "ewayBillDate": now.strftime('%Y-%m-%d %H:%M:%S'),
            "validUpto": valid_until.strftime('%Y-%m-%d %H:%M:%S'),
            "Status": "GEN"
        }

    def cancel_ewaybill(self, ewb_no: str, reason: str, remarks: str, tenant: Tenant) -> Dict[str, Any]:
        return {
            "Success": True,
            "ewayBillNo": ewb_no,
            "cancelDate": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "Status": "CNL"
        }


def generate_ewaybill_for_sale(sale: Sales, transport_details: Dict[str, Any], user=None) -> Tuple[bool, str]:
    """Generates E-Way Bill for a Sales Invoice."""
    if not sale.isApproved:
        return False, "Invoice must be approved before generating an E-Way Bill."

    if sale.ewayBillStatus == Sales.EWayBillStatus.GENERATED and sale.ewayBillNo:
        return False, f"E-Way Bill already generated for this invoice (EWB: {sale.ewayBillNo})."

    # Update transport fields on sale
    if 'transportMode' in transport_details:
        sale.transportMode = transport_details['transportMode']
    if 'transporterId' in transport_details:
        sale.transporterId = transport_details['transporterId']
    if 'transporterName' in transport_details:
        sale.transporterName = transport_details['transporterName']
    if 'vehicleNo' in transport_details:
        sale.vehicleNo = transport_details['vehicleNo']
    if 'vehicleType' in transport_details:
        sale.vehicleType = transport_details['vehicleType']
    if 'distanceKm' in transport_details:
        sale.distanceKm = int(transport_details['distanceKm'] or 0)

    tenant = sale.tenant
    payload = build_ewaybill_payload(sale, transport_details)
    provider = MockEWayBillProvider()

    try:
        res = provider.generate_ewaybill(payload, tenant)
        if res.get('Success'):
            sale.ewayBillNo = str(res['ewayBillNo'])
            sale.ewayBillDate = django_timezone.now()
            sale.validUpto = django_timezone.now() + timedelta(days=2)
            sale.ewayBillStatus = Sales.EWayBillStatus.GENERATED
            sale.save(update_fields=[
                'transportMode', 'transporterId', 'transporterName', 'vehicleNo', 'vehicleType',
                'distanceKm', 'ewayBillNo', 'ewayBillDate', 'validUpto', 'ewayBillStatus', 'lastUpdatedOn'
            ])
            logger.info(f"[EWayBill] Generated EWB {sale.ewayBillNo} for Sales ID {sale.id}")
            return True, f"E-Way Bill {sale.ewayBillNo} generated successfully."
        else:
            sale.ewayBillStatus = Sales.EWayBillStatus.PENDING
            sale.save(update_fields=['ewayBillStatus', 'lastUpdatedOn'])
            return False, "E-Way Bill generation failed on portal."
    except Exception as exc:
        logger.error(f"[EWayBill] Exception generating EWB for Sale {sale.id}: {str(exc)}", exc_info=True)
        return False, f"System error generating E-Way Bill: {str(exc)}"


def cancel_ewaybill_for_sale(sale: Sales, cancel_reason: str, cancel_remarks: str, user=None) -> Tuple[bool, str]:
    """Cancels an active E-Way Bill for a sale."""
    if not sale.ewayBillNo or sale.ewayBillStatus != Sales.EWayBillStatus.GENERATED:
        return False, "No active generated E-Way Bill found to cancel."

    provider = MockEWayBillProvider()
    try:
        res = provider.cancel_ewaybill(sale.ewayBillNo, cancel_reason or "1", cancel_remarks or "Order Cancelled", sale.tenant)
        if res.get('Success'):
            sale.ewayBillStatus = Sales.EWayBillStatus.CANCELLED
            sale.save(update_fields=['ewayBillStatus', 'lastUpdatedOn'])
            logger.info(f"[EWayBill] Cancelled EWB {sale.ewayBillNo} for Sales ID {sale.id}")
            return True, "E-Way Bill cancelled successfully."
        else:
            return False, "Failed to cancel E-Way Bill on portal."
    except Exception as exc:
        logger.error(f"[EWayBill] Exception cancelling EWB for Sale {sale.id}: {str(exc)}", exc_info=True)
        return False, f"System error cancelling E-Way Bill: {str(exc)}"
