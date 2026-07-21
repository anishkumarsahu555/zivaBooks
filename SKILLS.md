---
name: ziva-books
description: Comprehensive development skill and architectural reference for the zivaBooks multi-tenant business operations application (Django 6.0, Python 3.14, SQLite/PostgreSQL, Redis, Celery). Use when developing, refactoring, testing, or debugging features, APIs, services, templates, or domain models in zivaBooks.
---

# zivaBooks Agent Skill & Developer Guide

`zivaBooks` is a multi-tenant Django enterprise management platform covering tenant-scoped sales, collections (with OTP verification), purchase management, orders, inventory & stock ledgers (FEFO batch tracking), cash counter billing, accounting books (cashbook, bankbook, party ledger), staff permissions, dynamic dashboards, licensing/activation middleware, and AI assistant capabilities.

---

## 1. Technology Stack & Environment

- **Language & Framework**: Python `>=3.14`, Django `>=6.0.3`
- **Package & Dependency Manager**: `uv` (standard command prefix: `uv run python manage.py <command>`)
- **Databases**:
  - **Development**: SQLite (`db.sqlite3` in project root)
  - **Production**: PostgreSQL (`psycopg>=3.2.10`)
- **Cache & Message Broker**: Redis (`django-redis>=6.0.0`, Celery `>=5.4.0`)
- **Task Queue**: Celery (runs tasks synchronously during test runs with `CELERY_TASK_ALWAYS_EAGER = True`)
- **Static & Media Serving**: WhiteNoise (`CompressedManifestStaticFilesStorage`), custom media serve route
- **Document Generation**: WeasyPrint (PDF generation), `openpyxl` (Excel reports)
- **PWA & Image Processing**: `django-pwa`, `django-stdimage`
- **AI Integration**: OpenAI SDK / Ollama (`home/apis/ziva_ai.py`, `home/services/ai_context.py`)

---

## 2. Directory Structure & App Responsibilities

```text
zivaBooks/
├── zivaBooks/               # Core project configuration (settings.py, urls.py, health.py, wsgi.py, asgi.py)
├── home/                    # Primary domain app (models, views, apis, services, navigation, tests)
│   ├── apis/                # Domain API endpoints (sales, collections, orders, inventory, books, etc.)
│   ├── services/            # Pure business logic and domain service modules
│   ├── management/commands/ # Management commands (seed_mock_data, seed_roles_permissions, backfills)
│   ├── templatetags/        # Custom template filters (formatting.py)
│   └── tests/               # Comprehensive automated unit & integration test suite (40+ test files)
├── inventory/               # Inventory domain sub-app (api.py, services.py, views.py, models.py)
├── activation/              # Licensing & tenant activation app (middleware.py, models.py, views.py)
├── utils/                   # Shared infrastructure utilities (logger.py)
├── templates/               # Global HTML template hierarchy (Django templates + PWA shell)
├── static/ & staticfiles/   # Static assets (JS, CSS, PWA service worker, icons)
├── media/                   # Tenant uploaded media & invoice attachments
└── manage.py                # Django CLI entry point
```

---

## 3. Core Architectural Patterns & Code Boundaries

### 3.1 Base Model Inheritance
All domain models in `home/models.py` follow a strict multi-tenant inheritance hierarchy:
1. `TimeStampedModel`: Provides `created_at` and `updated_at`.
2. `AuditedModel`: Tracks `created_by` and `updated_by` via thread-local context (`AuditUserMiddleware`).
3. `TenantAwareModel`: Combines timestamping + auditing and adds `tenant = ForeignKey('Tenant')`.
4. `SoftDeleteModel`: Provides `is_deleted` and `deleted_at`.
5. `TenantOwnedModel`: Inherits from `TenantAwareModel` and `SoftDeleteModel`. **Use `TenantOwnedModel` for all new multi-tenant data entities.**

### 3.2 Tenant Isolation Rule
- **Mandatory**: Every database query, cache key, and business logic execution MUST be scoped to the current active tenant (`request.tenant` or `tenant_id`).
- Never perform unscoped `Model.objects.all()` or `filter(id=x)` lookups without including `tenant=request.tenant`.

### 3.3 Service vs API Layer Separation (Maintainability Guidelines)
Refer to [MAINTAINABILITY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MAINTAINABILITY.md) for refactoring boundaries:
- **`home/services/`**: Contains pure python domain logic, permission checks, line item parsers, inventory mutation helpers, export context generators, and GST calculation formulas.
- **`home/apis/`**: Contains HTTP request handling, DataTables integrations, endpoint wrappers, JSON responses, and permissions validation.
- **Refactoring Rule**: Domain API modules in `home/apis/` MUST import business rules from `home/services/`. **Do NOT import shared helpers from `home/apiView.py` in new code.**

---

## 4. Key Services & Domain APIs Map

