from __future__ import annotations

import json

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from home.models import (
    BookTransaction,
    InventoryItem,
    InventoryLedgerEntry,
    InventoryStock,
    InvoiceSeries,
    PartyLedgerEntry,
    ProformaInvoice,
    Sales,
)
from home.tests.utils import SeededTenantMixin


class ProformaInvoiceTests(SeededTenantMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.client.force_login(self.owner)
        self.item = InventoryItem.objects.create(
            tenant=self.tenant,
            name='GST Paint',
            sku='GST-PAINT',
            defaultStockBucket=InventoryItem.StockBucket.GST,
            gstRate=18.0,
            defaultSalesPrice=100.0,
        )
        self.proforma_series = InvoiceSeries.objects.create(
            tenant=self.tenant,
            module=InvoiceSeries.Module.PROFORMA,
            seriesCode='PI',
            startNumber=1,
            endNumber=9999,
            nextNumber=1,
            paddingLength=4,
            isActive=True,
            isDefault=True,
        )
        self.sales_series = InvoiceSeries.objects.create(
            tenant=self.tenant,
            module=InvoiceSeries.Module.SALES,
            seriesCode='INVPI',
            startNumber=1,
            endNumber=9999,
            nextNumber=1,
            paddingLength=4,
            isActive=True,
            isDefault=True,
        )

    def _line_items(self, quantity, *, unit_price=100.0):
        taxable = round(quantity * unit_price, 2)
        gst = round(taxable * 18.0 / 100.0, 2)
        return json.dumps([{
            'itemId': self.item.pk,
            'stockBucket': InventoryItem.StockBucket.GST,
            'quantity': quantity,
            'unitPrice': unit_price,
            'taxableAmount': taxable,
            'gstRate': 18.0,
            'gstAmount': gst,
            'lineTotal': taxable + gst,
        }])

    def _create_proforma(self):
        response = self.client.post(
            reverse('homeApp:add_proforma_invoice_api'),
            data={
                'invoiceSeriesId': str(self.proforma_series.pk),
                'colDate': '25/03/2026',
                'validUntil': '31/03/2026',
                'party': f'{self.party.name} @ {self.party.pk}',
                'remark': 'paint proforma',
                'terms': 'Valid for seven days',
                'lineItems': self._line_items(2),
            },
        )
        self.assertEqual(response.status_code, 200, msg=getattr(response, 'content', b''))
        self.assertEqual(response.json()['message'], 'success', msg=response.json())
        return ProformaInvoice.objects.get(remark='paint proforma')

    def test_proforma_creation_does_not_post_books_party_ledger_or_stock(self):
        book_count = BookTransaction.objects.filter(tenant=self.tenant, source=BookTransaction.Source.SALES).count()
        party_ledger_count = PartyLedgerEntry.objects.filter(tenant=self.tenant, party=self.party).count()
        sales_ledger_count = InventoryLedgerEntry.objects.filter(tenant=self.tenant, item=self.item, sourceType=InventoryLedgerEntry.SourceType.SALES).count()
        stock_count = InventoryStock.objects.filter(tenant=self.tenant, item=self.item).count()
        proforma = self._create_proforma()

        self.assertEqual(proforma.invoiceNumber, 'PI0001')
        self.assertEqual(proforma.proformaID, 'PI000001')
        self.assertEqual(proforma.amount, 236.0)
        self.assertEqual(proforma.lines.filter(isDeleted=False).count(), 1)
        self.assertEqual(BookTransaction.objects.filter(tenant=self.tenant, source=BookTransaction.Source.SALES).count(), book_count)
        self.assertEqual(PartyLedgerEntry.objects.filter(tenant=self.tenant, party=self.party).count(), party_ledger_count)
        self.assertEqual(InventoryLedgerEntry.objects.filter(tenant=self.tenant, item=self.item, sourceType=InventoryLedgerEntry.SourceType.SALES).count(), sales_ledger_count)
        self.assertEqual(InventoryStock.objects.filter(tenant=self.tenant, item=self.item).count(), stock_count)

    def test_proforma_pdf_downloads(self):
        proforma = self._create_proforma()
        response = self.client.get(reverse('homeApp:generate_proforma_invoice_pdf'), data={'id': proforma.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

        list_pdf_response = self.client.get(reverse('homeApp:export_proforma_list_pdf'), data={
            'startDate': '25/03/2026',
            'endDate': '25/03/2026',
            'staffID': 'All',
            'status': 'All',
        })
        self.assertEqual(list_pdf_response.status_code, 200)
        self.assertEqual(list_pdf_response['Content-Type'], 'application/pdf')

        list_excel_response = self.client.get(reverse('homeApp:export_proforma_list_excel'), data={
            'startDate': '25/03/2026',
            'endDate': '25/03/2026',
            'staffID': 'All',
            'status': 'All',
        })
        self.assertEqual(list_excel_response.status_code, 200)
        self.assertIn('spreadsheetml', list_excel_response['Content-Type'])

    def test_proforma_pages_render(self):
        proforma = self._create_proforma()

        add_response = self.client.get(reverse('homeApp:add_proforma_invoice'))
        list_response = self.client.get(reverse('homeApp:proforma_invoice_list'))
        edit_response = self.client.get(reverse('homeApp:edit_proforma_invoice', args=[proforma.pk]))

        self.assertEqual(add_response.status_code, 200)
        self.assertContains(add_response, 'Add Proforma Invoice')
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, 'Proforma Invoice List')
        self.assertContains(list_response, 'Proforma Export')
        self.assertContains(list_response, 'PDF Report')
        self.assertContains(list_response, 'Excel Export')
        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(edit_response, proforma.invoiceNumber)

    def test_convert_proforma_creates_sales_and_moves_stock_once(self):
        purchase_response = self.client.post(
            reverse('homeApp:add_purchase_invoice_api'),
            data={
                'party': str(self.party.pk),
                'invoiceNumber': 'P-PROFORMA-1',
                'invoiceDate': '25/03/2026',
                'gstTreatment': 'gst',
                'claimStatus': 'not_applicable',
                'lineItems': json.dumps([{
                    'itemId': self.item.pk,
                    'stockBucket': InventoryItem.StockBucket.GST,
                    'quantity': 5,
                    'unitCost': 60.0,
                    'taxableAmount': 300.0,
                    'gstRate': 18.0,
                    'gstAmount': 54.0,
                    'lineTotal': 354.0,
                }]),
            },
        )
        self.assertEqual(purchase_response.status_code, 200, msg=getattr(purchase_response, 'content', b''))
        self.assertEqual(purchase_response.json()['message'], 'success', msg=purchase_response.json())
        proforma = self._create_proforma()
        sales_count = Sales.objects.filter(tenant=self.tenant).count()

        response = self.client.post(
            reverse('homeApp:convert_proforma_to_sales_api'),
            data={
                'ID': str(proforma.pk),
                'salesInvoiceSeriesId': str(self.sales_series.pk),
                'paymentMode': Sales.Mode.CREDIT,
                'colDate': '26/03/2026',
            },
        )

        self.assertEqual(response.status_code, 200, msg=getattr(response, 'content', b''))
        self.assertEqual(response.json()['message'], 'success', msg=response.json())
        proforma.refresh_from_db()
        self.assertEqual(proforma.status, ProformaInvoice.Status.CONVERTED)
        self.assertIsNotNone(proforma.convertedSales_id)
        sale = proforma.convertedSales
        self.assertEqual(sale.invoiceNumber, 'INVPI0001')
        self.assertEqual(sale.lines.filter(isDeleted=False).count(), 1)
        stock = InventoryStock.objects.get(tenant=self.tenant, item=self.item, stockBucket=InventoryItem.StockBucket.GST)
        self.assertEqual(stock.quantityOnHand, 3.0)

        second_response = self.client.post(
            reverse('homeApp:convert_proforma_to_sales_api'),
            data={'ID': str(proforma.pk), 'salesInvoiceSeriesId': str(self.sales_series.pk)},
        )
        self.assertEqual(second_response.status_code, 400)
        self.assertEqual(Sales.objects.filter(tenant=self.tenant).count(), sales_count + 1)
