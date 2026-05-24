"""Startup checks for the webstore app.

Registered against Django's `system_check` framework so misconfigured
prod deploys fail fast (at `manage.py runserver` / `migrate` / `check`)
rather than silently mis-encrypting secrets or accepting unverified
webhooks.
"""
from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def stripe_and_encryption_checks(app_configs, **kwargs):
    issues = []
    debug = getattr(settings, "DEBUG", False)

    field_key = getattr(settings, "FIELD_ENCRYPTION_KEY", "")
    if not debug and not field_key:
        issues.append(Error(
            "FIELD_ENCRYPTION_KEY is not set in production.",
            hint=(
                "Encrypted columns will fall back to deriving the key from "
                "SECRET_KEY. That works, but ties the encryption key to "
                "Django's session/CSRF secret — rotating one forces "
                "rotating the other. Generate a dedicated value:\n"
                "  python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
                "Then set it as FIELD_ENCRYPTION_KEY in your environment."
            ),
            id="webstore.E001",
        ))

    secret = getattr(settings, "STRIPE_SECRET_KEY", "")
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    if not debug and secret and not webhook_secret:
        issues.append(Warning(
            "STRIPE_SECRET_KEY is set but STRIPE_WEBHOOK_SECRET is missing.",
            hint=(
                "Connected-account status updates rely on Stripe webhooks. "
                "Without STRIPE_WEBHOOK_SECRET we cannot verify incoming "
                "events. Create a webhook destination in your Stripe "
                "Dashboard (Developers → Webhooks → Add destination), "
                "subscribe to v2.core.account[requirements].updated and "
                "the v2 capability events, then paste the signing secret "
                "into STRIPE_WEBHOOK_SECRET."
            ),
            id="webstore.W002",
        ))

    if debug and secret and secret.startswith("sk_live_"):
        issues.append(Warning(
            "Live Stripe key in use while DEBUG=True.",
            hint=(
                "STRIPE_SECRET_KEY starts with 'sk_live_' but DEBUG is True. "
                "Use test-mode keys (sk_test_...) during development to "
                "avoid accidentally creating real charges."
            ),
            id="webstore.W001",
        ))

    return issues
