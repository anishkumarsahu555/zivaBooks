# zivaBooks Engineering Rules & Coding Standards

This document establishes the **strict, non-negotiable engineering rules, coding standards, and architectural invariants** for `zivaBooks`. Every engineer and AI agent operating in this repository must comply with these guidelines.

---

## 1. Core Architectural Tenets

### 1.1 Multi-Tenant Data Isolation (Zero Unscoped Queries)
- **Mandatory Filter**: Every database query, aggregation, or lookup MUST be explicitly scoped to the active tenant:
  ```python
  # CORRECT
  Sale.objects.filter(tenant=request.tenant, id=sale_id)
  Party.objects.filter(tenant=request.tenant)

  # STRICTLY FORBIDDEN
  Sale.objects.get(id=sale_id)  # Security violation! Cross-tenant leak risk
  Party.objects.all()           # Unscoped access
  ```
- **Base Model Inheritance**: All new business domain models MUST inherit from `TenantOwnedModel` (located in `home/models.py`). This guarantees:
  - `tenant = ForeignKey('Tenant')`
  - Automatic timestamps (`created_at`, `updated_at` from `TimeStampedModel`)
  - User auditing (`created_by`, `updated_by` from `AuditedModel`)
  - Soft-delete support (`is_deleted`, `deleted_at` from `SoftDeleteModel`)
- **Foreign Key Cross-Tenant Validation**: When linking related entities (e.g., adding an `InventoryItem` to a `SaleLine`), verify that the related object belongs to the **same tenant** as the parent record.
- **Cache Isolation**: Any key stored in Redis or memory cache must incorporate the tenant ID:
  ```python
  cache_key = f"tenant:{tenant.id}:inventory_summary:{warehouse_id}"
  ```

---

### 1.2 Strict Layered Separation (Services vs APIs vs Views)
`zivaBooks` enforces a strict 4-tier architecture:
```
Presentation Layer (home/views.py, templates/)
         │
         ▼
API & Controller Layer (home/apis/*.py)
         │
         ▼
Domain Service Layer (home/services/*.py)
         │
         ▼
Data Access & ORM Layer (home/models.py)
```

1. **Service Layer (`home/services/`)**:
   - Contains pure business logic, tax formulas, state transitions, stock mutations, permission checkers, and export payload builders.
   - **No HTTP constructs**: Do not pass Django `request` or return `JsonResponse`/`HttpResponse` inside service functions. Receive plain Python types (primitives, domain objects, dicts, querysets).
   - Functions must be deterministic, isolated, and easily unit-testable.

2. **API Layer (`home/apis/`)**:
   - Handles HTTP requests, parameter extraction, JSON response generation, and DataTables formatting.
   - Delegates all business logic, tax calculations, and database mutations directly to `home/services/`.
   - **No Legacy Imports**: Do **NOT** import business helpers from `home/apiView.py` in new code. All shared logic belongs in `home/services/`.

3. **Presentation / Views (`home/views.py`)**:
   - Responsible for rendering HTML templates, setting template contexts, and menu assembly (`home/navigation.py`).
   - Must not contain heavy business calculations or direct stock mutations.

---

### 1.3 Financial & Inventory Integrity

1. **FEFO Batch Allocation Rule**:
   - All stock deductions (sales, cash-counter billing, consumption) must use First-Expired, First-Out (FEFO) batch allocation implemented in `home/services/inventory_lines.py`.
   - Batches with earlier `expiry_date` must be allocated before later ones.
   - Negative inventory is strictly disallowed unless an explicit non-negative override flag is configured for the tenant.

2. **Immutable Double-Entry Stock Ledger**:
   - Every stock movement (Purchase, Sale, Return, Adjustment, Transfer) must write a ledger entry to `InventoryLedgerEntry`.
   - Never update or delete ledger records directly—always write compensating reversal entries.

3. **Sequence Numbers & Invoicing**:
   - Invoice numbers, proforma numbers, and voucher numbers must use tenant-safe sequence generators (`home/services/sequences.py`).
   - Never generate sequence numbers with naive `Model.objects.count() + 1` to avoid race conditions.

4. **Tax Calculation (GST Standards)**:
   - Always use `home/services/invoices.py` for calculating tax breakups:
     - Intra-state (same state code): CGST (50%) + SGST (50%)
     - Inter-state (different state code): IGST (100%)
   - Respect tenant-level Non-GST mode flags when calculating totals.

---

## 2. Coding Standards & Python Conventions

