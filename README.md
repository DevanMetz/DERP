# DERP — Devan's Enterprise Resource Planner

An open-source, single-tenant Enterprise Resource Planner (ERP) for small-to-medium businesses. Fully licensed under the **MIT License**.

DERP is designed with a premium, modern glassmorphic look-and-feel, combining robust double-entry financial ledger logic with a highly responsive, personalized client-side workspace.

---

## 🌟 Key Features

### 1. Modern Personalized UI & Workspace
* **Drag-and-Drop Grid Dashboard**: Reorganize app modules on the homepage using native HTML5 drag-and-drop with browser `localStorage` persistent layouts (`130px` square grid).
* **Global Favorites Star Pinning**: Toggle star shortcuts (`★`) on module cards to pin them instantly as gold-amber shortcut badges at the top of the global navbar.
* **Smart Contextual Navigation**: The header detects your current path and shows only navigation tabs relevant to your active app workspace, automatically deduplicating pinned shortcuts to keep the navbar streamlined.

### 2. Automatic Document Generation (P2P & O2C)
* **Purchase-to-Pay (Receipt ➡️ Bill)**: Generate a draft vendor bill directly from a Goods Receipt with a single click. Pre-populates lines with received quantities, unit costs, and expense accounts, preventing duplicate billing.
* **Order-to-Cash (Sales Order ➡️ Shipment/Invoice)**: Confirming a Sales Order automatically confirms the order, creates a draft customer Invoice, and posts `ISSUE` type StockMovements for all stock items.
* **Safe transactional rollbacks**: If raw material quantities are insufficient during confirmation, the transaction is fully rolled back, preserving stock values.
* **GL Double-Posting Prevention**: Skips duplicate stock issue movements when posting the draft invoice to SENT, while still posting balanced COGS and Inventory GL entries.
* **Draft Deletion Clean-up**: Deleting a draft invoice automatically reverses the stock issue by posting reversing `RECEIPT` movements to return items to inventory.

### 3. Balanced Double-Entry Financial Ledger
* **Immutable Journal Entries**: All business transactions are processed through the core posting service (`accounting.services.post_transaction()`), guaranteeing balanced DR/CR totals.
* **Strict Reversing Voids**: Voiding invoices, bills, or manual journal entries generates gap-free reversing entries (DR/CR swapped), keeping historical ledger records immutable.
* **Financial Reports**: Trial Balance, Balance Sheet, Profit & Loss statement, and General Ledger with full account drill-downs to individual journal entries.

### 4. Inventory, Costing & Valuation
* **Weighted Average Costing (WAC)**: Every stock `RECEIPT` or positive `ADJUSTMENT` movement automatically recalculates the product's average cost base:
  $$\text{New Cost} = \frac{(\text{Current Qty} \times \text{Current Cost}) + (\text{Received Qty} \times \text{Received Cost})}{\text{Current Qty} + \text{Received Qty}}$$
* **Automatic COGS Postings**: Invoice postings automatically issue physical stock and write balanced **DR COGS (5100)** / **CR Inventory (1300)** lines into the journal transaction.
* **Low-Stock Alerting**: High-impact red warning badges in the catalog and a dedicated dashboard widget alert operators of items below their low-stock thresholds.

### 5. Manufacturing & Recipes (BOMs)
* **Dynamic Cost Rollups**: Calculate finished goods' standard recipe unit costs dynamically based on component raw material costs and product usage ratios.
* **Interactive Shortage Visualizer**: The Manufacturing Order detail page matches raw component target requirements against current stock-on-hand, alerting the operator of shortages in red/green badges.
* **Atomic Completion**: Completing a Manufacturing Order atomically issues all component materials, receives the finished goods, and posts a balanced double-entry GL transaction.

### 6. Interactive Analytics Dashboard
* **Real-time Financial Indicators**: Real-time YTD Sales Revenue, Cost base, Net Profit, and outstanding AR/AP positions.
* **Book Valuation Reconciliation**: A validation engine matching General Ledger inventory accounts against live physical stock values to identify variances immediately.
* **Beautiful Charting**: Interactive Chart.js charts detailing month-by-month cash flows and doughnut charts dividing warehouse inventory value (with top-5 item isolating and dynamic grouping).

### 7. PDF Document Downloads
* **On-Demand PDF Generation**: Single-click downloading of professional vector-based PDF documents for Sales Orders, Invoices, and Purchase Orders generated on-the-fly via ReportLab.
* **DERP Corporate Styling**: Styled with unified brand typography, corporate navy headers, alternating table row shading, and structured notes/totals bands.

