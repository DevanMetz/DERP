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

*Note: Lot and Serial Number entries are backed by dedicated `Lot` and `SerialNumber` database tables. When a `lot_id` or `serial_no` is provided to `post_stock_movement()`, the system dynamically executes validation checks:
- **Quantity limits**: Serial-tracked movements must have a quantity of exactly `1.0000`.
- **Double-assignment checks**: Receiving or adjusting-in a serial number will fail if that serial number is already `IN_STOCK` for the product.
- **Traceability checks**: Issuing a serial number will fail if the serial number is not currently `IN_STOCK` for the product.
- **Lot auto-resolution**: Providing a `lot_id` automatically resolves or creates the `Lot` record and links any associated serial numbers to it for full batch traceability.*

## Warehouse Locations and Stock Transfers

DERP supports multi-warehouse inventory tracking and stock transfers:
- **Locations**: Master database entries representing physical storage sites (e.g., `"Warehouse A"`, `"Showroom"`, `"Main Warehouse"`).
- **Location Stock**: Tracks inventory quantities of each stock product individually per warehouse location.
- **Stock Transfers**: Shifts stock between locations without affecting global stock on hand or average cost (WAC).

### Key Rules and Validation
- **Default Location**: To maintain backward compatibility, if a movement (Receipt, Issue, or Adjustment) is posted without a location, it automatically resolves to `"Main Warehouse"`.
- **Legacy Self-Healing**: Unallocated legacy stock or direct `StockOnHand` seed values are automatically mapped to `"Main Warehouse"` the first time that product is processed by `post_stock_movement()`.
- **Transfer Constraints**:
  - Source and destination locations must be different.
  - The source location must have sufficient stock for the transfer to succeed.
  - Serialized items being transferred must currently be `IN_STOCK` and located at the source location. Successful transfers update the serial number's location to the destination.