### 4.1 Domain Services (`home/services/`)
- `collections.py`: Collection permissions, payment mode validation, date-preserving timestamp helpers, list shaping.
- `orders.py`: Order permissions, transition state maps, sale-link rules, edit/delete guards.
- `sales.py` & `proforma.py`: Sales/proforma mode parsing, bank/party validation, base querysets.
- `invoices.py`: GST state normalization, GST breakup calculation, shared buyer invoice fields, payload totals.
- `inventory_lines.py`: Line item parsing, stock-bucket validation, FEFO batch resolution, non-GST feature gates.
- `inventory_mutations.py`: Line writes for sales, proforma, purchases, cash-counter, stock rebuilds, return vouchers.
- `inventory_catalog.py`: Inventory category, unit, and tax-profile parsing and master data payload application.
- `cash_counter.py` & `cash_counter_invoicing.py`: Cash counter permissions, return-source parsing, invoice payload application.
- `sequences.py`: Tenant-safe sequence generation (sales, proforma, voucher numbers).
- `reports.py`: Currency/INR formatting (`format_inr`), safe sheet titles, PDF context builders, XLSX generator wrappers.
- `messages.py`: WhatsApp message dispatch wrappers and status loggers.
- `books.py`: Cashbook & bankbook permission checks, running statement row calculations.
- `party_ledger_reports.py`: Customer ledger date parsing, voucher display formatters, running ledger calculations.
- `attendance.py`: Monthly attendance matrix builders, geocoding helpers, image URL helpers.
- `audit_reports.py`: Audit log scoping, datetime formatting, detail payload shaping.
- `analytics.py`: Trend bucketing, financial percentage metrics, report date range parsing.
- `tenant_settings.py`: Non-GST shadow access validation, feature flag toggles.
- `dashboard_summaries.py`: Tenant-scoped summary metrics (admin, staff, party, bank, sales, orders).
- `ai_context.py`: AI contextual prompt construction and business state summarizer for `ziva_ai.py`.

### 4.2 Domain APIs (`home/apis/`)
- `sales.py` & `sales_reports.py`: Sales CRUD, approvals, search, daily sales PDF/XLSX exports, thermal challans.
- `proforma.py` & `proforma_reports.py`: Proforma CRUD, proforma-to-sales conversion, PDF/XLSX exports.
- `collections.py` & `cash_counter_collections.py`: OTP request/verify, collection posting, approval, cheque reminders, list exports.
- `orders.py`: Order CRUD, delivery OTP verification, attachment deletion, status updates, order PDF generation.
- `cash_counter.py`: Thermal printing, invoice lookup, counter datatables, return-source invoice lookups.
- `inventory_stock.py`, `inventory_purchases.py`, `inventory_returns.py`, `inventory_catalog.py`, `inventory_reports.py`:
  - Stock ledger, batch expiry reports, stock adjustments, warehouse transfers, expired batch deactivations, purchase invoices, sales/purchase returns.
- `books.py` & `book_reports.py`: Manual book transactions, cash/bank transfers, cashbook & bankbook PDF/XLSX exports.
- `party_ledger_reports.py`: Party ledger DataTables, customer-facing ledger PDF/XLSX exports.
- `attendance.py` & `attendance_reports.py`: Attendance marking, staff location tracking, daily/monthly PDF/XLSX reports.
- `messages.py` & `audit.py`: WhatsApp message log, resend API, audit log DataTables and CSV/XLSX exports.
- `admin_master.py`: CRUD for staff groups, staff users, party groups, parties, banks, invoice series.
- `dashboard_summaries.py`: Summary counts and card payloads for tenant control panels.
- `ziva_ai.py`: Conversational AI query processing with project domain context.

---

## 5. Licensing & Middleware Architecture

### 5.1 `TenantActivationMiddleware` (`activation/middleware.py`)
Checks `Validity` and `EcomValidity` models for the active tenant:
- **Valid Tenant**: Access allowed; shows license validity date on dashboard.
- **Expired Tenant**:
  - **HTML Page Requests**: Silently redirects to `/activate/` (unless requesting an allowlisted path).
  - **JSON API Requests (`/api/`)**: Returns HTTP `403` JSON payload:
    ```json
    {"message": "license_expired", "redirect": "/activate/"}
    ```

### 5.2 Expired License Allowlisted Paths
The following path prefixes remain operational even when a tenant license is expired:
- `/home/` (loads expired notification banner)
- `/` (login screen)
- `/postLogin/` & `/logout/`
- `/activate/` (license activation form)
- `/admin/*` (Django admin interface)
- `/static/*` & `/media/*`

---

## 6. Common Development Commands

### 6.1 Setup & Local Execution
```bash
# Apply database migrations
uv run python manage.py migrate

# Seed required system roles and default permissions
uv run python manage.py seed_roles_permissions

# Seed mock tenant data for local testing/development
uv run python manage.py seed_mock_data

# Start local Django development server
uv run python manage.py runserver
```

### 6.2 Running Automated Tests
```bash
# Run full test suite
uv run python manage.py test

# Run tests for specific apps or modules
uv run python manage.py test home
uv run python manage.py test activation
uv run python manage.py test home.tests.test_inventory_purchase_sales
uv run python manage.py test home.tests.test_api_crud_parties
```

### 6.3 Maintenance & Backfill Commands
```bash
# Backfill financial books for historical transactions
uv run python manage.py backfill_books

# Backfill party ledgers
uv run python manage.py backfill_party_ledger
```

---

## 7. Testing & Quality Assurance Guidelines

When writing or modifying code in `zivaBooks`, adhere to these QA rules:
1. **Tenant Isolation**: Always verify multi-tenant boundaries (ensure Tenant A cannot view or edit Tenant B's data).
2. **Permission Checks**: Ensure role permissions (`add_collection`, `can_approve_collection`, `view_party`, etc.) are explicitly verified using decorators or service helpers.
3. **OTP Workflows**: Verify OTP generation, TTL expiration, customer portal visibility, and verification matching before posting collections or order deliveries.
4. **Inventory & Accounting Integrity**:
   - Stock movements must create appropriate `InventoryLedgerEntry` records.
   - Batch deductions must obey FEFO (First-Expired, First-Out).
   - Sales, purchases, returns, and cash-counter transactions must correctly update running totals in cashbook, bankbook, and party ledgers.
5. **Regression Verification**: Always execute `uv run python manage.py test` to ensure zero regressions across existing test cases outlined in [TESTCASES.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/TESTCASES.md).
