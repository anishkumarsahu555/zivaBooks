# zivaBooks Maintainability Guide

This guide records the refactor boundaries for zivaBooks so future cleanup stays incremental and behavior-safe.

## Current Hotspots

- `home/apiView.py`: remaining API surface across parties/staff/admin, dashboard summaries, collection reports, and legacy message dispatch helpers.
- `home/views.py`: page views, customer portal helpers, permission presets, and page context logic.
- `home/urls.py`: mixed page routes, API routes, reporting routes, and inventory route imports.
- Large templates with inline JavaScript, especially dashboard, inventory, collection, order, and reporting pages.

## Refactor Rules

- Keep one behavior-preserving extraction per change set.
- Run focused tests after each extraction, then a wider regression target.
- Keep public URL names and import paths stable unless a change explicitly migrates callers.
- Move business rules into small modules before changing behavior.
- Prefer tenant-aware helpers and services over repeated raw model lookups.
- Do not rename `home/dashboard.py` into a package without a dedicated migration, because existing imports use `home.dashboard`.

## Dashboard Boundary

Dashboard code is now split by responsibility:

- `home/dashboard.py`: public dashboard state builder used by context processors and tests.
- `home/dashboard_alerts.py`: action alert counts and alert links.
- `home/dashboard_charts.py`: chart card definitions and chart JSON API payloads.

The dashboard API route still uses the existing URL name:

- `homeApp:get_dashboard_charts_api`

`home/apiView.py` imports the chart API view from `home/dashboard_charts.py` to keep `home/urls.py` stable while reducing the API file size.

## Collections Boundary

Collection code now has a service layer:

- `home/services/collections.py`: collection permission helpers, party ID parsing, collection list queryset shaping, payment-mode validation, verified labels, and date-preserving collection timestamp helpers.
- `home/apis/collections.py`: collection OTP endpoints, collection posting/editing, collection list datatables, cheque reminder datatable, collection export, delete, and approval endpoints.

The public collection URL paths and names are unchanged. Shared reporting and messaging helpers still live in `home/apiView.py` until those boundaries are extracted.

## Orders Boundary

Order code now has a service and API boundary:

- `home/services/orders.py`: order permission helpers, order status normalization, transition maps, status timestamp updates, sale-link behavior, edit/delete guards, public IDs, and order list queryset shaping.
- `home/apis/orders.py`: order delivery OTP endpoints, order create/update/delete/approval/status endpoints, order attachment delete, order list datatables, order exports, order PDF generation, and sale-link endpoints.

The public order URL paths and names are unchanged. Shared report rendering helpers still live in `home/apiView.py` until the reporting extraction phase.

## Shared Service Boundaries

Shared helpers that were previously anchored in `home/apiView.py` now live in service modules:

- `home/services/sequences.py`: tenant sequence defaults and tenant-safe sequence generation.
- `home/services/reports.py`: INR formatting, compact money formatting, PDF base context, safe sheet titles, and XLSX report response generation.
- `home/services/messages.py`: WhatsApp message status recording and message dispatch wrapper.
- `home/services/invoices.py`: GST number/state normalization, invoice-kind normalization, GST breakup calculation, shared buyer invoice fields, and sales/proforma invoice payload totals.
- `home/services/inventory_lines.py`: tenant feature gates for inventory/invoicing/non-GST, line item request parsing, line totals and round-off helpers, inventory item/warehouse parsing, stock-bucket validation, quantity validation, and FEFO batch resolution.
- `home/services/inventory_mutations.py`: inventory-aware line writes for sales, proforma invoices, cash-counter invoices, purchases, proforma-to-sales conversion, stock rebuilds, non-negative validation, and cash-counter return voucher mirroring.
- `home/services/inventory_catalog.py`: inventory category/unit/tax-profile parsing and payload application for inventory catalog master data.

Domain API modules should import these services directly. New modules should not import shared helpers from `home/apiView.py`.

## Cash-Counter Boundary

Cash-counter code now has API and service boundaries:

