# zivaBooks Database Schema & Data Model Specification

This document provides a detailed overview of the database models, relationships, field definitions, indexes, and soft-delete mechanisms in **zivaBooks**.

---

## 1. Core Model Inheritance Architecture

```text
┌──────────────────────────────────────────────────────────┐
│                    TimeStampedModel                      │
│   created_at (DateTimeField), updated_at (DateTimeField) │
└────────────────────────────┬─────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────┐
│                      AuditedModel                        │
│   created_by (FK User), updated_by (FK User)             │
└────────────────────────────┬─────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────┐
│                    TenantAwareModel                      │
│   tenant (FK Tenant)                                     │
└────────────────────────────┬─────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────┐
│                     SoftDeleteModel                      │
│   is_deleted (BooleanField), deleted_at (DateTimeField)  │
└────────────────────────────┬─────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────┐
│                    TenantOwnedModel                      │
│   All zivaBooks domain models inherit from this class!   │
└──────────────────────────────────────────────────────────┘
```

---

## 2. Domain Model Subsystems

### 2.1 Multi-Tenant Core & Staff Administration

```mermaid
erDiagram
    Tenant ||--o{ TenantMembership : has
    User ||--o{ TenantMembership : belongs_to
    Tenant ||--o{ StaffGroup : defines
    Tenant ||--o{ StaffUser : manages
    StaffGroup ||--o{ StaffUser : assigns
    
    Tenant {
        int id PK
        string name
        string code
        boolean is_active
    }
    StaffGroup {
        int id PK
        string name
        json permissions
    }
    StaffUser {
        int id PK
        string username
        string phone
        string role
    }
```

- **`Tenant`**: Root tenant model storing organization name, tenant code, active state, and configuration flags.
- **`TenantMembership`**: Links Django `User` accounts to `Tenant`s with default flags (`is_default`).
- **`StaffGroup`**: Custom group definition containing role permission sets mapped to Django `Permission` objects.
- **`StaffUser`**: Tenant staff profile linked to `User` and `StaffGroup`.

---

### 2.2 Counterparties & Financial Master Data

```mermaid
erDiagram
    Tenant ||--o{ PartyGroup : categories
    PartyGroup ||--o{ Party : contains
    Tenant ||--o{ Bank : owns
    
    Party {
        int id PK
        string name
        string phone
        string gstin
        string party_type "CUSTOMER / SUPPLIER"
        decimal opening_balance
    }
    Bank {
        int id PK
        string bank_name
        string account_number
        string ifsc_code
        decimal opening_balance
    }
```

- **`PartyGroup`**: Categorizes parties (e.g. Retailers, Wholesalers, Distributors).
- **`Party`**: Customer or vendor master record with credit limits, GSTIN, and opening balance.
- **`Bank`**: Tenant bank account entity storing branch, account number, and balances.

---

### 2.3 Inventory & Stock Management

```mermaid
erDiagram
    Tenant ||--o{ InventoryItem : owns
    Tenant ||--o{ InventoryWarehouse : operates
    InventoryItem ||--o{ InventoryStock : aggregates
    InventoryWarehouse ||--o{ InventoryStock : stores
    InventoryItem ||--o{ InventoryBatch : tracks
    InventoryBatch ||--o{ InventoryBatchStock : holds
    InventoryWarehouse ||--o{ InventoryBatchStock : locates
    InventoryItem ||--o{ InventoryLedgerEntry : logs
    
    InventoryItem {
        int id PK
        string name
        string sku
        decimal default_selling_price
        decimal default_purchase_price
    }
    InventoryBatch {
        int id PK
        string batch_number
        date expiry_date
        date mfg_date
    }
    InventoryLedgerEntry {
        int id PK
        string voucher_type "PURCHASE / SALE / RETURN / ADJUSTMENT"
        decimal quantity_in
        decimal quantity_out
    }
```

- **`InventoryStock`**: Current total stock quantity per item per warehouse.
- **`InventoryBatchStock`**: Current available stock quantity per item, per warehouse, per specific batch.
- **`InventoryLedgerEntry`**: Immutable transaction audit log tracking all physical inventory entries and exits.

---

### 2.4 Sales, Proforma & Purchasing

```mermaid
erDiagram
    Party ||--o{ Sales : bills
    Sales ||--o{ SalesLine : contains
    InventoryItem ||--o{ SalesLine : references
    
    Sales {
        int id PK
        string invoice_number
        date invoice_date
        decimal subtotal
        decimal tax_amount
        decimal total_amount
        string status "DRAFT / APPROVED / CANCELLED"
    }
    SalesLine {
        int id PK
        decimal quantity
        decimal unit_price
        decimal tax_rate
        decimal total_price
    }
```

- **`Sales` & `SalesLine`**: Tax invoice headers and line items.
- **`ProformaInvoice` & `ProformaInvoiceLine`**: Estimates/quotes that convert into Sales Invoices.
- **`PurchaseInvoice` & `PurchaseLine`**: Inward stock purchase vouchers.
- **`CashCounter` & `CashCounterLine`**: POS counter sales transactions.

---

### 2.5 Licensing & Tenant Validity

```mermaid
erDiagram
    Tenant ||--o{ Validity : tracks
    
    Validity {
        int id PK
        date activationDate
        date expiryDate
        string activationType "TRIAL / ANNUAL / LIFETIME"
    }
```

- **`Validity`**: Records activation dates and expiry bounds evaluated dynamically by `TenantActivationMiddleware`.

---

## 3. Indexing Strategy & Performance Rules

All models inheriting from `TenantOwnedModel` implement compound indexes containing `tenant` to ensure high query performance:

```python
class Meta:
    indexes = [
        models.Index(fields=['tenant', 'is_deleted'], name='idx_tenant_deleted'),
        models.Index(fields=['tenant', 'created_at'], name='idx_tenant_created'),
    ]
```

**Database Performance Rules**:
1. **Always index `tenant`**: Queries MUST include `tenant_id` in compound indexes.
2. **Never delete rows directly**: Use soft deletion (`is_deleted=True`, `deleted_at=timezone.now()`) to maintain audit integrity.
