# Getting Started

DERP is a self-hosted Django ERP application backed by PostgreSQL. One
installation represents one organization and includes its ERP workspace,
public website, storefront, and users.

## Local Setup

```bash
python -m venv .venv
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Copy `.env.example` to `.env` and configure at least `DATABASE_URL`,
`SECRET_KEY`, `DEBUG`, and `ALLOWED_HOSTS`.

The initial superuser is assigned the DERP Admin role automatically. Public
signup is disabled; an administrator creates additional user accounts from
within the ERP.

## Where To Start

- `/derp/`: module shortcuts and operational alerts.
- `/derp/dashboard/`: financial KPIs, valuation, and activity.
- `/derp/company/`: business profile used on documents.
- `/derp/website/`: website pages and branding.
- `/derp/agents/`: reusable AI routines.
- `/shop/`: the public storefront.

## Data Model

`Company` and `WebsiteSettings` are singleton records. Products, orders,
accounting records, and public pages all belong to this installation's single
database namespace.

## Development Habit

Run focused tests for the area you change, then run the full test suite before
shipping broader workflow changes.
