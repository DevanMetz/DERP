# Website Editor & Public Tenant Sites

The Website Editor module allows tenants to build and publish responsive, database-backed public websites under their own root domain (`/`), completely powered by the ERP.

## How it works

1. **Root Domain Isolation**: The entire ERP lives under the `/derp/` subpath prefix. Accessing the bare root domain `/` will automatically render the page designated as the homepage (`is_homepage=True`).
2. **Subpages (`/p/<slug>/`)**: Subpages (e.g., About Us, Contact Us) are served securely on `/p/<slug>/` using search-engine friendly URL paths.
3. **Auto-Populating Templates**: If a tenant has not built a website yet, accessing their site home `/` will automatically initialize a beautiful 3-page template website consisting of:
   - **Home**: A stunning landing page highlighting key features and call-to-action paths.
   - **About Us**: A details page outlining mission and values.
   - **Contact Us**: A fully functional lead capture form.

## Accessing the Website Editor

To create and modify your public pages:
- Navigate to `/derp/website/` inside the ERP Workspace (or click **"Website Editor"** in the sidebar).
- Note: Access is strictly restricted to **Administrators** and **Managers**.

## Formatting & Design Guide

Your website is wrapped in a premium modern CSS grid container (`templates/public_base.html`). Write clean HTML directly in the page markup block to structure pages beautifully:

### Section Grids
To create multiple side-by-side columns that stack on mobile, wrap elements in a responsive grid style:
```html
<section style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 24px;">
  <div style="background: white; padding: 24px; border: 1px solid #e2e8f0; border-radius: 12px;">
    <h3>Column 1</h3>
    <p>Details go here.</p>
  </div>
  <div style="background: white; padding: 24px; border: 1px solid #e2e8f0; border-radius: 12px;">
    <h3>Column 2</h3>
    <p>Details go here.</p>
  </div>
</section>
```

### Call to Action Buttons
Use standard primary and secondary styling classes to create gorgeous buttons:
```html
<a href="/p/contact/" class="btn">Get Started</a>
<a href="/p/about-us/" class="btn secondary" style="border: 1px solid #e2e8f0; border-radius: 8px;">Learn More</a>
```

### Global Dynamic Header
Any new page you create and mark as **Published** will automatically and dynamically append to the public website's header navigation bar instantly.
