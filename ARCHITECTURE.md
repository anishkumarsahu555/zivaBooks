# zivaBooks — System Architecture & Technical Specification

This document provides a comprehensive technical overview of the **zivaBooks** system architecture, data models, middleware pipeline, multi-tenant isolation mechanisms, domain subsystems, license plan governance, and design patterns.

---

## 1. High-Level Architecture Overview

`zivaBooks` is built as a **Multi-Tenant Django Enterprise Platform** providing modular business operations management for sales, collections, inventory, accounting ledgers, POS counter operations, staff governance, and AI business intelligence.

```mermaid
graph TD
    Client[Web Browser / PWA / Mobile Client] --> WSGI[Uvicorn ASGI / Gunicorn WSGI Gateway]
    WSGI --> MW[Django Middleware Pipeline]
    
    subgraph Middleware Stack
        MW --> SecMW[Security & WhiteNoise MW]
        SecMW --> SessMW[Session MW]
        SessMW --> AuthMW[Authentication MW]
        AuthMW --> LicMW[TenantActivationMiddleware]
        LicMW --> AuditMW[AuditUserMiddleware]
        AuditMW --> LogMW[RequestLoggingMiddleware]
    end
    
    LogMW --> Router[Django URL Router: zivaBooks/urls.py]
    
    subgraph Layered Application Core
        Router --> Views[View Layer: home/views.py, inventory/views.py]
        Router --> APIs[API Layer: home/apis/, inventory/api.py]
        
        Views --> Services[Service Layer: home/services/]
        APIs --> Services
        
        Services --> DomainModels[Data Access Layer: home/models.py]
    end
    
    subgraph Storage & Infrastructure
        DomainModels --> DB[(SQLite Dev / PostgreSQL Prod)]
        Services --> Redis[(Redis Cache & Broker)]
        Services --> Celery[Celery Async Workers & Beat]
        Services --> PDF[WeasyPrint PDF Engine]
        APIs --> AI[OpenAI / Ollama Local LLM Gateway]
    end
```

---

## 2. Technology Stack

| Layer | Component | Specification |
|---|---|---|
| **Core Framework** | Web Framework | Django `>= 6.0.3` (ASGI / WSGI) |
| **Language Runtime** | Runtime | Python `>= 3.14` |
| **Package Management** | Dependency Manager | `uv` via `pyproject.toml` & `uv.lock` |
| **Database** | Development / Production | SQLite 3 (Dev) / PostgreSQL via `psycopg>=3.2.10` (Prod) |
| **Caching & Task Queue** | Cache & Broker | Redis via `django-redis>=6.0.0` & Celery `>=5.4.0` |
| **Static & Media** | Assets & Uploads | WhiteNoise (`CompressedManifestStaticFilesStorage`), `django-stdimage>=6.0.2` |
| **Document Engine** | PDF / Excel | WeasyPrint `>= 68.1`, `openpyxl >= 3.1.5`, `qrcode>=8.2` |
| **Frontend UI** | PWA & Templating | Django Templates, HTML5 DataTables (`django-datatables-view`), `django-pwa>=2.0.1` |
| **AI Integration** | Intelligence | OpenAI SDK `>= 2.44.0` / Ollama Local LLM (`home/apis/ziva_ai.py`) |

---

## 3. Data Model Inheritance & Multi-Tenancy Design

Multi-tenancy in `zivaBooks` is implemented using **Row-Level Shared-Database Isolation** enforced via base model inheritance.

### 3.1 Inheritance Hierarchy

```mermaid
classDiagram
    class TimeStampedModel {
        +datetime datetime
        +datetime lastUpdatedOn
    }
    class AuditedModel {
        +GenericRelation audit_logs
    }
    class TenantAwareModel {
        +Tenant tenant
    }
    class SoftDeleteModel {
        +boolean isDeleted
    }
    class TenantOwnedModel {
    }

    TimeStampedModel <|-- TenantAwareModel
    AuditedModel <|-- TenantAwareModel
    TenantAwareModel <|-- TenantOwnedModel
    SoftDeleteModel <|-- TenantOwnedModel
    
    TenantOwnedModel <|-- Sales
    TenantOwnedModel <|-- Collection
    TenantOwnedModel <|-- Order
    TenantOwnedModel <|-- InventoryItem
    TenantOwnedModel <|-- InventoryBatchStock
    TenantOwnedModel <|-- CashCounter
    TenantOwnedModel <|-- BookTransaction
    TenantOwnedModel <|-- PartyLedgerEntry
```

