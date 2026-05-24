# Website Editor & Public Tenant Sites

The Website Editor lets tenants build, design, and publish responsive, database-backed public pages under their root domain (`/`), composed in a full-viewport WYSIWYG builder. Pages share global branding (logo, colors, fonts, custom CSS) and integrate with the [webstore](./webstore.md) for product/category blocks.

---

## How It Works

1. **Root Domain Isolation**: The entire ERP lives under the `/derp/` subpath prefix (e.g., `/derp/dashboard/`). Accessing the bare root domain `/` renders the page designated as the homepage (`is_homepage=True`).
2. **Subpages (`/p/<slug>/`)**: Subpages are served on `/p/<slug>/` using search-engine friendly URL paths.
3. **Dynamic Navigation**: Any page marked **Published** is automatically added to the public website's header navigation. The header also shows a Shop link and cart icon when the webstore has products.
4. **Auto-Populating Templates**: If a tenant has not built a website yet, accessing their site home `/` automatically initializes a 3-page template website (Home, About Us, Contact Us).

---

## Accessing the Website Editor

Navigate to `/derp/website/` inside the ERP Workspace (or click **Website Editor** in the sidebar). Access is restricted to **Administrators** and **Managers**.

---

## The Full-Viewport Page Builder

When creating (`/derp/website/add/`) or editing (`/derp/website/<id>/edit/`) a page, the editor takes over the entire viewport — the standard app chrome (header, sidebar) is hidden and replaced with a focused builder shell:

```text
┌────────────────────────────────────────────────────────────┐
│ Exit · Brand · [Page Title]   /p/[slug]   📱 💻 🖥  [Save] │
├──────────┬─────────────────────────────────────────────────┤
│ 🧱 Blocks│                                                 │
│ ⚙ Style │           ┌──────────────────────────┐         │
│ 📄 Page  │           │                          │         │
│ 🔍 SEO   │           │   Live editable preview  │         │
│ 🕒 Hist  │           │   (iframe = canvas)     │         │
│ 💻 Code  │           │                          │         │
│          │           └──────────────────────────┘         │
│ [panel   │                                                 │
│  content]│                                                 │
└──────────┴─────────────────────────────────────────────────┘
```

### Top bar

- **Exit** — back to the website editor dashboard
- **Brand** — shortcut to `/derp/website/settings/` (logo, colors, fonts)
- **Title input + slug** — edit inline; the slug previews the public URL (`/p/<slug>/`)
- **Viewport switcher** — Desktop (100%) / Tablet (820px) / Mobile (390px). Animates between widths so you can verify responsive behavior at a glance.
- **Status pill** — Draft / Published, driven by the `is_published` toggle
- **Save** — submits the form; preserves CSRF, autosaves the local draft

### Left rail (collapsible)

Six tabs, icon-only at 56px, expandable to a 320px panel:

| Tab | Purpose |
| --- | --- |
| 🧱 **Blocks** | Drag-and-drop or click-to-append block library. Quick-start templates (Home, About, Contact, Webstore Landing) live here too. |
| ⚙ **Inspector** | Per-section style controls (padding slider, border radius, background, text alignment, move up/down, delete). Auto-opens when you click a section in the canvas. |
| 📄 **Page** | Title, slug, homepage toggle, published toggle |
| 🔍 **SEO** | Meta description (with 0/160 character counter), keywords, Open Graph image URL |
| 🕒 **History** | Revision list — click any snapshot to restore it |
| 💻 **Code** | Raw HTML editor with an Apply button to push changes back into the canvas |

### Canvas (the iframe)

The preview IS the editor. Sections render with hover overlays (`↑ ↓ ⚙ ×`), click any text to edit it inline, and selection wires the Inspector tab automatically. Dropping a block from the sidebar shows a live insertion indicator showing exactly where it will land.

---

## Block Library

### General-purpose blocks

