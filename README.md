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

### Public Tenant Website & Full-Viewport Page Builder

- **Root Domain Isolation**: The entire ERP lives under the `/derp/` subpath prefix, serving the public website directly on the root domain `/`.
- **Full-Viewport WYSIWYG Editor**: An immersive page builder with a slim top bar, collapsible left tool rail (Blocks · Inspector · Page · SEO · History · Code), and the live preview itself as the editing canvas.
- **Viewport Switcher**: Desktop / Tablet (820px) / Mobile (390px) with animated transitions to verify responsive behavior in place.
- **Block Library**: 19 block types covering Hero, Features, Pricing, Stats, FAQ, CTA, Text, Image+Text, Testimonials, Logo Cloud, Team, Contact Form, Newsletter, Gallery, Video, plus a dedicated Webstore group (Product Grid, Featured Product, Shop Benefits, Categories).
- **Inline Text Editing**: Click directly on headings, paragraphs, buttons, list items, or links on the canvas and start typing copy in-place.
- **Drag-and-Drop with Live Drop Indicator**: Drag block cards into the iframe canvas; a blue insertion line shows the exact landing position.
- **Per-Section Inspector**: Click any section to open style controls — padding slider, border radius, background presets, text alignment, move up/down, delete.
- **Bi-directional HTML Parser**: Toggle between raw HTML code and the visual canvas seamlessly.
- **Quick-Start Templates**: One-click scaffolds for Home, About, Contact, or a full Webstore Landing page.
- **Global Theme Settings**: Centralized customization of Website Brand Name, Logo Image, Google Fonts selectors (Inter, Roboto, Outfit, Poppins, Playfair Display), HSL Brand Colors, and a global custom CSS block.
- **Page Revision History**: Chronological `PageRevision` log to record backups on save and restore past versions with one click.
- **Local Draft Autosave**: 15-second autosave to `localStorage` with a recovery toast on reopen.
- **Advanced SEO Controls**: Custom keyword tags, meta summaries, Open Graph sharing cards, and character counters.

### Webstore & Stripe Connect Payments

- **Catalog & PDP**: Public storefront under `/shop/` with category listings, product detail pages, image galleries, sale pricing (strike-through compare-at), and a Featured product strip.
- **Session Cart with HTMX**: Add to cart, change quantities, and remove items without page reloads. Live cart badge in the public header.
- **Multi-Tenant Stripe Connect (Standard Accounts)**: Each tenant connects their own Stripe account via OAuth — charges land in their balance, never the platform's.
- **Tenant-Owned Webhooks**: Each tenant registers their own `checkout.session.completed` webhook on their own subdomain.
- **Column-Level Encryption**: `acct_…` IDs and `whsec_…` signing secrets stored Fernet-encrypted on `WebsiteSettings`.
- **Integrated ERP Posting**: `complete_checkout()` atomically creates a `Customer` (matched by email), `SalesOrder` → `Invoice` (posted with full GL impact) → `Payment` with `PaymentApplication`, reusing the existing service-layer functions.
- **Idempotent Webhook Handler**: Safe under Stripe's retry behavior; re-running with the same checkout is a no-op.
- **Dev-Mode Simulator**: Before Stripe is configured, checkout redirects to a manual "Complete Payment (Dev)" button that exercises the full ERP wiring without charging real cards.
- **Startup Hardening**: Django system checks fail prod deploys if `FIELD_ENCRYPTION_KEY` is unset or Stripe Connect config is partial; warns in dev if a live key is loaded.

See [docs/webstore.md](docs/webstore.md) for the full onboarding flow, models, and security model.

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

### AI Copilot

- Conversational sidebar panel on every page (bring-your-own OpenAI key, browser-only).
- Drafts Purchase Orders, Sales Orders, and Manufacturing Orders, and posts Stock Movements (receipts, issues, adjustments) from plain English (`bought 5 PLA filament from BambuLab at $20 each`, `sold 3 widgets to Acme for $50 each`, `build 50 widgets`, `received 100 of WIDGET at $5 each`, `wrote off 5 damaged widgets`).
- Fuzzy vendor / customer / product / BOM search — tolerates misspellings and missing spaces.
- Multi-turn slot filling: carries vendor, product, qty, unit cost across turns until the draft is complete.
- "Try your best" defaults: fills missing slots with `product.cost` / `product.price` and the first active counterparty or stock product.
- Page-context awareness: on `/customers/123/`, *"what did they buy last month?"* resolves to that customer automatically; on `/boms/4/`, *"make 100"* uses that BOM.
- Look-up tools: vendor / customer / product / BOM search, stock levels, recent purchase prices, open POs, record detail with recent activity.
- Preview-confirm safety: every write or immediate post is staged behind a signed, 30-minute action token; nothing is created or committed without an explicit click.
- Audit trail: each chat, preview, and confirm is logged in a per-tenant `CopilotAuditEvent` table.
- Persistent chat history in browser `localStorage` (per-tenant), with a Clear button.
- Per-tenant row caps and write rate limits apply — the copilot can't be used to bulk-load junk data.

See [docs/ai-copilot.md](docs/ai-copilot.md) for the full feature list, examples, and safety model.

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
| Payments | Stripe (Connect Standard accounts) |
| Encryption | cryptography (Fernet for column-level secrets) |
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
webstore/           Public storefront, cart, checkout, Stripe Connect integration
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
| `STRIPE_SECRET_KEY` | Platform key for Stripe Connect OAuth (never charges money) | empty |
| `STRIPE_PUBLISHABLE_KEY` | Platform publishable key (reserved) | empty |
| `STRIPE_CONNECT_CLIENT_ID` | Connect app `ca_…` for OAuth | empty |
| `FIELD_ENCRYPTION_KEY` | Fernet key for column-level encryption (per-tenant Stripe secrets) | falls back to `SECRET_KEY` |
| `WEBSTORE_CASH_ACCOUNT_CODE` | Asset account code that receives online sales | `1010` |

