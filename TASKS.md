# zivaBooks — Project Roadmap & Task Tracker

This document tracks completed features, active backlog tasks, technical debt, and future milestones for the **zivaBooks** platform.

---

## 🚦 Status Legend
- 🟢 **Completed** — Tested, merged, and operational in production/main.
- 🟡 **In Progress** — Under active design or development.
- ⚪ **Backlog / Planned** — Prioritized for upcoming sprints.
- 🔴 **Blocked / Deferred** — Awaiting external dependency or architectural alignment.

---

## 1. Recently Completed Milestones (v0.1.0)

| Epic / Feature | Description | Status |
|---|---|---|
| **Custom Tenant License Plans** | Implemented `LicensePlan` with `is_custom=True`, annual pricing, seat limits, and 25+ granular feature toggles. Added `tenant.apply_license_plan()` syncing `{slug}:Tenant Admin` permissions. | 🟢 Completed |
| **Ziva AI Plan-Linked Quota** | Linked Ziva AI assistant query limits to active subscription tier (`ziva_ai_max_queries_per_month`), with automatic monthly query reset. | 🟢 Completed |
| **Admin License Inquiry Notifications** | Automated instant email notifications to system administrators whenever a prospect submits an inquiry via the marketing portal. | 🟢 Completed |
| **Recovery Email & 2FA Enhancements** | Re-engineered recovery email modal, token-based verification, and user credential management. | 🟢 Completed |
| **Business Profile Settings** | Added tenant self-service business profile settings for GSTIN, legal name, billing address, and logo branding. | 🟢 Completed |
| **Public Marketing & Pricing Portal** | Implemented public landing website with dynamic plan cards, FAQ section, and contact inquiry modal. | 🟢 Completed |
| **FEFO Inventory Stock Ledger** | Full First-Expiry-First-Out batch tracking with `InventoryBatchStock` and immutable audit logs via `InventoryLedgerEntry`. | 🟢 Completed |
| **Dual-Party Collection OTP** | Payer OTP verification on cash/cheque collection vouchers to eliminate collection discrepancies. | 🟢 Completed |
| **Accounting PDF & Excel Reports** | High-fidelity A4 and thermal printing for Cashbook, Bankbook, and Party Ledgers via WeasyPrint and openpyxl. | 🟢 Completed |
| **Non-GST Shadow Mode** | PIN-protected dual accounting mode allowing separate tracking of non-GST inventory and transactions. | 🟢 Completed |
| **Automated Test Coverage** | Added 40+ unit and integration test suites covering tenant isolation, permissions, API smoke, and ledger integrity. | 🟢 Completed |

---

## 2. Active Development Backlog (Sprint 2026-Q4)

### 2.1 POS & Operations Enhancements
- [ ] **POS Counter Real-Time Notifications**
  - Integrate Django Channels / WebSockets for multi-counter live cash handoff alerts.
  - Priority: Medium | Effort: 3 days.
- [ ] **PWA Camera Barcode Scanner**
  - Add native HTML5 camera barcode/QR code reader for fast batch scanning on mobile.
  - Priority: High | Effort: 2 days.
- [ ] **Thermal Print Customization Engine**
  - Add tenant settings for customizable header text, QR payment code, and footer greetings on 2-inch and 3-inch thermal receipts.
  - Priority: Medium | Effort: 2 days.

### 2.2 GST & Taxation Compliance
- [ ] **GSTR-1 & GSTR-3B Export Engine**
  - Standardized JSON and Excel reports formatted according to Indian GST portal requirements.
  - Priority: High | Effort: 4 days.
- [ ] **Direct NIC E-Invoice & E-Way Bill Integration**
  - Finalize production credentials handling for NIC direct API gateway and ClearTax GSP fallback.
  - Priority: Medium | Effort: 5 days.

### 2.3 Automated Background Jobs (Celery & Redis)
- [ ] **Celery Beat Scheduled Tasks**
  - Automatic deactivation of expired zero-stock batches (`deactivate_expired_zero_stock_batches_api`).
  - Automated monthly reset of `TenantAiUsage.queries_used_this_month`.
  - Nightly database integrity check and tenant ledger balance reconciliation.
  - Priority: High | Effort: 2 days.

---

## 3. Technical Debt & Architectural Optimizations

| Item | Area | Rationale | Priority |
|---|---|---|---|
| **Queryset N+1 Optimization** | Ledger Reports | Add `select_related('party', 'tenant')` and `prefetch_related('lines')` on heavy ledger queries to reduce DB roundtrips. | High |
| **Migration Squashing** | Database | Clean up and squash historical migration files in `home` and `inventory` before major release. | Medium |
| **Type Annotations** | Service Layer | Add strict Python 3.14 type annotations across `home/services/` for improved IDE autocompletion and static analysis. | Medium |
| **PWA Cache Invalidation** | Frontend | Automate cache busting for service worker assets during production `collectstatic`. | Medium |
| **Redis Session Engine** | Infrastructure | Benchmark Redis session backend (`django.contrib.sessions.backends.cache`) vs DB sessions for high concurrency. | Low |

---

## 4. Quality Assurance & Testing Backlog

- [ ] **Load Testing with Locust**
  - Benchmark 500 concurrent sales transactions and OTP validations per tenant.
- [ ] **Cross-Tenant Security Audit**
  - Automated penetration test ensuring zero possibility of cross-tenant IDOR vulnerabilities.
- [ ] **Offline PWA Sync Validation**
  - Validate offline queueing of collection vouchers and automatic sync when network connectivity is restored.

---

## 5. Completed Tasks History

- **2026-10**: Merged Pull Request #12: Code refactoring, test suite updates, and dynamic custom license plans.
- **2026-09**: Added Ziva AI monthly token renewal tied to tenant license plans.
- **2026-08**: Implemented business profile settings, recovery email verification modal, and admin email dispatch for license inquiries.
- **2026-07**: Complete migration to `uv` package manager and Python 3.14 compatibility validation.
