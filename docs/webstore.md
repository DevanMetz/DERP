# Webstore & Stripe Connect

The `webstore` app turns your tenant's existing `inventory.Product` catalog into a real online store with cart, checkout, and Stripe-processed payments. Orders land in the ERP as `sales.SalesOrder` → `Invoice` → `Payment` records that your team already knows how to handle.

---

## Architecture in one minute

The storefront is a *thin* tenant-scoped layer on top of the ERP. It does not duplicate Product or Customer data — it overlays online-only metadata (slug, marketing copy, gallery images, online price) on the existing master records.

```text
Customer browses → /shop/                     (catalog)
                  /shop/p/<slug>/             (product detail page)
                  /shop/cart/                 (session cart, HTMX-driven)
                  /shop/checkout/             (email + shipping address)
                       ↓
            Stripe Checkout (hosted page on stripe.com)
                       ↓
       checkout.session.completed webhook → ERP records
                       ↓
SalesOrder (confirmed) → Invoice (posted) → Payment + JournalEntry
```

Payment processing is **Stripe Connect (Standard accounts)** — each tenant connects their *own* Stripe account, charges land in their balance directly, the DERP platform is never in the money path.

---

## Models

| Model | Purpose |
| --- | --- |
| `Category` | Hierarchical product taxonomy (slug, image, parent self-FK) |
| `ProductStorefront` | OneToOne over `inventory.Product`. Adds slug, category, online price, compare-at price, featured flag, marketing copy, SEO fields |
| `ProductImage` | Gallery (`FK → ProductStorefront`, ordered) |
| `Address` | Structured shipping/billing snapshot. Not tied to `Customer` so guest checkouts don't mutate historical records |
| `Checkout` | UUID-token pending order. Stores cart JSON, totals, Stripe IDs, and the resulting `sales_order` once paid |

The cart itself is **session-only** — JSON on `request.session`, no DB row until checkout. Quantity changes via HTMX swap the cart partial without a page reload.

---

## Customer-facing routes

| URL | Purpose |
| --- | --- |
| `/shop/` | Catalog landing (categories + featured + all products) |
| `/shop/c/<slug>/` | Category listing |
| `/shop/p/<slug>/` | Product detail page (gallery, sale price, related items) |
| `/shop/cart/` | Cart (HTMX qty updates and line removal) |
| `POST /shop/cart/add/<product_id>/` | Add to cart |
| `POST /shop/cart/update/<product_id>/` | Set qty (0 = remove) |
| `POST /shop/cart/remove/<product_id>/` | Remove line |
| `/shop/checkout/` | Email + shipping address form |
| `/shop/checkout/success/?token=…` | Post-payment landing |
| `/shop/checkout/cancel/?token=…` | Stripe-cancel landing |
| `/shop/checkout/dev/?token=…` | Dev-mode simulator (only when Stripe is not connected) |
| `POST /shop/webhooks/stripe/` | Stripe webhook (signature-verified) |

All shop pages extend `templates/public_base.html` so they inherit branding (logo, brand name, colors, font) from `WebsiteSettings` automatically. A Shop link and cart icon with live count badge are added to the public header.

---

## Admin routes (Stripe Connect OAuth)

Restricted to **Administrators** and **Managers**.

| URL | Purpose |
| --- | --- |
| `/derp/stripe/connect/` | Kick off Connect OAuth (redirects to `connect.stripe.com`) |
| `/derp/stripe/callback/` | OAuth return — exchanges `code` for `acct_…`, stores encrypted |
| `POST /derp/stripe/disconnect/` | Revoke + clear local credentials |
| `POST /derp/stripe/webhook-secret/` | Save the tenant's per-account `whsec_…` |

---

## Setup: tenant onboarding flow

A tenant goes from "ERP only" to "accepting real payments" in five steps.

### 1. Add a product

In Django admin (`/admin/inventory/product/`), create or pick a Product. Make sure `is_sellable=True`, `is_active=True`, and `price` is set. Upload a product image if you have one.

### 2. Add storefront metadata

Go to `/admin/webstore/productstorefront/` → **Add**. Pick the product, fill in:
- `slug` (auto-filled from name if blank)
- `category` (optional)
- `short_tagline`, `online_description` (marketing copy)
- `online_price` (0 = use `Product.price`)
- `compare_at_price` (>0 enables strike-through sale display)
- `is_online_active`, `is_featured`

