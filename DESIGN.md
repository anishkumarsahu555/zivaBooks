# zivaBooks Design System & UI/UX Standards

This document establishes the **UI/UX Design System, Frontend Architecture, and Visual Standards** for `zivaBooks`. It serves as the single source of truth for UI components, color palettes, responsive layouts, PWA behavior, and printable invoice documents.

---

## 1. Design Philosophy & UX Principles

1. **Information Density with Clarity**: Business operators (accountants, store owners, cashiers) handle heavy transactional data. Layouts prioritize clean readability, fast scanning, and minimal wasted whitespace.
2. **Mobile-First & PWA-Ready**: Seamless operation across desktop monitors, tablets, and smartphones. Navigation adapts between a collapsible desktop sidebar and a bottom navigation bar / mobile drawer.
3. **Speed & Zero-Friction Input**: Invoicing and cash-counter interfaces are optimized for rapid keyboard input (Tab traversal, auto-focus, barcode scanner input).
4. **Consistency**: Unified status colors, Indian Rupee formatting (`₹ 1,50,000.00`), and predictable action placements across all screens.

---

## 2. Color Palette & Semantic Tokens

### 2.1 Brand & Neutral Scale
| Token Name | Hex Code | Usage |
|---|---|---|
| `--color-bg-app` | `#f8fafc` | Global application background (Slate-50) |
| `--color-surface` | `#ffffff` | Card surfaces, modals, table backgrounds |
| `--color-border` | `#e2e8f0` | Dividers, table borders, input outlines (Slate-200) |
| `--color-border-subtle` | `#f1f5f9` | Inner table borders, subtle dividers (Slate-100) |
| `--color-text-main` | `#0f172a` | Primary text, titles, values (Slate-900) |
| `--color-text-muted` | `#64748b` | Secondary captions, timestamps, table headers (Slate-500) |
| `--color-primary` | `#2563eb` | Primary buttons, active tabs, brand accents (Blue-600) |
| `--color-primary-dark`| `#1d4ed8` | Primary button hover/active states (Blue-700) |
| `--color-sidebar-bg` | `#1e293b` | Desktop navigation sidebar background (Slate-800) |

### 2.2 Semantic Status Colors
| Status | Background | Text Color | Meaning / Application |
|---|---|---|---|
| **Success / Paid** | `#dcfce7` (Green-100) | `#15803d` (Green-700) | Fully paid, approved, active license, in-stock |
| **Warning / Partial** | `#fef3c7` (Amber-100) | `#b45309` (Amber-700) | Partially paid, pending approval, expiring batch |
| **Danger / Overdue** | `#fee2e2` (Red-100) | `#b91c1c` (Red-700) | Unpaid/overdue invoice, expired license, out-of-stock, cancelled |
| **Info / Proforma** | `#e0f2fe` (Sky-100) | `#0369a1` (Sky-700) | Proforma invoice, draft status, informational notices |
| **Neutral / Inactive**| `#f1f5f9` (Slate-100) | `#475569` (Slate-600) | Closed session, archived item, disabled toggle |

---

## 3. Typography & Formatting Standards

