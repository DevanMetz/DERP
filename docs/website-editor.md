# Website Editor & Public Tenant Sites

The Website Editor module allows tenants to build, design, and publish responsive, database-backed public websites under their own root domain (`/`), completely powered by the ERP. It features a visual, block-based Drag-and-Drop Page Builder with inline text editing, global brand styling customizers, and page revisions log tracking.

---

## How It Works

1. **Root Domain Isolation**: The entire ERP lives under the `/derp/` subpath prefix (e.g., `/derp/dashboard/`, `/derp/inventory/`). Accessing the bare root domain `/` will automatically render the page designated as the homepage (`is_homepage=True`).
2. **Subpages (`/p/<slug>/`)**: Subpages (e.g., `/p/about-us/`, `/p/contact/`) are served securely on `/p/<slug>/` using search-engine friendly URL paths.
3. **Dynamic Navigation**: Any page marked as **Published** is automatically and dynamically added to the public website's header navigation bar instantly.
4. **Auto-Populating Templates**: If a tenant has not built a website yet, accessing their site home `/` will automatically initialize a beautiful 3-page template website consisting of **Home**, **About Us**, and **Contact Us**.

---

## Accessing the Website Editor

To create and modify your public pages:
- Navigate to `/derp/website/` inside the ERP Workspace (or click **"Website Editor"** in the sidebar).
- Note: Access is strictly restricted to **Administrators** and **Managers**.

---

## Page Builder Workspaces

When creating (`/derp/website/add/`) or editing (`/derp/website/<id>/edit/`) a page, the editor features two togglable modes:

### 1. 🧱 Visual Drag-and-Drop Builder (Default)
A visual page authoring layout featuring a split-screen canvas:
* **Sidebar Block Drawer**: Drag modern pre-designed block components (Hero Banner, Features Grid, Pricing Cards, Stats Banner, FAQ List, Simple Text, CTA Section) from the sidebar and drop them onto the canvas. *(Single-clicking a block also appends it to the canvas)*.
* **Inline Text Editing**: Click directly on any header, paragraph, button, list item, or link on the canvas and start typing your own copy instantly. A blue outline guides your focus.
* **Block Controls**: Hover over any section on the canvas to display sorting controls:
  * Move block up (`⬆️`) or down (`⬇️`) in real-time.
  * Delete block (`🗑️`) permanently.

### 2. 💻 HTML Code
A raw, syntax-friendly monospace textarea for developers who want absolute control over layout styles, CSS, and inline HTML structures. A drawer of pre-designed blocks is available to click-insert boilerplate markup instantly.

---

## Page Revisions & Restore

To ensure peace of mind, every save is version-controlled:
* Every time a page is saved with modified HTML content, the system automatically creates a `PageRevision` snapshot in the database, logging the timestamp and the administrator who made the change.
* Inside the page editor, navigate to the **"🕒 Revision History"** tab, select a previous snapshot, and click to restore older content directly back into your active workspace.

---

## Advanced SEO & Social Metadata

Inside the **"🔍 SEO & Social Metadata"** tab of the page editor, you can optimize search rankings:
* **Meta Description Summary**: An SEO description box containing a character counter (highlights red if you exceed the ideal search engine limit of 160 characters).
* **SEO Keywords**: Comma-separated keyword search tags.
* **Social Sharing (Open Graph) Image**: Direct URL to a custom sharing card image (displays when links are shared on Slack, LinkedIn, or Facebook).

---

## Global Website & Theme Settings

To customize the branding of your entire public site, navigate to the Website Editor dashboard and click **"Global Theme Settings"** (`/derp/website/settings/`). You can configure:
* **Website Brand Name**: Customizes header brand names and copyright footers.
* **Logo Image Link**: URL to an SVG or PNG brand logo (hides text brand headers when configured).
* **Theme Colors (Color Pickers)**: Configure primary brand colors and secondary interactive buttons dynamically using HSL color pickers.
* **Typography Select**: Select your brand font family (Inter, Roboto, Outfit, Poppins, or Playfair Display). The website automatically imports and renders the typography globally.
* **Custom Global CSS**: Add custom class overrides, brand keyframe animations, or structural layouts safely.
