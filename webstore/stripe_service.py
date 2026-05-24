"""
Stripe Connect integration (V2 Accounts API).

This module is the only place that talks to Stripe directly. Views call
the helpers below; the helpers route every request through a
`stripe.StripeClient` instance that's lazily initialized from
`settings.STRIPE_SECRET_KEY`.

Architecture summary
--------------------
- We use the **V2 Accounts API** (the modern Connect surface). We
  *never* set `type='express'` / `'standard'` / `'custom'` at the top
  level — the V2 schema replaces that with structured `configuration`,
  `defaults`, and `identity` objects.
- All per-connected-account calls go through the V1 API using the
  `Stripe-Account` header (passed via the request-options dict).
- Webhooks are *thin* events. The webhook delivers an event ID and we
  fetch the full event with `v2.core.events.retrieve(id)` before
  acting on it.
- A single platform webhook destination receives events for all
  connected accounts. Each tenant's connected `acct_...` is stored on
  `core.WebsiteSettings.stripe_account_id` (Fernet-encrypted at rest).

Why not OAuth?
--------------
We previously sketched an OAuth Standard-account flow. The V2 API
supersedes it: the platform creates the account directly via
`v2.core.accounts.create(...)`, then onboarding is driven by an
`AccountLink`. No OAuth client_id, no redirect_uri allowlist on Stripe's
side, no `whsec_` per tenant — just one webhook destination on the
platform.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.conf import settings

try:
    import stripe  # type: ignore
    from stripe import StripeClient  # type: ignore
except ImportError:  # pragma: no cover
    stripe = None  # type: ignore
    StripeClient = None  # type: ignore


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------
_client: "StripeClient | None" = None


def get_client() -> "StripeClient":
    """Return a memoized `StripeClient` configured with the platform key.

    The Stripe SDK no longer wants you to mutate `stripe.api_key`
    globally — instead, create a single `StripeClient` and pass it
    around. This factory builds that client once per process and
    reuses it for every request.

    Raises a configuration error with a useful hint if the platform's
    secret key hasn't been set in the environment yet.
    """
    global _client
    if _client is not None:
        return _client

    # ───── PLACEHOLDER: STRIPE_SECRET_KEY ─────────────────────────────
    # Set this in your `.env` (local dev) or your hosting platform's
    # environment variables (production). It's the *platform's* test or
    # live key from https://dashboard.stripe.com/apikeys — NEVER a
    # connected account's key.
    secret = getattr(settings, "STRIPE_SECRET_KEY", "")
    if not secret:
        raise RuntimeError(
            "STRIPE_SECRET_KEY is not configured. Add it to your environment:\n"
            "  STRIPE_SECRET_KEY=sk_test_...   (test mode, recommended for dev)\n"
            "  STRIPE_SECRET_KEY=sk_live_...   (real charges, production only)\n"
            "Grab it from https://dashboard.stripe.com/apikeys"
        )

    if stripe is None or StripeClient is None:
        raise RuntimeError(
            "The 'stripe' Python package is not installed. Run:\n"
            "  pip install -r requirements.txt"
        )

    # The SDK auto-selects the latest pinned API version (2026-04-22.dahlia
    # at the time of writing). We deliberately don't override it.
    _client = StripeClient(secret)
    return _client


def platform_is_configured() -> bool:
    """Cheap check used by views to decide whether to show the
    'Connect Stripe' button vs the configuration warning banner."""
    return bool(getattr(settings, "STRIPE_SECRET_KEY", "") and stripe is not None)


# ---------------------------------------------------------------------------
# Connected account creation (V2)
# ---------------------------------------------------------------------------
def create_connected_account(*, display_name: str, contact_email: str) -> Any:
    """Create a V2 connected account for a tenant.

    We pass exactly the four top-level properties the V2 schema expects.
    Do NOT add `type='express'` / `'standard'` / `'custom'` — that's the
    V1 shape and the V2 endpoint will reject it.

    The structured objects mean:
      • `identity.country='us'`     — where the account is based.
        Pull this from the tenant later if you support international
        merchants. Per spec, hard-coded to 'us' for the sample.
      • `dashboard='full'`          — give the account a Stripe-hosted
        dashboard. Use 'none' if you're embedding everything in your
        own UI (Connect Embedded Components).
      • `defaults.responsibilities` — who handles fees and chargeback
        losses. 'stripe' means Stripe deducts them from the account
        balance directly (the simplest setup). Use 'application' if
        the platform wants to absorb them.
      • `configuration.merchant.capabilities.card_payments.requested`
        — request the ability to accept card payments. The capability
        won't be `active` until onboarding is complete.

    Returns the V2 account object; `.id` is `acct_...`.
    See https://docs.stripe.com/api/v2/core/accounts/object for the
    full response shape.
    """
    client = get_client()
    account = client.v2.core.accounts.create({
        # ───── PLACEHOLDER: collect from the tenant during signup ────
        # The tenant's public-facing brand name. Shown in the Stripe
        # dashboard and on receipts.
        "display_name": display_name,
        # The tenant's primary contact. Stripe sends onboarding and
        # account-status emails here.
        "contact_email": contact_email,

        "identity": {
            "country": "us",
        },
        "dashboard": "full",
        "defaults": {
            "responsibilities": {
                "fees_collector": "stripe",
                "losses_collector": "stripe",
            },
        },
        "configuration": {
            # An empty `customer` object opts the account in to acting
            # as a customer (e.g. paying for subscriptions to the
            # platform). Leave the dict empty unless you have specific
            # configuration to apply.
            "customer": {},
            "merchant": {
                "capabilities": {
                    "card_payments": {
                        "requested": True,
                    },
                },
            },
        },
    })
    log.info("Created Stripe V2 account %s", account.id)
    return account


# ---------------------------------------------------------------------------
# Onboarding (Account Links V2)
# ---------------------------------------------------------------------------
def create_onboarding_link(*, account_id: str, refresh_url: str, return_url: str) -> Any:
    """Create a V2 Account Link the tenant uses to complete onboarding.

    Stripe-hosted onboarding collects the legal/banking info Stripe
    needs to satisfy KYC for the merchant. We open it in the browser by
    redirecting to `account_link.url`. The link expires after a few
    minutes — if the tenant doesn't finish in time, they'll be sent
    back to `refresh_url` and we generate a fresh one.

    On successful completion (or even partial completion) Stripe
    redirects to `return_url`. Once the user returns, retrieve the
    account from the API to check whether the capability is active —
    DO NOT trust `return_url` alone as proof of completion (the user
    can navigate there directly).
    """
    client = get_client()
    account_link = client.v2.core.account_links.create({
        "account": account_id,
        "use_case": {
            "type": "account_onboarding",
            "account_onboarding": {
                # `merchant` covers the card-payments capability;
                # `customer` covers acting as a customer on the
                # platform. Configure both so the account is fully set
                # up regardless of what flows through it later.
                "configurations": ["merchant", "customer"],
                "refresh_url": refresh_url,
                "return_url": return_url,
            },
        },
    })
    return account_link


# ---------------------------------------------------------------------------
# Account status (always from the API, never from a cache)
# ---------------------------------------------------------------------------
def retrieve_account_status(account_id: str) -> dict:
    """Pull the live status of a connected account.

    Per spec: always retrieve from the API rather than reading a cached
    flag from the database. Capability and requirements state can
    change at any time (e.g. when financial regulators require new
    information).

    We pass `include=["configuration.merchant", "requirements"]` to
    pull the two sub-objects we actually need. V2 retrieves are sparse
    by default for performance — you must opt in to nested fields.

    Returns a flat dict the view can render directly.
    """
    client = get_client()
    account = client.v2.core.accounts.retrieve(
        account_id,
        params={"include": ["configuration.merchant", "requirements"]},
    )

    # Capability lookup. The `?.` chain from the JS example becomes
    # cautious `getattr`s + dict lookups here so a missing field gives
    # us "inactive" instead of an AttributeError.
    card_payments = (
        getattr(getattr(account.configuration, "merchant", None), "capabilities", None)
        or {}
    )
    card_payments_status = None
    if card_payments:
        cp = card_payments.get("card_payments") if isinstance(card_payments, dict) else getattr(card_payments, "card_payments", None)
        if cp is not None:
            card_payments_status = cp.get("status") if isinstance(cp, dict) else getattr(cp, "status", None)
    ready_to_process_payments = card_payments_status == "active"

    # Requirements lookup. `minimum_deadline.status` tells us whether
    # Stripe is actively waiting on the user to submit something.
    requirements_status = None
    if getattr(account, "requirements", None):
        summary = getattr(account.requirements, "summary", None)
        if summary is not None:
            deadline = summary.get("minimum_deadline") if isinstance(summary, dict) else getattr(summary, "minimum_deadline", None)
            if deadline is not None:
                requirements_status = deadline.get("status") if isinstance(deadline, dict) else getattr(deadline, "status", None)

    onboarding_complete = requirements_status not in ("currently_due", "past_due")

    return {
        "account_id": account.id,
        "display_name": getattr(account, "display_name", "") or "",
        "contact_email": getattr(account, "contact_email", "") or "",
        "card_payments_status": card_payments_status,
        "requirements_status": requirements_status,
        "ready_to_process_payments": ready_to_process_payments,
        "onboarding_complete": onboarding_complete,
        # The raw object — handy for templates that want to dump
        # everything for debugging.
        "raw": account,
    }


# ---------------------------------------------------------------------------
# Products on a connected account (V1 + Stripe-Account header)
# ---------------------------------------------------------------------------
def create_product(
    *,
    account_id: str,
    name: str,
    description: str,
    price_in_cents: int,
    currency: str = "usd",
) -> Any:
    """Create a product on a *connected* account.

    Products live on the connected account, not the platform. We pass
    the account ID via the request-options dict's `stripe_account` key
    — the SDK turns that into the `Stripe-Account` HTTP header.

    `default_price_data` creates a matching Price object in the same
    call so we don't need a second round trip.
    """
    client = get_client()
    product = client.v1.products.create(
        params={
            "name": name,
            "description": description or "",
            "default_price_data": {
                # Amount in the currency's smallest unit (cents for USD,
                # yen for JPY, etc.). Always do this as an integer at
                # the model boundary so floating-point doesn't bite.
                "unit_amount": int(price_in_cents),
                "currency": currency,
            },
        },
        # The SDK looks for `stripe_account` here; it becomes the
        # `Stripe-Account` header which scopes the call to the
        # connected account rather than the platform.
        options={"stripe_account": account_id},
    )
    return product


def list_products(*, account_id: str, limit: int = 20) -> list:
    """List active products on a connected account.

    `expand=['data.default_price']` inlines each product's Price object
    so the storefront can show `product.default_price.unit_amount`
    without a second API call per product.
    """
    client = get_client()
    products = client.v1.products.list(
        params={
            "limit": limit,
            "active": True,
            "expand": ["data.default_price"],
        },
        options={"stripe_account": account_id},
    )
    return list(products.data)


def retrieve_product(*, account_id: str, product_id: str) -> Any:
    """Fetch a single product (and its Price) from a connected account."""
    client = get_client()
    return client.v1.products.retrieve(
        product_id,
        params={"expand": ["default_price"]},
        options={"stripe_account": account_id},
    )


# ---------------------------------------------------------------------------
# Hosted Checkout — direct charge with application fee
# ---------------------------------------------------------------------------
def create_direct_charge_checkout_session(
    *,
    account_id: str,
    product_name: str,
    unit_amount: int,
    currency: str,
    quantity: int,
    application_fee_amount: int,
    success_url: str,
    cancel_url: str,
    customer_email: str = "",
) -> Any:
    """Create a hosted Checkout Session that charges the connected
    account directly and skims a platform fee.

    Direct charges are the simplest Connect billing model:
      • The connected account is the merchant of record.
      • Funds settle into the connected account's balance.
      • Our platform skims `application_fee_amount` (in minor units)
        into the platform balance via `payment_intent_data`.
      • The customer sees the connected account's name on their card
        statement, not the platform's.

    We pass `stripe_account` in the request options so the session is
    created *on the connected account*, not on the platform.
    """
    client = get_client()

    line_items = [{
        "quantity": quantity,
        "price_data": {
            "currency": currency,
            "unit_amount": unit_amount,
            "product_data": {
                "name": product_name,
            },
        },
    }]

    params: dict = {
        "mode": "payment",
        "line_items": line_items,
        "payment_intent_data": {
            # Platform's per-transaction fee (in minor units). Set to
            # 0 if you don't want to charge a fee for this transaction.
            "application_fee_amount": int(application_fee_amount),
        },
        "success_url": success_url,
        "cancel_url": cancel_url,
    }
    if customer_email:
        params["customer_email"] = customer_email

    session = client.v1.checkout.sessions.create(
        params=params,
        options={"stripe_account": account_id},
    )
    return session


def retrieve_checkout_session(*, account_id: str, session_id: str) -> Any:
    """Retrieve a Checkout Session — useful on the success page so we
    can show the customer what they actually paid for (and so we can
    verify the session is genuinely paid rather than trusting the
    redirect URL)."""
    client = get_client()
    return client.v1.checkout.sessions.retrieve(
        session_id,
        options={"stripe_account": account_id},
    )


# ---------------------------------------------------------------------------
# Webhook parsing (thin events)
# ---------------------------------------------------------------------------
def parse_thin_event(payload: bytes, sig_header: str):
    """Verify the signature on an incoming thin-event webhook and
    return the parsed `EventNotification`.

    Thin events are the V2 webhook format. They contain just an event
    ID and minimal metadata — you fetch the full event with
    `retrieve_full_event()` if your handler needs it. This avoids
    payloads bloating as Stripe's data model grows.
    """
    # ───── PLACEHOLDER: STRIPE_WEBHOOK_SECRET ────────────────────────
    # Set this in your environment. Grab it from your webhook
    # destination's "Signing secret" in
    # https://dashboard.stripe.com/webhooks after you create one.
    # Use the *test* mode secret while developing.
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        raise RuntimeError(
            "STRIPE_WEBHOOK_SECRET is not configured. Add it to your environment:\n"
            "  STRIPE_WEBHOOK_SECRET=whsec_...\n"
            "Get it from your webhook destination in https://dashboard.stripe.com/webhooks"
        )

    client = get_client()
    # In the Python SDK, the equivalent of JS's `parseThinEvent` is
    # `parse_event_notification`. Same semantics: verifies signature,
    # returns a typed event notification object.
    return client.parse_event_notification(payload, sig_header, webhook_secret)


def retrieve_full_event(event_id: str):
    """Fetch the full V2 event when a thin webhook tells us something
    we care about. The full event includes the data we need to
    actually do something useful (capability statuses, requirements
    deltas, etc.)."""
    client = get_client()
    return client.v2.core.events.retrieve(event_id)