- **Font Family**: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif, "Apple Color Emoji"`
- **Monospace Font**: `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace` (Used for Invoice Numbers, SKUs, GSTIN, and OTP codes).
- **Type Scale**:
  - `H1 (Page Title)`: `1.5rem (24px)`, Semi-bold (`font-weight: 600`)
  - `H2 (Section Header)`: `1.25rem (20px)`, Semi-bold
  - `Body / Normal`: `0.875rem (14px)`, Regular (`font-weight: 400`)
  - `Small / Metadata`: `0.75rem (12px)`, Medium (`font-weight: 500`)
- **Currency & Indian Numbering**:
  - Always use the custom Django filter `|format_inr` or JS helper for currency display.
  - Correct format: `₹ 1,23,456.78` (Indian Lakhs/Crores grouping).
  - Never format as Western thousand millions (e.g. avoid `₹ 123,456.78`).

---

## 4. UI Components & Layout Guidelines

### 4.1 Metric KPI Cards (Dashboard & Summaries)
- Contained in a standard 4-column responsive grid (`grid-template-columns: repeat(auto-fit, minmax(240px, 1fr))`).
- Structure:
  - Top: Label / Metric title (`text-muted, text-xs uppercase`) + Category Icon.
  - Middle: Big numeric value (`text-2xl font-bold text-main`).
  - Bottom: Comparative trend or sub-label (`text-xs, e.g. "vs previous month"`).

### 4.2 DataTables & List Views
- **Server-Side Integration**: Use DataTables server-side pagination for all tables exceeding 50 records to prevent DOM bloat.
- **Header Styling**: Compact, muted text, uppercase (`text-xs uppercase text-muted bg-slate-50`).
- **Row Styling**: Alternating hover effect (`hover:bg-slate-50`), vertical borders subtle, row height approx. `44px` for touch safety.
- **Action Column**: Dropdown button or icon buttons aligned to the right.

### 4.3 Form Design & Invoicing Tables
- **GSTIN Input Mask**: Auto-capitalized, monospace, 15 characters, real-time regex validation (`^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$`).
- **Dynamic Line Items**:
  - Item Autocomplete Search (searches item name, SKU, barcode).
  - Batch Dropdown (auto-populates with FEFO suggestion, shows expiry date and stock).
  - Quantity input (numeric, min 1).
  - Rate, Discount (%), GST profile (auto-applied from item master), Line Amount.
  - Real-time tax breakdown card showing Taxable Total, CGST, SGST, IGST, Round-off, and Grand Total.

### 4.4 Modals & Drawer Overlays
- Modal overlays must use `backdrop-filter: blur(4px); background: rgba(15, 23, 42, 0.5);`.
- Pressing `Escape` key or clicking outside closes the modal.
- Focus is automatically trapped within the modal when opened.

---

## 5. Printable Document Templates (WeasyPrint & Thermal)

### 5.1 A4 Tax Invoices (`reports/invoice_pdf.html`)
- **Engine**: WeasyPrint (`weasyprint>=68.1`).
- **CSS Specifications**:
  ```css
  @page {
    size: A4 portrait;
    margin: 12mm 10mm 15mm 10mm;
    @bottom-right {
      content: "Page " counter(page) " of " counter(pages);
      font-size: 8pt;
      color: #64748b;
    }
  }
  ```
- **Structure**:
  1. Header: Tenant Logo, Business Legal Name, Address, GSTIN, State & Code.
  2. Invoice Metadata Block: Invoice Number, Date, Place of Supply, Reverse Charge (Y/N).
  3. Bill To / Ship To: Customer Legal Name, Address, GSTIN, State.
  4. Itemized Table: S.No, Item Description, HSN/SAC, Qty, Unit, Rate, Discount, Taxable Value, CGST/SGST/IGST rates and amounts.
  5. Summary & Bank Details: Amount in words, Bank Name, Account No, IFSC, UPI QR Code.
  6. Terms & Conditions and Authorized Signatory Box.

### 5.2 POS Thermal Receipts (58mm & 80mm)
- **Engine**: Browser Direct Print / ESC-POS formatting.
- **Specifications**:
  - Width: Exactly `80mm` (or `58mm` depending on printer profile).
  - Margin: `0mm`.
  - High-contrast pure monochrome (`#000000` text, no background fills).
  - Compact monospace layout (`font-size: 9pt; line-height: 1.2;`).
  - Barcode / QR code centered at the bottom for quick invoice scan/return lookup.

---

## 6. Progressive Web App (PWA) Standards

- **Manifest**: Managed via `django-pwa` (`manifest.json`).
- **Service Worker**: Caches offline shell assets (`static/css/`, `static/js/`, PWA icons, offline fallback page).
- **Safe Area Insets**: All full-screen mobile views must account for notch safe areas:
  ```css
  padding-top: env(safe-area-inset-top);
  padding-bottom: env(safe-area-inset-bottom);
  ```
- **Network Status Banner**: Persistent unobtrusive pill alert appears when client connectivity is lost (`"You are offline. Showing cached data."`).

---

## 7. Design System Update Protocol

Whenever:
1. A new UI component or widget is added to templates or static CSS,
2. Invoice PDF layouts or thermal print templates are modified,
3. Color tokens or status badge mappings are updated,

**Update this file immediately** to preserve design consistency across all screens and reports.
