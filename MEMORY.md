# zivaBooks — Core System Memory & Knowledge Base

This document serves as the persistent system memory and architectural source of truth for **zivaBooks** for human engineers and AI developer agents.

---

## 1. System Identity & Mission

**zivaBooks** is an enterprise-grade, multi-tenant business operations and accounting platform built on **Python 3.14+** and **Django 6.0+**. It is designed for retail distributors, wholesale traders, multi-counter merchants, and enterprises requiring:
- Rigid row-level multi-tenancy.
- First-Expiry-First-Out (**FEFO**) batch inventory tracking.
- Dual-entry cashbook, bankbook, and party ledger accounting.
- POS counter operations with dual-party **OTP verification**.
- Flexible software licensing with dynamic custom plans (25+ granular feature toggles).
- Built-in **Ziva AI** assistant with monthly quota management and tenant context grounding.
- Offline-capable **Progressive Web App (PWA)** interface and thermal/A4 document printing.

---

## 2. Technology Stack & Runtime

| Layer | Component | Details |
|---|---|---|
| **Language Runtime** | Python | `>= 3.14` |
| **Package Manager** | uv | Fast packaging via `pyproject.toml` and `uv.lock` |
| **Web Framework** | Django | `>= 6.0.3` (ASGI/WSGI via Uvicorn & Gunicorn) |
| **Database** | Dev / Prod | SQLite 3 (Dev) / PostgreSQL via `psycopg>=3.2.10` (Prod) |
| **Caching & Queue** | Redis & Celery | `django-redis>=6.0.0`, `celery>=5.4.0` |
| **Static & Media** | WhiteNoise & StdImage | WhiteNoise (`CompressedManifestStaticFilesStorage`), `django-stdimage>=6.0.2` |
| **Document Engine** | PDF & Excel | `weasyprint>=68.1`, `openpyxl>=3.1.5`, `qrcode>=8.2` |
| **Frontend UI** | Django Templates & PWA | HTML5/CSS3, DataTables (`django-datatables-view`), `django-pwa>=2.0.1` |
| **AI Integration** | OpenAI & Ollama | `openai>=2.44.0` / Ollama Local LLM fallback |

---

## 3. Core Architecture & Design Patterns

### 3.1 Model Inheritance Hierarchy
All tenant data is strictly partitioned using row-level isolation via base abstract models:
1. `TimeStampedModel`: Provides `datetime` (auto_now_add) and `lastUpdatedOn` (auto_now).
2. `AuditedModel`: Contains `audit_logs = GenericRelation('home.AuditLog')` and links to thread-local audit events.
3. `TenantAwareModel`: Inherits from `TimeStampedModel` + `AuditedModel`, defines `tenant = models.ForeignKey('Tenant', on_delete=models.CASCADE)`.
4. `SoftDeleteModel`: Provides `isDeleted = models.BooleanField(default=False)`.
5. `TenantOwnedModel`: Inherits from `TenantAwareModel` + `SoftDeleteModel`. **All business models inherit from `TenantOwnedModel`**.

### 3.2 Tenant Context & Isolation Invariant
- **HTTP Scope**: Middleware inspects authenticated user session/membership to attach `request.tenant`.
- **Queryset Invariant**: In views and APIs, always use `tenant_qs(request, Model)` or filter explicitly with `tenant=request.tenant, isDeleted=False`.
- **Thread-Local Audit**: `home.middleware.AuditUserMiddleware` attaches the active user to thread-local storage so that model save operations record the creator/updater in `AuditLog`.

### 3.3 Dynamic License Plan Architecture
- Plans are represented by `LicensePlan` models:
  - Standard plans: `Starter` (₹19,999/yr, 5 seats), `Growth` (₹34,999/yr, 15 seats), `Enterprise` (₹59,999/yr, Unlimited).
  - Custom plans: Created with `is_custom=True`, hidden from public website, assigned to specific enterprise tenants.
- Assigning a plan via `tenant.apply_license_plan(plan)`:
  - Syncs boolean flags on `Tenant` (`inventoryEnabled`, `advancedInvoicingEnabled`, `einvoice_enabled`, `nonGstShadowEnabled`, `enableAttendanceLocationMaps`).
  - Calls `sync_tenant_admin_group_permissions()` to map 25+ feature toggles directly to Django permissions on `{slug}:Tenant Admin` group.
  - Initializes or resets `TenantAiUsage` with the plan's `ziva_ai_max_queries_per_month` allocation.
- **Middleware Enforcement**: `activation.middleware.TenantActivationMiddleware` checks license expiration (`Validity` or plan expiration). If expired, all non-allowlisted routes redirect to `/activate/` or return 403 JSON (`{"message": "license_expired"}`).
  - Allowlisted paths: `/home/` (shows expired banner), `/`, `/postLogin/`, `/logout/`, `/activate/`, `/static/`, `/media/`, `/admin/`.

### 3.4 FEFO Batch Inventory & Stock Ledger
- Every stock-tracked product maintains batch records (`InventoryBatch`) with `batch_number`, `manufacturing_date`, and `expiry_date`.
- Stock quantities are mirrored in `InventoryStock` (item-level) and `InventoryBatchStock` (batch + warehouse level).
- **Mutations**: Controlled strictly via `home/services/inventory_mutations.py`:
  - Purchases / Opening Stock: Add batches and record positive `InventoryLedgerEntry`.
  - Sales Invoices: Consumes batches using First-Expiry-First-Out (**FEFO**), decrementing batch stock and logging debit entries.
  - Transfers & Adjustments: `InventoryTransferVoucher` and `InventoryAdjustmentVoucher` maintain double-sided ledger integrity.

