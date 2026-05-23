# Business Workflows

DERP keeps accounting and inventory synchronized by routing business actions through service functions.

## Sales order to invoice

1. Create a sales order and lines.
2. Confirm the sales order.
3. DERP creates a draft invoice and issues stock for stock products.
4. Post the invoice to create AR, revenue, tax, COGS, and inventory GL lines.
5. Void the invoice with a reversing journal entry if needed.

Sales confirmation rolls back if stock is insufficient.

## Purchase order to bill

1. Create a purchase order and lines.
2. Issue the purchase order.
3. Receive goods against one or more lines.
4. Create a draft bill from the purchase order or a goods receipt.
5. Post the bill to accounts payable and expense or inventory-related accounts.
6. Reverse goods receipts or void bills through service-layer actions.

Goods receipt billing prevents duplicate bills from the same receipt.

## Manufacturing order completion

1. Create a BOM for a finished product.
2. Create and confirm a manufacturing order.
3. Complete the order.
4. DERP issues raw materials, receives finished goods, updates finished-good cost, and posts a balanced GL entry.

Completion is atomic. If raw materials are short, stock and accounting remain unchanged.

## AI Copilot drafting

The copilot panel (robot icon at bottom-right) can take any of these workflows from a plain-English sentence to a draft preview the user confirms:

- `bought 5 PLA filament from BambuLab at $20 each` → draft PO
- `sold 3 widgets to Acme for $50 each` → draft SO
- `build 50 widgets` → draft MO

It also resolves the record on the page you're viewing. On a customer page, *"what did they buy last month?"* answers itself; on a vendor page, *"draft a PO"* skips the "which vendor?" step.

See [AI Copilot](ai-copilot) for the full feature list, examples, and safety model.
