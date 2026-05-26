# Deployment

DERP runs as a conventional self-hosted Django application: one installation,
one organization's data, one PostgreSQL database. Railway works well, and the
same configuration applies to any host capable of running Python and Postgres.

## Required Services

- PostgreSQL database
- Python 3.11+ runtime
- Gunicorn or another WSGI server
- Static file serving through bundled WhiteNoise or a fronting CDN

## Environment

Set production values through your host's secret/environment manager.

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Django signing secret; use a long random value |
| `DEBUG` | Set to `False` in production |
| `DATABASE_URL` | PostgreSQL connection URL |
| `ALLOWED_HOSTS` | Comma-separated host allowlist |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated `https://...` origins when required |
| `RAILWAY_PUBLIC_DOMAIN` | Railway-provided domain, accepted automatically when present |
| `RESEND_API_KEY` | Optional outbound password-reset email configuration |
| `DEFAULT_FROM_EMAIL` | Optional sender address |

### Stripe Storefront Payments

Stripe is only needed when the public storefront accepts real payments.

| Variable | Purpose |
| --- | --- |
| `STRIPE_SECRET_KEY` | Secret key for the installation owner's Stripe account |
| `STRIPE_WEBHOOK_SECRET` | Signing secret for `checkout.session.completed` |
| `WEBSTORE_CASH_ACCOUNT_CODE` | Optional cash account code; defaults to `1010` |

Create one Stripe webhook endpoint:

```text
https://your-domain.example/shop/webhooks/stripe/
```

Subscribe it to `checkout.session.completed`. DERP verifies the webhook
signature before posting a paid checkout into ERP records.

## Startup And Migrations

Fresh install:

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic --noinput
gunicorn config.wsgi
```

The repository `start.sh` performs the deploy-time sequence:

```bash
python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec gunicorn config.wsgi --bind "0.0.0.0:${PORT:-8000}" --log-file -
```

On Railway, use `./start.sh` as the start command and provide a PostgreSQL
service plus the environment values above.

## Startup Checks

Run checks in CI or a release phase:

```bash
python manage.py check
```

The storefront reports configuration warnings when:

- `webstore.W001`: a live Stripe key is loaded while `DEBUG=True`.
- `webstore.W002`: Stripe Checkout is configured without a webhook secret.

## Security Behavior

With `DEBUG=False`, DERP enables HTTPS redirection, secure cookies, proxy SSL
header support, HSTS, `nosniff`, frame denial, and a restrictive referrer
policy. Keep secrets out of the repository and terminate traffic over HTTPS.

Stripe secrets are server environment values. This version does not store
connected-account credentials in the application database.

## Upgrading From A Multi-Tenant Deployment

Older SaaS-oriented DERP versions placed application tables in a separate
PostgreSQL schema for each customer. The self-hosted application uses regular
tables in one database namespace.

This is not an automatic in-place upgrade for an existing hosted database.
Take a full backup, choose the organization to preserve, and either export
and import its data into a new installation or create a reviewed database
migration tailored to that schema.

## Smoke Test

After deploying, verify:

```bash
curl -s -o /dev/null -w "site: %{http_code}\n" https://your-domain.example/
curl -s -o /dev/null -w "erp:  %{http_code}\n" https://your-domain.example/derp/
curl -s -o /dev/null -w "shop: %{http_code}\n" https://your-domain.example/shop/
```

Unauthenticated `/derp/` requests may redirect to sign-in; the public site and
storefront should render normally.
