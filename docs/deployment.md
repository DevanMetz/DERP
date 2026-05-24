# Deployment

DERP is designed to run as a conventional Django application with PostgreSQL, static file collection, and a WSGI server. The repository is provisioned for Railway out of the box; the same steps apply on any host that can run a Python process with a Postgres connection.

## Required services

- PostgreSQL database
- Python 3.11+ runtime
- Gunicorn or another WSGI server
- Static file serving through Whitenoise (bundled) or a fronting CDN

## Environment

Set production values for the variables below. All are read from environment variables (or a `.env` file in development). Keep `.env` gitignored.

### Core

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Django secret key |
| `DEBUG` | Must be `False` in production |
| `DATABASE_URL` | `postgres://USER:PASS@HOST:PORT/DBNAME` |
| `BASE_DOMAIN` | Root domain for tenant subdomains |
| `ALLOWED_HOSTS` | Comma-separated host allowlist |
| `RAILWAY_PUBLIC_DOMAIN` | Auto-injected on Railway |

### Email & captcha

| Variable | Purpose |
| --- | --- |
| `RESEND_API_KEY` | Resend SMTP/API key for outbound mail |
| `DEFAULT_FROM_EMAIL` | Outbound sender address |
| `TURNSTILE_SITE_KEY` | Cloudflare Turnstile site key |
| `TURNSTILE_SECRET_KEY` | Cloudflare Turnstile secret key |

### Webstore & Stripe Connect

Required only if you want tenants to accept payments through the storefront. See [webstore.md](./webstore.md) for the full setup flow.

| Variable | Purpose |
| --- | --- |
| `STRIPE_SECRET_KEY` | Platform key — mints V2 connected accounts and signs every API call |
| `STRIPE_PUBLISHABLE_KEY` | Platform publishable key (reserved for future embedded use) |
| `STRIPE_WEBHOOK_SECRET` | Signing secret for the single platform-wide webhook destination |
| `FIELD_ENCRYPTION_KEY` | Fernet key for column-level encryption. Generate once with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and back it up in your password manager — losing it makes every encrypted row unrecoverable |
| `WEBSTORE_CASH_ACCOUNT_CODE` | Chart-of-accounts code that receives online sales (defaults to `1010`) |

### Startup validation

`webstore.checks` registers Django system checks that fail `manage.py check` in production when the config is unsafe. Run it as part of your deploy pipeline so a misconfigured deploy fails before serving traffic:

```bash
python manage.py check
```

This guards against:
- `webstore.E001` — `FIELD_ENCRYPTION_KEY` unset when `DEBUG=False`
- `webstore.W001` — live Stripe key (`sk_live_`) loaded while `DEBUG=True`
- `webstore.W002` — `STRIPE_SECRET_KEY` set but `STRIPE_WEBHOOK_SECRET` missing (status sync and ERP fulfillment can't run)

## Migrations

`django-tenants` splits migrations into two passes: the public/shared schema, then each tenant schema.

### Fresh environment

```bash
python manage.py migrate_schemas --shared     # public schema: shared apps + tenants
python manage.py create_public_tenant         # creates the platform's public tenant
python manage.py migrate_schemas              # iterates all tenant schemas, applies TENANT_APPS migrations
```

### Every deploy

Run both passes on every deploy so new tenant migrations land. The simplest pattern is a release/predeploy hook:

```bash
python manage.py migrate_schemas --shared && python manage.py migrate_schemas
```

On Railway, put this in a `Procfile`:

```text
release: python manage.py check && python manage.py migrate_schemas --shared && python manage.py migrate_schemas
web: gunicorn config.wsgi
```

The `release` step blocks the web start until all checks pass and migrations apply cleanly — a failure rolls back the deploy without disrupting the previous version.

### Schema boundaries to remember

`SHARED_APPS` tables live in the `public` schema (the platform landing/signup pages). `TENANT_APPS` tables (including `core`, `webstore`, `accounting`, `inventory`, `sales`, `purchasing`, `manufacturing`) live in each tenant's schema only. Code that runs on every request — middleware, context processors, template tags — must tolerate being invoked on the public schema, where TENANT_APPS tables do not exist. `core.context_processors.website_context` and `webstore.context_processors.cart_summary` both short-circuit when `connection.tenant.schema_name == "public"`. Anything new that queries TENANT_APPS tables in a request-time hook should follow that pattern.

## Security behavior

When `DEBUG=False`, Django security settings enable:

- HTTPS redirect (`SECURE_SSL_REDIRECT`)
- Secure session and CSRF cookies
- Proxy SSL header trust for terminating load balancers
- HSTS for one year with subdomain include
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`

Stripe Connect secrets (`acct_…` and `whsec_…`) are Fernet-encrypted at the column level via `webstore.fields.EncryptedCharField` — a DB snapshot reveals nothing without the runtime `FIELD_ENCRYPTION_KEY`. Rotate that key independently of `SECRET_KEY` so session-cookie rotation and column-encryption rotation can happen on separate cadences.

## Smoke test after deploy

A working production deploy responds 200 on the platform landing and a tenant subdomain root. Quick check:

```bash
curl -s -o /dev/null -w "platform: %{http_code}\n" https://your-domain/
curl -s -o /dev/null -w "tenant:   %{http_code}\n" https://your-tenant.your-domain/derp/
```

Both should be 200. A 500 on the platform landing typically means a context processor is querying a TENANT_APPS table on the public schema (see "Schema boundaries to remember" above).
