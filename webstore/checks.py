"""Startup checks for direct Stripe Checkout configuration."""
from django.conf import settings
from django.core.checks import Warning, register


@register()
def stripe_checks(app_configs, **kwargs):
    issues = []
    secret = getattr(settings, "STRIPE_SECRET_KEY", "")
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    if secret and not webhook_secret:
        issues.append(Warning(
            "STRIPE_SECRET_KEY is set but STRIPE_WEBHOOK_SECRET is missing.",
            hint=(
                "Create a Stripe webhook endpoint for checkout.session.completed "
                "and set its signing secret as STRIPE_WEBHOOK_SECRET."
            ),
            id="webstore.W002",
        ))

    if getattr(settings, "DEBUG", False) and secret.startswith("sk_live_"):
        issues.append(Warning(
            "Live Stripe key in use while DEBUG=True.",
            hint="Use test-mode Stripe keys during development.",
            id="webstore.W001",
        ))

    return issues
