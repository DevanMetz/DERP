# Webstore & Stripe Connect (V2 Accounts)

The `webstore` app turns your tenant's existing `inventory.Product` catalog into a real online store with cart, checkout, and Stripe-processed payments. Orders land in the ERP as `sales.SalesOrder` → `Invoice` → `Payment` records that your team already knows how to handle.

This doc covers two flows:

1. **The real storefront** — `/shop/` with cart, ProductStorefront-backed catalog, ERP wiring on payment success.
2. **The Stripe sample storefront** — `/sample/store/<account_id>/` — a minimal canonical-Connect example that fetches products directly from Stripe via the `Stripe-Account` header. Use it as reference code when integrating Connect into other parts of your app.

Both share the same Stripe Connect (V2 Accounts) onboarding flow and webhook destination.

---

## Architecture in one minute

```text
Customer browses → /shop/                       (real storefront)
                  or
                  /sample/store/<acct_…>/       (sample storefront)
                       ↓
            Stripe Checkout (hosted on stripe.com)
            ↑
            Created on the connected account via
            stripeClient.v1.checkout.sessions.create(
                params={...},
                options={"stripe_account": "acct_..."}
            )
                       ↓
       Customer pays → funds land in tenant's Stripe balance
                       ↓
            Webhooks → /shop/webhooks/stripe/ (thin events)
                       ↓
    checkout.session.completed → ERP records
                       ↓
SalesOrder (confirmed) → Invoice (posted) → Payment + JournalEntry
```

Payment processing is **Stripe Connect V2 Accounts** — the platform creates a connected account on the tenant's behalf via `v2.core.accounts.create()` and onboards through an Account Link. No OAuth, no per-tenant webhook secrets.

---

## Models

| Model | Purpose |
| --- | --- |
| `Category` | Hierarchical product taxonomy |
| `ProductStorefront` | OneToOne over `inventory.Product`. Adds slug, category, online price, compare-at price, featured flag, marketing copy |
| `ProductImage` | Gallery |
| `Address` | Structured shipping/billing snapshot |
| `Checkout` | UUID-token pending order; becomes a SalesOrder on payment success |

`core.WebsiteSettings` stores per-tenant Stripe state:

| Field | Encrypted? | Purpose |
| --- | --- | --- |
| `stripe_account_id` | Yes (Fernet) | `acct_…` returned by `v2.core.accounts.create()` |
| `stripe_publishable_key` | No | Reserved; cached for any future frontend embed |
| `stripe_webhook_secret` | Yes (Fernet) | Legacy per-tenant secret (kept for migration compatibility; not used in V2 flow) |
| `stripe_connected_at` | No | UI display |

The cart itself is **session-only** — JSON on `request.session`. No DB row until checkout.

---

## Customer-facing routes

| URL | Purpose |
| --- | --- |
| `/shop/` | Real storefront landing (categories + featured + all products) |
| `/shop/c/<slug>/` | Category listing |
| `/shop/p/<slug>/` | Product detail page |
| `/shop/cart/` | Cart with HTMX qty updates |
| `/shop/checkout/` | Email + shipping address form |
| `/shop/checkout/success/?token=…` | Post-payment landing |
| `/shop/webhooks/stripe/` | One platform-wide webhook destination (thin events) |
| `/sample/store/<account_id>/` | **Sample storefront** — public product list pulled live from Stripe |
| `/sample/store/<account_id>/checkout/<product_id>/` | Sample direct-charge Checkout Session |
| `/sample/store/<account_id>/success/` | Sample post-payment landing |

> **URL note:** the sample storefront embeds the raw `acct_…` ID in the URL for self-contained simplicity. In production, swap for a stable tenant slug or UUID so rotating the connected account doesn't break customer-facing URLs.

---

## Admin routes (Stripe Connect V2)

Restricted to **Administrators** and **Managers**.

| URL | Purpose |
| --- | --- |
| `/derp/stripe/onboard/` | Create V2 account if needed + redirect to Stripe Account Link |
| `/derp/stripe/return/` | Stripe redirects here after onboarding |
| `POST /derp/stripe/disconnect/` | Clear the local mapping (account on Stripe is untouched) |
| `/derp/stripe/sample/products/new/` | Form that creates a product on the connected account |

---

## Setup: tenant onboarding flow

A tenant goes from "ERP only" to "accepting real payments" in three steps.

### 1. Platform configures Stripe once

In your platform's `.env`:

```bash
STRIPE_SECRET_KEY=sk_test_…           # platform key (mints connected accounts)
STRIPE_PUBLISHABLE_KEY=pk_test_…
STRIPE_WEBHOOK_SECRET=whsec_…         # Thin destination (V2 account events)
STRIPE_WEBHOOK_SECRET_V1=whsec_…      # Snapshot destination (checkout.session.completed)
FIELD_ENCRYPTION_KEY=<48 random bytes>
```