### 8. Custom Product Image Icons & Avatars
* **Visual Catalog Thumbnails**: Renders `40x40px` rounded image icons for products in the master inventory list.
* **Responsive Visual Fallbacks**: Generates a stylish, color-coded SKU letter-initial placeholder matching the product type when no image is uploaded.
* **Premium Detail Avatars**: Displays a high-resolution `96x96px` rounded avatar on the product detail page alongside KPI costings.
* **Multipart Form Uploads**: Seamless file upload selectors in the product editor using robust Pillow-backed validations.

### 9. System-wide Deep Linking
* **Every Record Linked**: All document cross-references throughout the UI are interactive — customer/vendor names, product SKUs, invoice numbers, journal entry references, goods receipt numbers, and manufacturing order numbers all link directly to their respective detail pages.
* **Financial Report Drill-downs**: Account codes and names in Trial Balance, Balance Sheet, and P&L reports link directly to the filtered General Ledger view for that account. Individual GL entries link to their source journal.
* **Source Document Linking**: Posted journal entries display and link to the originating source document (Invoice, Bill, Goods Receipt, Manufacturing Order).

### 10. Customer & Vendor Profile Dashboards
* **Customer Dashboard**: Full profile view showing contact info, billing/shipping addresses, lifetime posted revenue, outstanding AR balance, and a chronological history of all related Sales Orders and Invoices.
* **Vendor Dashboard**: Full profile view showing contact info, default expense account, business address, internal notes, lifetime purchases, outstanding AP balance, and a chronological history of all related Purchase Orders and Bills.
* **List-Level Action Splits**: Customer and vendor list views provide separate **View** (profile dashboard) and **Edit** actions for each record.

### 11. Data Export
* **ZIP Archive of CSVs**: Export any combination of tables to a ZIP file of Excel-ready CSV spreadsheets. Foreign Key fields are written as relational primary key IDs for clean re-importability.
* **Django JSON Backups**: Full database snapshots in standard Django fixture format, fully restoration-ready via `manage.py loaddata`.
* **Selective Export Dashboard**: Glassmorphic UI with per-table checkboxes and a master Select/Deselect All toggle with live record-count display.

### 12. Data Import
* **Atomic CSV Bulk Upload**: Map CSV columns to database fields with create-or-update logic. If an `id` column matches existing records they are updated in-place; otherwise new records are created. Full rollback on any validation error.
* **JSON Backup Restoration**: Restore full database states from standard Django JSON fixture backups, preserving all Foreign Keys and primary key values.
* **Detailed Error Reporting**: Row-specific error messages (e.g., `Row 4: Related 'default_expense_account' with ID '9999' does not exist`) for easy debugging of import files.

---

## 🛠️ Technology Stack

| Layer | Technology |
|---|---|
| **Core Backend** | Python 3.13+, Django 5.1 |
| **Database** | PostgreSQL 15+ |
| **Document Generation** | ReportLab (vector PDF) |
| **Image Handling** | Pillow |
| **Frontend Charting** | Chart.js |
| **UI Interactions** | HTML5 Drag & Drop API, Browser `localStorage` |
| **Audit Logging** | `django-simple-history` |
| **Styling** | Custom modern CSS (glassmorphism, responsive grid) |

---

## 📂 Directory Layout

```
config/             Django project settings, urls, routing
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

## 🚀 Getting Started & Setup

### Prerequisites
* Python 3.13+ installed
* PostgreSQL 15+ server running and accessible
* Virtual environment configured

### Installation & Run

1. Clone the repository and navigate to the directory:
   ```bash
   git clone https://github.com/DevanMetz/DERP.git
   cd DERP
   ```
2. Set up a virtual environment and activate it:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   ```
3. Install package dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy the environment template and configure your database credentials:
   ```bash
   cp .env.example .env
   # Edit .env with your PostgreSQL connection details
   ```
5. Apply database schema migrations:
   ```bash
   python manage.py migrate
   ```
6. Seed the Chart of Accounts:
   ```bash
   python manage.py seed_chart_of_accounts
   ```
7. Create an Administrator account:
   ```bash
   python manage.py createsuperuser
   ```
8. Start the local development server:
   ```bash
   python manage.py runserver 8001
   ```
   Then open [http://localhost:8001](http://localhost:8001) in your browser.

### Running Automated Tests

Run the full Django unit test suite covering WAC costing, document generation, atomic rollbacks, void reversals, PDF generation, data import/export, and more:
```bash
python manage.py test
```

> **68 unit tests** across all modules — all green. ✅

---

## 🗂️ Module Overview

| Module | URL Prefix | Description |
|---|---|---|
| Home / Workspace | `/` | Drag-and-drop app grid, low-stock alerts |
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

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for the full text.
