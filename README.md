# DERP — Open Source ERP Software for Small Business

**DERP** (Devan's Enterprise Resource Planner) is a **free, open source ERP** for small and medium businesses. It bundles **double-entry accounting**, **inventory management with weighted average costing**, **sales orders & invoicing**, **purchase orders & bills**, and **manufacturing with bills of materials (BOMs)** into a single, lightweight Django application. **MIT licensed** — self-host it on your own server, or use the hosted version free.

> **Open source ERP · Free ERP software · Self-hosted ERP · Multi-tenant SaaS · Django ERP**

🌐 **Hosted version:** **[inventorymanager.xyz](https://inventorymanager.xyz)** — sign up and get a private workspace at `yourcompany.inventorymanager.xyz` in under a minute.
📂 **Source code:** [github.com/DevanMetz/DERP](https://github.com/DevanMetz/DERP)
📜 **License:** MIT — use it for anything, including commercial projects.

## Why DERP?

Most ERPs are either prohibitively expensive (per-seat SaaS) or sprawling enterprise platforms that take a team of consultants to deploy. DERP is the opposite:

- **Truly free** — MIT license, no per-seat fees, no contributor agreement, no "open core" upsell
- **Lightweight** — one Django codebase, no microservices, no plugin marketplace
- **Self-host or hosted** — run it yourself on a $5 VPS, or use the free hosted version
- **Your data is yours** — standard PostgreSQL, export to CSV or JSON anytime
- **Production-ready** — schema-per-tenant isolation, Argon2 password hashing, HTTPS+HSTS, rate limiting, CAPTCHA

---

## 🌟 Key Features

### 1. Multi-Tenant SaaS Architecture
* **Schema-per-tenant isolation**: Each company runs in its own private PostgreSQL schema — zero data bleed between customers.
* **Self-serve signup**: Enter a company name, subdomain, email, and password. Your workspace is provisioned and Chart of Accounts is seeded automatically.
* **Subdomain routing**: Every tenant gets a dedicated URL (`acme.inventorymanager.xyz`). The `django-tenants` middleware routes all requests to the correct schema transparently.

### 2. Modern Personalized UI & Workspace
* **Drag-and-Drop Grid Dashboard**: Reorganize app modules on the homepage using native HTML5 drag-and-drop with browser `localStorage` persistent layouts (`130px` square grid).
* **Global Favorites Star Pinning**: Toggle star shortcuts (`★`) on module cards to pin them instantly as gold-amber shortcut badges at the top of the global navbar.
* **Smart Contextual Navigation**: The header detects your current path and shows only navigation tabs relevant to your active app workspace, automatically deduplicating pinned shortcuts.

### 3. Automatic Document Generation (P2P & O2C)
* **Purchase-to-Pay (Receipt ➡️ Bill)**: Generate a draft vendor bill directly from a Goods Receipt with a single click. Pre-populates lines with received quantities, unit costs, and expense accounts, preventing duplicate billing.
* **Order-to-Cash (Sales Order ➡️ Shipment/Invoice)**: Confirming a Sales Order automatically confirms the order, creates a draft customer Invoice, and posts `ISSUE` type StockMovements for all stock items.
* **Safe transactional rollbacks**: If raw material quantities are insufficient during confirmation, the transaction is fully rolled back, preserving stock values.
* **GL Double-Posting Prevention**: Skips duplicate stock issue movements when posting the draft invoice to SENT, while still posting balanced COGS and Inventory GL entries.
* **Draft Deletion Clean-up**: Deleting a draft invoice automatically reverses the stock issue by posting reversing `RECEIPT` movements to return items to inventory.

### 4. Balanced Double-Entry Financial Ledger
* **Immutable Journal Entries**: All business transactions are processed through the core posting service (`accounting.services.post_transaction()`), guaranteeing balanced DR/CR totals.
* **Strict Reversing Voids**: Voiding invoices, bills, or manual journal entries generates gap-free reversing entries (DR/CR swapped), keeping historical ledger records immutable.
* **Financial Reports**: Trial Balance, Balance Sheet, Profit & Loss statement, and General Ledger with full account drill-downs to individual journal entries.

### 5. Inventory, Costing & Valuation
* **Weighted Average Costing (WAC)**: Every stock `RECEIPT` or positive `ADJUSTMENT` movement automatically recalculates the product's average cost base:
  $$\text{New Cost} = \frac{(\text{Current Qty} \times \text{Current Cost}) + (\text{Received Qty} \times \text{Received Cost})}{\text{Current Qty} + \text{Received Qty}}$$
* **Automatic COGS Postings**: Invoice postings automatically issue physical stock and write balanced **DR COGS (5100)** / **CR Inventory (1300)** lines into the journal transaction.
* **Low-Stock Alerting**: High-impact red warning badges in the catalog and a dedicated dashboard widget alert operators of items below their low-stock thresholds.

### 6. Manufacturing & Recipes (BOMs)
* **Dynamic Cost Rollups**: Calculate finished goods' standard recipe unit costs dynamically based on component raw material costs and product usage ratios.
* **Interactive Shortage Visualizer**: The Manufacturing Order detail page matches raw component target requirements against current stock-on-hand, alerting the operator of shortages in red/green badges.
* **Atomic Completion**: Completing a Manufacturing Order atomically issues all component materials, receives the finished goods, and posts a balanced double-entry GL transaction.

### 7. Interactive Analytics Dashboard
* **Real-time Financial Indicators**: Real-time YTD Sales Revenue, Cost base, Net Profit, and outstanding AR/AP positions.
* **Book Valuation Reconciliation**: A validation engine matching General Ledger inventory accounts against live physical stock values to identify variances immediately.
* **Beautiful Charting**: Interactive Chart.js charts detailing month-by-month cash flows and doughnut charts dividing warehouse inventory value (with top-5 item isolating and dynamic grouping).

### 8. PDF Document Downloads
* **On-Demand PDF Generation**: Single-click downloading of professional vector-based PDF documents for Sales Orders, Invoices, and Purchase Orders generated on-the-fly via ReportLab.
* **DERP Corporate Styling**: Styled with unified brand typography, corporate navy headers, alternating table row shading, and structured notes/totals bands.

### 9. Custom Product Image Icons & Avatars
* **Visual Catalog Thumbnails**: Renders `40x40px` rounded image icons for products in the master inventory list.
* **Responsive Visual Fallbacks**: Generates a stylish, color-coded SKU letter-initial placeholder matching the product type when no image is uploaded.
* **Premium Detail Avatars**: Displays a high-resolution `96x96px` rounded avatar on the product detail page alongside KPI costings.
* **Multipart Form Uploads**: Seamless file upload selectors in the product editor (JPG/PNG/GIF/WebP, max 5 MB).

### 10. System-wide Deep Linking
* **Every Record Linked**: All document cross-references throughout the UI are interactive — customer/vendor names, product SKUs, invoice numbers, journal entry references, goods receipt numbers, and manufacturing order numbers all link directly to their respective detail pages.
* **Financial Report Drill-downs**: Account codes and names in Trial Balance, Balance Sheet, and P&L reports link directly to the filtered General Ledger view for that account.
* **Source Document Linking**: Posted journal entries display and link to the originating source document.

### 11. Customer & Vendor Profile Dashboards
* **Customer Dashboard**: Full profile view showing contact info, billing/shipping addresses, lifetime posted revenue, outstanding AR balance, and a chronological history of all related Sales Orders and Invoices.
* **Vendor Dashboard**: Full profile view showing contact info, default expense account, business address, internal notes, lifetime purchases, outstanding AP balance, and a chronological history of all related Purchase Orders and Bills.

### 12. Data Export & Import
* **ZIP Archive of CSVs**: Export any combination of tables to a ZIP file of Excel-ready CSV spreadsheets.
* **Django JSON Backups**: Full database snapshots in standard Django fixture format, fully restoration-ready via `manage.py loaddata`.
* **Atomic CSV Bulk Upload**: Map CSV columns to database fields with create-or-update logic and full rollback on any validation error.
* **JSON Backup Restoration**: Restore full database states from standard Django JSON fixture backups.

---

## 🛠️ Technology Stack

| Layer | Technology |
|---|---|
| **Core Backend** | Python 3.13+, Django 5.1 |
| **Database** | PostgreSQL 15+ |
| **Multi-tenancy** | django-tenants (schema-per-tenant) |
| **Authentication** | django-allauth (email + TOTP 2FA) |
| **Password Hashing** | Argon2 |
| **Document Generation** | ReportLab (vector PDF) |
| **Image Handling** | Pillow |
| **Frontend Charting** | Chart.js |
| **UI Interactions** | HTML5 Drag & Drop API, Browser `localStorage` |
| **Audit Logging** | django-simple-history |
| **Styling** | Custom modern CSS (glassmorphism, responsive grid) |
| **Hosting** | Railway (PostgreSQL + Gunicorn + Whitenoise) |

---

## 📂 Directory Layout

```
config/             Django project settings, urls, routing
tenants/            Tenant/domain models, self-serve signup, rate limiting
core/               Company profile, dashboard analytics, data import/export
accounting/         Accounts, Journal Entries, Payments, posting engine, financial reports
inventory/          Products, Stock Movements, Stock On Hand, costing calculations
sales/              Customers, Sales Orders, Customer Invoices
purchasing/         Vendors, Purchase Orders, Goods Receipts, Bills
manufacturing/      Bill of Materials (BOM), BOM Components, Manufacturing Orders
templates/          Server-rendered templates and base layouts
media/              Uploaded product images (generated at runtime)
```

---

## 🚀 Getting Started

### Option A — Use the hosted version

Go to **[inventorymanager.xyz](https://inventorymanager.xyz)**, click **Create your workspace**, and you're up in under a minute.

### Option B — Self-host

#### Prerequisites
* Python 3.13+
* PostgreSQL 15+
* Virtual environment

#### Installation

1. Clone and enter the repo:
   ```bash
   git clone https://github.com/DevanMetz/DERP.git
   cd DERP
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Copy the environment template and configure your database:
   ```bash
   cp .env.example .env
   # Edit .env — set DATABASE_URL and BASE_DOMAIN at minimum
   ```

5. Run shared-schema migrations (tenant routing tables):
   ```bash
   python manage.py migrate_schemas --shared
   ```

6. Create the public tenant record:
   ```bash
   python manage.py create_public_tenant
   ```

7. Start the development server:
   ```bash
   python manage.py runserver 8001
   ```

   Then visit [http://localhost:8001](http://localhost:8001) to sign up and create a tenant.

#### Running Tests

```bash
python manage.py test
```

> **68 unit tests** across all modules — all green. ✅

---

## 🗂️ Module Overview

| Module | URL Prefix | Description |
|---|---|---|
| Landing / Signup | `/` (public domain) | Marketing page and self-serve tenant signup |
| Home / Workspace | `/` (tenant subdomain) | Drag-and-drop app grid, low-stock alerts |
| Analytics Dashboard | `/dashboard/` | KPIs, Chart.js revenue & inventory charts |
| Products | `/products/` | Inventory catalog, costing, image uploads |
| Stock Movements | `/stock-movements/` | Receipt, Issue, Adjustment ledger |
| Customers | `/customers/` | Profile dashboards, AR tracking |
| Sales Orders | `/sales-orders/` | O2C order management, PDF download |
| Invoices | `/invoices/` | Customer invoicing, void, PDF download |
| Vendors | `/vendors/` | Profile dashboards, AP tracking |
| Purchase Orders | `/purchase-orders/` | P2P order management, PDF download |
| Goods Receipts | `/goods-receipts/` | Warehouse receiving, auto-bill generation |
| Bills | `/bills/` | Vendor billing, void |
| Accounts | `/accounts/` | Chart of Accounts |
| Journal Entries | `/journals/` | Manual JE creation, void/reverse |
| Payments | `/payments/` | AR/AP payment application |
| Reports | `/reports/` | Trial Balance, Balance Sheet, P&L, GL |
| Bill of Materials | `/boms/` | Recipe management, cost rollups |
| Manufacturing Orders | `/manufacturing-orders/` | Production runs, shortage checking |
| Data Export | `/export/` | CSV ZIP & JSON fixture downloads |
| Data Import | `/import/` | CSV bulk upload & JSON restoration |

---

## 🔒 Security

* HTTPS enforced in production with HSTS (1 year, including subdomains)
* Argon2 password hashing
* Login rate limiting: 5 failed attempts per 5 minutes per account
* Signup rate limiting: 5 attempts per hour per IP (database-backed)
* CSRF protection on all state-changing requests
* `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`
* File uploads capped at 5 MB with extension whitelist
* Schema isolation: tenant data is physically separated at the PostgreSQL schema level

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for the full text.