Stripe forbids mixing V1 and V2 events on a single webhook destination, so create **two** destinations in your Stripe Dashboard. Both point at the same URL (`https://<your-platform>/shop/webhooks/stripe/`) and both listen to **Events from: Connected accounts**.

**Destination 1 — Thin payload (V2)**:
- `v2.core.account[requirements].updated`
- `v2.core.account[configuration.merchant].capability_status_updated`
- `v2.core.account[configuration.customer].capability_status_updated`
- Signing secret → `STRIPE_WEBHOOK_SECRET`

**Destination 2 — Snapshot payload (V1)**:
- `checkout.session.completed`
- Signing secret → `STRIPE_WEBHOOK_SECRET_V1`

The single endpoint at `/shop/webhooks/stripe/` tries the thin verifier first and falls back to the snapshot verifier, so both destinations can hit the same URL without conflict.

### 2. Tenant onboards

- Tenant signs in → goes to `/derp/website/settings/`
- Clicks **Onboard to collect payments**
- `webstore.views.stripe_onboard`:
  - Calls `stripe_service.create_connected_account(display_name, contact_email)` if the tenant doesn't yet have an `acct_…`
  - Stores the new `acct_…` on `WebsiteSettings.stripe_account_id` (encrypted)
  - Calls `stripe_service.create_onboarding_link(account_id, refresh_url, return_url)` to mint a V2 Account Link
  - Redirects the tenant to the Stripe-hosted onboarding page
- Tenant completes Stripe's KYC flow → Stripe redirects to `/derp/stripe/return/`
- Settings page now shows live status pulled from the API via `stripe_service.retrieve_account_status()`

### 3. Tenant adds products and shares the link

- From the Settings page, click **Add a sample product** → fill out the form → server calls `client.v1.products.create({...}, options={"stripe_account": account_id})`
- Public storefront is at `/sample/store/<account_id>/`
- Or use the real `/shop/` storefront — products there come from the local `ProductStorefront` model and checkout goes through the cart flow

---

## How `complete_checkout` posts to the ERP

`webstore/services.py::complete_checkout(checkout)` is idempotent and atomic. Triggered by the `checkout.session.completed` webhook for the real-store flow. The sample storefront skips this entirely — it just creates the Stripe charge and Stripe records the sale on the connected account.

1. **Find or create `sales.Customer`** by email
2. **Resolve revenue accounts** via `resolve_revenue_account(product, customer)`
3. **Create `SalesOrder` (DRAFT)** with lines
4. **`confirm_sales_order(order)`** → CONFIRMED (issues stock)
5. **`create_invoice_from_sales_order(order)`** → draft Invoice
6. **`post_invoice(invoice)`** → AR/revenue/COGS journal entry, status SENT
7. **`receive_payment(...)`** → Payment + JournalEntry, invoice flips to PAID
8. **Mark `Checkout.status = PAID`**, link `sales_order`

If anything raises, the entire transaction rolls back and Stripe automatically retries the webhook.

---

## How Stripe-Account routing works

| Operation | API surface | How the connected account is selected |
| --- | --- | --- |
| Create connected account | `v2.core.accounts.create()` | Returned `acct_…` is the new account |
| Onboarding link | `v2.core.account_links.create({"account": acct_id, ...})` | Embedded in the request body |
| Retrieve status | `v2.core.accounts.retrieve(acct_id, params={"include": [...]})` | Path parameter |
| Webhooks (thin) | `client.parse_event_notification(payload, sig, whsec)` | One destination handles all accounts; event payload carries `account` field |
| Create product | `v1.products.create(params, options={"stripe_account": acct_id})` | `Stripe-Account` header |
| List products | `v1.products.list(params, options={"stripe_account": acct_id})` | `Stripe-Account` header |
| Checkout Session | `v1.checkout.sessions.create(params, options={"stripe_account": acct_id})` | `Stripe-Account` header — funds settle into that account |

The Python SDK accepts the connected account ID as `options={"stripe_account": "acct_..."}` on every v1 call. The SDK turns that into the `Stripe-Account` HTTP header.

---

## Webhook handling (thin events)

The single webhook destination at `/shop/webhooks/stripe/` receives **thin events**. Thin events contain only an event ID; we fetch the full event with `v2.core.events.retrieve(id)` if our handler needs it.

```python
# webstore/views.py::stripe_webhook (excerpt)
notification = stripe_service.parse_thin_event(payload, sig_header)
event_type   = notification.type

if event_type.startswith("v2.core.account"):
    full      = stripe_service.retrieve_full_event(notification.id)
    acct_id   = full.related_object.id
    stripe_service.retrieve_account_status(acct_id)
elif event_type == "checkout.session.completed":
    # V1 event — has data.object embedded
    services.complete_checkout(checkout, ...)
```

