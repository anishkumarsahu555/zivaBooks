from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from home.models import (
    CashCounter,
    InventoryCategory,
    InventoryAdjustmentVoucher,
    InventoryItem,
    InventoryLedgerEntry,
    InventoryOpeningVoucher,
    InventoryStock,
    InventoryTaxProfile,
    InventoryUnit,
    InventoryTransferVoucher,
    InventoryWarehouse,
    InvoiceSeries,
    PurchaseInvoice,
    PurchaseInvoiceRevision,
    PurchaseReturnVoucher,
    Sales,
    SalesReturnVoucher,
    StaffUser,
    Tenant,
    TenantMembership,
)
from home.tests.utils import SeededTenantMixin


User = get_user_model()


class InventoryPurchaseSalesTests(SeededTenantMixin, TestCase):
    def setUp(self):
        self.client.force_login(self.owner)
        self.item = InventoryItem.objects.create(
            tenant=self.tenant,
            name='GST Cement',
            sku='GST-CEMENT',
            defaultStockBucket=InventoryItem.StockBucket.GST,
            gstRate=18.0,
            defaultSalesPrice=120.0,
        )
        self.sales_series = InvoiceSeries.objects.create(
            tenant=self.tenant,
            module=InvoiceSeries.Module.SALES,
            seriesCode='INV',
            startNumber=1,
            endNumber=9999,
            nextNumber=1,
            paddingLength=4,
            isActive=True,
            isDefault=True,
        )
        self.counter_series = InvoiceSeries.objects.create(
            tenant=self.tenant,
            module=InvoiceSeries.Module.CASH_COUNTER,
            seriesCode='CCR',
            startNumber=1,
            endNumber=9999,
            nextNumber=1,
            paddingLength=4,
            isActive=True,
            isDefault=True,
        )

    def _line_items(self, quantity, *, bucket=InventoryItem.StockBucket.GST, unit_cost=100.0, unit_price=120.0):
        return json.dumps([{
            'itemId': self.item.pk,
            'stockBucket': bucket,
            'quantity': quantity,
            'unitCost': unit_cost,
            'unitPrice': unit_price,
            'taxableAmount': quantity * unit_cost,
            'gstRate': 18.0,
            'gstAmount': round(quantity * unit_cost * 0.18, 2),
            'lineTotal': round(quantity * unit_price, 2),
        }])

    def test_purchase_invoice_creates_stock_and_claim_history(self):
        self.tenant.nonGstShadowEnabled = True
        self.tenant.save(update_fields=['nonGstShadowEnabled', 'lastUpdatedOn'])
        create_response = self.client.post(
            reverse('homeApp:add_purchase_invoice_api'),
            data={
                'party': str(self.party.pk),
                'invoiceNumber': 'P-1001',
                'invoiceDate': '25/03/2026',
                'gstTreatment': PurchaseInvoice.GstTreatment.NON_GST,
                'claimStatus': PurchaseInvoice.ClaimStatus.PENDING,
                'remark': 'initial purchase',
                'lineItems': self._line_items(10, unit_cost=80.0, unit_price=0.0),
            },
        )
        self.assertEqual(create_response.status_code, 200, msg=getattr(create_response, 'content', b''))
        self.assertEqual(create_response.json()['message'], 'success')
        purchase = PurchaseInvoice.objects.get(invoiceNumber='P-1001')
        stock = InventoryStock.objects.get(tenant=self.tenant, item=self.item, stockBucket=InventoryItem.StockBucket.GST)
        self.assertEqual(stock.quantityOnHand, 10.0)

        update_response = self.client.post(
            reverse('homeApp:update_purchase_invoice_api'),
            data={
                'purchaseId': str(purchase.pk),
                'party': str(self.party.pk),
                'invoiceNumber': 'P-1001',
                'invoiceDate': '25/03/2026',
                'gstTreatment': PurchaseInvoice.GstTreatment.NON_GST_CLAIMABLE,
                'claimStatus': PurchaseInvoice.ClaimStatus.CLAIMED,
                'revisionReason': 'Claimed later with supporting record',
                'remark': 'revised purchase',
                'lineItems': self._line_items(10, unit_cost=80.0, unit_price=0.0),
            },
        )
        self.assertEqual(update_response.status_code, 200, msg=getattr(update_response, 'content', b''))
        self.assertEqual(update_response.json()['message'], 'success')
        revision = PurchaseInvoiceRevision.objects.get(purchaseInvoice=purchase)
        self.assertEqual(revision.previousGstTreatment, PurchaseInvoice.GstTreatment.NON_GST)
        self.assertEqual(revision.newGstTreatment, PurchaseInvoice.GstTreatment.NON_GST_CLAIMABLE)
        self.assertEqual(revision.previousClaimStatus, PurchaseInvoice.ClaimStatus.PENDING)
        self.assertEqual(revision.newClaimStatus, PurchaseInvoice.ClaimStatus.CLAIMED)

    def test_sales_and_delete_restore_inventory_without_breaking_amount_flow(self):
        self.client.post(
            reverse('homeApp:add_purchase_invoice_api'),
            data={
                'party': str(self.party.pk),
                'invoiceNumber': 'P-1002',
                'invoiceDate': '25/03/2026',
                'gstTreatment': PurchaseInvoice.GstTreatment.GST,
                'claimStatus': PurchaseInvoice.ClaimStatus.NOT_APPLICABLE,
                'lineItems': self._line_items(10, unit_cost=80.0, unit_price=0.0),
            },
        )

        sale_response = self.client.post(
            reverse('homeApp:add_sales_by_admin_api'),
            data={
                'invoiceSeriesId': str(self.sales_series.pk),
                'paymentMode': 'Cash',
                'colDate': '25/03/2026',
                'party': f'{self.party.name} @ {self.party.pk}',
                'amount': '',
                'remark': 'inventory sale',
                'lineItems': self._line_items(3, unit_cost=0.0, unit_price=120.0),
            },
        )
        self.assertEqual(sale_response.status_code, 200, msg=getattr(sale_response, 'content', b''))
        self.assertEqual(sale_response.json()['message'], 'success')
        sale = Sales.objects.get(remark='inventory sale')
        stock = InventoryStock.objects.get(tenant=self.tenant, item=self.item, stockBucket=InventoryItem.StockBucket.GST)
        self.assertEqual(stock.quantityOnHand, 7.0)
        self.assertEqual(float(sale.amount), 360.0)

        delete_response = self.client.post(reverse('homeApp:delete_sales'), data={'userID': str(sale.pk)})
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()['message'], 'success')
        stock.refresh_from_db()
        self.assertEqual(stock.quantityOnHand, 10.0)

    def test_cash_counter_return_creates_linked_sales_return_voucher_without_double_counting_stock(self):
        self.client.post(
            reverse('homeApp:add_purchase_invoice_api'),
            data={
                'party': str(self.party.pk),
                'invoiceNumber': 'P-1003',
                'invoiceDate': '25/03/2026',
                'gstTreatment': PurchaseInvoice.GstTreatment.GST,
                'claimStatus': PurchaseInvoice.ClaimStatus.NOT_APPLICABLE,
                'lineItems': self._line_items(10, unit_cost=80.0, unit_price=0.0),
            },
        )

        response = self.client.post(
            reverse('homeApp:add_cash_counter_entry_api'),
            data={
                'invoiceSeriesId': str(self.counter_series.pk),
                'mode': 'Return',
                'party': f'{self.party.name} @ {self.party.pk}',
                'amount': '240',
                'remark': 'counter return',
                'lineItems': self._line_items(2, unit_cost=0.0, unit_price=120.0),
            },
        )
        self.assertEqual(response.status_code, 200, msg=getattr(response, 'content', b''))
        self.assertEqual(response.json()['message'], 'success')

        counter = CashCounter.objects.get(remark='counter return')
        self.assertIsNotNone(counter.linkedSalesReturnVoucher_id)
        voucher = SalesReturnVoucher.objects.get(pk=counter.linkedSalesReturnVoucher_id)
        self.assertEqual(voucher.partyID_id, self.party.pk)
        self.assertEqual(voucher.totalAmount, 240.0)
        self.assertEqual(voucher.lines.filter(isDeleted=False).count(), 1)

        stock = InventoryStock.objects.get(tenant=self.tenant, item=self.item, stockBucket=InventoryItem.StockBucket.GST)
        self.assertEqual(stock.quantityOnHand, 12.0)

    def test_inventory_snapshot_is_tenant_scoped(self):
        other_tenant = Tenant.objects.create(name='Other Tenant', slug='other-tenant')
        other_user = User.objects.create_user(username='other.owner', password='secret123')
        TenantMembership.objects.create(tenant=other_tenant, user=other_user, role=TenantMembership.Role.OWNER, isDefault=True)
        other_staff = StaffUser.objects.create(tenant=other_tenant, user_ID=other_user, name='Other Staff', isActive='Active')
        other_item = InventoryItem.objects.create(tenant=other_tenant, name='Other Item', defaultStockBucket=InventoryItem.StockBucket.NON_GST)
        InventoryStock.objects.create(tenant=other_tenant, item=other_item, stockBucket=InventoryItem.StockBucket.NON_GST, quantityOnHand=99)
        InventoryStock.objects.create(tenant=other_tenant, item=other_item, stockBucket=InventoryItem.StockBucket.GST, quantityOnHand=0)

        InventoryStock.objects.create(tenant=self.tenant, item=self.item, stockBucket=InventoryItem.StockBucket.GST, quantityOnHand=5)
        InventoryStock.objects.create(tenant=self.tenant, item=self.item, stockBucket=InventoryItem.StockBucket.NON_GST, quantityOnHand=1)

        response = self.client.get(reverse('homeApp:get_inventory_snapshot_api'))
        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['name'], 'GST Cement')
        self.assertEqual(payload[0]['gst'], 5.0)
        self.assertEqual(payload[0]['non_gst'], 1.0)

    def test_inventory_and_purchase_pages_load_for_owner(self):
        inventory_page = self.client.get(reverse('homeApp:inventory_list'))
        self.assertEqual(inventory_page.status_code, 200)
        inventory_items_page = self.client.get(reverse('homeApp:inventory_items_page'))
        self.assertEqual(inventory_items_page.status_code, 200)
        inventory_masters_page = self.client.get(reverse('homeApp:inventory_masters_page'))
        self.assertEqual(inventory_masters_page.status_code, 200)
        inventory_stock_page = self.client.get(reverse('homeApp:inventory_stock_page'))
        self.assertEqual(inventory_stock_page.status_code, 200)
        inventory_vouchers_page = self.client.get(reverse('homeApp:inventory_vouchers_page'))
        self.assertEqual(inventory_vouchers_page.status_code, 200)
        purchase_page = self.client.get(reverse('homeApp:purchase_list'))
        self.assertEqual(purchase_page.status_code, 200)
        non_gst_page = self.client.get(reverse('homeApp:non_gst_shadow_page'))
        self.assertEqual(non_gst_page.status_code, 200)

    def test_non_gst_shadow_blocks_non_gst_defaults_and_purchase_flows_until_enabled(self):
        self.tenant.nonGstShadowEnabled = False
        self.tenant.save(update_fields=['nonGstShadowEnabled', 'lastUpdatedOn'])

        item_response = self.client.post(
            reverse('homeApp:add_inventory_item_api'),
            data={
                'name': 'Hidden Bucket Item',
                'defaultStockBucket': InventoryItem.StockBucket.NON_GST,
            },
        )
        self.assertEqual(item_response.status_code, 200)
        self.assertEqual(item_response.json()['message'], 'error')
        self.assertIn('Non-GST', item_response.json()['error'])

        purchase_response = self.client.post(
            reverse('homeApp:add_purchase_invoice_api'),
            data={
                'party': str(self.party.pk),
                'invoiceNumber': 'P-SHADOW-1',
                'invoiceDate': '25/03/2026',
                'gstTreatment': PurchaseInvoice.GstTreatment.NON_GST,
                'claimStatus': PurchaseInvoice.ClaimStatus.PENDING,
                'lineItems': self._line_items(2, bucket=InventoryItem.StockBucket.NON_GST, unit_cost=80.0, unit_price=0.0),
            },
        )
        self.assertEqual(purchase_response.status_code, 200)
        self.assertEqual(purchase_response.json()['message'], 'error')
        self.assertIn('Non-GST', purchase_response.json()['error'])

    def test_non_gst_shadow_can_be_enabled_with_six_digit_code(self):
        self.tenant.nonGstShadowEnabled = False
        self.tenant.save(update_fields=['nonGstShadowEnabled', 'lastUpdatedOn'])

        set_code_response = self.client.post(
            reverse('homeApp:configure_non_gst_shadow_code_api'),
            data={'accessCode': '654321', 'password': 'alpha123'},
        )
        self.assertEqual(set_code_response.status_code, 200)
        self.assertEqual(set_code_response.json()['message'], 'success')

        toggle_response = self.client.post(
            reverse('homeApp:toggle_non_gst_shadow_api'),
            data={'accessCode': '654321', 'password': 'alpha123', 'enabled': '1'},
        )
        self.assertEqual(toggle_response.status_code, 200)
        self.assertEqual(toggle_response.json()['message'], 'success')
        self.tenant.refresh_from_db()
        self.assertTrue(self.tenant.nonGstShadowEnabled)

        purchase_response = self.client.post(
            reverse('homeApp:add_purchase_invoice_api'),
            data={
                'party': str(self.party.pk),
                'invoiceNumber': 'P-SHADOW-2',
                'invoiceDate': '25/03/2026',
                'gstTreatment': PurchaseInvoice.GstTreatment.NON_GST,
                'claimStatus': PurchaseInvoice.ClaimStatus.PENDING,
                'lineItems': self._line_items(2, bucket=InventoryItem.StockBucket.NON_GST, unit_cost=80.0, unit_price=0.0),
            },
        )
        self.assertEqual(purchase_response.status_code, 200, msg=getattr(purchase_response, 'content', b''))
        self.assertEqual(purchase_response.json()['message'], 'success')

    def test_inventory_item_saves_extended_product_fields(self):
        response = self.client.post(
            reverse('homeApp:add_inventory_item_api'),
            data={
                'name': 'Premium Pipe',
                'sku': 'PIPE-01',
                'barcode': '1234567890',
                'categoryName': 'Pipes',
                'brandName': 'AquaFlow',
                'unitName': 'pcs',
                'hsnCode': '7306',
                'description': 'CPVC pressure pipe',
                'defaultStockBucket': 'gst',
                'gstRate': '18',
                'defaultPurchasePrice': '85',
                'defaultSalesPrice': '120',
                'mrp': '140',
                'minimumStockLevel': '5',
                'reorderLevel': '8',
                'isSaleBlocked': '',
            },
        )
        self.assertEqual(response.status_code, 200, msg=getattr(response, 'content', b''))
        self.assertEqual(response.json()['message'], 'success')
        item = InventoryItem.objects.get(name='Premium Pipe')
        self.assertEqual(item.categoryName, 'Pipes')
        self.assertEqual(item.brandName, 'AquaFlow')
        self.assertEqual(item.barcode, '1234567890')
        self.assertEqual(float(item.defaultPurchasePrice), 85.0)
        self.assertEqual(float(item.mrp), 140.0)

    def test_inventory_category_and_tax_profile_crud(self):
        category_response = self.client.post(
            reverse('homeApp:add_inventory_category_api'),
            data={'name': 'Pipes', 'code': 'PIPE', 'description': 'Pipe category'},
        )
        self.assertEqual(category_response.status_code, 200, msg=getattr(category_response, 'content', b''))
        self.assertEqual(category_response.json()['message'], 'success')
        category = InventoryCategory.objects.get(name='Pipes')

        tax_response = self.client.post(
            reverse('homeApp:add_inventory_tax_profile_api'),
            data={'name': 'PVC 18%', 'hsnCode': '3917', 'gstRate': '18', 'cessRate': '0', 'description': 'PVC tax'},
        )
        self.assertEqual(tax_response.status_code, 200, msg=getattr(tax_response, 'content', b''))
        self.assertEqual(tax_response.json()['message'], 'success')
        tax_profile = InventoryTaxProfile.objects.get(name='PVC 18%')

        update_category = self.client.post(
            reverse('homeApp:update_inventory_category_api'),
            data={'categoryId': str(category.pk), 'name': 'Pipe Fittings', 'code': 'PIPE-FIT', 'description': 'Updated'},
        )
        self.assertEqual(update_category.status_code, 200)
        category.refresh_from_db()
        self.assertEqual(category.name, 'Pipe Fittings')

        update_tax = self.client.post(
            reverse('homeApp:update_inventory_tax_profile_api'),
            data={'taxProfileId': str(tax_profile.pk), 'name': 'PVC 12%', 'hsnCode': '3917', 'gstRate': '12', 'cessRate': '1'},
        )
        self.assertEqual(update_tax.status_code, 200)
        tax_profile.refresh_from_db()
        self.assertEqual(float(tax_profile.gstRate), 12.0)

        list_categories = self.client.get(reverse('homeApp:list_inventory_categories_api'))
        self.assertEqual(list_categories.status_code, 200)
        self.assertIn('Pipe Fittings', [row['Name'] for row in list_categories.json()['data']])

        list_tax = self.client.get(reverse('homeApp:list_inventory_tax_profiles_api'))
        self.assertEqual(list_tax.status_code, 200)
        self.assertIn('PVC 12%', [row['Name'] for row in list_tax.json()['data']])

        delete_category = self.client.post(reverse('homeApp:delete_inventory_category_api'), data={'categoryId': str(category.pk)})
        self.assertEqual(delete_category.status_code, 200)
        category.refresh_from_db()
        self.assertTrue(category.isDeleted)

        delete_tax = self.client.post(reverse('homeApp:delete_inventory_tax_profile_api'), data={'taxProfileId': str(tax_profile.pk)})
        self.assertEqual(delete_tax.status_code, 200)
        tax_profile.refresh_from_db()
        self.assertTrue(tax_profile.isDeleted)

    def test_inventory_unit_crud_and_item_link(self):
        create_response = self.client.post(
            reverse('homeApp:add_inventory_unit_api'),
            data={'name': 'Bundle Pieces', 'symbol': 'bps', 'allowsDecimal': '', 'description': 'Counted unit'},
        )
        self.assertEqual(create_response.status_code, 200, msg=getattr(create_response, 'content', b''))
        self.assertEqual(create_response.json()['message'], 'success')
        unit = InventoryUnit.objects.get(name='Bundle Pieces')

        item_response = self.client.post(
            reverse('homeApp:add_inventory_item_api'),
            data={
                'name': 'Unit Linked Item',
                'sku': 'UNIT-1',
                'unitId': str(unit.pk),
                'defaultStockBucket': 'gst',
                'defaultSalesPrice': '25',
            },
        )
        self.assertEqual(item_response.status_code, 200, msg=getattr(item_response, 'content', b''))
        item = InventoryItem.objects.get(name='Unit Linked Item')
        self.assertEqual(item.unit_id, unit.pk)
        self.assertEqual(item.unitName, 'bps')

        update_response = self.client.post(
            reverse('homeApp:update_inventory_unit_api'),
            data={'unitId': str(unit.pk), 'name': 'Bundle Pieces', 'symbol': 'bp', 'allowsDecimal': '1'},
        )
        self.assertEqual(update_response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.unitName, 'bp')

        list_response = self.client.get(reverse('homeApp:list_inventory_units_api'))
        self.assertEqual(list_response.status_code, 200)
        self.assertIn('bp', [row['Symbol'] for row in list_response.json()['data']])

        delete_response = self.client.post(reverse('homeApp:delete_inventory_unit_api'), data={'unitId': str(unit.pk)})
        self.assertEqual(delete_response.status_code, 200)
        unit.refresh_from_db()
        self.assertTrue(unit.isDeleted)

    def test_inventory_item_update_and_delete_support_master_links(self):
        category = InventoryCategory.objects.create(tenant=self.tenant, name='Tiles')
        tax_profile = InventoryTaxProfile.objects.create(tenant=self.tenant, name='Tiles 28%', hsnCode='6907', gstRate=28.0)
        item = InventoryItem.objects.create(tenant=self.tenant, name='Floor Tile', defaultStockBucket=InventoryItem.StockBucket.GST)

        update_response = self.client.post(
            reverse('homeApp:update_inventory_item_api'),
            data={
                'itemId': str(item.pk),
                'name': 'Floor Tile Premium',
                'sku': 'TILE-01',
                'categoryId': str(category.pk),
                'taxProfileId': str(tax_profile.pk),
                'defaultStockBucket': 'gst',
                'defaultPurchasePrice': '120',
                'defaultSalesPrice': '160',
            },
        )
        self.assertEqual(update_response.status_code, 200, msg=getattr(update_response, 'content', b''))
        self.assertEqual(update_response.json()['message'], 'success')
        item.refresh_from_db()
        self.assertEqual(item.category_id, category.pk)
        self.assertEqual(item.taxProfile_id, tax_profile.pk)
        self.assertEqual(item.categoryName, 'Tiles')
        self.assertEqual(item.hsnCode, '6907')
        self.assertEqual(float(item.gstRate), 28.0)

        delete_response = self.client.post(reverse('homeApp:delete_inventory_item_api'), data={'itemId': str(item.pk)})
        self.assertEqual(delete_response.status_code, 200)
        item.refresh_from_db()
        self.assertTrue(item.isDeleted)

    def test_inventory_warehouse_creation_and_default_listing(self):
        response = self.client.post(
            reverse('homeApp:add_inventory_warehouse_api'),
            data={
                'name': 'Front Godown',
                'code': 'FG',
                'address': 'Counter side',
                'isDefault': '1',
            },
        )
        self.assertEqual(response.status_code, 200, msg=getattr(response, 'content', b''))
        self.assertEqual(response.json()['message'], 'success')
        warehouse = InventoryWarehouse.objects.get(name='Front Godown')
        self.assertTrue(warehouse.isDefault)

        list_response = self.client.get(reverse('homeApp:list_inventory_warehouses_api'))
        self.assertEqual(list_response.status_code, 200)
        payload = list_response.json()['data']
        self.assertEqual(payload[0]['Name'], 'Front Godown')
        self.assertTrue(payload[0]['IsDefault'])

    def test_opening_stock_adds_inventory_balance(self):
        warehouse = InventoryWarehouse.objects.create(
            tenant=self.tenant,
            name='Main Godown',
            code='MG',
            isDefault=True,
        )
        response = self.client.post(
            reverse('homeApp:add_inventory_opening_stock_api'),
            data={
                'entryDate': '25/03/2026',
                'warehouseId': str(warehouse.pk),
                'remark': 'Year opening',
                'lineItems': self._line_items(12, unit_cost=75.0, unit_price=0.0),
            },
        )
        self.assertEqual(response.status_code, 200, msg=getattr(response, 'content', b''))
        self.assertEqual(response.json()['message'], 'success')
        voucher = InventoryOpeningVoucher.objects.get(tenant=self.tenant)
        self.assertTrue(voucher.voucherNumber.startswith('OS'))
        stock = InventoryStock.objects.get(tenant=self.tenant, item=self.item, stockBucket=InventoryItem.StockBucket.GST)
        self.assertEqual(stock.quantityOnHand, 12.0)
        ledger = InventoryLedgerEntry.objects.get(
            tenant=self.tenant,
            item=self.item,
            sourceType=InventoryLedgerEntry.SourceType.OPENING,
        )
        self.assertEqual(ledger.warehouse_id, warehouse.pk)
        self.assertEqual(ledger.quantityDelta, 12.0)

    def test_purchase_and_sales_generate_stock_ledger_entries(self):
        purchase_response = self.client.post(
            reverse('homeApp:add_purchase_invoice_api'),
            data={
                'party': str(self.party.pk),
                'invoiceNumber': 'P-2001',
                'invoiceDate': '25/03/2026',
                'gstTreatment': PurchaseInvoice.GstTreatment.GST,
                'claimStatus': PurchaseInvoice.ClaimStatus.NOT_APPLICABLE,
                'lineItems': self._line_items(8, unit_cost=90.0, unit_price=0.0),
            },
        )
        self.assertEqual(purchase_response.status_code, 200, msg=getattr(purchase_response, 'content', b''))

        sale_response = self.client.post(
            reverse('homeApp:add_sales_by_admin_api'),
            data={
                'invoiceSeriesId': str(self.sales_series.pk),
                'paymentMode': 'Cash',
                'colDate': '25/03/2026',
                'party': f'{self.party.name} @ {self.party.pk}',
                'remark': 'ledger sale',
                'lineItems': self._line_items(3, unit_cost=0.0, unit_price=120.0),
            },
        )
        self.assertEqual(sale_response.status_code, 200, msg=getattr(sale_response, 'content', b''))

        purchase_ledger = InventoryLedgerEntry.objects.get(
            tenant=self.tenant,
            item=self.item,
            sourceType=InventoryLedgerEntry.SourceType.PURCHASE,
        )
        sales_ledger = InventoryLedgerEntry.objects.get(
            tenant=self.tenant,
            item=self.item,
            sourceType=InventoryLedgerEntry.SourceType.SALES,
        )
        self.assertEqual(purchase_ledger.quantityDelta, 8.0)
        self.assertEqual(sales_ledger.quantityDelta, -3.0)
        self.assertTrue(InventoryWarehouse.objects.filter(tenant=self.tenant, isDefault=True).exists())

        ledger_response = self.client.get(reverse('homeApp:list_inventory_ledger_entries_api'))
        self.assertEqual(ledger_response.status_code, 200)
        self.assertEqual(len(ledger_response.json()['data']), 2)

    def test_sales_invoice_persists_b2b_gst_fields_and_downloads_pdf(self):
        self.party.isB2B = True
        self.party.gstNumber = '22AAAAA0000A1Z5'
        self.party.legalName = 'Alpha Retail LLP'
        self.party.billingAddress = 'GST Road'
        self.party.save(update_fields=['isB2B', 'gstNumber', 'legalName', 'billingAddress', 'lastUpdatedOn'])
        self.client.post(
            reverse('homeApp:add_purchase_invoice_api'),
            data={
                'party': str(self.party.pk),
                'invoiceNumber': 'P-2002',
                'invoiceDate': '25/03/2026',
                'gstTreatment': PurchaseInvoice.GstTreatment.GST,
                'claimStatus': PurchaseInvoice.ClaimStatus.NOT_APPLICABLE,
                'lineItems': self._line_items(5, unit_cost=90.0, unit_price=0.0),
            },
        )
        gst_sale_lines = json.dumps([{
            'itemId': self.item.pk,
            'stockBucket': InventoryItem.StockBucket.GST,
            'quantity': 2,
            'unitPrice': 120.0,
            'taxableAmount': 240.0,
            'gstRate': 18.0,
            'gstAmount': 43.2,
            'lineTotal': 283.2,
        }])
        response = self.client.post(
            reverse('homeApp:add_sales_by_admin_api'),
            data={
                'invoiceSeriesId': str(self.sales_series.pk),
                'paymentMode': 'Cash',
                'colDate': '25/03/2026',
                'party': str(self.party.pk),
                'invoiceKind': 'b2b',
                'buyerLegalName': 'Alpha Retail LLP',
                'buyerGstNumber': '22AAAAA0000A1Z5',
                'buyerBillingAddress': 'GST Road',
                'placeOfSupply': 'Manipur',
                'remark': 'gst invoice sale',
                'lineItems': gst_sale_lines,
            },
        )
        self.assertEqual(response.status_code, 200, msg=getattr(response, 'content', b''))
        self.assertEqual(response.json()['message'], 'success')
        sale = Sales.objects.get(remark='gst invoice sale')
        self.assertEqual(sale.invoiceKind, Sales.InvoiceKind.B2B)
        self.assertEqual(sale.buyerGstNumber, '22AAAAA0000A1Z5')
        self.assertEqual(float(sale.taxableAmount), 240.0)
        self.assertEqual(float(sale.gstAmount), 43.2)
        self.assertEqual(float(sale.amount), 283.2)
        line = sale.lines.get(isDeleted=False)
        self.assertEqual(float(line.gstAmount), 43.2)
        pdf = self.client.get(reverse('homeApp:generate_sales_invoice_pdf'), data={'id': sale.pk})
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')

    def test_inventory_adjustment_voucher_updates_stock_and_ledger(self):
        warehouse = InventoryWarehouse.objects.create(tenant=self.tenant, name='Main', isDefault=True)
        self.client.post(
            reverse('homeApp:add_inventory_opening_stock_api'),
            data={
                'entryDate': '25/03/2026',
                'warehouseId': str(warehouse.pk),
                'lineItems': self._line_items(10, unit_cost=75.0, unit_price=0.0),
            },
        )

        response = self.client.post(
            reverse('homeApp:add_inventory_adjustment_voucher_api'),
            data={
                'entryDate': '25/03/2026',
                'warehouseId': str(warehouse.pk),
                'direction': 'decrease',
                'remark': 'Physical count shortage',
                'lineItems': self._line_items(2, unit_cost=75.0, unit_price=0.0),
            },
        )
        self.assertEqual(response.status_code, 200, msg=getattr(response, 'content', b''))
        self.assertEqual(response.json()['message'], 'success')
        self.assertTrue(InventoryAdjustmentVoucher.objects.filter(tenant=self.tenant, isDeleted=False).exists())
        stock = InventoryStock.objects.get(tenant=self.tenant, item=self.item, stockBucket=InventoryItem.StockBucket.GST)
        self.assertEqual(stock.quantityOnHand, 8.0)
        ledger = InventoryLedgerEntry.objects.get(tenant=self.tenant, item=self.item, sourceType=InventoryLedgerEntry.SourceType.ADJUSTMENT)
        self.assertEqual(ledger.quantityDelta, -2.0)

        voucher = InventoryAdjustmentVoucher.objects.get(tenant=self.tenant, isDeleted=False)
        update_response = self.client.post(
            reverse('homeApp:update_inventory_adjustment_voucher_api'),
            data={
                'voucherId': str(voucher.pk),
                'entryDate': '25/03/2026',
                'warehouseId': str(warehouse.pk),
                'direction': 'increase',
                'remark': 'Physical count excess',
                'lineItems': self._line_items(1, unit_cost=75.0, unit_price=0.0),
            },
        )
        self.assertEqual(update_response.status_code, 200, msg=getattr(update_response, 'content', b''))
        self.assertEqual(update_response.json()['message'], 'success')
        stock.refresh_from_db()
        self.assertEqual(stock.quantityOnHand, 11.0)

    def test_inventory_transfer_voucher_moves_stock_between_warehouses(self):
        from_warehouse = InventoryWarehouse.objects.create(tenant=self.tenant, name='Front', isDefault=True)
        to_warehouse = InventoryWarehouse.objects.create(tenant=self.tenant, name='Back')
        self.client.post(
            reverse('homeApp:add_inventory_opening_stock_api'),
            data={
                'entryDate': '25/03/2026',
                'warehouseId': str(from_warehouse.pk),
                'lineItems': self._line_items(9, unit_cost=75.0, unit_price=0.0),
            },
        )

        response = self.client.post(
            reverse('homeApp:add_inventory_transfer_voucher_api'),
            data={
                'entryDate': '25/03/2026',
                'fromWarehouseId': str(from_warehouse.pk),
                'toWarehouseId': str(to_warehouse.pk),
                'remark': 'Shift to back godown',
                'lineItems': self._line_items(4, unit_cost=75.0, unit_price=0.0),
            },
        )
        self.assertEqual(response.status_code, 200, msg=getattr(response, 'content', b''))
        self.assertEqual(response.json()['message'], 'success')
        self.assertTrue(InventoryTransferVoucher.objects.filter(tenant=self.tenant, isDeleted=False).exists())
        stock = InventoryStock.objects.get(tenant=self.tenant, item=self.item, stockBucket=InventoryItem.StockBucket.GST)
        self.assertEqual(stock.quantityOnHand, 9.0)
        transfer_ledgers = InventoryLedgerEntry.objects.filter(
            tenant=self.tenant,
            item=self.item,
            sourceType__in=[InventoryLedgerEntry.SourceType.TRANSFER_OUT, InventoryLedgerEntry.SourceType.TRANSFER_IN],
        )
        self.assertEqual(transfer_ledgers.count(), 2)
        self.assertEqual(sum(row.quantityDelta for row in transfer_ledgers), 0.0)

        voucher = InventoryTransferVoucher.objects.get(tenant=self.tenant, isDeleted=False)
        update_response = self.client.post(
            reverse('homeApp:update_inventory_transfer_voucher_api'),
            data={
                'voucherId': str(voucher.pk),
                'entryDate': '25/03/2026',
                'fromWarehouseId': str(from_warehouse.pk),
                'toWarehouseId': str(to_warehouse.pk),
                'remark': 'Shift larger qty',
                'lineItems': self._line_items(6, unit_cost=75.0, unit_price=0.0),
            },
        )
        self.assertEqual(update_response.status_code, 200, msg=getattr(update_response, 'content', b''))
        self.assertEqual(update_response.json()['message'], 'success')
        transfer_ledgers = InventoryLedgerEntry.objects.filter(
            tenant=self.tenant,
            item=self.item,
            sourceType__in=[InventoryLedgerEntry.SourceType.TRANSFER_OUT, InventoryLedgerEntry.SourceType.TRANSFER_IN],
        )
        self.assertEqual(transfer_ledgers.count(), 2)
        self.assertEqual(sum(row.quantityDelta for row in transfer_ledgers), 0.0)

    def test_purchase_return_reduces_stock(self):
        warehouse = InventoryWarehouse.objects.create(tenant=self.tenant, name='Main', isDefault=True)
        purchase_response = self.client.post(
            reverse('homeApp:add_purchase_invoice_api'),
            data={
                'party': str(self.party.pk),
                'invoiceNumber': 'P-3001',
                'invoiceDate': '25/03/2026',
                'gstTreatment': PurchaseInvoice.GstTreatment.GST,
                'claimStatus': PurchaseInvoice.ClaimStatus.NOT_APPLICABLE,
                'warehouseId': str(warehouse.pk),
                'lineItems': self._line_items(8, unit_cost=90.0, unit_price=0.0),
            },
        )
        self.assertEqual(purchase_response.status_code, 200, msg=getattr(purchase_response, 'content', b''))
        purchase = PurchaseInvoice.objects.get(invoiceNumber='P-3001')

        response = self.client.post(
            reverse('homeApp:add_purchase_return_voucher_api'),
            data={
                'entryDate': '25/03/2026',
                'warehouseId': str(warehouse.pk),
                'purchaseInvoiceId': str(purchase.pk),
                'party': str(self.party.pk),
                'remark': 'Damaged bags',
                'lineItems': self._line_items(3, unit_cost=90.0, unit_price=0.0),
            },
        )
        self.assertEqual(response.status_code, 200, msg=getattr(response, 'content', b''))
        self.assertEqual(response.json()['message'], 'success')
        self.assertTrue(PurchaseReturnVoucher.objects.filter(tenant=self.tenant, isDeleted=False).exists())
        stock = InventoryStock.objects.get(tenant=self.tenant, item=self.item, stockBucket=InventoryItem.StockBucket.GST)
        self.assertEqual(stock.quantityOnHand, 5.0)
        ledger = InventoryLedgerEntry.objects.get(tenant=self.tenant, item=self.item, sourceType=InventoryLedgerEntry.SourceType.PURCHASE_RETURN)
        self.assertEqual(ledger.quantityDelta, -3.0)

        voucher = PurchaseReturnVoucher.objects.get(tenant=self.tenant, isDeleted=False)
        update_response = self.client.post(
            reverse('homeApp:update_purchase_return_voucher_api'),
            data={
                'voucherId': str(voucher.pk),
                'entryDate': '25/03/2026',
                'warehouseId': str(warehouse.pk),
                'purchaseInvoiceId': str(purchase.pk),
                'party': str(self.party.pk),
                'remark': 'Only one bag return',
                'lineItems': self._line_items(1, unit_cost=90.0, unit_price=0.0),
            },
        )
        self.assertEqual(update_response.status_code, 200, msg=getattr(update_response, 'content', b''))
        self.assertEqual(update_response.json()['message'], 'success')
        stock.refresh_from_db()
        self.assertEqual(stock.quantityOnHand, 7.0)

    def test_sales_return_increases_stock(self):
        warehouse = InventoryWarehouse.objects.create(tenant=self.tenant, name='Main', isDefault=True)
        self.client.post(
            reverse('homeApp:add_inventory_opening_stock_api'),
            data={
                'entryDate': '25/03/2026',
                'warehouseId': str(warehouse.pk),
                'lineItems': self._line_items(7, unit_cost=75.0, unit_price=0.0),
            },
        )
        sale_response = self.client.post(
            reverse('homeApp:add_sales_by_admin_api'),
            data={
                'invoiceSeriesId': str(self.sales_series.pk),
                'paymentMode': 'Cash',
                'colDate': '25/03/2026',
                'party': f'{self.party.name} @ {self.party.pk}',
                'remark': 'returnable sale',
                'lineItems': self._line_items(4, unit_cost=0.0, unit_price=120.0),
            },
        )
        self.assertEqual(sale_response.status_code, 200, msg=getattr(sale_response, 'content', b''))
        sale = Sales.objects.get(remark='returnable sale')

        response = self.client.post(
            reverse('homeApp:add_sales_return_voucher_api'),
            data={
                'entryDate': '25/03/2026',
                'warehouseId': str(warehouse.pk),
                'salesId': str(sale.pk),
                'party': str(self.party.pk),
                'remark': 'Customer return',
                'lineItems': self._line_items(2, unit_cost=0.0, unit_price=120.0),
            },
        )
        self.assertEqual(response.status_code, 200, msg=getattr(response, 'content', b''))
        self.assertEqual(response.json()['message'], 'success')
        self.assertTrue(SalesReturnVoucher.objects.filter(tenant=self.tenant, isDeleted=False).exists())
        stock = InventoryStock.objects.get(tenant=self.tenant, item=self.item, stockBucket=InventoryItem.StockBucket.GST)
        self.assertEqual(stock.quantityOnHand, 5.0)
        ledger = InventoryLedgerEntry.objects.get(tenant=self.tenant, item=self.item, sourceType=InventoryLedgerEntry.SourceType.SALES_RETURN)
        self.assertEqual(ledger.quantityDelta, 2.0)

        voucher = SalesReturnVoucher.objects.get(tenant=self.tenant, isDeleted=False)
        update_response = self.client.post(
            reverse('homeApp:update_sales_return_voucher_api'),
            data={
                'voucherId': str(voucher.pk),
                'entryDate': '25/03/2026',
                'warehouseId': str(warehouse.pk),
                'salesId': str(sale.pk),
                'party': str(self.party.pk),
                'remark': 'Only one item accepted back',
                'lineItems': self._line_items(1, unit_cost=0.0, unit_price=120.0),
            },
        )
        self.assertEqual(update_response.status_code, 200, msg=getattr(update_response, 'content', b''))
        self.assertEqual(update_response.json()['message'], 'success')
        stock.refresh_from_db()
        self.assertEqual(stock.quantityOnHand, 4.0)
