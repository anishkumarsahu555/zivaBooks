from __future__ import annotations

import json
from datetime import date
from django.test import TestCase
from django.urls import reverse

from home.models import (
    InventoryCategory,
    InventoryUnit,
    InventoryTaxProfile,
    InventoryWarehouse,
    InventoryItem,
    InventoryAdjustmentVoucher,
    InventoryAdjustmentLine,
    InventoryTransferVoucher,
    InventoryTransferLine,
    InventoryStock,
    InventoryLedgerEntry,
    PurchaseLine,
    SalesLine,
    CashCounterLine,
    PurchaseReturnLine,
    SalesReturnLine,
    InventoryOpeningLine,
    InventoryOpeningVoucher,
    PurchaseInvoice,
    Sales,
    CashCounter,
    PurchaseReturnVoucher,
    SalesReturnVoucher,
)
from home.tests.utils import SeededTenantMixin


class InventoryApisIntegrationTests(SeededTenantMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.tenant.inventoryEnabled = True
        self.tenant.save(update_fields=['inventoryEnabled', 'lastUpdatedOn'])

        # Clear transactional tables first to avoid ProtectedError when clearing masters
        PurchaseLine.objects.filter(tenant=self.tenant).delete()
        SalesLine.objects.filter(tenant=self.tenant).delete()
        CashCounterLine.objects.filter(tenant=self.tenant).delete()
        PurchaseReturnLine.objects.filter(tenant=self.tenant).delete()
        SalesReturnLine.objects.filter(tenant=self.tenant).delete()
        InventoryAdjustmentLine.objects.filter(tenant=self.tenant).delete()
        InventoryTransferLine.objects.filter(tenant=self.tenant).delete()
        InventoryOpeningLine.objects.filter(tenant=self.tenant).delete()
        InventoryLedgerEntry.objects.filter(tenant=self.tenant).delete()
        InventoryStock.objects.filter(tenant=self.tenant).delete()

        # Delete parent vouchers/transactions
        PurchaseInvoice.objects.filter(tenant=self.tenant).delete()
        Sales.objects.filter(tenant=self.tenant).delete()
        CashCounter.objects.filter(tenant=self.tenant).delete()
        PurchaseReturnVoucher.objects.filter(tenant=self.tenant).delete()
        SalesReturnVoucher.objects.filter(tenant=self.tenant).delete()
        InventoryOpeningVoucher.objects.filter(tenant=self.tenant).delete()
        InventoryAdjustmentVoucher.objects.filter(tenant=self.tenant).delete()
        InventoryTransferVoucher.objects.filter(tenant=self.tenant).delete()

        # Clear master tables
        InventoryItem.objects.filter(tenant=self.tenant).delete()
        InventoryCategory.objects.filter(tenant=self.tenant).delete()
        InventoryUnit.objects.filter(tenant=self.tenant).delete()
        InventoryTaxProfile.objects.filter(tenant=self.tenant).delete()
        InventoryWarehouse.objects.filter(tenant=self.tenant).delete()

        # Create a default warehouse
        self.warehouse = InventoryWarehouse.objects.create(
            tenant=self.tenant,
            name="Main Store",
            code="MAIN",
            isDefault=True,
            isActive=True,
        )

        # Create a test item to be used for voucher tests
        self.category = InventoryCategory.objects.create(
            tenant=self.tenant,
            name="Construction",
        )
        self.unit = InventoryUnit.objects.create(
            tenant=self.tenant,
            name="Bags",
            symbol="bag",
        )
        self.tax_profile = InventoryTaxProfile.objects.create(
            tenant=self.tenant,
            name="Standard Tax",
            hsnCode="2523",
            gstRate=18.0,
        )
        self.item = InventoryItem.objects.create(
            tenant=self.tenant,
            name="API Cement",
            sku="API-CEM",
            category=self.category,
            unit=self.unit,
            taxProfile=self.tax_profile,
            defaultStockBucket=InventoryItem.StockBucket.GST,
            gstRate=18.0,
        )

    def test_category_crud_apis(self):
        """
        Verify Category creation, listing, updating, and deleting via APIs.
        """
        # List categories (should contain the one we created in setUp)
        response = self.client.get(reverse('homeApp:list_inventory_categories_api'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')
        self.assertEqual(len(response.json()['data']), 1)
        self.assertEqual(response.json()['data'][0]['Name'], 'Construction')

        # Add category
        response = self.client.post(
            reverse('homeApp:add_inventory_category_api'),
            data={
                'name': 'Electricals',
                'code': 'ELEC',
                'description': 'Wires, switches, bulbs',
                'isActive': '1',
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')
        new_cat_id = response.json()['categoryId']

        # Verify added category exists
        new_cat = InventoryCategory.objects.get(pk=new_cat_id)
        self.assertEqual(new_cat.name, 'Electricals')
        self.assertEqual(new_cat.code, 'ELEC')

        # Update category
        response = self.client.post(
            reverse('homeApp:update_inventory_category_api'),
            data={
                'categoryId': str(new_cat_id),
                'name': 'Electrical Goods',
                'code': 'ELEC-GOODS',
                'description': 'Updated wires & bulbs description',
                'isActive': '1',
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')

        new_cat.refresh_from_db()
        self.assertEqual(new_cat.name, 'Electrical Goods')
        self.assertEqual(new_cat.code, 'ELEC-GOODS')

        # Delete category
        response = self.client.post(
            reverse('homeApp:delete_inventory_category_api'),
            data={'categoryId': str(new_cat_id)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')

        new_cat.refresh_from_db()
        self.assertTrue(new_cat.isDeleted)

    def test_unit_crud_apis(self):
        """
        Verify Unit creation, listing, updating, and deleting via APIs.
        """
        # List units
        response = self.client.get(reverse('homeApp:list_inventory_units_api'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')
        self.assertEqual(len(response.json()['data']), 1)

        # Add unit
        response = self.client.post(
            reverse('homeApp:add_inventory_unit_api'),
            data={
                'name': 'Kilograms',
                'symbol': 'kg',
                'allowsDecimal': 'true',
                'description': 'Weight unit',
                'isActive': '1',
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')
        new_unit_id = response.json()['unitId']

        new_unit = InventoryUnit.objects.get(pk=new_unit_id)
        self.assertEqual(new_unit.name, 'Kilograms')
        self.assertEqual(new_unit.symbol, 'kg')
        self.assertTrue(new_unit.allowsDecimal)

        # Update unit
        response = self.client.post(
            reverse('homeApp:update_inventory_unit_api'),
            data={
                'unitId': str(new_unit_id),
                'name': 'Kilos',
                'symbol': 'kgs',
                'allowsDecimal': 'false',
                'description': 'Kilo unit',
                'isActive': '1',
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')

        new_unit.refresh_from_db()
        self.assertEqual(new_unit.name, 'Kilos')
        self.assertEqual(new_unit.symbol, 'kgs')
        self.assertFalse(new_unit.allowsDecimal)

        # Delete unit
        response = self.client.post(
            reverse('homeApp:delete_inventory_unit_api'),
            data={'unitId': str(new_unit_id)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')

        new_unit.refresh_from_db()
        self.assertTrue(new_unit.isDeleted)

    def test_tax_profile_crud_apis(self):
        """
        Verify Tax Profile creation, listing, updating, and deleting via APIs.
        """
        # List tax profiles
        response = self.client.get(reverse('homeApp:list_inventory_tax_profiles_api'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')

        # Add tax profile
        response = self.client.post(
            reverse('homeApp:add_inventory_tax_profile_api'),
            data={
                'name': 'Luxury GST',
                'hsnCode': '9999',
                'gstRate': '28.0',
                'cessRate': '12.0',
                'description': 'Luxury Goods Tax',
                'isActive': '1',
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')
        tax_id = response.json()['taxProfileId']

        tax_profile = InventoryTaxProfile.objects.get(pk=tax_id)
        self.assertEqual(tax_profile.name, 'Luxury GST')
        self.assertEqual(tax_profile.gstRate, 28.0)
        self.assertEqual(tax_profile.cessRate, 12.0)

        # Update tax profile
        response = self.client.post(
            reverse('homeApp:update_inventory_tax_profile_api'),
            data={
                'taxProfileId': str(tax_id),
                'name': 'Super Luxury GST',
                'hsnCode': '9999',
                'gstRate': '30.0',
                'cessRate': '15.0',
                'description': 'Updated Luxury Tax',
                'isActive': '1',
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')

        tax_profile.refresh_from_db()
        self.assertEqual(tax_profile.name, 'Super Luxury GST')
        self.assertEqual(tax_profile.gstRate, 30.0)
        self.assertEqual(tax_profile.cessRate, 15.0)

        # Delete tax profile
        response = self.client.post(
            reverse('homeApp:delete_inventory_tax_profile_api'),
            data={'taxProfileId': str(tax_id)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')

        tax_profile.refresh_from_db()
        self.assertTrue(tax_profile.isDeleted)

    def test_warehouse_crud_apis(self):
        """
        Verify Warehouse creation, listing, updating, and deleting via APIs.
        """
        # List warehouses
        response = self.client.get(reverse('homeApp:list_inventory_warehouses_api'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')
        self.assertEqual(len(response.json()['data']), 1)

        # Add warehouse
        response = self.client.post(
            reverse('homeApp:add_inventory_warehouse_api'),
            data={
                'name': 'Secondary Store',
                'code': 'SEC',
                'address': 'Industrial Area Block B',
                'isActive': '1',
                'isDefault': 'false',
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')
        wh_id = response.json()['warehouseId']

        wh = InventoryWarehouse.objects.get(pk=wh_id)
        self.assertEqual(wh.name, 'Secondary Store')
        self.assertEqual(wh.code, 'SEC')
        self.assertFalse(wh.isDefault)

        # Update warehouse
        response = self.client.post(
            reverse('homeApp:update_inventory_warehouse_api'),
            data={
                'warehouseId': str(wh_id),
                'name': 'Secondary Warehouse',
                'code': 'SEC-WH',
                'address': 'Updated Industrial Area',
                'isDefault': 'true',
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')

        wh.refresh_from_db()
        self.assertEqual(wh.name, 'Secondary Warehouse')
        self.assertTrue(wh.isDefault)

        # Delete warehouse
        response = self.client.post(
            reverse('homeApp:delete_inventory_warehouse_api'),
            data={'warehouseId': str(wh_id)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')

        wh.refresh_from_db()
        self.assertTrue(wh.isDeleted)

    def test_adjustment_voucher_crud_apis(self):
        """
        Verify Adjustment Voucher CRUD via APIs.
        """
        # 1. Create adjustment voucher (direction = increase)
        lines = [{
            'itemId': self.item.pk,
            'quantity': 10.0,
            'stockBucket': 'gst',
            'unitCost': 150.0,
            'remark': 'initial adjustment',
        }]
        response = self.client.post(
            reverse('homeApp:add_inventory_adjustment_voucher_api'),
            data={
                'entryDate': '25/03/2026',
                'remark': 'Adjustment stock increase',
                'warehouseId': str(self.warehouse.pk),
                'direction': 'increase',
                'lineItems': json.dumps(lines),
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')
        voucher_id = response.json()['voucherId']
        voucher_number = response.json()['voucherNumber']
        self.assertTrue(voucher_number.startswith('ADJ'))

        # Verify stock and ledger
        stock = InventoryStock.objects.get(tenant=self.tenant, item=self.item, stockBucket=InventoryItem.StockBucket.GST)
        self.assertEqual(stock.quantityOnHand, 10.0)

        ledger_qs = InventoryLedgerEntry.objects.filter(tenant=self.tenant, item=self.item)
        self.assertEqual(ledger_qs.count(), 1)
        self.assertEqual(ledger_qs.first().quantityDelta, 10.0)

        # 2. Get Adjustment Voucher details
        detail_response = self.client.get(
            reverse('homeApp:get_inventory_adjustment_voucher_detail_api'),
            data={'voucherId': str(voucher_id)}
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()['message'], 'success')
        detail_data = detail_response.json()['data']
        self.assertEqual(detail_data['VoucherNumber'], voucher_number)
        self.assertEqual(len(detail_data['Lines']), 1)
        self.assertEqual(detail_data['Lines'][0]['itemId'], self.item.pk)
        self.assertEqual(detail_data['Lines'][0]['quantity'], 10.0)

        # 3. Update adjustment voucher (change quantity to 15)
        updated_lines = [{
            'itemId': self.item.pk,
            'quantity': 15.0,
            'stockBucket': 'gst',
            'unitCost': 155.0,
            'remark': 'revised adjustment',
        }]
        update_response = self.client.post(
            reverse('homeApp:update_inventory_adjustment_voucher_api'),
            data={
                'voucherId': str(voucher_id),
                'entryDate': '25/03/2026',
                'remark': 'Revised adjustment',
                'warehouseId': str(self.warehouse.pk),
                'direction': 'increase',
                'lineItems': json.dumps(updated_lines),
            }
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()['message'], 'success')

        # Verify updated stock
        stock.refresh_from_db()
        self.assertEqual(stock.quantityOnHand, 15.0)

        # 4. List Adjustment Vouchers
        list_response = self.client.get(reverse('homeApp:list_inventory_adjustment_vouchers_api'))
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()['message'], 'success')
        self.assertEqual(len(list_response.json()['data']), 1)

        # 5. Delete Adjustment Voucher
        delete_response = self.client.post(
            reverse('homeApp:delete_inventory_adjustment_voucher_api'),
            data={'voucherId': str(voucher_id)}
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()['message'], 'success')

        # Stock should return to 0.0
        stock.refresh_from_db()
        self.assertEqual(stock.quantityOnHand, 0.0)

        voucher = InventoryAdjustmentVoucher.objects.get(pk=voucher_id)
        self.assertTrue(voucher.isDeleted)

    def test_transfer_voucher_crud_apis(self):
        """
        Verify Transfer Voucher CRUD via APIs.
        """
        # Create a destination warehouse
        dest_warehouse = InventoryWarehouse.objects.create(
            tenant=self.tenant,
            name="Secondary Warehouse",
            code="SEC",
            isActive=True,
        )

        # Add initial stock to MAIN store using adjustment voucher
        adj_lines = [{
            'itemId': self.item.pk,
            'quantity': 20.0,
            'stockBucket': 'gst',
            'unitCost': 150.0,
        }]
        adj_response = self.client.post(
            reverse('homeApp:add_inventory_adjustment_voucher_api'),
            data={
                'entryDate': '25/03/2026',
                'remark': 'Initial stock for transfer test',
                'warehouseId': str(self.warehouse.pk),
                'direction': 'increase',
                'lineItems': json.dumps(adj_lines),
            }
        )
        self.assertEqual(adj_response.status_code, 200)

        # Create transfer voucher (transfer 8 bags from MAIN to SEC)
        transfer_lines = [{
            'itemId': self.item.pk,
            'quantity': 8.0,
            'stockBucket': 'gst',
            'unitCost': 150.0,
            'remark': 'transfer block 1',
        }]
        response = self.client.post(
            reverse('homeApp:add_inventory_transfer_voucher_api'),
            data={
                'entryDate': '25/03/2026',
                'remark': 'Inter-warehouse transfer',
                'fromWarehouseId': str(self.warehouse.pk),
                'toWarehouseId': str(dest_warehouse.pk),
                'lineItems': json.dumps(transfer_lines),
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'success')
        voucher_id = response.json()['voucherId']
        voucher_number = response.json()['voucherNumber']
        self.assertTrue(voucher_number.startswith('TRF'))

        # Ledger entries check: one for TRANSFER_OUT (-8), one for TRANSFER_IN (+8)
        ledger_out = InventoryLedgerEntry.objects.filter(
            tenant=self.tenant,
            item=self.item,
            sourceType=InventoryLedgerEntry.SourceType.TRANSFER_OUT
        )
        self.assertEqual(ledger_out.count(), 1)
        self.assertEqual(ledger_out.first().quantityDelta, -8.0)
        self.assertEqual(ledger_out.first().warehouse_id, self.warehouse.pk)

        ledger_in = InventoryLedgerEntry.objects.filter(
            tenant=self.tenant,
            item=self.item,
            sourceType=InventoryLedgerEntry.SourceType.TRANSFER_IN
        )
        self.assertEqual(ledger_in.count(), 1)
        self.assertEqual(ledger_in.first().quantityDelta, 8.0)
        self.assertEqual(ledger_in.first().warehouse_id, dest_warehouse.pk)

        # 2. Get Transfer Voucher details
        detail_response = self.client.get(
            reverse('homeApp:get_inventory_transfer_voucher_detail_api'),
            data={'voucherId': str(voucher_id)}
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()['message'], 'success')
        detail_data = detail_response.json()['data']
        self.assertEqual(detail_data['VoucherNumber'], voucher_number)
        self.assertEqual(detail_data['FromWarehouseID'], self.warehouse.pk)
        self.assertEqual(detail_data['ToWarehouseID'], dest_warehouse.pk)
        self.assertEqual(len(detail_data['Lines']), 1)
        self.assertEqual(detail_data['Lines'][0]['quantity'], 8.0)

        # 3. Update transfer voucher (change quantity to 5)
        updated_transfer_lines = [{
            'itemId': self.item.pk,
            'quantity': 5.0,
            'stockBucket': 'gst',
            'unitCost': 150.0,
            'remark': 'updated transfer amount',
        }]
        update_response = self.client.post(
            reverse('homeApp:update_inventory_transfer_voucher_api'),
            data={
                'voucherId': str(voucher_id),
                'entryDate': '25/03/2026',
                'remark': 'Updated transfer remark',
                'fromWarehouseId': str(self.warehouse.pk),
                'toWarehouseId': str(dest_warehouse.pk),
                'lineItems': json.dumps(updated_transfer_lines),
            }
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()['message'], 'success')

        # Check ledger update
        self.assertEqual(ledger_out.first().quantityDelta, -5.0)
        self.assertEqual(ledger_in.first().quantityDelta, 5.0)

        # 4. List Transfer Vouchers
        list_response = self.client.get(reverse('homeApp:list_inventory_transfer_vouchers_api'))
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()['message'], 'success')
        self.assertEqual(len(list_response.json()['data']), 1)

        # 5. Delete Transfer Voucher
        delete_response = self.client.post(
            reverse('homeApp:delete_inventory_transfer_voucher_api'),
            data={'voucherId': str(voucher_id)}
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()['message'], 'success')

        voucher = InventoryTransferVoucher.objects.get(pk=voucher_id)
        self.assertTrue(voucher.isDeleted)

        # Ledger entries should go back to 1 (only the initial adjustment)
        self.assertEqual(InventoryLedgerEntry.objects.filter(tenant=self.tenant, item=self.item).count(), 1)