- `home/services/cash_counter.py`: cash-counter permissions and base querysets for cash-counter and cash-counter collection lists.
- `home/services/cash_counter_invoicing.py`: cash-counter billing modes, return-source parsing, GST breakup, invoice action buttons, invoice payload application, and cash-counter line/return sync entry points.
- `home/apis/cash_counter.py`: cash-counter invoice lookup, thermal print, invoice PDF, cash-counter posting/editing, counter datatables, dashboard summary APIs, delete, and cash-counter report exports.
- `home/apis/cash_counter_collections.py`: cash-counter collection posting, list datatable, and edit endpoints.
- `home/apis/inventory_returns.py`: return-source invoice lookup, source detail payloads, purchase return voucher CRUD/list/detail, and sales return voucher CRUD/list/detail.
- `home/apis/inventory_catalog.py`: inventory item, category, unit, and tax-profile CRUD/list endpoints.
- `home/apis/inventory_stock.py`: inventory warehouse CRUD/list endpoints, opening stock endpoints, adjustment vouchers, transfer vouchers, and expired zero-stock batch deactivation.
- `home/apis/inventory_purchases.py`: purchase invoice create/update/delete/list/detail endpoints and purchase GST treatment/claim status normalization.
- `home/apis/inventory_reports.py`: inventory snapshot, batch availability, stock ledger, batch expiry report, and batch ledger endpoints.
- `home/apis/sales.py`: sales create/update/delete/approval/search endpoints and sales datatable APIs.
- `home/apis/proforma.py`: proforma create/update/delete/convert endpoints and proforma datatable API.
- `home/apis/sales_reports.py`: sales invoice PDF, thermal challan, daily sales PDF/XLSX, and sales list export endpoints.
- `home/apis/proforma_reports.py`: proforma invoice PDF and proforma list PDF/XLSX export endpoints.
- `home/apis/business_reports.py`: daily business summary PDF/XLSX and monthly business summary XLSX endpoints.
- `home/apis/analytics_reports.py`: business snapshot, revenue/collection trends, receivables, staff productivity, cash position, order pipeline, exception dashboard, failed-message APIs, and analytics export endpoints.
- `home/apis/books.py`: bank balance API, manual book transaction CRUD, cash/bank transfer endpoints, and book transaction datatable API.
- `home/apis/book_reports.py`: cashbook and bankbook PDF/XLSX export endpoints.
- `home/apis/party_ledger_reports.py`: party ledger datatable and party ledger PDF/XLSX export endpoints.
- `home/apis/attendance.py`: attendance dashboard summary, attendance marking, staff location tracking APIs, and staff/admin attendance datatables.
- `home/apis/attendance_reports.py`: attendance list export plus staff/admin daily and monthly attendance PDF/XLSX report endpoints.
- `home/apis/messages.py`: WhatsApp message datatable and resend endpoint.
- `home/apis/audit.py`: audit log datatable, audit detail endpoint, and audit CSV/XLSX export endpoints.
- `home/apis/tenant_settings.py`: Non-GST shadow status/passcode/toggle endpoints and tenant inventory/invoicing feature flag update endpoint.
- `home/apis/admin_master.py`: staff group, staff user, party group, bank, invoice series, and party CRUD/list/detail APIs.
- `home/apis/dashboard_summaries.py`: admin, staff, party, bank, sales, and order dashboard summary endpoints.
- `home/services/sales.py`: sales permissions, sales mode parsing, bank/party parsing, and sales list base queryset helpers shared by sales APIs and inventory return flows.
- `home/services/proforma.py`: proforma permissions and record-level edit/filter helpers.
- `home/services/invoice_series.py`: invoice series selection, available number calculation, and invoice number reservation.
- `home/services/analytics.py`: analytics report range parsing, money/date formatting, trend bucketing, percent-change, and table row helpers.
- `home/services/books.py`: book date parsing, cashbook/bankbook permissions, export account selection, and running statement row calculations.
- `home/services/party_ledger_reports.py`: party ledger date parsing, customer-visible filters, voucher display helpers, and running ledger statement calculations.
- `home/services/attendance.py`: attendance list queryset shaping, attendance image URL helpers, attendance location reverse geocoding, and monthly attendance matrix builders.
- `home/services/audit_reports.py`: tenant audit queryset scoping, audit filtering, audit access checks, detail payload formatting, and audit datetime formatting.
- `home/services/tenant_settings.py`: tenant settings permission checks, boolean normalization, and Non-GST shadow access-code validation.
- `home/services/dashboard_summaries.py`: tenant-scoped dashboard card summary calculations for admin, staff, party, bank, sales, and orders.

The public cash-counter URL paths and names are unchanged. Cash-counter modules should import inventory line helpers from `home/services/inventory_lines.py`; runtime imports from `home/apiView.py` are retained only where tests or legacy book/ledger sync monkeypatches depend on them.

## Recommended Next Extractions

1. Collections:
   - keep collection approval shared between collection and cash-counter collection flows
2. Orders:
   - keep sale-link rules in `home/services/orders.py`
3. Reports:
   - `home/apis/reports.py`
   - shared date parsing and export metadata helpers
4. Inventory:
   - keep stock/batch reporting behind `home/apis/inventory_reports.py`
5. URL groups:
   - split `home/urls.py` into route modules once API functions have clear domain modules.
6. Collection reports:
   - move the remaining collection report PDF/XLSX helpers out of `home/apiView.py`.
7. URL groups:
   - split `home/urls.py` into route modules once API functions have clear domain modules.
