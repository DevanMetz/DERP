# Getting Started

DERP is a Django ERP application backed by PostgreSQL and `django-tenants`. The app is split into a public landing/signup area and tenant workspaces for company data.

## Local setup

Use the same setup flow as the README:

```bash
python -m venv .venv
pip install -r requirements.txt
python manage.py migrate_schemas --shared
python manage.py create_public_tenant
python manage.py runserver 8001
```

Copy `.env.example` to `.env` and configure at least `DATABASE_URL`, `SECRET_KEY`, `DEBUG`, and `BASE_DOMAIN`.

## Tenant workspaces

Each company gets its own PostgreSQL schema. The tenant middleware resolves requests by domain or subdomain and switches database schema context before tenant views run.

## Where to start in the app

- Home: workspace module grid and low-stock alerts.
- Dashboard: financial KPIs, inventory valuation, and monthly activity.
- Company: business profile used by documents and settings.
- Data Export and Data Import: backup, CSV, and restore flows.

## Development habit

Run focused tests for the area you change, then run the full suite before merging broader workflow changes.
