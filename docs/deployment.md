# Deployment

DERP is designed to run as a conventional Django application with PostgreSQL, static file collection, and a WSGI server.

## Required services

- PostgreSQL database
- Python runtime
- Gunicorn or another WSGI server
- Static file serving through Whitenoise or a fronting platform

## Environment

Set production values for:

- `SECRET_KEY`
- `DEBUG=False`
- `DATABASE_URL`
- `BASE_DOMAIN`
- `ALLOWED_HOSTS`
- `RAILWAY_PUBLIC_DOMAIN` when deploying on Railway
- `RESEND_API_KEY` and `DEFAULT_FROM_EMAIL` when email is enabled

## Migrations

Run shared migrations before tenant migrations when provisioning a fresh environment.

```bash
python manage.py migrate_schemas --shared
python manage.py create_public_tenant
```

Tenant schemas are created during signup/provisioning and should be migrated with the `django-tenants` workflow.

## Security behavior

When `DEBUG=False`, Django security settings enable HTTPS redirect, secure cookies, proxy SSL header handling, HSTS, content type sniffing protection, frame denial, and strict referrer policy.