### 3.5 Dual-Entry Bookkeeping & Ledgers
- `BookAccount`: Accounts categorized by `CASH`, `BANK`, `PARTY`, or `GENERAL`.
- `BookTransaction`: Cashbook and Bankbook transactions with automatic balance calculation.
- `PartyLedgerEntry`: Customer and supplier debit/credit records linked to sales invoices, credit notes, and collections.
- `JournalEntry` & `JournalLine`: General accounting journal vouchers for financial audits.

### 3.6 Counter POS & Dual OTP Workflow
- Cash counters have active sessions (`CashCounter`).
- Field collections or counter collections generate a 4-digit or 6-digit OTP sent via SMS/WhatsApp to the payer.
- Cash collection requires entering customer-provided OTP to complete and lock the transaction, preventing staff misappropriation.

### 3.7 Ziva AI Subsystem
- Located in `home/apis/ziva_ai.py` and `home/services/ai_context.py`.
- Features context grounding: Pulls tenant product catalog, sales summaries, and ledger balances to answer conversational queries.
- Router tries OpenAI first (`gpt-4o-mini` / configured model) and falls back to local Ollama if unavailable.
- Monthly quota is tracked in `TenantAiUsage`. Each query increments `queries_used_this_month`. If quota is reached, requests are safely blocked until monthly renewal.

---

## 4. Key Directory & Module Map

```text
zivaBooks/
├── activation/                # License validity checks, activation keys, middleware
│   ├── middleware.py         # TenantActivationMiddleware (allowlist enforcement)
│   ├── models.py             # Validity, EcomValidity
│   └── views.py              # Activation UI & key submission
├── home/                      # Primary business operations & enterprise app
│   ├── apis/                 # JSON API endpoints (admin, sales, inventory, books, AI)
│   ├── management/commands/  # CLI seeds (roles, mock data) & backfill scripts
│   ├── middleware.py         # AuditUserMiddleware, RequestLoggingMiddleware
│   ├── models.py             # Tenant, StaffUser, Sales, Collection, Ledgers, Plans
│   ├── permission_bundles.py # 25+ feature toggles mapped to Django permissions
│   ├── services/             # Pure service layer (FEFO, invoices, books, AI context)
│   └── views.py              # HTML views, auth, dashboards, customer portal
├── inventory/                 # Inventory models re-export, catalog APIs & vouchers
│   ├── api.py                # CRUD endpoints for inventory entities
│   ├── models.py             # Exports InventoryItem, Batches, Vouchers from home.models
│   └── views.py              # Inventory dashboards, stock tables, shadow mode
├── utils/                     # Shared helpers (OTP generation, formatters, PDF utils)
├── zivaBooks/                 # Django core config (settings, urls, celery, wsgi/asgi)
│   ├── celery.py             # Celery broker configuration
│   ├── health.py             # /health/ probe endpoint
│   └── settings.py           # Multi-tenant settings, WhiteNoise, Redis, Database
├── start.sh                   # Unified dev/prod/worker launch script
├── pyproject.toml             # Python 3.14+ dependencies managed by uv
└── manage.py                  # Standard Django CLI runner
```

---

## 5. Environment Variables Reference

| Variable | Dev Default | Production Requirement |
|---|---|---|
| `DEBUG` | `True` | Must be `False` |
| `SECRET_KEY` | `zivabooks-local-dev-insecure-key` | High-entropy random secret key |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,*` | Explicit comma-separated FQDNs |
| `CSRF_TRUSTED_ORIGINS` | Empty | `https://app.yourdomain.com` |
| `DATABASE_ENGINE` | `sqlite` | `postgres` |
| `POSTGRES_DB` | `zivaBooks` | Production database name |
| `POSTGRES_USER` | `postgres` | Database username |
| `POSTGRES_PASSWORD` | Empty | Strong password |
| `POSTGRES_HOST` | `127.0.0.1` | DB host or container alias |
| `POSTGRES_PORT` | `5432` | DB port |
| `USE_REDIS_CACHE` | `False` | `True` |
| `REDIS_CACHE_URL` | `redis://127.0.0.1:6379/1` | Redis connection URI |
| `CELERY_BROKER_URL` | `redis://127.0.0.1:6379/0` | Celery broker URI |
| `OPENAI_API_KEY` | Empty | OpenAI API key for Ziva AI |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama local endpoint |

---

## 6. Critical Engineering Guidelines & Gotchas

1. **Always Use `tenant_qs`**:
   Never execute queries like `Sales.objects.all()` or `Party.objects.filter(...)` in user-facing views without tenant scoping. Always use `tenant_qs(request, Sales)` or filter with `tenant=request.tenant`.
2. **Atomic Inventory & Ledger Updates**:
   Every inventory mutation or collection approval must run inside `transaction.atomic()` to guarantee that stock counts and ledger balances never diverge.
3. **Preserve Soft Deletes**:
   Do not call `.delete()` on `TenantOwnedModel` instances directly unless hard-delete is explicitly intended. Set `isDeleted = True` and save.
4. **Group Permissions Sync**:
   When updating license plans or tenant features, call `tenant.apply_license_plan()` or `tenant.sync_tenant_admin_group_permissions()`. This ensures the Django group permissions cache is invalidated and updated.
5. **Date Parsing Standard**:
   Dates from web forms and mobile clients may arrive formatted as `DD/MM/YYYY` or `YYYY-MM-DD`. Use the centralized date parsing helpers in `home/views.py` and `home/services/`.
6. **PDF Generation Constraints**:
   WeasyPrint requires Cairo and Pango system libraries. In headless Linux/Docker environments, ensure `libpango-1.0-0`, `libcairo2`, and `libgdk-pixbuf2.0-0` are installed.
