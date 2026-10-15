# zivaBooks Task Board, Backlog & Roadmap

This document is the authoritative **Work Breakdown Structure (WBS), Sprint Task Board, and Technical Roadmap** for `zivaBooks`. It tracks active development, upcoming backlogs, technical debt, and historical completed milestones.

---

## 1. Task Lifecycle & Status Legend

Every task follows a strict progression:
- `[BACKLOG]` — Planned for future iterations; requirements scoped.
- `[TODO]` — Prioritized for the current sprint; ready for implementation.
- `[IN_PROGRESS]` — Actively being developed by an engineer or AI agent.
- `[IN_REVIEW / TESTING]` — Code written; running automated tests and QA validation.
- `[COMPLETED]` — Shipped to branch, tests passing, and documentation synchronized.

---

## 2. Active Sprint (Current Focus)

### 🚀 Active Tasks
| Task ID | Component | Description | Priority | Status | Assignee |
|---|---|---|---|---|---|
| **TSK-101** | Docs / AI | **Master Documentation & AI Governance Hub**: Establish unified documentation system (`README.md`, `MEMORY.md`, `ARCHITECTURE.md`, `TASKS.md`, `DESIGN.md`, `RULES.md`, `COMMANDS.md`, `AGENTS.md`) with continuous sync rules. | P0 | `[IN_PROGRESS]` | AI Pair / Lead |
| **TSK-115** | DevOps | **Clean & Unified Single Docker Compose Deployment**: Rework Docker deployment into a single, self-contained `docker-compose.yml` with automatic migrations, volume management, healthchecks, and zero-config defaults. | P0 | `[COMPLETED]` | AI Pair / Lead |
| **TSK-102** | DevOps | **Docker Integration Verification**: Verify end-to-end multi-container setup (Django, PostgreSQL, Redis, Celery) on branch `feature/docker-integration`. | P1 | `[COMPLETED]` | DevOps / Dev |
| **TSK-103** | Core | **Database Port & Connection Fallback Resilience**: Ensure graceful SQLite development fallback when PostgreSQL on port 5433 is not reachable locally. | P1 | `[TODO]` | Backend |

---

## 3. Product Backlog & Upcoming Milestones

### 3.1 High Priority (P0 / P1)
- [ ] **TSK-104** `[Core/Services]` **Complete Deprecation of `home/apiView.py`**: Migrate remaining legacy helpers to dedicated domain services under `home/services/` per [MAINTAINABILITY.md](file:///Users/anishkumarsahu/ZivaInfotech/longTerm/zivaBooks/MAINTAINABILITY.md).
- [ ] **TSK-105** `[Celery/Beat]` **Periodic Invoice Due Reminders**: Implement Celery Beat cron job to dispatch WhatsApp/Email payment reminders 3 days before invoice due date.
- [ ] **TSK-106** `[Inventory]` **Bulk Barcode Stock Intake**: Introduce batch barcode scanning interface for high-volume warehouse goods receipt notes (GRN).
- [ ] **TSK-107** `[Customer Portal]` **Self-Service Statement Download**: Allow portal customers to export date-range filtered statement of accounts directly to signed PDF.

### 3.2 Medium Priority (P2)
- [ ] **TSK-108** `[Reports]` **GSTR-1 & GSTR-3B Tax Filing Export**: Generate direct government-compliant JSON/Excel payloads for GST portal upload.
- [ ] **TSK-109** `[POS / Counter]` **Offline Cash Counter Queue**: Enable local browser IndexedDB storage for offline POS billing with auto-sync when online.
- [ ] **TSK-110** `[Security]` **Enhanced Two-Factor Authentication (2FA)**: Add TOTP authenticator app support for admin and manager logins.

### 3.3 Low Priority / Future Enhancements (P3)
- [ ] **TSK-111** `[UI/UX]` **Dark Mode Theme**: Add dark mode color scheme toggle honoring user OS preferences.
- [ ] **TSK-112** `[Analytics]` **Predictive Stock Reordering**: AI-driven reorder recommendation based on 90-day moving average sales velocity.

---

## 4. Technical Debt & Refactoring Register

| Debt ID | Subsystem | Description | Impact | Target Sprint |
|---|---|---|---|---|
| **DEBT-01** | `home/apiView.py` | Legacy monolithic file still holds backwards-compatible endpoints. | Maintainability | Sprint 4 |
| **DEBT-02** | Tests / Postgres | Some test cases expect active PostgreSQL functions when run under SQLite. Add database-engine conditionals. | Test Portability | Current |
| **DEBT-03** | Frontend Assets | Static CSS has inline utility duplicates in older templates. Standardize to shared component classes. | Asset Size | Sprint 5 |

---

## 5. Completed Milestones History

- **TSK-115 — Completed 2026-10-15T12:40+05:30**: Streamlined Docker deployment into a single, self-contained `docker-compose.yml`. Integrated automated PostgreSQL connectivity checking, migrations, static files, and role seeding in `docker-entrypoint.sh`. Configured named volumes (`postgres_data`, `redis_data`, `media_data`) with zero-config sane defaults. Removed redundant compose files (`docker-compose.prod.yml`, `docker-compose.dev.yml`). Validated through 10 automated unit tests and full live container bootstrap.

- **TSK-114 — Completed 2026-10-14T13:34+05:30**: Added executable local start.sh with lifecycle/build/migrate/log/shell/test shortcuts, local configuration guards and failure-path tests. All 18 targeted tests pass; no live container or data changes.

- **TSK-113 — Completed 2026-10-14T13:23+05:30**: Unified Compose with local/production env templates, retained bind paths and updated backup helper. Eleven relevant Django/configuration/backup tests pass. Live containers were not started.

| Milestone ID | Date | Component | Deliverable Summary |
|---|---|---|---|
| **M-01** | 2026-03 | Multi-Tenancy | Base model hierarchy (`TenantOwnedModel`, `TenantAwareModel`, `SoftDeleteModel`) and row-level isolation. |
| **M-02** | 2026-04 | Inventory | FEFO batch tracking, `InventoryLedgerEntry` double-entry stock accounting, and warehouse allocation. |
| **M-03** | 2026-05 | Invoicing & GST | Sales, proforma invoices, GST tax calculation (CGST/SGST/IGST), and thermal POS printing. |
| **M-04** | 2026-06 | Collections & OTP | Collection posting with OTP verification flow, payment mode tracking, and approval permissions. |
| **M-05** | 2026-07 | Accounting Books | Cashbook, Bankbook, and dynamic Party Ledger statements with running balances. |
| **M-06** | 2026-08 | PWA & Offline | Service worker integration, offline shell caching, and responsive mobile navigation. |
| **M-07** | 2026-09 | Licensing | `TenantActivationMiddleware` with validity period checks, plan gating, and activation portal. |
| **M-08** | 2026-10 | Docker Integration | Docker Compose multi-container setup (Django, PostgreSQL, Redis, Celery) with host bind mounts. |

---

## 6. Task Update & Maintenance Protocol

Whenever you:
1. **Start working on a task**: Set status to `[IN_PROGRESS]` and assign your name or agent persona.
2. **Finish writing code & passing tests**: Move task to `[COMPLETED]`, record in Completed Milestones if major, and cross-reference in `MEMORY.md`.
3. **Discover an architectural issue or bug**: Log it immediately in the **Technical Debt Register** or **Product Backlog**.

## Branch maintenance — 2026-10-15

- Completed 2026-10-15 (Asia/Kolkata): reconciled `feature/docker-integration` with `origin/main` at `4187eff`. All 17 targeted startup, Compose configuration, and backup tests passed; existing initial migrations remain unchanged.