## Running Tests

Run the full test suite:

```bash
python manage.py test
```

The repository currently contains 76 test methods across the core, accounting, inventory, sales, purchasing, manufacturing, and tenant modules. The highest-value tests cover tenant-aware setup, financial posting integrity, stock movement validation, sales order automation, purchasing receipt/bill flows, manufacturing completion, import/export, PDFs, in-app docs, and dashboard/report behavior.

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
| Public Website Home | `/` on a tenant workspace root |
| Public Website Subpage | `/p/<slug>/` |
| Website Editor Dashboard | `/derp/website/` |
| Website Editor Settings | `/derp/website/settings/` |
| Website Editor Actions | `/derp/website/add/`, `/derp/website/<id>/edit/`, `/derp/website/<id>/delete/` |
| Tenant home | `/derp/` on a tenant workspace |
| Dashboard | `/derp/dashboard/` |
| Company setup | `/derp/company/` |
| Data export | `/derp/export/` |
| Data import | `/derp/import/` |
| Search | `/derp/search/` |
| Products | `/derp/products/`, `/derp/products/new/`, `/derp/products/<id>/`, `/derp/products/<id>/edit/` |
| Inventory ledger | `/derp/products/ledger/` |
| Customers | `/derp/customers/`, `/derp/customers/new/`, `/derp/customers/<id>/`, `/derp/customers/<id>/edit/` |
| Sales orders | `/derp/sales-orders/`, `/derp/sales-orders/new/`, `/derp/sales-orders/<id>/` |
| Sales order actions | `/derp/sales-orders/<id>/confirm/`, `/unconfirm/`, `/invoice/`, `/undo-invoice/`, `/pdf/` |
| Invoices | `/derp/invoices/`, `/derp/invoices/new/`, `/derp/invoices/<id>/` |
| Invoice actions | `/derp/invoices/<id>/post/`, `/void/`, `/pdf/` |
| Customer payments | `/derp/payments/new/` |
| Vendors | `/derp/vendors/`, `/derp/vendors/new/`, `/derp/vendors/<id>/`, `/derp/vendors/<id>/edit/` |
| Purchase orders | `/derp/purchase-orders/`, `/derp/purchase-orders/new/`, `/derp/purchase-orders/<id>/` |
| Purchase order actions | `/derp/purchase-orders/<id>/issue/`, `/unissue/`, `/receive/`, `/bill/`, `/undo-bill/`, `/pdf/` |
| Goods receipts | `/derp/goods-receipts/<id>/`, `/derp/goods-receipts/<id>/bill/`, `/derp/goods-receipts/<id>/reverse/` |
| Bills | `/derp/bills/`, `/derp/bills/new/`, `/derp/bills/<id>/` |
| Bill actions | `/derp/bills/<id>/post/`, `/void/` |
| Vendor payments | `/derp/vendor-payments/new/` |
| Journals | `/derp/journal/`, `/derp/journal/new/`, `/derp/journal/<id>/`, `/derp/journal/<id>/reverse/` |
| Reports | `/derp/reports/trial-balance/`, `/derp/reports/income-statement/`, `/derp/reports/balance-sheet/`, `/derp/reports/general-ledger/` |
| BOMs | `/derp/boms/`, `/derp/boms/create/`, `/derp/boms/<id>/`, `/derp/boms/<id>/edit/` |
| Manufacturing orders | `/derp/manufacturing-orders/`, `/derp/manufacturing-orders/create/`, `/derp/manufacturing-orders/<id>/` |
| Manufacturing actions | `/derp/manufacturing-orders/<id>/confirm/`, `/complete/`, `/cancel/` |
| AI Copilot | `/derp/ai/chat/` (POST chat turn), `/derp/ai/confirm/` (POST signed action token) |
| Storefront | `/shop/`, `/shop/c/<slug>/`, `/shop/p/<slug>/` |
| Cart | `/shop/cart/`, `/shop/cart/add/<id>/`, `/shop/cart/update/<id>/`, `/shop/cart/remove/<id>/` |
| Checkout | `/shop/checkout/`, `/shop/checkout/success/`, `/shop/checkout/cancel/`, `/shop/checkout/dev/` |
| Stripe webhook | `/shop/webhooks/stripe/` (signature-verified, per-tenant) |
| Stripe Connect (admin) | `/derp/stripe/connect/`, `/derp/stripe/callback/`, `/derp/stripe/disconnect/`, `/derp/stripe/webhook-secret/` |

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
- Per-tenant row caps and per-user write rate limits (100/min) prevent a single tenant from filling shared storage with junk data. See `core/limits.py`.
- AI copilot writes go through a preview → signed action token (30 min TTL) → confirm flow; nothing is created without an explicit second click. Every chat, preview, and confirm is logged in `core_copilotauditevent` per tenant.
- Stripe Connect secrets (`acct_…`, `whsec_…`) are stored Fernet-encrypted at the column level via `webstore.fields.EncryptedCharField`; the platform never holds tenant funds because charges route through `stripe_account=…` against each tenant's connected account.
- Per-tenant webhook signing secrets verify every Stripe webhook before any ERP write; mis-signed payloads are rejected with HTTP 400.
- Startup checks fail prod deploys if `FIELD_ENCRYPTION_KEY` is unset or Stripe Connect is half-configured.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
