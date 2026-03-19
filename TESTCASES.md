# zivaBooks — Project Test Cases (All Features)

This document is a **project-level test suite** (manual/QA) that covers the end-to-end feature surface exposed by `home/urls.py`, `activation/urls.py`, and tenant activation middleware.

## 1) Test Setup (Common)

- **Build/Run**: `uv run python manage.py runserver`
- **Seed data (recommended)**: `uv run python manage.py seed_mock_data`
- **Browsers**: Chrome + Firefox
- **Roles**
  - **Owner/Admin**: Tenant owner/admin (full control center access)
  - **Staff (Collector)**: Can add/view collections
  - **Staff (Audit/Read-only)**: View-only, no add/edit/delete
  - **Customer/Party**: Customer portal (party ledger + OTP viewing)
- **Multi-tenant**: At least 2 tenants with different memberships and license states.

## 2) License / Activation (Tenant-based) + Allowlist

Allowlisted when expired (still accessible): `/home/`, `/`, `/postLogin/`, `/logout/`, `/activate/`, `/static/*`, `/media/*`, `/admin/*`.

| ID | Scenario | Preconditions | Steps | Expected Result |
|---|---|---|---|---|
| LIC-01 | Expired tenant blocks non-allowlisted pages | Tenant validity `expiryDate < today` | Login → open any module URL (e.g. `/customer_home/`) | Redirects to `/activate/` (silent block) |
| LIC-02 | Allowlisted `/home/` still works (expired) + banner | Tenant expired | Login → open `/home/` | Loads dashboard; shows **“Activation expired. Please contact ZivaInfotech for activation.”** |
| LIC-03 | Allowlisted `/` (login) works (expired) | Tenant expired | Logout → open `/` | Login screen loads |
| LIC-04 | Allowlisted `/postLogin/` works (expired) | Tenant expired | Submit login form (POST `/postLogin/`) | JSON response `data: "/activate/"` |
| LIC-05 | Allowlisted `/logout/` works (expired) | Tenant expired | Open `/logout/` | Logs out; redirects to login |
| LIC-06 | Allowlisted `/activate/` works (expired) | Tenant expired | Open `/activate/` | Activation page loads (staff vs customer template based on role) |
| LIC-07 | Allowlisted `/admin/` works (expired) | Tenant expired | Open `/admin/` | Shows admin login (or admin home if superuser logged in) **not** redirected to `/activate/` |
| LIC-08 | Allowlisted static/media works (expired) | Tenant expired | Open known static file (e.g. serviceworker, css) | Static response is served (200/304) |
| LIC-09 | API requests fail with JSON (expired) | Tenant expired | Call any `/api/...` endpoint with `Accept: application/json` | Returns `403` JSON `{"message":"license_expired","redirect":"/activate/"}` |
| LIC-10 | Valid tenant shows “License Reminder” | Tenant validity `expiryDate > today` | Login → open `/home/` | Shows **“Your App License is Valid till DD-MM-YYYY”** |
| LIC-11 | Trial/no-validity tenant message | No validity row for tenant/global | Login → open `/home/` | Shows **“You are using a trial version.”** |
| LIC-12 | Tenant-specific validity overrides global | Global validity valid; tenant validity expired | Login with tenant user → open non-allowlisted URL | Blocked (redirect `/activate/`) despite global validity |
| LIC-13 | Second tenant unaffected | Tenant A expired; Tenant B valid | Login as Tenant B user → open module URL | Works normally; no activation block |

## 3) Authentication / Session / Access Control

| ID | Scenario | Preconditions | Steps | Expected Result |
|---|---|---|---|---|
| AUTH-01 | Login success | Valid user | Login via `/` → `/postLogin/` | Redirects/lands on `/home/` or role landing page |
| AUTH-02 | Login failure | Invalid password | Submit login | Error shown; no session created |
| AUTH-03 | Logout clears session | Logged in | Open `/logout/` then open `/home/` | Redirects to login |
| AUTH-04 | Tenant context resolution | User has multiple memberships | Login → verify tenant shown in dashboard | Default membership tenant selected; fallback tenant selected if no default |
| AUTH-05 | Permission-required view denied | User without required permission | Open a protected module URL | Redirect/403 as per decorator behaviour |

## 4) Navigation / Sidebar / Dashboard

| ID | Scenario | Preconditions | Steps | Expected Result |
|---|---|---|---|---|
| NAV-01 | Sidebar hides modules without permission | User with minimal perms | Login → observe sidebar | Only permitted sections and actions appear |
| NAV-02 | Dashboard action tiles match sidebar | Any logged-in user | `/home/` | “Quick Actions” mirrors sidebar items |
| NAV-03 | License message displayed on dashboard | Validity exists | `/home/` | Correct license/expired/trial message shown |
| NAV-04 | Customer activation page shows customer nav | Party user + expired | `/activate/` | Customer portal navigation visible; staff/admin items hidden |

