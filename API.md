# zivaBooks API Reference & Specification

This document details the public and internal REST/JSON API contracts, authentication rules, error handling specifications, and request/response payloads for **zivaBooks**.

---

## 1. Authentication & Common Standards

### 1.1 Authentication & Session
- **Session Authentication**: All standard API endpoints require an active session cookie (`sessionid`) authenticated via `/postLogin/`.
- **CSRF Protection**: State-changing endpoints (`POST`, `PUT`, `DELETE`) require the `X-CSRFToken` header.
- **Tenant Context**: The active tenant is automatically resolved from `request.user.tenant` or the user's active `TenantMembership`.

### 1.2 Global Error Responses

#### A. License Expired (`HTTP 403 Forbidden`)
Returned when the tenant's license validity has expired (`TenantActivationMiddleware`):
```json
{
  "message": "license_expired",
  "redirect": "/activate/"
}
```

#### B. Permission Denied (`HTTP 403 Forbidden`)
Returned when the logged-in user lacks the required permission bundle:
```json
{
  "status": "error",
  "message": "Permission denied."
}
```

#### C. Validation Error (`HTTP 400 Bad Request`)
```json
{
  "status": "error",
  "message": "Invalid payment mode or missing required parameter: party_id"
}
```

---

## 2. Core API Endpoint Catalog

### 2.1 Sales & Invoicing APIs (`home/apis/sales.py`, `home/apis/proforma.py`)

#### `POST /api/add_sales_api/`
Creates a new sales invoice and records double-entry inventory deductions.

**Request Payload**:
```json
{
  "party_id": 12,
  "invoice_date": "2026-07-21",
  "items": [
    {
      "item_id": 45,
      "warehouse_id": 2,
      "quantity": 10,
      "unit_price": 250.00,
      "tax_rate": 18.0
    }
  ],
  "payment_mode": "bank",
  "bank_id": 3,
  "amount_paid": 2950.00
}
```

**Response Payload**:
```json
{
  "status": "success",
  "message": "Sale created successfully",
  "data": {
    "sales_id": 1042,
    "invoice_number": "INV-2026-0089",
    "total_amount": 2950.00
  }
}
```

#### `POST /api/convert_proforma_to_sales_api/`
Converts an existing proforma invoice into an official sales invoice.

**Request Payload**:
```json
{
  "proforma_id": 88
}
```

---

### 2.2 Collections & OTP Verification APIs (`home/apis/collections.py`)

#### `POST /api/request_collection_otp_api/`
Generates a time-bound 6-digit collection OTP for verification.

**Request Payload**:
```json
{
  "party_id": 12,
  "amount": 5000.00,
  "payment_mode": "cash"
}
```

**Response Payload**:
```json
{
  "status": "success",
  "otp": "849201",
  "expires_in_seconds": 600
}
```

#### `POST /api/verify_collection_otp_api/`
Verifies the customer OTP entered by a collector and posts the collection.

**Request Payload**:
```json
{
  "party_id": 12,
  "otp": "849201",
  "amount": 5000.00,
  "payment_mode": "cash"
}
```

---

### 2.3 POS Cash Counter APIs (`home/apis/cash_counter.py`)

#### `POST /api/add_cash_counter_entry_api/`
Creates a cash counter POS billing transaction with immediate stock deduction.

**Request Payload**:
```json
{
  "customer_name": "Walk-in Customer",
  "phone": "9876543210",
  "items": [
    {"item_id": 45, "quantity": 2, "unit_price": 100.00}
  ],
  "amount_paid": 200.00
}
```

---

### 2.4 Inventory & Stock APIs (`home/apis/inventory_stock.py`)

#### `GET /api/inventory/stock_ledger/`
Returns the double-entry stock transaction ledger filtered by warehouse and date.

**Query Parameters**:
- `item_id` (optional): Filter by item.
- `warehouse_id` (optional): Filter by warehouse.
- `start_date` & `end_date`: YYYY-MM-DD.

---

### 2.5 Accounting Books & Ledger APIs (`home/apis/books.py`)

#### `GET /api/get_bank_balances_api/`
Returns current bank account balances for all banks belonging to the active tenant.

**Response Payload**:
```json
{
  "status": "success",
  "banks": [
    {"id": 1, "bank_name": "HDFC Bank", "account_number": "XXXX1234", "balance": 154200.50},
    {"id": 2, "bank_name": "ICICI Bank", "account_number": "XXXX5678", "balance": 87500.00}
  ]
}
```

---

### 2.6 Attendance & Staff Location APIs (`home/apis/attendance.py`)

#### `POST /api/add_attendance/`
Marks staff attendance with reverse-geocoded GPS coordinates and selfie capture.

**Request Payload**:
```json
{
  "latitude": 17.440081,
  "longitude": 78.348915,
  "location_address": "HITEC City, Hyderabad",
  "status": "PRESENT"
}
```

---

### 2.7 Ziva AI Assistant API (`home/apis/ziva_ai.py`)

#### `POST /api/ziva_ai/chat/`
Sends a query to the AI assistant using tenant-scoped operational context.

**Request Payload**:
```json
{
  "message": "What were the top 3 selling items this month?"
}
```

**Response Payload**:
```json
{
  "status": "success",
  "response": "Based on your tenant's sales records for this month, your top 3 selling items are:\n1. Widget A (150 units)\n2. Item B (95 units)\n3. Product C (80 units)"
}
```