1. **`TimeStampedModel`**: Provides automatic timestamp fields (`datetime` created, `lastUpdatedOn` modified).
2. **`AuditedModel`**: Captures user tracking via `AuditLog` populated automatically via thread-local context from `AuditUserMiddleware`.
3. **`TenantAwareModel`**: Combines timestamping and auditing with a mandatory Foreign Key to `Tenant`.
4. **`SoftDeleteModel`**: Provides `isDeleted` flag for safe audit recovery without data loss.
5. **`TenantOwnedModel`**: Inherits from both `TenantAwareModel` and `SoftDeleteModel`. **All business domain entities inherit from `TenantOwnedModel`.**

---

## 4. Middleware & Request Processing Pipeline

The request pipeline enforces security headers, static asset delivery, authentication, tenant validity checks, and audit user thread context.

```mermaid
sequenceDiagram
    autonumber
    actor Client
    participant MW as Middleware Stack
    participant LicMW as TenantActivationMiddleware
    participant AuditMW as AuditUserMiddleware
    participant LogMW as RequestLoggingMiddleware
    participant Handler as API / View Handler
    participant DB as Database

    Client->>MW: HTTP Request
    MW->>LicMW: Evaluate Tenant Validity & License
    alt License Expired & Non-Allowlisted Path
        alt Path is /api/*
            LicMW-->>Client: 403 Forbidden (JSON: {"message": "license_expired"})
        else Web Page
            LicMW-->>Client: 302 Redirect to /activate/
        end
    else License Valid OR Allowlisted Path
        LicMW->>AuditMW: Set Thread-Local User Context
        AuditMW->>LogMW: Start Request Timer
        LogMW->>Handler: Execute Handler
        Handler->>DB: Tenant-Scoped Query (tenant=request.tenant)
        DB-->>Handler: Query Result Set
        Handler-->>LogMW: HTTP Response
        LogMW-->>LogMW: Log [HTTP Method] Path -> Status, User, Tenant, Duration(ms)
        LogMW-->>Client: HTTP Response
    end
```

### 4.1 `TenantActivationMiddleware` Policy
- **Active License**: Request proceeds normally. A banner displaying `"Your App License is Valid till DD-MM-YYYY"` is shown on the dashboard.
- **Expired License**:
  - **HTML Navigation**: Silently redirects user requests to `/activate/`.
  - **API Calls (`/api/`)**: Responds with HTTP `403` JSON payload: `{"message": "license_expired", "redirect": "/activate/"}`.
  - **Allowlist**: Unrestricted access to `/home/`, `/`, `/postLogin/`, `/logout/`, `/activate/`, `/static/*`, `/media/*`, `/admin/*`.

### 4.2 `AuditUserMiddleware` Context
Attaches `request.user` to a thread-local proxy so Django ORM `save()` methods on `AuditedModel` instances automatically record `created_by` and `updated_by` without requiring manual field assignment in views.

---

## 5. Dynamic License Plan & Permission Governance

```mermaid
graph TD
    subgraph Plan Creation
        SuperAdmin["Superadmin creates LicensePlan (is_custom=True / Standard)"]
        Toggles["Configure 25+ Subsystem Feature Toggles & Seat Limits"]
        SuperAdmin --> Toggles
    end

    subgraph Assignment & Synchronization
        Assign["Assign plan to Tenant in Admin: tenant.license_plan = plan"]
        Apply["Execute tenant.apply_license_plan(plan)"]
        Assign --> Apply
        
        Apply --> SyncFlags["Sync Tenant Flags (inventoryEnabled, advancedInvoicingEnabled, etc.)"]
        Apply --> SyncPerms["Sync '{slug}:Tenant Admin' Django Group Permissions"]
        Apply --> SyncAI["Initialize / Reset TenantAiUsage Query Allocation"]
    end

    subgraph Runtime Enforcement
        SyncPerms --> RBAC["UI Sidebar & Views check request.user.has_perm(...)"]
        SyncFlags --> Gating["API Endpoints check feature guards (FeatureGuardMixin)"]
        SyncAI --> AIQuota["Ziva AI Gateway checks queries_used < max_queries"]
    end
```

### 5.1 Dynamic `LicensePlan` Subsystem
- Standard plans: `Starter` (₹19,999/yr, 5 seats), `Growth` (₹34,999/yr, 15 seats), `Enterprise` (₹59,999/yr, Unlimited).
- Custom enterprise plans: `is_custom=True` hides the plan from the public marketing website while allowing custom pricing and bespoke feature configurations.
- Calling `tenant.apply_license_plan(plan)`:
  1. Synchronizes `Tenant` feature flags.
  2. Maps active feature toggles to permission strings via `home.permission_bundles.get_permissions_for_license_plan()`.
  3. Updates the `{slug}:Tenant Admin` Django Group.
  4. Flushes cached permission lookups and allocates fresh `TenantAiUsage` monthly query limits.

