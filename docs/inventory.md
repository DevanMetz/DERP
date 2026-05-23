# Inventory

Inventory tracks products, stock-on-hand quantities, stock movements, and product cost.

## Product types

- Stock products carry inventory quantities and average cost.
- Service products do not create stock-on-hand records.

## Stock movements

Use `inventory.services.post_stock_movement()` for all stock updates. It validates item type, quantity direction, and over-issue rules before writing movement records.

Movement types include:

- Receipt: increases stock and can update weighted-average cost.
- Issue: decreases stock and prevents negative on-hand balances.
- Adjustment: corrects inventory quantities.

## Weighted-average costing

Positive stock receipts recalculate a product's cost from the previous on-hand value plus the new receipt value.

```text
new cost = ((current qty * current cost) + (received qty * received cost)) / new qty
```

## Workflow links

Stock movements can reference source documents such as invoices, goods receipts, and manufacturing orders. Those references are rendered as links in inventory views where possible.