- **Hero Banner** — Centered headline + CTA on a soft gradient
- **Features Grid** — 3-up cards with icon + heading + description
- **Pricing** — Two-tier plans with "Most Popular" highlight
- **Stats Banner** — Dark banner with three large numbers
- **FAQ** — Question/answer pairs
- **Call to Action** — Strong CTA on a brand-colored background
- **Text** — Heading + paragraph
- **Image + Text** — 50/50 split layout
- **Testimonials** — 3-up customer quote cards with avatars and 5-star ratings
- **Logo Cloud** — "Trusted by" strip
- **Team** — 4-up people grid
- **Contact Form** — Name / email / message
- **Newsletter** — Email capture on a dark background
- **Gallery** — Image grid
- **Video Embed** — Play-button hero with caption

### Webstore blocks

These render styled mockup data in the editor; on the live site they can be wired to real product data from `webstore.ProductStorefront`:

- **Product Grid** — Card-based product catalog (4-up)
- **Featured Product** — Hero product spotlight with sale price
- **Shop Benefits** — Free shipping / returns / secure checkout / support strip
- **Categories** — Browse-by-category tiles

### Quick-start templates

The Blocks tab includes a "Quick Start Template" panel (visible on the Create flow only) that drops pre-composed pages:

- **Home** — Hero + Features
- **About** — Text + Stats
- **Contact** — Contact Form
- **🛒 Webstore Landing** — Hero + Shop Benefits + Categories + Product Grid + Featured Product + Testimonials + Newsletter

---

## Editing Model

- **Click any text** (heading, paragraph, link, button, list item) on the canvas to edit inline. Each editable element gets a `contenteditable` attribute and a focus outline.
- **Section toolbar** appears on hover in the top-right corner of any block: `↑` move up, `↓` move down, `⚙` open Inspector, `×` delete.
- **Drag from sidebar** → drop on canvas. A blue insertion indicator shows the exact drop position.
- **Click a block card** in the sidebar to append it to the end of the canvas (no drag needed).
- **Inspector** updates the selected section's inline styles and re-serializes the HTML back into the form's `html_content` field every change.

---

## Local Draft Autosave & Recovery

Every 15 seconds, the canvas serializes to a `localStorage` entry keyed by page ID. When you reopen a page, if a local draft is newer than the server copy, a "Restore Backup" toast appears in the corner. Choose Restore or Discard. Successful save clears the draft.

---

## Page Revisions

Every save with modified HTML creates a `PageRevision` snapshot (`html_content`, `author`, `created_at`). The History tab lists the most recent 10 snapshots. Click one to restore — unsaved changes are confirmed-discarded first.

---

## SEO & Social

The SEO tab covers:

- **Meta description** with a live `0 / 160` character counter (turns red over 160)
- **Keywords** (comma-separated)
- **Social sharing image URL** for Open Graph cards on Slack, LinkedIn, Facebook, etc.

These fields land in `<meta>` tags in `templates/public_base.html` and propagate to all pages via `og:title`, `og:description`, `og:image`.

---

## Global Website & Theme Settings

`/derp/website/settings/` configures site-wide branding (the **Brand** shortcut in the page editor top bar jumps straight here):

- **Website Brand Name** — Shown in the header and copyright footer.
- **Logo Image URL** — SVG or PNG; hides the default 🌐 emoji when set.
- **Primary / Secondary Colors** — HSL color pickers driving the CSS variables `--primary-dark` and `--primary` on every public page.
- **Typography** — Google Font selector (Inter, Roboto, Outfit, Poppins, Playfair Display). Loads the chosen weight set automatically.
- **Custom Global CSS** — Raw CSS injected into the public site's `<style>` block. Useful for keyframe animations, third-party widget overrides, or one-off page styles.
- **Stripe Payments** — Connect with Stripe button + webhook secret panel. See [webstore docs](./webstore.md#setup-tenant-onboarding-flow) for the full onboarding flow.