**Signature verification:** `parse_thin_event` uses the platform's `STRIPE_WEBHOOK_SECRET` to verify the `Stripe-Signature` header. Tampered payloads are rejected with HTTP 400.

**Idempotency:** the ERP-posting service `complete_checkout` checks `if checkout.status == PAID: return checkout` first, so Stripe's automatic webhook retries are safe.

### Local testing with the Stripe CLI

```bash
stripe listen \
  --thin-events 'v2.core.account[requirements].updated,v2.core.account[configuration.merchant].capability_status_updated,v2.core.account[configuration.customer].capability_status_updated' \
  --forward-thin-to http://localhost:8001/shop/webhooks/stripe/
```

The CLI prints a temporary `whsec_…` you put into `.env` for local testing.

---

## Encryption at rest

Two fields on `WebsiteSettings` use `webstore.fields.EncryptedCharField` — Fernet-encrypted (AES-128-CBC + HMAC-SHA256):

- `stripe_account_id`
- `stripe_webhook_secret` (kept for backwards compat; unused in V2 flow)

The encryption key derives from `FIELD_ENCRYPTION_KEY` (falls back to `SECRET_KEY` if unset). A DB snapshot leaks nothing without the runtime key.

**Generate a key once:**

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Paste into `.env` as `FIELD_ENCRYPTION_KEY=…`. Treat it like a password — losing it makes every encrypted row unrecoverable.

---

## Environment variables

| Variable | Purpose | Required? |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | Platform secret key — mints connected accounts and signs all API calls | Required for Connect |
| `STRIPE_PUBLISHABLE_KEY` | Platform publishable key | Optional (reserved) |
| `STRIPE_WEBHOOK_SECRET` | Thin destination signing secret (V2 account events) | Required for status sync |
| `STRIPE_WEBHOOK_SECRET_V1` | Snapshot destination signing secret (V1 `checkout.session.completed`) | Required for real-store ERP fulfillment |
| `FIELD_ENCRYPTION_KEY` | Fernet key for encrypted columns | Required in production |
| `WEBSTORE_CASH_ACCOUNT_CODE` | Asset account that receives online sales | Optional; defaults to `1010` |

Per-tenant: only `stripe_account_id` (on `WebsiteSettings`). No per-tenant secrets needed in V2.

---

## Startup checks

`webstore/checks.py` registers Django system checks that fail `manage.py check`:

| ID | Severity | Triggers when |
| --- | --- | --- |
| `webstore.E001` | Error | `DEBUG=False` and `FIELD_ENCRYPTION_KEY` is unset |
| `webstore.W001` | Warning | `DEBUG=True` but `STRIPE_SECRET_KEY` starts with `sk_live_` (about to charge real cards) |
| `webstore.W002` | Warning | `DEBUG=False`, `STRIPE_SECRET_KEY` set but `STRIPE_WEBHOOK_SECRET` missing (status sync will not work) |

---

## What's not built yet

- **Tax calculation** — `Customer.tax_rate` defaults to 0. Stripe Tax integration recommended for a real solution.
- **Shipping rate calculation** — flat zero. Add a table or carrier API.
- **Order confirmation emails** — wire up via the existing Resend config.
- **Customer accounts** — checkout is guest-only.
- **Embedded Connect components** — currently we use Stripe-hosted onboarding (`dashboard='full'`). If you want everything inside DERP, switch to `dashboard='none'` and integrate Connect Embedded Components.
- **Refunds** — Stripe dashboard works for now; build a server-side refund flow if you need automation.

---

## Files

```text
webstore/
  __init__.py
  admin.py                 Django admin (Category, ProductStorefront, ProductImage, Address, Checkout)
  apps.py                  Registers startup checks in .ready()
  cart.py                  Cart class (session-backed)
  checks.py                Django system checks (E001/W001/W002)
  context_processors.py    cart_count, cart_subtotal — public-schema-safe
  fields.py                EncryptedCharField (Fernet)
  forms.py                 CheckoutForm (real-store)
  models.py                Category, ProductStorefront, ProductImage, Address, Checkout
  services.py              snapshot_cart_for_checkout, complete_checkout
  stripe_service.py        V2 Accounts API + StripeClient wrapper. ALL Stripe calls go through here.
  urls.py                  /shop/, /sample/store/, /derp/stripe/...
  views.py                 Catalog, cart, checkout, sample storefront, V2 onboarding, webhook
  migrations/0001_initial.py

templates/webstore/
  catalog.html, category.html, product_detail.html
  cart.html, _cart_body.html, _cart_summary.html
  _product_card.html
  checkout.html, checkout_success.html, checkout_cancel.html, checkout_dev.html
  stripe_sample_product_form.html
  stripe_sample_storefront.html
  stripe_sample_storefront_success.html
```
