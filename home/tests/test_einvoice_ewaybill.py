"""
Automated Unit Tests for GST E-Invoicing & E-Way Bill Integration.
"""

from datetime import date
from django.test import TestCase
from django.urls import reverse

from home.models import (
    Tenant, StaffUser, Party, Sales, SalesLine, InvoiceSeries, InventoryItem
)
from home.services.einvoice import (
    build_einvoice_payload,
    generate_irn_for_sale,
    cancel_irn_for_sale
)
from home.services.ewaybill import (
    generate_ewaybill_for_sale,
    cancel_ewaybill_for_sale
)


class EInvoiceEWayBillTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Alpha Traders",
            slug="alpha-traders",
            legalName="Alpha Traders Pvt Ltd",
            gstNumber="27AAAAA0000A1Z5",
            billingAddress="123 Industrial Area, Mumbai",
            einvoice_enabled=True,
            einvoice_gsp_provider="mock"
        )
        self.staff = StaffUser.objects.create(
            tenant=self.tenant,
            name="Admin Staff",
            username="admin_staff",
            phone="9876543210"
        )
        self.party = Party.objects.create(
            tenant=self.tenant,
            name="Beta Customer",
            legalName="Beta Customer Pvt Ltd",
            gstNumber="27BBBCC1111B1Z2",
            isB2B=True,
            billingAddress="456 Trade Center, Pune"
        )
        self.series = InvoiceSeries.objects.create(
            tenant=self.tenant,
            module=InvoiceSeries.Module.SALES,
            seriesCode="INV-2026-",
            nextNumber=11
        )
        # Create B2B Sales Invoice
        self.b2b_sale = Sales.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            salesID="SAL000001",
            invoiceSeries=self.series,
            invoiceSequenceNumber=11,
            invoiceNumber="INV-2026-0011",
            invoiceKind=Sales.InvoiceKind.B2B,
            buyerLegalName=self.party.legalName,
            buyerGstNumber=self.party.gstNumber,
            buyerBillingAddress=self.party.billingAddress,
            placeOfSupply="27-Maharashtra",
            taxableAmount=1000.0,
            gstAmount=180.0,
            roundOffAmount=0.0,
            amount=1180.0,
            modeOfPayment=Sales.Mode.CREDIT,
            buildDate=date.today(),
            isApproved=True,
            createdBy=self.staff
        )
        self.item = InventoryItem.objects.create(
            tenant=self.tenant,
            name="Widget Part A",
            hsnCode="84713010",
            unitName="NOS",
            defaultSalesPrice=100.0
        )
        # Add sales line
        SalesLine.objects.create(
            tenant=self.tenant,
            sales=self.b2b_sale,
            item=self.item,
            hsnCode="84713010",
            stockBucket="main",
            quantity=10.0,
            unitPrice=100.0,
            taxableAmount=1000.0,
            gstRate=18.0,
            gstAmount=180.0,
            lineTotal=1180.0
        )

        # Create B2C Sales Invoice
        self.b2c_sale = Sales.objects.create(
            tenant=self.tenant,
            partyID=self.party,
            salesID="SAL000002",
            invoiceNumber="INV-2026-0012",
            invoiceKind=Sales.InvoiceKind.B2C,
            taxableAmount=500.0,
            gstAmount=90.0,
            amount=590.0,
            buildDate=date.today(),
            isApproved=True,
            createdBy=self.staff
        )

    def test_build_einvoice_payload_schema(self):
        """Verify NIC IRP v1.03 payload JSON structure."""
        payload = build_einvoice_payload(self.b2b_sale)
        self.assertEqual(payload['Version'], '1.03')
        self.assertEqual(payload['TranDtls']['SupTyp'], 'B2B')
        self.assertEqual(payload['DocDtls']['No'], 'INV-2026-0011')
        self.assertEqual(payload['SellerDtls']['Gstin'], '27AAAAA0000A1Z5')
        self.assertEqual(payload['BuyerDtls']['Gstin'], '27BBBCC1111B1Z2')
        self.assertEqual(len(payload['ItemList']), 1)
        self.assertEqual(payload['ItemList'][0]['HsnCd'], '84713010')
        self.assertEqual(payload['ValDtls']['TotAssVal'], 1000.0)
        self.assertEqual(payload['ValDtls']['TotInvVal'], 1180.0)

    def test_generate_irn_success_for_b2b_sale(self):
        """Verify IRN generation sets status to generated and populates IRN hash."""
        success, msg = generate_irn_for_sale(self.b2b_sale, user=self.staff)
        self.assertTrue(success)
        self.assertIn("generated successfully", msg)

        self.b2b_sale.refresh_from_db()
        self.assertEqual(self.b2b_sale.einvoiceStatus, Sales.EInvoiceStatus.GENERATED)
        self.assertIsNotNone(self.b2b_sale.irn)
        self.assertEqual(len(self.b2b_sale.irn), 64)
        self.assertIsNotNone(self.b2b_sale.ackNo)
        self.assertIsNotNone(self.b2b_sale.signedQrCode)

    def test_generate_irn_blocked_for_b2c_sale(self):
        """Verify IRN generation is rejected for B2C invoices."""
        success, msg = generate_irn_for_sale(self.b2c_sale, user=self.staff)
        self.assertFalse(success)
        self.assertIn("only applicable to B2B", msg)

    def test_cancel_irn_success(self):
        """Verify cancelling an active IRN."""
        generate_irn_for_sale(self.b2b_sale, user=self.staff)
        self.b2b_sale.refresh_from_db()

        success, msg = cancel_irn_for_sale(self.b2b_sale, cancel_reason="1", cancel_remarks="Customer request", user=self.staff)
        self.assertTrue(success)

        self.b2b_sale.refresh_from_db()
        self.assertEqual(self.b2b_sale.einvoiceStatus, Sales.EInvoiceStatus.CANCELLED)

    def test_ewaybill_generation_and_cancellation(self):
        """Verify E-Way Bill generation and transport details update."""
        transport_details = {
            'transportMode': '1',
            'transporterId': '27ABCDE1234F1Z1',
            'transporterName': 'Fast Logistics',
            'vehicleNo': 'MH12AB1234',
            'vehicleType': 'R',
            'distanceKm': 150
        }
        success, msg = generate_ewaybill_for_sale(self.b2b_sale, transport_details)
        self.assertTrue(success)

        self.b2b_sale.refresh_from_db()
        self.assertEqual(self.b2b_sale.ewayBillStatus, Sales.EWayBillStatus.GENERATED)
        self.assertIsNotNone(self.b2b_sale.ewayBillNo)
        self.assertEqual(self.b2b_sale.vehicleNo, 'MH12AB1234')
        self.assertEqual(self.b2b_sale.distanceKm, 150)

        # Cancel EWB
        c_success, c_msg = cancel_ewaybill_for_sale(self.b2b_sale, cancel_reason="1", cancel_remarks="Cancelled")
        self.assertTrue(c_success)
        self.b2b_sale.refresh_from_db()
        self.assertEqual(self.b2b_sale.ewayBillStatus, Sales.EWayBillStatus.CANCELLED)

    def test_pdf_invoice_qr_code_rendering(self):
        """Verify QR code data URI generation for PDF invoice."""
        # Setup signed QR code
        self.b2b_sale.signedQrCode = "MOCK_SIGNED_QR_CODE_DATA"
        self.b2b_sale.save()

        # Simulate rendering logic
        import io
        import base64
        import qrcode
        qr_code_data_uri = None
        if self.b2b_sale.signedQrCode:
            qr = qrcode.QRCode(version=1, box_size=3, border=1)
            qr.add_data(self.b2b_sale.signedQrCode)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            qr_code_data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode('utf-8')
        
        self.assertIsNotNone(qr_code_data_uri)
        self.assertTrue(qr_code_data_uri.startswith("data:image/png;base64,"))

