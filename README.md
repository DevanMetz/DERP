# DERP - Open Source ERP Software for Small Business

**DERP** (Devan's Enterprise Resource Planner) is a free, open source ERP for small and medium businesses. It combines double-entry accounting, inventory management with weighted-average costing, sales orders and invoicing, purchase orders and bills, manufacturing with bills of materials, and tenant-based workspaces in a lightweight Django application.

DERP is MIT licensed. You can self-host it, modify it, or use it as the foundation for a business system without per-seat fees or open-core restrictions.

- Hosted version: [inventorymanager.xyz](https://inventorymanager.xyz)
- Source code: [github.com/DevanMetz/DERP](https://github.com/DevanMetz/DERP)
- License: MIT, see [LICENSE](LICENSE)

## Why DERP?

Most ERP systems are either expensive SaaS products or large enterprise platforms that take significant time to deploy. DERP aims for the middle ground: a practical, understandable ERP that small teams can run and extend.

- Free and open source under the MIT license
- One Django codebase with server-rendered templates
- Schema-per-tenant isolation with `django-tenants`
- PostgreSQL-backed business records and standard export paths
- Accounting, inventory, sales, purchasing, and manufacturing in one system
- Service-layer workflows for financial and stock integrity

## Key Features

### Multi-Tenant SaaS Architecture

- Schema-per-tenant isolation: each company runs in its own PostgreSQL schema.
- Self-serve signup: company name, subdomain, email, and password create a workspace.
- Subdomain routing: `django-tenants` maps each request to the correct tenant schema.
- Public landing/signup routes are separated from tenant workspace routes.

### Workspace and Navigation

- Tenant home workspace with application module shortcuts.
- Dashboard views for operational and financial indicators.
- Contextual navigation for accounting, inventory, sales, purchasing, and manufacturing workflows.
- Company setup, data import, data export, and global search screens.

### Accounting

- Double-entry journal posting through `accounting.services.post_transaction()`.
- Balanced debit and credit validation before entries are persisted.
- Immutable posted journal entries.
- Reversing entries for voids and corrections.
- Gap-free document numbering backed by the core numbering service.
- Financial reports:
  - Trial Balance
  - Balance Sheet
  - Income Statement
  - General Ledger with drill-down links

### Inventory

- Product catalog with stock and service item types.
- Stock movement ledger for receipts, issues, and adjustments.
- Weighted-average cost recalculation on stock receipts and positive adjustments.
- Stock-on-hand tracking with validation against over-issuing.
- Low-stock thresholds and dashboard alerts.
- Product image uploads with media storage support.

### Sales

- Customer records and customer profile dashboards.
- Sales order creation, confirmation, invoicing, and undo flows.
- Automatic draft invoice creation when confirming a sales order.
- Stock issue creation during sales order confirmation.
- Invoice posting with AR, revenue, tax, COGS, and inventory postings.
- Invoice voiding through reversing journal entries.
- PDF downloads for sales orders and invoices.

### Purchasing

- Vendor records and vendor profile dashboards.
- Purchase order issue and unissue flows.
- Goods receipts with inventory updates.
- Draft bill creation from purchase orders or goods receipts.
- Duplicate bill prevention for goods receipt billing.
- Bill posting and voiding through accounting journal entries.
- Goods receipt reversal with stock rollback.
- PDF downloads for purchase orders.

### Manufacturing

- Bills of materials for finished goods recipes.
- BOM component validation to prevent circular finished-good usage.
- Cost rollups from component product costs.
- Manufacturing order draft, confirm, complete, and cancel flows.
- Completion issues raw materials, receives finished goods, updates finished-good cost, and posts balanced GL entries.
- Shortage validation rolls the whole completion transaction back when materials are insufficient.

### Data Import and Export

- JSON fixture export for backup and restoration workflows.
- ZIP archive export of CSV files.
- CSV import with create-or-update behavior.
- JSON backup restoration with transactional rollback on failure.

### PDF Documents

- ReportLab-based PDF generation for:
  - Sales Orders
  - Invoices
  - Purchase Orders

## Technology Stack

| Layer | Technology |
| --- | --- |
| Backend | Python, Django 5.x |
| Database | PostgreSQL |
| Multi-tenancy | django-tenants |
| Authentication | Django auth, django-allauth |
| MFA support | django-allauth MFA, fido2 |
| Audit history | django-simple-history |
| HTMX support | django-htmx |
| Password hashing | Argon2 |
| PDF generation | ReportLab |
| Image handling | Pillow |
| Static files | Whitenoise |
| Production server | Gunicorn |

See [requirements.txt](requirements.txt) for exact dependency ranges.

## Directory Layout

```text
config/             Django settings, URL routing, WSGI, public URL config
tenants/            Tenant/domain models, public signup, tenant provisioning
core/               User model, company profile, dashboard, import/export, numbering
accounting/         Accounts, journal entries, posting service, reports
inventory/          Products, stock movements, stock-on-hand, costing logic
sales/              Customers, sales orders, invoices, customer payments
purchasing/         Vendors, purchase orders, goods receipts, bills, vendor payments
manufacturing/      BOMs, BOM components, manufacturing orders
projects/           Project app placeholder
templates/          Server-rendered Django templates
media/              Runtime-uploaded media files
```

## Getting Started

### Option A - Use the hosted version

Go to [inventorymanager.xyz](https://inventorymanager.xyz), create a workspace, and use your company subdomain.

### Option B - Run locally

#### Prerequisites

- Python 3.11 or newer
- PostgreSQL
- A virtual environment

#### Installation

1. Clone and enter the repo:

   ```bash
   git clone https://github.com/DevanMetz/DERP.git
   cd DERP
   ```

2. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   ```

   Windows:

   ```powershell
   .venv\Scripts\activate
   ```

   macOS/Linux:

   ```bash
   source .venv/bin/activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Copy the environment template:

   ```bash
   cp .env.example .env
   ```

   Configure at least:

   ```text
   SECRET_KEY=...
   DEBUG=True
   DATABASE_URL=postgres://erp:erp@localhost:5432/erp
   BASE_DOMAIN=localhost
   ```

5. Run shared-schema migrations:

   ```bash
   python manage.py migrate_schemas --shared
   ```

6. Create the public tenant:

   ```bash
   python manage.py create_public_tenant
   ```

7. Start the development server:

   ```bash
   python manage.py runserver 8001
   ```

8. Open [http://localhost:8001](http://localhost:8001) and create a tenant workspace.

## Environment Variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `SECRET_KEY` | Django secret key | `dev-only-do-not-use-in-prod` |
| `DEBUG` | Enables development behavior | `False` |
| `DATABASE_URL` | PostgreSQL connection string | `postgres://erp:erp@localhost:5432/erp` |
| `BASE_DOMAIN` | Root domain for tenant subdomains | `localhost` |
| `ALLOWED_HOSTS` | Comma-separated host allowlist | `localhost,127.0.0.1` |
| `RAILWAY_PUBLIC_DOMAIN` | Optional Railway host | empty |
| `TURNSTILE_SITE_KEY` | Cloudflare Turnstile site key | empty |
| `TURNSTILE_SECRET_KEY` | Cloudflare Turnstile secret key | empty |
| `RESEND_API_KEY` | Resend SMTP/API key | empty |
| `DEFAULT_FROM_EMAIL` | Outbound email sender | `noreply@inventorymanager.xyz` |

## Running Tests

Run the full test suite:

```bash
python manage.py test
```

The repository currently contains 72 test methods across the core, accounting, inventory, sales, purchasing, and manufacturing modules. The highest-value tests cover financial posting integrity, stock movement validation, sales order automation, purchasing receipt/bill flows, manufacturing completion, import/export, PDFs, and dashboard/report behavior.

For focused runs:

```bash
python manage.py test accounting
python manage.py test inventory
python manage.py test sales
python manage.py test purchasing
python manage.py test manufacturing
python manage.py test core
```

## Module Routes

| Area | URL paths |
| --- | --- |
| Public landing/signup | `/` on the public domain |
| Tenant home | `/` on a tenant workspace |
| Dashboard | `/dashboard/` |
| Company setup | `/company/` |
| Data export | `/export/` |
| Data import | `/import/` |
| Search | `/search/` |
| Products | `/products/`, `/products/new/`, `/products/<id>/`, `/products/<id>/edit/` |
| Inventory ledger | `/products/ledger/` |
| Customers | `/customers/`, `/customers/new/`, `/customers/<id>/`, `/customers/<id>/edit/` |
| Sales orders | `/sales-orders/`, `/sales-orders/new/`, `/sales-orders/<id>/` |
| Sales order actions | `/sales-orders/<id>/confirm/`, `/unconfirm/`, `/invoice/`, `/undo-invoice/`, `/pdf/` |
| Invoices | `/invoices/`, `/invoices/new/`, `/invoices/<id>/` |
| Invoice actions | `/invoices/<id>/post/`, `/void/`, `/pdf/` |
| Customer payments | `/payments/new/` |
| Vendors | `/vendors/`, `/vendors/new/`, `/vendors/<id>/`, `/vendors/<id>/edit/` |
| Purchase orders | `/purchase-orders/`, `/purchase-orders/new/`, `/purchase-orders/<id>/` |
| Purchase order actions | `/purchase-orders/<id>/issue/`, `/unissue/`, `/receive/`, `/bill/`, `/undo-bill/`, `/pdf/` |
| Goods receipts | `/goods-receipts/<id>/`, `/goods-receipts/<id>/bill/`, `/goods-receipts/<id>/reverse/` |
| Bills | `/bills/`, `/bills/new/`, `/bills/<id>/` |
| Bill actions | `/bills/<id>/post/`, `/void/` |
| Vendor payments | `/vendor-payments/new/` |
| Journals | `/journal/`, `/journal/new/`, `/journal/<id>/`, `/journal/<id>/reverse/` |
| Reports | `/reports/trial-balance/`, `/reports/income-statement/`, `/reports/balance-sheet/`, `/reports/general-ledger/` |
| BOMs | `/boms/`, `/boms/create/`, `/boms/<id>/`, `/boms/<id>/edit/` |
| Manufacturing orders | `/manufacturing-orders/`, `/manufacturing-orders/create/`, `/manufacturing-orders/<id>/` |
| Manufacturing actions | `/manufacturing-orders/<id>/confirm/`, `/complete/`, `/cancel/` |

## Development Notes

- Business workflows should go through service functions rather than duplicating posting logic in views.
- Posted journal entries are intentionally immutable; corrections should use reversing entries.
- Stock issues should be validated through inventory services so stock-on-hand cannot be overdrawn.
- Multi-step workflows that affect stock and accounting should be atomic.
- Tenant-aware commands and migrations should use the `django-tenants` workflow.

## Security

- HTTPS and HSTS are enabled when `DEBUG=False`.
- Argon2 is the preferred password hasher.
- Login failures are rate-limited through django-allauth settings.
- Signup attempts are tracked by tenant signup logic.
- CSRF protection is enabled for state-changing requests.
- Security headers include `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: strict-origin-when-cross-origin`.
- Upload memory limits are capped at 5 MB.
- Tenant data is separated by PostgreSQL schema.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