Use the inline form to upload gallery images.

### 3. Connect Stripe

Go to `/derp/website/settings/` and click **Connect with Stripe**. Stripe handles signup if you don't have an account. You'll be redirected back with your `acct_…` stored encrypted on `WebsiteSettings`.

### 4. Register a webhook in your Stripe dashboard

In your own Stripe dashboard → Developers → Webhooks → Add endpoint, pointing at:

```text
https://<your-tenant-domain>/shop/webhooks/stripe/
```

Select event `checkout.session.completed`. After creation, copy the **Signing secret** (`whsec_…`) and paste it back into the **Stripe Payments** panel on `/derp/website/settings/`.

### 5. Try it

Visit `/shop/`. Browse, add to cart, check out. Stripe charges the card, the webhook fires, and the order lands as a confirmed `SalesOrder` + posted `Invoice` + applied `Payment` in your ERP.

---

## Dev mode (no Stripe required)

Before connecting Stripe, the checkout button on `/shop/checkout/` redirects to `/shop/checkout/dev/?token=…` instead of Stripe. That page shows a "Complete Payment (Dev)" button that runs the **same** server-side fulfillment code the real webhook triggers — `SalesOrder`, `Invoice`, `Payment`, and journal entries all get created.

This lets you exercise the full ERP wiring end-to-end before any real payment integration. The dev path automatically disables once the tenant connects Stripe.

---

## How `complete_checkout` posts to the ERP

`webstore/services.py::complete_checkout(checkout)` is idempotent and atomic. Either everything posts or nothing does. It calls the existing service-layer functions in `sales/services.py` — no duplicate posting logic.

1. **Find or create `sales.Customer`** by email (case-insensitive).
2. **Resolve revenue accounts** for each line via `resolve_revenue_account(product, customer)`.
3. **Create `SalesOrder` (DRAFT)** with lines.
4. **`confirm_sales_order(order)`** → status `CONFIRMED` (issues stock for stock products).
5. **`create_invoice_from_sales_order(order)`** → draft `Invoice`.
6. **`post_invoice(invoice)`** → writes the AR/revenue/tax/COGS journal entry, flips invoice to `SENT`.
7. **`receive_payment(...)`** → creates a `Payment` (direction=received, method=card), applies it to the invoice, posts the cash/AR journal entry. Invoice flips to `PAID`.
8. **Mark `Checkout.status = PAID`**, link `sales_order`, store `stripe_payment_intent`.

If anything raises, the entire transaction rolls back and the webhook handler returns 500. Stripe retries the webhook automatically.

---

## Stripe Connect: how multi-tenancy works

Each tenant has their own Stripe account. The DERP platform holds only:

- `STRIPE_SECRET_KEY` — platform key, used **only** to mint OAuth tokens (never charges money)
- `STRIPE_CONNECT_CLIENT_ID` — your Connect app's client ID (`ca_…`)

Each tenant's `WebsiteSettings` stores:

- `stripe_account_id` — `acct_…` returned by OAuth (encrypted at rest)
- `stripe_publishable_key` — cached for any future frontend use
- `stripe_webhook_secret` — `whsec_…` from their own dashboard (encrypted at rest)
- `stripe_connected_at` — for the settings UI

API calls pass `stripe_account=ws.stripe_account_id`, which routes the call to that tenant's account. Funds land in the tenant's Stripe balance, never the platform's.

Each tenant registers their own webhook on their own subdomain (`https://<tenant>/shop/webhooks/stripe/`), so webhook routing happens at the django-tenants middleware level — no cross-tenant dispatch logic needed.

---

## Encryption at rest

Two `WebsiteSettings` fields use `webstore.fields.EncryptedCharField` — Fernet-encrypted (AES-128-CBC + HMAC-SHA256) at write time, decrypted on attribute access:

- `stripe_account_id`
- `stripe_webhook_secret`

The encryption key is derived from `FIELD_ENCRYPTION_KEY` (falls back to `SECRET_KEY` if unset). A DB snapshot leaks nothing useful without the runtime key.

**Generate a key once:**

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Paste the output into `.env` as `FIELD_ENCRYPTION_KEY=…`. Treat it like a password — never commit, never log. Back it up in your secrets manager; losing it makes every encrypted row unrecoverable.

