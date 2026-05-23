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

## Lot and Serial Number Tracking

The `StockMovement` model includes `lot_id` and `serial_no` fields to support granular tracking of physical goods:
- **Lot ID**: Used to group stock items produced or received together (e.g., for batch tracking, expiration tracking, or quality controls).
- **Serial Number**: Used to track unique individual units (e.g., for high-value electronics, warranty management, or customer returns).

*Note: In the current version, `lot_id` and `serial_no` are captured as free-text tracking fields on movement records. Future updates will introduce dedicated master tables for Lot and Serial records to enforce uniqueness, prevent double-assigning serial numbers, and support end-to-end trace history across manufacturing and returns.*