---

## 6. Core Domain Subsystems

```mermaid
graph LR
    subgraph Financials & Ledger
        Sales[Sales & Invoicing]
        Proforma[Proforma Invoices]
        Collections[Collections & OTP]
        Books[Cashbook & Bankbook]
        PartyLedger[Party Ledgers]
    end

    subgraph Operations & Logistics
        Inventory[Inventory & Warehouses]
        StockLedger[Stock Ledger & FEFO]
        Purchases[Purchase Invoices]
        Returns[Sales & Purchase Returns]
        CashCounter[POS Cash Counter]
    end

    subgraph Administration & Governance
        Tenants[Tenant & Memberships]
        Staff[Staff & Permissions]
        Audit[Audit Logs]
        Attendance[Attendance & Location]
        Activation[Licensing & Validity]
        ZivaAI[Ziva AI Assistant]
    end

    Sales --> StockLedger
    Purchases --> StockLedger
    Returns --> StockLedger
    CashCounter --> StockLedger
    
    Sales --> PartyLedger
    Collections --> PartyLedger
    Collections --> Books
    CashCounter --> Books
```

### 6.1 Inventory & FEFO Stock Ledger Subsystem
- **Master Data**: `InventoryItem`, `InventoryCategory`, `InventoryUnit`, `InventoryTaxProfile`, `InventoryWarehouse`.
- **Stock Tracking**:
  - `InventoryStock`: Aggregated stock quantity per warehouse.
  - `InventoryBatch` & `InventoryBatchStock`: Granular batch tracking with manufacturing and expiry dates.
  - `InventoryLedgerEntry`: Immutable double-entry stock transaction log (Sales, Purchases, Returns, Adjustments, Transfers).
- **FEFO (First-Expired, First-Out) Algorithm**:
  - When generating sales or cash-counter invoices, `home/services/inventory_lines.py` resolves batch availability by prioritizing batches closest to expiration (`expiry_date ASC`).

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant Service as inventory_mutations.py
    participant FEFO as inventory_lines.py
    participant BatchDB as InventoryBatchStock
    participant LedgerDB as InventoryLedgerEntry

    Client->>Service: Post Sale Invoice (Item ID, Qty)
    Service->>FEFO: Resolve FEFO Batches(Tenant, Item, Qty)
    FEFO->>BatchDB: Query Active Batches (ORDER BY expiry_date ASC)
    BatchDB-->>FEFO: Available Batches
    FEFO-->>Service: Batch Allocation List [(Batch A, Qty 5), (Batch B, Qty 5)]
    Service->>BatchDB: Deduct Allocated Stock Quantities
    Service->>LedgerDB: Record Double-Entry Inventory Ledger Log
    Service-->>Client: Sale Posted & Stock Updated
