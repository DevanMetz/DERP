# Website Editor And Public Site

The Website Editor lets the organization running DERP publish branded public
pages from the same self-hosted installation as its ERP and storefront.

## Routes And Access

| URL | Purpose |
| --- | --- |
| `/` | Published homepage |
| `/p/<slug>/` | Published public subpage |
| `/shop/` | Public storefront |
| `/derp/website/` | Website management dashboard |
| `/derp/website/settings/` | Branding and Stripe environment guidance |
| `/derp/website/add/` | New page builder |
| `/derp/website/<id>/edit/` | Page builder for an existing page |

Website administration is restricted to Admin and Manager roles.

## Page Builder

The full-window builder provides:

- A block library for marketing, content, contact, and storefront sections.
- Drag and drop placement with inline text editing.
- Desktop, tablet, and mobile previews.
- Page title, slug, draft/published status, and homepage selection.
- SEO fields for description, keywords, and social sharing image.
- Raw HTML editing for deliberate customizations.
- Revision snapshots on save and local draft recovery.

If no public pages exist when the public home is opened, DERP initializes
starter Home, About Us, and Contact Us pages.

## Branding

Website settings apply to all public pages:

- Brand name and logo URL.
- Primary and secondary colors.
- Google Font family.
- Custom site-wide CSS.

The website and storefront share the public header, navigation, and cart
summary.

## Payments

This self-hosted version accepts storefront payments through the hosting
organization's Stripe account. Configure `STRIPE_SECRET_KEY` and
`STRIPE_WEBHOOK_SECRET` on the server and subscribe a Stripe webhook at
`/shop/webhooks/stripe/` to `checkout.session.completed`.

See [Webstore And Stripe Checkout](./webstore.md#real-payments).