**Limitations:** encrypted columns can't be queried with `filter(field__exact=…)` because Fernet uses a random IV per write. Look up rows by another column (e.g. tenant FK) first, then read the decrypted value in Python.

---

## Environment variables

Platform-level (your `.env`):

| Variable | Purpose | Required? |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | Platform key for OAuth token exchange | Required to enable Connect onboarding |
| `STRIPE_PUBLISHABLE_KEY` | Platform publishable key | Currently unused; reserved |
| `STRIPE_CONNECT_CLIENT_ID` | Your Connect app's `ca_…` | Required for OAuth |
| `FIELD_ENCRYPTION_KEY` | Fernet key for column encryption | Required in production (falls back to `SECRET_KEY` in dev) |
| `WEBSTORE_CASH_ACCOUNT_CODE` | Asset account code that receives online sales | Optional; defaults to `1010` |

Per-tenant (stored in `WebsiteSettings`, never in env): `stripe_account_id`, `stripe_webhook_secret`.

---

## Startup checks

`webstore/checks.py` registers Django system checks that fail `manage.py check` (and therefore `runserver` / `migrate`) when the config is unsafe:

| ID | Severity | Triggers when |
| --- | --- | --- |
| `webstore.E001` | Error | `DEBUG=False` and `FIELD_ENCRYPTION_KEY` is unset |
| `webstore.E002` | Error | `DEBUG=False` and exactly one of `STRIPE_SECRET_KEY` / `STRIPE_CONNECT_CLIENT_ID` is set (partial config) |
| `webstore.W001` | Warning | `DEBUG=True` but `STRIPE_SECRET_KEY` starts with `sk_live_` (you're about to charge real cards from dev) |

---

## What's not built yet

These are intentionally outside the v1 scope. Add them when you need them:

- **Tax calculation** — `Customer.tax_rate` defaults to 0. Invoices post with `tax_total=0` unless you set per-customer rates. Add Stripe Tax integration or a regional table for a real solution.
- **Shipping rate calculation** — `Checkout.shipping_total` is always 0. Add a flat rate, weight-based table, or a carrier API integration.
- **Order confirmation emails** — the success page mentions "receipt sent" but nothing actually sends. Wire it up via the existing Resend config in `EMAIL_HOST_PASSWORD`.
- **Stock decrement on payment** — `StockMovement(ISSUE)` is created when `sales.confirm_sales_order` runs, which it does during checkout completion. If you want fulfillment-time issuing instead, refactor the service flow.
- **Customer accounts** — checkout is guest-only. The `Customer` record is created/matched by email, but there's no login or order-history page for shop visitors yet.
- **Platform fees** — pass `application_fee_amount` to `stripe.checkout.Session.create()` to take a per-transaction cut. Currently zero.
- **Stripe Tax / Klarna / Apple Pay** — Stripe Checkout supports these out of the box; enable them in each tenant's Stripe dashboard.
- **Webhook retries / dead-letter queue** — Stripe retries failed webhooks automatically for 3 days. For longer windows, integrate with Celery or similar.

---

## Files

```text
webstore/
  __init__.py
  admin.py                 Django admin for Category, ProductStorefront, ProductImage, Address, Checkout
  apps.py                  Registers startup checks in .ready()
  cart.py                  Cart class (session-backed, line resolution, subtotal calc)
  checks.py                Django system checks for FIELD_ENCRYPTION_KEY and Stripe config
  context_processors.py    Exposes cart_count and cart_subtotal to all templates
  fields.py                EncryptedCharField (Fernet)
  forms.py                 CheckoutForm (email + shipping address)
  models.py                Category, ProductStorefront, ProductImage, Address, Checkout
  services.py              snapshot_cart_for_checkout, complete_checkout
  stripe_service.py        Tenant-aware Stripe SDK wrapper (platform_is_configured, tenant_is_connected, OAuth, Session, webhook verify)
  urls.py                  All /shop/ and /derp/stripe/ routes
  views.py                 Catalog, cart, checkout, Stripe Connect, webhook
  migrations/0001_initial.py

templates/webstore/
  catalog.html
  category.html
  product_detail.html
  cart.html, _cart_body.html, _cart_summary.html
  _product_card.html
  checkout.html, checkout_success.html, checkout_cancel.html, checkout_dev.html
```
