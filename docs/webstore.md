# Webstore And Stripe Checkout

The `webstore` app turns this DERP installation's inventory products into a
public catalog with a session-backed cart and checkout. When Stripe reports a
completed payment, DERP posts the sale through its normal sales and accounting
workflows.

## Routes

| URL | Purpose |
| --- | --- |
| `/shop/` | Public storefront landing |
| `/shop/c/<slug>/` | Product category |
| `/shop/p/<slug>/` | Product detail |
| `/shop/cart/` | Cart |
| `/shop/checkout/` | Customer email and shipping form |
| `/shop/checkout/success/?token=...` | Payment result page |
| `/shop/webhooks/stripe/` | Stripe webhook endpoint |

## Data Model

| Model | Purpose |
| --- | --- |
| `Category` | Product taxonomy |
| `ProductStorefront` | Public pricing and merchandising for an inventory product |
| `ProductImage` | Product gallery images |
| `Address` | Checkout address snapshot |
| `Checkout` | Pending cart snapshot connected to ERP fulfillment |

The cart is held in the browser session. A `Checkout` row is created only
after the customer submits the checkout form.

## Development Checkout

When `STRIPE_SECRET_KEY` is blank, checkout opens a local completion screen.
This makes development possible without Stripe credentials. Posting the manual
completion uses the same ERP fulfillment service as a paid webhook.

## Real Payments

Configure the server environment:

```dotenv
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

In Stripe, add a webhook endpoint for:

```text
https://your-domain.example/shop/webhooks/stripe/
```

Subscribe to `checkout.session.completed`.

Checkout flow:

1. The customer submits their cart and address.
2. DERP creates a local `Checkout` record and a hosted Stripe Checkout Session.
3. DERP records the checkout token in Stripe Session metadata.
4. Stripe sends a signed `checkout.session.completed` webhook after payment.
5. DERP validates the signature, finds the checkout by metadata token, and
   completes it exactly once.

Stripe payments go directly to the Stripe account configured by the person
hosting this installation. There are no connected accounts or onboarding
flows in the self-hosted version.

## ERP Fulfillment

`webstore.services.complete_checkout(checkout)` is transactional and
idempotent. For a paid cart it:

1. Finds or creates a customer by email.
2. Creates and confirms a sales order, issuing stock.
3. Builds and posts an invoice.
4. Receives payment into the configured cash account.
5. Marks the checkout paid and links the sales order.

If processing fails, the database transaction rolls back and Stripe can retry
the webhook.

## Configuration Checks

`python manage.py check` warns when:

| ID | Condition |
| --- | --- |
| `webstore.W001` | A live Stripe key is configured while `DEBUG=True` |
| `webstore.W002` | `STRIPE_SECRET_KEY` is configured without `STRIPE_WEBHOOK_SECRET` |

## Current Boundaries

- Checkout is guest-only.
- Tax and shipping-rate integrations are not built in.
- Refunds are handled in Stripe rather than through an ERP refund workflow.
- Order confirmation email delivery is not yet wired to checkout fulfillment.
