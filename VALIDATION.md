# zivaBooks — Validation, Testing & QA Verification Guide

This document defines the validation strategy, automated test suites, QA checklists, and pre-deployment verification criteria for **zivaBooks**.

---

## 1. Automated Test Architecture

The automated test suite in `zivaBooks` covers tenant isolation, API smoke tests, financial ledger math, FEFO stock mutations, security permissions, and license plan enforcement.

### 1.1 Running the Automated Test Suite

```bash
# Run all tests
uv run python manage.py test

# Run tests with detailed verbosity
uv run python manage.py test -v 2

# Run specific functional areas
uv run python manage.py test home.tests.test_license_plans
uv run python manage.py test home.tests.test_functional_end_to_end_flows
uv run python manage.py test home.tests.test_cache_tenant_isolation
```

---

## 2. Test Suite Catalog

| Test Module | Focus Area | Key Verifications |
|---|---|---|
| `test_api_smoke_all_endpoints.py` | API Surface Coverage | Tests HTTP 200/403 status codes across all registered API endpoints. |
| `test_smoke_seeded_tenants.py` | Seed Reliability | Validates database consistency after running `seed_mock_data`. |
| `test_cache_tenant_isolation.py` | Caching Security | Asserts that cached Redis keys do not cross tenant boundaries. |
| `test_license_plans.py` | Licensing & Permissions | Validates dynamic `LicensePlan` creation, seat limits, and permission sync to `{slug}:Tenant Admin`. |
| `activation/tests/test_activation_allowlist.py` | License Middleware | Validates that expired tenants can access allowlisted routes (`/home/`, `/activate/`) but are blocked on operational routes. |
| `test_functional_end_to_end_flows.py` | Business Flows | Validates the complete cycle: Sales Invoice -> Cash Collection -> OTP Verification -> Ledger Update. |
| `test_inventory_purchase_sales.py` | FEFO Stock Mutations | Verifies batch creation on purchase and automated oldest-expiry batch consumption on sales billing. |
| `test_cash_counter_books.py` | POS & Cashbooks | Tests cash counter opening, session sales accumulation, and closing handoff into cashbook. |
| `test_cash_counter_party_ledger.py` | Party Ledgers | Asserts that counter transactions correctly debit and credit customer balances. |
| `test_ziva_ai.py` | AI Assistant | Validates monthly quota decrementing and plan-based monthly reset logic. |
| `test_attendance.py` & `test_staff_location.py` | Field Operations | Tests geolocation coordinate logging, reverse geocoding, and map rendering permissions. |
| `test_party_ledger_pdf.py` & `test_bankbook_pdf.py` | Document Engine | Tests WeasyPrint A4 PDF rendering to prevent crashes caused by missing fonts or invalid styles. |

---

## 3. Manual QA Verification Protocols

### Protocol QA-01: Multi-Tenant Data Isolation
- **Objective**: Verify that User from Tenant A cannot observe or manipulate data from Tenant B.
- **Steps**:
  1. Login as `admin@tenant-a.com` in Chrome; login as `admin@tenant-b.com` in Firefox.
  2. In Tenant A, create a new customer party "Alpha Distributors" and note its database ID (`#101`).
  3. In Tenant B, attempt to request `/api/get_party_detail/?party_id=101`.
- **Expected Result**: API returns `404 Not Found` or `403 Forbidden`. Tenant B's party list remains completely unaffected.

### Protocol QA-02: FEFO Batch Inventory Allocation
- **Objective**: Ensure the billing engine automatically consumes the batch with the earliest expiry date.
- **Steps**:
  1. Create product "Paracetamol 500mg".
  2. Receive Purchase Batch 1: Batch `B-001`, Expiry `2027-01-01`, Qty `50`.
  3. Receive Purchase Batch 2: Batch `B-002`, Expiry `2026-12-01`, Qty `50`.
  4. Generate a Sales Invoice for `60` units of "Paracetamol 500mg".
- **Expected Result**: System automatically consumes `50` units from Batch `B-002` (earlier expiry) and `10` units from Batch `B-001`. Stock ledger confirms both deductions.

### Protocol QA-03: Dual-Party OTP Collection Workflow
- **Objective**: Validate that a collection cannot be marked as verified without the payer's OTP.
- **Steps**:
  1. Open a Cash Counter and initiate a new collection voucher of `₹ 5,000` against customer "Radha Stores".
  2. Note the generated OTP in test logs or simulator.
  3. Enter an incorrect OTP (`9999`) and submit.
  4. Enter the correct OTP and submit.
- **Expected Result**: Incorrect OTP returns an error and keeps voucher in `PENDING_OTP` state. Correct OTP marks collection as `APPROVED`, updates customer ledger, and credits the counter drawer.

### Protocol QA-04: Non-GST Shadow Mode Security
- **Objective**: Verify that Non-GST accounting records are protected behind the 6-digit access PIN.
- **Steps**:
  1. Navigate to Settings -> Non-GST Shadow Mode.
  2. Verify that shadow menus and inventory items are invisible by default.
  3. Set a 6-digit access PIN (`123456`) and unlock shadow mode.
  4. Create a non-GST transaction.
  5. Lock shadow mode or logout.
- **Expected Result**: All non-GST records and menus disappear from navigation and standard reports.

### Protocol QA-05: License Expiration Graceful Degradation
- **Objective**: Ensure expired tenants receive clear activation prompts without breaking the application shell.
- **Steps**:
  1. In Django Admin, set the tenant's `Validity.expiryDate` to yesterday's date.
  2. Refresh `/home/`.
  3. Attempt to access `/customer_home/` or `/inventory/`.
- **Expected Result**: `/home/` renders with warning banner *"Activation expired. Please contact ZivaInfotech for activation."* Navigating to `/inventory/` cleanly redirects to `/activate/`. Static assets and login/logout remain accessible.

---

## 4. Pre-Deployment Release Checklist

Before tagging and deploying a new release to production:

- [ ] All automated tests pass: `uv run python manage.py test`.
- [ ] No migrations are unapplied: `uv run python manage.py showmigrations`.
- [ ] Django check reports zero errors: `uv run python manage.py check --deploy`.
- [ ] Static files collect cleanly: `uv run python manage.py collectstatic --noinput`.
- [ ] Database backup completed.
- [ ] Health check endpoint `/health/` returns `{"status": "healthy"}`.
- [ ] Redis and Celery workers are operational.
- [ ] Verification of allowlisted URLs on expired license states.