### 2.1 Python & Django Conventions
- **Target Runtime**: Python `>= 3.14`, Django `>= 6.0`.
- **Tooling**: Use `uv` as the package and virtualenv runner (`uv run ...`).
- **Typing**: Use standard Python type hints for public service functions and utility methods:
  ```python
  def calculate_invoice_totals(
      tenant: Tenant,
      items: list[dict],
      is_interstate: bool
  ) -> dict[str, Decimal]:
  ```
- **Monetary Precision**: Always use `Decimal` for currency calculations, tax amounts, and unit rates. Never use float arithmetic for monetary values.
- **Docstrings & Comments**:
  - Maintain documentation integrity: preserve all existing comments and docstrings.
  - Add docstrings explaining domain intent, inputs, and return payloads on all service functions.

### 2.2 Database & Migration Hygiene
- **Never Alter Applied Migrations**: Once a migration has been committed, never edit its migration file directly. Always create a new forward migration (`uv run python manage.py makemigrations`).
- **Foreign Key Indexing**: Always define `db_index=True` or an explicit index on foreign keys and frequently filtered query fields (`tenant`, `status`, `created_at`).
- **Descriptive Related Names**: Use explicit, meaningful `related_name` attributes on ForeignKeys:
  ```python
  tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='sale_invoices')
  ```
- **Atomic Transactions**: Wrap multi-table operations (e.g., posting an invoice, updating batches, and logging inventory ledger) in `transaction.atomic()`.

---

## 3. Security, Permissions & Compliance

### 3.1 Permissions & Access Control
- Every API endpoint and view must enforce authentication and permission checks:
  ```python
  @login_required
  def my_view(request):
      if not request.user.has_perm('home.can_view_reports'):
          return HttpResponseForbidden("Unauthorized")
  ```
- Use the tenant-scoped permission system (`StaffGroup`, `TenantMembership`).
- Navigation menus must check permissions before displaying links (`home/navigation.py`).

### 3.2 OTP Verification Standard
- High-risk operations (Collections, Order deliveries, Sensitive approvals) require One-Time Password (OTP) verification:
  - Generation: Stored with cryptographic hash, short TTL (e.g., 5-10 minutes).
  - Validation: Must check tenant ID, party ID, and transaction amount match.
  - Never log raw OTP tokens in plaintext log files.

### 3.3 CSRF & Input Sanitization
- All state-mutating HTTP requests (POST, PUT, DELETE, PATCH) must require CSRF validation (`{% csrf_token %}` or `X-CSRFToken` header).
- Validate and sanitize all user input before saving or executing queries. Use parameterized ORM queries; never concatenate strings into raw SQL.

---

## 4. Testing & Quality Mandates

### 4.1 Verification Invariant
- **No PR or Commit without Automated Tests**:
  - Any new feature, API endpoint, or service module must have corresponding unit or integration tests in `home/tests/`.
  - Any bug fix must include a regression test that fails prior to the fix and passes after.
- **Full Suite Integrity**:
  - Run tests using `DATABASE_ENGINE=sqlite uv run python manage.py test` before committing.
  - All existing tests (218+ tests) must pass with zero errors and zero failures.

### 4.2 Test Isolation
- Tests must use Django's `TestCase` or `TransactionTestCase`.
- Always seed required test fixtures or create test tenants/users in `setUp()` or `setUpTestData()`.
- Do not rely on persistent database state between test runs.

---

## 5. Documentation Synchronization Mandate

Whenever changes occur in the codebase, the following files MUST be kept in sync:

| Type of Change | Mandatory Documentation to Update |
|---|---|
| **Any feature, fix, or milestone completed** | `TASKS.md` (move task), `MEMORY.md` (record decision/milestone) |
| **New API endpoint, URL route, or contract change** | `API.md`, `README.md` |
| **New or modified database model / schema change** | `DATABASE.md`, `ARCHITECTURE.md` |
| **New service module or architectural shift** | `ARCHITECTURE.md`, `MAINTAINABILITY.md` |
| **New CLI command, script, or environment variable** | `COMMANDS.md`, `setup.md`, `README.md` |
| **UI component, layout, or design token change** | `DESIGN.md` |
| **Agent workflow, prompt rule, or automation change** | `AGENTS.md`, `RULES.md` |

---

## 6. Pre-Commit Checklist

Before declaring any task complete or committing code:
- [ ] Tenant scoping verified on every ORM query (`tenant=request.tenant`).
- [ ] Business logic placed in `home/services/` (not in `apiView.py` or templates).
- [ ] Monetary operations use `Decimal` with proper rounding.
- [ ] New model fields properly migrated (`makemigrations` executed).
- [ ] Security permissions and CSRF checks in place.
- [ ] Automated tests written and passing (`DATABASE_ENGINE=sqlite uv run python manage.py test`).
- [ ] Documentation updated across `MEMORY.md`, `TASKS.md`, and relevant docs.
