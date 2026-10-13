# zivaBooks — Engineering Rules & Architectural Invariants

This document establishes the mandatory engineering rules, security policies, and architectural invariants for all developers and AI agents contributing to **zivaBooks**.

---

## 🛡️ Rule 1: Multi-Tenant Data Isolation (CRITICAL)

Multi-tenancy in `zivaBooks` relies on row-level shared database isolation. A failure in tenant isolation is considered a **Severity-1 Security Vulnerability**.

1. **Never use unfiltered querysets**:
   - ❌ **FORBIDDEN**: `Sales.objects.all()`, `Party.objects.filter(name=...)`
   - ✅ **MANDATORY**: `tenant_qs(request, Sales)`, or `Sales.objects.filter(tenant=request.tenant, isDeleted=False)`
2. **Always validate relational foreign keys**:
   - When an API receives IDs (e.g., `party_id`, `item_id`, `warehouse_id`, `counter_id`), verify that the referenced record belongs to `request.tenant` before executing operations.
   - Example:
     ```python
     party = get_object_or_404(Party, id=party_id, tenant=request.tenant, isDeleted=False)
     ```
3. **Never rely solely on URL parameters for tenant context**:
   - The active tenant must be derived from `request.tenant` (established via authenticated user session), never trusted from unverified client parameters.

---

## 🏛️ Rule 2: Service Layer Separation

Views and API controllers must remain thin and focused exclusively on HTTP orchestration.

1. **No direct business logic in views**:
   - ❌ Do not write complex stock calculations, tax computations, or FEFO ledger updates inside `views.py` or `apiView.py`.
   - ✅ Delegate all domain logic to dedicated services inside `home/services/` (e.g., `inventory_mutations.py`, `books.py`, `sales.py`).
2. **Responsibilities of Views & APIs**:
   - Authenticate & check user permissions (`@login_required`, `@permission_required`, feature checks).
   - Validate and clean incoming request payload (JSON / Form POST).
   - Call service function with explicit arguments.
   - Return appropriate HTTP response (`JsonResponse` or template render).

---

## 💾 Rule 3: Database & Transaction Integrity

1. **Atomic Operations**:
   - Any operation mutating multiple database tables (e.g., finalizing an invoice, updating FEFO batch stock, creating ledger entries, and updating cash counter totals) **MUST** be wrapped in `transaction.atomic()`:
     ```python
     from django.db import transaction

     with transaction.atomic():
         invoice = create_sales_invoice(...)
         allocate_fefo_stock(...)
         record_party_ledger_entry(...)
     ```
2. **Soft Delete Compliance**:
   - All domain models inherit from `TenantOwnedModel` (which includes `SoftDeleteModel`).
   - Do **NOT** invoke `.delete()` unless a permanent purge is explicitly intended.
   - Set `isDeleted = True` and call `.save()`.
3. **Audit Log Tracking**:
   - Models inherit from `AuditedModel`. Never manually falsify or bypass thread-local user context managed by `home.middleware.AuditUserMiddleware`.

---

## 🔐 Rule 4: Security, Authentication & OTP Enforcement

1. **State-Changing Endpoints Require CSRF**:
   - All `POST`, `PUT`, `PATCH`, and `DELETE` requests must include valid CSRF tokens.
2. **OTP Verification Safeguards**:
   - OTP codes generated for collections or sensitive actions must:
     - Expire within a maximum of 10 minutes.
     - Be invalidated immediately upon successful verification (single-use).
     - Implement rate limiting (maximum 3 resend attempts per hour).
3. **Credential Security**:
   - Never log passwords, API secret keys, recovery email verification tokens, or OTPs in plaintext logs.
   - Use `django.contrib.auth.hashers` (`make_password`, `check_password`) for non-user security codes such as the 6-digit Non-GST Shadow Mode PIN (`nonGstShadowCodeHash`).

---

## 📦 Rule 5: License Plans & Feature Guarding

1. **Dynamic License Plan Checking**:
   - If a feature is gated by a subscription tier (e.g., FEFO inventory, multi-counter POS, Ziva AI, GPS tracking), enforce checks at both the UI layer (menu visibility) and the API layer (HTTP 403 response).
2. **Permission Synchronization**:
   - When altering a tenant's license plan, always invoke `tenant.apply_license_plan(plan)`. This automatically recalculates and applies permissions to `{slug}:Tenant Admin`.
3. **Allowlist Integrity**:
   - Do not remove allowlisted endpoints from `activation.middleware.TenantActivationMiddleware` without architectural sign-off. Essential endpoints (`/home/`, `/activate/`, `/logout/`, `/static/`) must remain accessible when a tenant license lapses.

---

## 📝 Rule 6: Code Style & Conventions

1. **Python & Django Standards**:
   - Target **Python >= 3.14** and **Django >= 6.0.3**.
   - Use meaningful variable and function names.
   - Write clear docstrings for public service functions and API endpoints.
   - Avoid importing wildcards in new files (`from module import *`). Use explicit imports.
2. **Financial Precision**:
   - Store monetary values using `models.DecimalField(max_digits=12, decimal_places=2)`. Never use `FloatField` for currency calculations to prevent floating-point rounding errors.

---

## 🧪 Rule 7: Testing Mandate Before Merging

1. **Automated Test Requirement**:
   - Every new feature, API endpoint, or refactored service module must be accompanied by automated test coverage in `home/tests/` or `activation/tests/`.
2. **Tenant Isolation Test Invariant**:
   - Tests for new endpoints must explicitly include a multi-tenant test case asserting that User A belonging to Tenant A cannot view, mutate, or delete data belonging to Tenant B.
3. **No Breaking Migrations**:
   - Migrations must be backward-compatible and tested using `uv run python manage.py migrate`.
