"""Startup checks for the webstore app.

Registered against Django's `system_check` framework so misconfigured prod
deploys fail fast (at `manage.py runserver` / `migrate` / `check`) rather
than silently mis-encrypting secrets or accepting unauthenticated webhooks.
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
                "SECRET_KEY. That works, but it ties the encryption key to "
                "Django's session/CSRF secret — rotating one forces rotating "
                "the other. Set a separate FIELD_ENCRYPTION_KEY env var "
                "(any high-entropy random string) so they can rotate "
                "independently."
            ),
            id="webstore.E001",
        ))

    has_secret = bool(getattr(settings, "STRIPE_SECRET_KEY", ""))
    has_client_id = bool(getattr(settings, "STRIPE_CONNECT_CLIENT_ID", ""))

    if not debug and (has_secret ^ has_client_id):
        # One set but not the other — partial config is worse than none.
        issues.append(Error(
            "Stripe Connect is half-configured.",
            hint=(
                "STRIPE_SECRET_KEY and STRIPE_CONNECT_CLIENT_ID must both "
                "be set (for OAuth to work) or both unset (to disable "
                "Connect onboarding). Currently only one is present."
            ),
            id="webstore.E002",
        ))

    if debug and has_secret and getattr(settings, "STRIPE_SECRET_KEY", "").startswith("sk_live_"):
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
