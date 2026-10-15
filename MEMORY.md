# zivaBooks Project Memory & Context Ledger

This document is the **living memory, persistent state tracker, and decision log** for `zivaBooks`. It ensures unbroken continuity across AI sessions, developer handoffs, and engineering iterations.

---

## 1. Project Identity & Baseline State

- **Application Name**: `zivaBooks`
- **Current Active Branch**: `feature/docker-integration`
- **Core Purpose**: Multi-tenant Django enterprise management platform covering sales, OTP collections, inventory (FEFO), accounting ledgers, POS counter operations, licensing, and reporting.
- **Runtime & Environment**:
  - Python: `>= 3.14`
  - Framework: Django `>= 6.0.3`
  - Package Runner: `uv` (standard prefix: `uv run python manage.py <cmd>`)
  - Databases: SQLite (local/dev default) and PostgreSQL via `psycopg>=3.2.10` (Docker/prod)
  - Cache & Message Broker: Redis (`django-redis>=6.0.0`) & Celery (`celery>=5.4.0`)
  - PDF & Reporting: WeasyPrint (`weasyprint>=68.1`), `openpyxl`
  - Frontend: Django Templates, DataTables (server-side), Vanilla JS, PWA (`django-pwa`)

---

## 2. Architectural Decisions Record (ADR Summary)

### ADR-01: Shared-Database Row-Level Multi-Tenancy
- **Decision**: Multi-tenancy is enforced at the ORM layer using base model inheritance (`TenantOwnedModel` inheriting from `TenantAwareModel`, `AuditedModel`, `TimeStampedModel`, and `SoftDeleteModel`).
- **Rationale**: Provides high developer ergonomics, shared schema migrations, and low infrastructure overhead while ensuring strict tenant segregation when queries include `tenant=request.tenant`.

### ADR-02: FEFO Batch Allocation & Double-Entry Stock Ledger
- **Decision**: Stock deduction follows First-Expired, First-Out (FEFO) batch sorting via `home/services/inventory_lines.py`. Every stock mutation is recorded in `InventoryLedgerEntry`.
- **Rationale**: Crucial for expiry-sensitive inventory (pharmaceuticals, FMCG, food distribution) and auditable stock reconciliation.

### ADR-03: Layered Service Architecture & `apiView.py` Deprecation
- **Decision**: Extract all business logic, tax computations, and mutations into `home/services/`. `home/apis/` handles HTTP/JSON.
- **Rationale**: Prevents fat controllers, eliminates circular dependencies, and enables isolated unit testing of domain logic without HTTP mocking.

### ADR-04: Tenant Licensing Middleware
- **Decision**: Enforce subscription validity via `TenantActivationMiddleware` which redirects expired web requests to `/activate/` and returns HTTP `403` JSON payloads on `/api/`.
- **Rationale**: Clean, centralized licensing enforcement without cluttering individual views.

### ADR-05: Docker Compose Local Stack with Host Bind Mounts
- **Decision**: Multi-container Docker Compose (`web`, `db`, `redis`, `worker`) mounts host directory `local-data/` rather than opaque named volumes.
- **Rationale**: Ensures transparent host inspection of PostgreSQL data, Redis dumps, and uploaded media while allowing standard containerized development.

---

## 3. Known Gotchas, Quirks & Technical Context

1. **Local Test Database Selection**:
   - The `.env` file defaults to `DATABASE_ENGINE=postgres` with port `5433`. If running tests locally without the PostgreSQL container running, pass `DATABASE_ENGINE=sqlite`:
     ```bash
     DATABASE_ENGINE=sqlite uv run python manage.py test
     ```
2. **Audit User Context (`AuditUserMiddleware`)**:
   - `created_by` and `updated_by` are set automatically using thread-local user state in `AuditedModel.save()`. In background tasks or scripts running outside the HTTP request cycle, explicitly pass the user if auditing is required.
3. **Celery Test Execution**:
   - In automated test runs, `CELERY_TASK_ALWAYS_EAGER = True` is enforced in settings, executing tasks synchronously without needing a running Redis broker.
4. **WeasyPrint on macOS**:
   - WeasyPrint requires native system libraries (`pango`, `cairo`, `gobject`). If generating PDFs locally on macOS without Docker, ensure `brew install pango cairo libffi` is installed.
