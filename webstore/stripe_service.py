"""Tenant-aware Stripe Connect (Standard accounts).

Every API call is scoped to a connected tenant's `acct_…` ID using the
`stripe_account` parameter, so funds land in the tenant's Stripe balance,
never the platform's. The platform's STRIPE_SECRET_KEY is used only to
authenticate the call itself and to mint OAuth tokens.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.urls import reverse

try:
    import stripe  # type: ignore
except ImportError:
    stripe = None  # type: ignore

from core.models import WebsiteSettings


# ---------- Platform readiness ----------
def platform_is_configured() -> bool:
    """True when the DERP platform has the credentials needed to run OAuth."""
    return bool(
        stripe is not None
        and getattr(settings, "STRIPE_SECRET_KEY", "")
        and getattr(settings, "STRIPE_CONNECT_CLIENT_ID", "")
    )


def tenant_is_connected(ws: WebsiteSettings | None = None) -> bool:
    """True when the current tenant has completed OAuth and pasted a webhook secret."""
    ws = ws or WebsiteSettings.get()
    return bool(ws.stripe_account_id and ws.stripe_webhook_secret)


def _client():
    if stripe is None:
        raise RuntimeError("stripe SDK is not installed.")
    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        raise RuntimeError("STRIPE_SECRET_KEY is not set in the platform .env.")
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def _to_minor_units(amount: Decimal, currency: str = "USD") -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1")))


# ---------- OAuth ----------
def oauth_authorize_url(*, state: str, redirect_uri: str) -> str:
    """Build the URL the tenant is sent to for Connect OAuth."""
    if not getattr(settings, "STRIPE_CONNECT_CLIENT_ID", ""):
        raise RuntimeError("STRIPE_CONNECT_CLIENT_ID is not set in the platform .env.")
    return (
        "https://connect.stripe.com/oauth/authorize"
        f"?response_type=code"
        f"&client_id={settings.STRIPE_CONNECT_CLIENT_ID}"
        f"&scope=read_write"
        f"&state={state}"
        f"&redirect_uri={redirect_uri}"
    )


def oauth_exchange_code(code: str) -> dict:
    """Exchange an OAuth code for an `acct_…` ID and publishable key.

    Returns a dict with stripe_user_id, stripe_publishable_key, etc.
    """
    s = _client()
    return s.OAuth.token(grant_type="authorization_code", code=code)


def oauth_revoke(stripe_user_id: str) -> dict:
    """Revoke the platform's access to a tenant's connected account."""
    s = _client()
    return s.OAuth.deauthorize(
        client_id=settings.STRIPE_CONNECT_CLIENT_ID,
        stripe_user_id=stripe_user_id,
    )


# ---------- Checkout (called per-customer-order, charges tenant's account) ----------
def create_checkout_session(*, checkout, request, ws: WebsiteSettings | None = None) -> dict:
    """Create a hosted Checkout Session against the current tenant's
    connected Stripe account."""
    ws = ws or WebsiteSettings.get()
    if not ws.stripe_account_id:
        raise RuntimeError("This tenant has not connected a Stripe account.")
    s = _client()

    line_items = []
    for item in checkout.cart_items:
        line_items.append({
            "quantity": int(item["qty"]),
            "price_data": {
                "currency": checkout.currency.lower(),
                "unit_amount": _to_minor_units(Decimal(str(item["unit_price"])), checkout.currency),
                "product_data": {
                    "name": item["name"],
                    "metadata": {"sku": item.get("sku", "")},
                },
            },
        })

    success_url = request.build_absolute_uri(
        reverse("shop_checkout_success") + f"?token={checkout.token}"
    )
    cancel_url = request.build_absolute_uri(
        reverse("shop_checkout_cancel") + f"?token={checkout.token}"
    )

    session = s.checkout.Session.create(
        mode="payment",
        line_items=line_items,
        customer_email=checkout.email,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"checkout_token": str(checkout.token)},
        payment_intent_data={
            "metadata": {"checkout_token": str(checkout.token)},
        },
        stripe_account=ws.stripe_account_id,  # <-- routes to the tenant's account
    )
    return session


# ---------- Webhook ----------
def verify_webhook(payload: bytes, sig_header: str, ws: WebsiteSettings | None = None):
    """Verify a webhook signed with the *tenant's* signing secret."""
    ws = ws or WebsiteSettings.get()
    if not ws.stripe_webhook_secret:
        raise RuntimeError("This tenant has not configured a webhook secret.")
    s = _client()
    return s.Webhook.construct_event(payload, sig_header, ws.stripe_webhook_secret)
