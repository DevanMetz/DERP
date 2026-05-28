# DERP - Self-Hosted Open Source ERP

DERP (Devan's Enterprise Resource Planner) is an open source ERP that one
organization runs for itself. It combines accounting, inventory, sales,
purchasing, manufacturing, a public website, a storefront, and optional AI
tools in a Django application you can deploy on Railway or any host with
PostgreSQL.

DERP is no longer structured as a hosted multi-customer SaaS platform. One
deployment represents one company, with its own users, data, website, and
Stripe configuration.

## Features

- Double-entry accounting, reports, and posted document workflows.
- Inventory with weighted-average costing, warehouses, lots, and serials.
- Sales orders, invoices, purchasing, bills, and manufacturing orders.
- Website editor and public storefront served by the same installation.
- Direct Stripe Checkout payments into the hosting organization's Stripe account.
- Copilot with preview and confirm controls before ERP writes.
- Agent Hub for reusable, user-owned AI routines.
- CSV/JSON data import and export tools.

## Architecture

- Django 5 application backed by PostgreSQL.
- A single `Company` and `WebsiteSettings` record per database.
- The public site lives at `/`; authenticated ERP pages live under `/derp/`.
- Accounts are created by the administrator rather than by public signup.
- Deployment uses standard Django migrations; there is no tenant middleware,
  subdomain routing, or schema-per-customer provisioning.

## Quick Start

Prerequisites: Python 3.11+ and PostgreSQL.

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then set at least:

```dotenv
DEBUG=True
SECRET_KEY=change-me-for-local-development
DATABASE_URL=postgres://erp:erp@localhost:5432/erp
ALLOWED_HOSTS=localhost,127.0.0.1
```

Prepare the database and create the first administrator:

```bash
python manage.py migrate
python manage.py ensure_default_admin
python manage.py runserver
```

The bootstrap admin receives the DERP Admin role automatically. You can control
the credentials with `DERP_DEFAULT_ADMIN_EMAIL`, `DERP_DEFAULT_ADMIN_USERNAME`,
and `DERP_DEFAULT_ADMIN_PASSWORD`. After signing in, the administrator can
create additional users inside the ERP.

Open:

- Public website: [http://localhost:8000/](http://localhost:8000/)
- ERP: [http://localhost:8000/derp/](http://localhost:8000/derp/)
- Storefront: [http://localhost:8000/shop/](http://localhost:8000/shop/)

## Deploying

Railway is a convenient option, but DERP works on any Python/PostgreSQL host.
Set production environment variables, attach a PostgreSQL database, and use
the repository startup script:

```bash
./start.sh
```

`start.sh` applies migrations, collects static files, and starts Gunicorn.
At minimum, production should configure `SECRET_KEY`, `DEBUG=False`,
`DATABASE_URL`, `ALLOWED_HOSTS`, and `CSRF_TRUSTED_ORIGINS` when needed.

On an empty database, startup creates one bootstrap admin account after
migrations. Set `DERP_DEFAULT_ADMIN_EMAIL`, `DERP_DEFAULT_ADMIN_USERNAME`, and
`DERP_DEFAULT_ADMIN_PASSWORD` in Railway to control that first login. If no
password is configured, DERP generates one and prints it once in the deploy
logs.

Startup also seeds demo ERP data when the business tables are empty, including
sample products, vendors, customers, purchases, manufacturing, sales, invoices,
and payments. Set `DERP_SEED_DEMO_DATA=False` to disable this behavior.

See [docs/deployment.md](docs/deployment.md) for deployment details.

## Storefront Payments

Stripe is optional. Without Stripe variables, local checkout provides a manual
completion page for development. To accept real storefront payments, set:

```dotenv
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

In Stripe, configure a webhook for `checkout.session.completed` pointing to:

```text
https://your-domain.example/shop/webhooks/stripe/
```

DERP creates Checkout Sessions directly in the installation owner's Stripe
account and fulfills paid checkouts into ERP sales and accounting records.
See [docs/webstore.md](docs/webstore.md).

## AI And Agent Hub

The Copilot stores an API key only in the user's browser and requires a
preview/confirm step for database writes. Agent Hub saves reusable prompts
that users explicitly open and send through Copilot; it does not run
unattended jobs.

See [docs/ai-copilot.md](docs/ai-copilot.md) and
[docs/agent-hub.md](docs/agent-hub.md).

## Moving From The Earlier SaaS Version

Earlier DERP versions used `django-tenants` and stored company application
data in PostgreSQL schemas selected by domain. This self-hosted version uses
ordinary tables for one installation.

There is no automatic in-place conversion of an existing schema-per-company
database. Before switching an existing deployment, back it up and either:

- start a new self-hosted database and import/export the company data you need; or
- write and validate a deliberate migration for the specific company schema
  that will become the installation's data.

Do not run this version against an important older production database without
a backup and a migration plan.

## Development

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

Useful routes:

| Area | Route |
| --- | --- |
| Public website | `/` |
| Storefront | `/shop/` |
| ERP home | `/derp/` |
| Dashboard | `/derp/dashboard/` |
| Agent Hub | `/derp/agents/` |
| Website editor | `/derp/website/` |
| Django admin | `/admin/` |

## Security Notes

- Keep `SECRET_KEY`, database credentials, and Stripe keys out of source control.
- Use HTTPS and `DEBUG=False` in production.
- Storefront fulfillment verifies Stripe webhook signatures.
- Copilot writes require role checks, signed previews, explicit confirmation,
  and are recorded in the audit log.

## License

DERP is released under the MIT License. See [LICENSE](LICENSE).
