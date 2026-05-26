"""Stripe Checkout helpers for a single self-hosted DERP installation."""
from __future__ import annotations

from typing import Any

from django.conf import settings

try:
    import stripe  # type: ignore
    from stripe import StripeClient  # type: ignore
except ImportError:  # pragma: no cover
    stripe = None  # type: ignore
    StripeClient = None  # type: ignore


_client: "StripeClient | None" = None


def get_client() -> "StripeClient":
    """Return a Stripe client configured with this installation's key."""
    global _client
    if _client is not None:
        return _client

    secret = getattr(settings, "STRIPE_SECRET_KEY", "")
    if not secret:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured.")
    if stripe is None or StripeClient is None:
        raise RuntimeError("The stripe Python package is not installed.")

    _client = StripeClient(secret)
    return _client


def is_configured() -> bool:
    return bool(getattr(settings, "STRIPE_SECRET_KEY", "") and stripe is not None)


def create_checkout_session(
    *,
    product_name: str,
    unit_amount: int,
    currency: str,
    quantity: int,
    success_url: str,
    cancel_url: str,
    checkout_token: str,
    customer_email: str = "",
) -> Any:
    """Start hosted payment on the self-hoster's Stripe account."""
    params: dict = {
        "mode": "payment",
        "line_items": [{
            "quantity": quantity,
            "price_data": {
                "currency": currency,
                "unit_amount": int(unit_amount),
                "product_data": {"name": product_name},
            },
        }],
        "metadata": {"checkout_token": checkout_token},
        "success_url": success_url,
        "cancel_url": cancel_url,
    }
    if customer_email:
        params["customer_email"] = customer_email

    return get_client().v1.checkout.sessions.create(params=params)


def verify_webhook_event(payload: bytes, sig_header: str):
    """Verify a standard Stripe webhook payload."""
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not configured.")
    if stripe is None:
        raise RuntimeError("The stripe Python package is not installed.")
    return stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