```

### 6.2 Collections & Dual-Party OTP Verification
- **Model**: `Collection` (Mode: Cash, Cheque, Bank Transfer, UPI).
- **OTP Safety**:
  1. Staff/Admin generates collection OTP via `/api/request_collection_otp_api/`.
  2. Customer views active OTP in Customer Portal (`/customer_home/`).
  3. Collector enters OTP during payment entry.
  4. System verifies OTP hash, TTL expiry, party ID, and payment amount matching before marking `otp_verified = True` and recording the collection.

### 6.3 Accounting Books & Ledgers
- **Cashbook**: Real-time statement of cash sales, cash counter receipts, and cash collections.
- **Bankbook**: Real-time statement of bank transfers, UPI receipts, and cleared cheques per `Bank` account.
- **Party Ledger**: Running debit/credit balance statement for customers and suppliers calculated dynamically in `home/services/party_ledger_reports.py`.
- **Journal Vouchers**: `JournalEntry` and `JournalLine` for formal auditing and double-entry adjustments.

### 6.4 Ziva AI Subsystem
- **Architecture**: `home/services/ai_context.py` constructs structured tenant operational summaries (sales trends, overdue collections, stock status) $\rightarrow$ feeds into `home/apis/ziva_ai.py` $\rightarrow$ calls LLM API (OpenAI or Ollama) $\rightarrow$ returns actionable conversational responses.
- **Quota Management**: Managed through `TenantAiUsage`. Each query increments `queries_used_this_month`. If quota is exhausted, further queries are gracefully blocked until monthly renewal.

---

## 7. Security, Auditing & Permissions

1. **Role-Based Access Control (RBAC)**:
   - Tenant-scoped `StaffGroup` models map to Django `Group` and `Permission` objects.
   - Dynamic UI sidebar rendering (`home/navigation.py`) hides unpermitted features.
   - Endpoint views enforce explicit permission checks (e.g. `@permission_required('home.can_approve_collection')`).
2. **Audit Logging**:
   - `AuditLog` captures model mutations (create, update, delete) with field diffs, timestamp, IP address, and user metadata via `home/audit.py`.
3. **Data Isolation**:
   - Every database query enforces tenant isolation (`tenant=request.tenant`). Cross-tenant data access is impossible at the ORM boundary.

---

## 8. Containerized Infrastructure & Deployment Topology

One root `docker-compose.yml`, selected with `.env.local` or `/etc/zivabooks/production.env`, defines both environments. DATA_ROOT selects `./local-data/zivabooks` locally or `/srv/zivabooks/data` on the VPS.

For local and production deployments, `zivaBooks` uses a decoupled 4-container architecture orchestrated via Docker Compose:

```mermaid
graph TD
    User([User Browser / Mobile PWA]) -->|HTTP Port 8000| Web[Django Web Service<br>Uvicorn]

    subgraph Containerized Services
        Web -->|Port 5432| DB[(PostgreSQL Database)]
        Web -->|Port 6379| Redis[(Redis Broker & Cache)]
        Worker[Celery Asynchronous Worker] -->|Listen Queues| Redis
        Worker -->|DB Queries| DB
    end

    subgraph Host Bind Mounts
        DB -.-> MountDB[./local-data/zivabooks/postgres]
        Redis -.-> MountRedis[./local-data/zivabooks/redis]
        Web -.-> MountMedia[./local-data/zivabooks/media]
        Worker -.-> MountMedia
    end
```

- **Web Container (`web`)**: Runs Django with WhiteNoise static asset compression. Uses image-built static assets; migrations run explicitly through the `migrate` tools service.
- **Database Container (`db`)**: PostgreSQL 17 storing tenant-partitioned business entities, with bounded connections and `DB_CONN_MAX_AGE=0` for ASGI.
- **Cache / Broker Container (`redis`)**: Redis 7+ providing session caching, rate-limiting counters, and task brokering for Celery.
- **Background Worker (`worker`)**: Celery worker executing long-running PDF compilation, asynchronous email dispatch, and WhatsApp notifications.
- **Host Bind Mounts**: Persistent data is mounted into host directory `./local-data/` for full operational transparency and straightforward local backups.

---

## 9. System Governance & Documentation Map

This architecture document is part of the synchronized 8-document governance system:

| Document | Primary Role | When to Update |
|---|---|---|
| **[README.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/README.md)** | Master Portal & Quick Start | Repo onboarding, stack summary, high-level features |
| **[MEMORY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MEMORY.md)** | Living State & Context Ledger | Every milestone, architectural decision (ADR), bug fix, or gotcha |
| **[ARCHITECTURE.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/ARCHITECTURE.md)** | System Blueprint & Specifications | Model hierarchy, subsystem flows, services refactoring, infrastructure |
| **[TASKS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/TASKS.md)** | Sprint Board, Backlog & Roadmap | Task planning, in-progress tracking, task completion |
| **[DESIGN.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/DESIGN.md)** | UI/UX & Print Design System | New components, color tokens, layout, A4/thermal templates |
| **[RULES.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/RULES.md)** | Non-Negotiable Engineering Rules | Coding standards, isolation rules, PR checklists |
| **[COMMANDS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/COMMANDS.md)** | CLI Runbook & Command Reference | New management commands, docker options, test runner flags |
| **[AGENTS.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/AGENTS.md)** | AI Agent Operating Protocol | Agent workflows, guardrails, automation rules |

---

## 10. Architecture Evolution & Change Tracking Protocol

Whenever new features, services, or updates are introduced:
1. **Model Changes**: If a new domain model is introduced, verify inheritance from `TenantOwnedModel`, document its relationships, and update the Class Diagram in Section 3.
2. **Service Layer Boundary**: Ensure business logic resides strictly in `home/services/`. Update Section 5 & 6 if a new domain service is introduced.
3. **Middleware Changes**: If request interception or auth flows change, update the Sequence Diagram in Section 4.
4. **Infrastructure Updates**: If caching layers, task queues, or container configs evolve, update Section 8.
5. **Cross-Document Cascade**: Log decisions in `MEMORY.md` and check off tasks in `TASKS.md`.