## 5) Staff Groups / Permissions (Tenant scoped)

| ID | Scenario | Preconditions | Steps | Expected Result |
|---|---|---|---|---|
| PERM-01 | Staff group list loads | Admin-like user | Open `/staff_group_list/` | List visible, scoped to tenant |
| PERM-02 | Create staff group | Admin-like user | Open `/staff_group_create/` → create group + permissions | Group created; permissions saved |
| PERM-03 | Edit staff group | Admin-like user | Edit existing group | Changes saved; reflected in access |
| PERM-04 | Staff group detail shows permissions | Admin-like user | Open `/staff_group_list/<id>/` | Shows assigned permissions |
| PERM-05 | Assign user to staff group updates Django group membership | User exists | Change staff user group assignment | User gains new group perms; old tenant staff groups removed |
| PERM-06 | Unrelated groups remain | User has non-tenant groups | Change staff group | Non-tenant groups untouched |
| PERM-07 | Staff cannot access admin staff/group pages | Staff user | Try `/user_list/` or staff-group URLs | Access denied |

## 6) Party / Customer (Portal)

| ID | Scenario | Preconditions | Steps | Expected Result |
|---|---|---|---|---|
| CUST-01 | Customer home loads | Party user (`view_party`) | Open `/customer_home/` | Customer portal loads |
| CUST-02 | Ledger KPI API returns totals | Party user + ledger entries | GET `/api/customer_ledger_kpis/` | Returns `opening/debit/credit/closing` and dates |
| CUST-03 | Change password success | Party user | POST `/customer/change-password/` with correct current pw + 6-digit new | Password updated; session preserved |
| CUST-04 | Change password validation | Party user | Wrong current password / non-6-digit new | 400 with proper error |
| CUST-05 | Customer OTP endpoint access denied for non-party | Staff user | GET `/api/customer_active_collection_otp/` | 403 “Permission denied.” |

## 7) Collections (Staff + Admin) + OTP

| ID | Scenario | Preconditions | Steps | Expected Result |
|---|---|---|---|---|
| COL-01 | Staff add collection page loads | Staff with `add_collection` | Open `/add_collection/` | Form loads; party + bank lists are tenant-scoped |
| COL-02 | Staff “My Collections” list loads | Staff with view perms | Open `/my_collection/` | List loads with capability flags based on perms |
| COL-03 | Admin “Collection List” loads | Admin-like (`can_approve_collection`) | Open `/collection_list/` | List loads; staff filter visible |
| COL-04 | Delete collection permission check | Staff without delete perm | Call `/api/delete_collection/` | Access denied |
| COL-05 | Approve collection permission check | Non-approver | Call `/api/approve_collection/` | Access denied |
| COL-06 | OTP generation (staff/admin) | Party exists | Request OTP via `/api/request_collection_otp_api/` | OTP created, TTL returned |
| COL-07 | OTP visible in customer portal | OTP exists | Party user opens OTP panel / calls `/api/customer_active_collection_otp/` | Shows active OTP + expires_in |
| COL-08 | OTP verify success | OTP exists | POST `/api/verify_collection_otp_api/` | Success; OTP marked verified (status update) |
| COL-09 | OTP required blocks submission if missing | OTP required enabled | Submit add-collection without OTP | API/UI blocks submission; error shown |
| COL-10 | OTP mismatch blocks submission | OTP exists but details mismatch | Submit with mismatched amount/mode/date | Reject with mismatch message |
| COL-11 | Cheque reminder list | Admin-like | Open `/cheque_reminder_list/` | Shows due cheques list |
| COL-12 | Collection report generation | Admin/staff | Run report endpoints | File downloads / response OK |

## 8) Attendance (Staff + Admin)

| ID | Scenario | Preconditions | Steps | Expected Result |
|---|---|---|---|---|
| ATT-01 | Staff attendance entry | Staff user | Open `/add_attendance/` → submit | Attendance saved |
| ATT-02 | Staff attendance report | Staff user | Open `/attendance_report/` | Only own/allowed scope shown |
| ATT-03 | Admin attendance report | Admin-like | Open `/attendance_report_admin/` | All tenant staff shown |
| ATT-04 | Attendance PDF generation | Valid data | Call PDF endpoints | PDF generated successfully |

## 9) Sales (Staff + Admin)

| ID | Scenario | Preconditions | Steps | Expected Result |
|---|---|---|---|---|
| SAL-01 | Add sales | Staff with permission | Open `/add_sales/` → submit | Sale created; tenant scoped |
| SAL-02 | Sales list (admin) | Admin-like | Open `/sales_list/` | Tenant-wide list shown |
| SAL-03 | My sales list (staff) | Staff | Open `/my_sales_list/` | Only own sales shown if scoped |
| SAL-04 | Edit blocked after approval (non-admin) | Staff user, approved sale | Edit via API | 403 denied |
| SAL-05 | Edit allowed after approval (admin-like) | Admin-like user | Edit via API | Saved successfully |
| SAL-06 | Send message | Valid sale | Call `/api/send_message_sales/` | Message queued/sent; status updated |

