# ERP — Phase 0 Scaffold

Single-tenant ERP for small businesses. MIT licensed. Built solo.

This scaffold gives you Phase 0: auth, company setup, chart of accounts,
the double-entry posting layer, document numbering, and audit logging.

## Stack

- Python 3.12+, Django 5.x
- PostgreSQL 15+
- `django-allauth` for auth (TOTP later)
- `django-simple-history` for audit logging
- Server-rendered templates; HTMX for interactive bits later

## Layout

```
config/             Django project settings, urls, wsgi
core/               Company, User, shared utilities
accounting/         Account, JournalEntry, JournalLine, posting service
inventory/          (stub for Phase 2)
sales/              (stub for Phase 1)
purchasing/         (stub for Phase 1)
manufacturing/      (stub for Phase 3)
projects/           (stub for Phase 4)
templates/          Base templates
```

## The posting layer is the foundation

Every business event in this ERP — every invoice, every payment, every
inventory movement, every manufacturing completion — posts to
`JournalEntry` and `JournalLine`. The *only* sanctioned way to create
journal entries is `accounting.services.post_transaction()`.

If you find yourself constructing `JournalLine` objects anywhere else,
stop. Add the use case to `post_transaction()` instead.

## Phase 0 "done" checklist

- [ ] Log in, set up company, configure chart of accounts
- [ ] Post a manual journal entry through the UI
- [ ] Trial balance balances to zero
- [ ] Cannot post an unbalanced entry (verified from Django shell, not just form)
- [ ] Cannot edit a posted entry through any code path
- [ ] Audit log records who created each entry
- [ ] Deployed and all of the above work on the deployed instance

## Setup

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # edit DATABASE_URL etc.
python manage.py migrate
python manage.py seed_chart_of_accounts
python manage.py createsuperuser
python manage.py runserver
```