5. **Indian Rupee Formatting (`|format_inr`)**:
   - Always use the custom template filter `formatting.py` for displaying currency in Indian numbering notation (`₹ 1,00,000.00`).
6. **OTP Hash Lifecycle**:
   - OTPs generated for collections and order deliveries have a strict TTL and are verified against party ID and payment amounts to prevent replay attacks.

---

## 4. Recent Decisions & Change Ledger

| Date / Commit | Change Summary | Impacted Components | Context / Notes |
|---|---|---|---|
| **2026-10-15** | Local Dev Mode without Docker Image Build | `start.sh`, `docker-compose.dev.yml`, `.env.local` | Added `--dev`, `--services`, `--dev-worker`, and `--sqlite` to run host Django with Docker DB/Redis (no image build required). |
| **2026-10-13** | Docker Integration & Bind Mounts Setup | `Dockerfile`, `docker-compose.yml`, `README.md` | Enabled 4-service Docker stack with host-visible `local-data/` mounts. |
| **2026-10-13** | Documentation System Overhaul | `README.md`, `MEMORY.md`, `ARCHITECTURE.md`, `TASKS.md`, `DESIGN.md`, `RULES.md`, `COMMANDS.md`, `AGENTS.md` | Established 8-file synchronized living documentation architecture for team & AI agents. |
| **2026-07-28** | Service Layer Refactoring & AI Context | `home/services/`, `home/apis/`, `home/services/ai_context.py` | Extracted core invoicing, collections, and AI context builders into dedicated modules. |
| **2026-06-04** | Coverage & CI Setup | `.coverage`, `.github/workflows/` | Added automated CI testing and test coverage reports. |

---

### Shared Compose decision — 2026-10-14

The root `docker-compose.yml` is now authoritative for local and production.
Select `--env-file .env.local` or `/etc/zivabooks/production.env`; tracked templates
are `.env.local.example` and `.env.production.example`. Removed the duplicate
production Compose/template and redirected operations.py to the shared file.
Both environments use the optimized production image/Uvicorn. Local env overrides
HTTP cookies, DEBUG and console email. Migrations remain explicit, static assets
are built into the image. DATA_ROOT replaces local LOCAL_DATA_ROOT/APP_NAME;
local storage stays `local-data/zivabooks/{postgres,redis,media}`. Never change an
existing cluster's credentials just by changing env values. Production resource
limits and Nginx/Certbot/backup workflow remain in place.

Created ignored, mode-600 `.env.local` using existing `.env` credentials; no secrets
were printed. All three local folders exist. Docker reported no running
containers; this task did not start them or alter databases/media. Eleven tests
passed via the SQLite Django runner (health, backup failure paths, both rendered
Compose configurations). End-to-end container startup remains a separate check.

### Local development wrapper — 2026-10-14

Added executable `start.sh`, defaulting to `.env.local` and root Compose. Startup
supports --build, opt-in --migrate, --no-worker and --follow; actions cover init,
status, stop/down/restart, logs, shells, management commands and host uv/SQLite tests.
Env files are parsed by Compose, never shell-sourced. Resolved configuration must
use DEBUG=true, a non-production project name and bind paths inside local-data/.
No volume removal/pruning/reset is exposed. Migration failures leave web/worker
stopped. --init preserves existing files; it does not import credentials from native
.env. Existing databases require their original credentials. Source changes need
rebuilds; generate migration files on the host because code is not bind-mounted.
Verified Bash syntax and 18 targeted tests through the SQLite Django runner,
including seven wrapper tests using fake Docker (no live data or containers touched).
Updated README.md, COMMANDS.md and setup.md with usage.

## 5. Memory Update Protocol

Whenever:
1. An architectural or schema decision is made $\rightarrow$ Add an ADR entry in Section 2.
2. A new bug, gotcha, or environment quirk is solved $\rightarrow$ Add to Section 3.
3. A milestone is completed or major change is committed $\rightarrow$ Append to Section 4.

## Rebase decisions — 2026-10-15

- Rebased Docker integration onto main at `4187eff`. Retained the feature branch shared Compose workflow and matching multi-stage Dockerfile/start script.
- Preserved main application fixes, initial migration contents, design and engineering guidelines, and combined runtime-data exclusions.
- Recovery branch: `codex/docker-integration-before-rebase-20261015`.
- Verification: 17 startup, Compose configuration, and backup tests passed; shell syntax validated.