## 10) Orders

| ID | Scenario | Preconditions | Steps | Expected Result |
|---|---|---|---|---|
| ORD-01 | Add order | Staff/admin | Open `/add_order/` → submit | Order created |
| ORD-02 | Orders list | Admin-like | Open `/orders_list/` | Tenant-wide orders shown |
| ORD-03 | My orders list | Staff | Open `/my_orders_list/` | Only own orders shown if scoped |
| ORD-04 | Approve order | Approver user | POST `/api/approve_order/` | Order marked approved |
| ORD-05 | Order PDF generation | Valid order | Call `/api/generate_order_pdf/` | PDF generated |
| ORD-06 | Attachment delete permission | Non-owner | Call `/api/delete_order_attachment/` | Access denied / blocked |

## 11) Cash Counter + Books

| ID | Scenario | Preconditions | Steps | Expected Result |
|---|---|---|---|---|
| CASH-01 | Cash counter home loads | Staff with module perms | Open `/cash_counter_home/` | Dashboard loads |
| CASH-02 | Add cash counter entry | Staff | POST `/api/add_cash_counter_entry_api/` | Entry created |
| CASH-03 | Edit/update cash counter | Staff/admin | Update endpoints | Updates saved; permission enforced |
| CASH-04 | Delete cash counter | No delete perm | Call `/api/delete_cash_counter/` | Denied |
| CASH-05 | Generate cash counter report | Allowed user | Call `/api/generate_cash_counter_report/` | Report generated |
| CASH-06 | Add collection from cash counter | Valid cash counter entry | Call `/api/add_collection_from_cash_counter_api/` | Collection created and linked |
| BOOK-01 | Cashbook loads | Allowed user | Open `/cashbook/` | Page loads, tenant scoped |
| BOOK-02 | Bankbook loads | Allowed user | Open `/bankbook/` | Page loads, tenant scoped |

## 12) Parties, Banks, Ledger

| ID | Scenario | Preconditions | Steps | Expected Result |
|---|---|---|---|---|
| PARTY-01 | Party group list | Admin-like | Open `/party_group_list/` | Tenant-only groups |
| PARTY-02 | Party list | Admin-like | Open `/party_list/` | Tenant-only parties |
| PARTY-03 | Party ledger page | Admin/staff with permission | Open `/party-ledger/<party_id>/` | Ledger visible (scoped) |
| BANK-01 | Bank list | Admin-like | Open `/bank_list/` | Tenant-only banks |
| BANK-02 | Bank balances API | Allowed user | Call `/api/get_bank_balances_api/` | Correct balances |
| BANK-03 | Manual book tx add/edit/delete | Allowed user | Use book tx APIs | CRUD works; tenant scoped |
| BANK-04 | Transfer cash to bank | Allowed user | Call `/api/transfer_cash_to_bank_api/` | Transaction created; balances updated |

## 13) Message History / Integrations

| ID | Scenario | Preconditions | Steps | Expected Result |
|---|---|---|---|---|
| MSG-01 | Message list loads | Allowed user | Open `/message_list/` | Messages visible |
| MSG-02 | Resend message | Message exists | Call `/api/re_send_message_sales/` | Message resend triggered |
| MSG-03 | Unsent message polling | Unsent messages exist | Call `/api/get_all_unsent_messages/` | Returns pending messages |
| MSG-04 | Update sent status | Allowed user | Call `/api/update_sent_msg_status/` | Status updated |
| MSG-05 | Enable tally posting | Admin-like | Call `/api/enable_tally_post_api/` | Flag enabled/updated |

## 14) Security / Hardening (Project-level)

| ID | Scenario | Preconditions | Steps | Expected Result |
|---|---|---|---|---|
| SEC-01 | CSRF enforced on state-changing endpoints | Normal session | Attempt POST without CSRF token (browser) | Rejected with CSRF error (403) where applicable |
| SEC-02 | Auth required for protected endpoints | Logged out | Call protected URL | Redirect to login / 403 |
| SEC-03 | Tenant isolation | 2 tenants | Attempt to access other tenant object by ID | Not found/denied; no cross-tenant data leak |
| SEC-04 | Permission isolation | Read-only group | Attempt add/edit/delete | Denied (403) |
| SEC-05 | Session fixation prevention | Login/logout cycle | Re-login | New session issued; prior session invalid |

## 15) Regression Checklist (Quick)

- Activation expired allowlist + redirects + JSON API behaviour
- License reminder message formatting `DD-MM-YYYY`
- Login/logout + role landing
- Sidebar sections per permission group
- Staff group updates sync Django groups correctly (remove old tenant staff groups)
- OTP: request → customer sees → staff verifies → collection saved with OTP verified
- All CRUD: parties, banks, collections, sales, orders, cash counter (tenant isolation)

