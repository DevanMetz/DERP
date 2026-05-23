# Inventory

Inventory tracks products, stock-on-hand quantities, stock movements, and product cost.

## Product types

- Stock products carry inventory quantities and average cost.
- Service products do not create stock-on-hand records.

## Product availability

Each product has workflow availability toggles that control where it can be selected:

- **Purchasable** products appear on purchase orders and vendor bills.
- **Sellable** products appear on sales orders and invoices.
- **Manufacturable** products can be assigned BOMs and selected for manufacturing orders.

Inactive products remain hidden from active transaction pickers regardless of these availability settings. Existing products default to enabled for all three workflows so upgrades preserve current behavior until a product is intentionally restricted.

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

### Transfers User Interface
A dedicated **Transfers** module is accessible under the Operations group in the main sidebar navigation (routing to `/products/transfers/`). Key features:
- **Interactive Filtering**: Refine transfers by Product catalog, Source Location (From), Destination Location (To), and Date ranges.
- **Directional Traceability**: Columns display the transaction timestamp, direction arrow indicators between warehouses, transfer quantities, custom memos, and the operating user for audits.
- **Serial Tracking Visibility**: High-value serialized items display their exact unique serial numbers inline.

### Warehouse Configuration
Users can view and manage their physical warehouse locations from the **Warehouses** configuration module:
- **Navigation**: Access the **Warehouses** option under the **Operations** section in the main sidebar (routing to `/products/locations/`).
- **Create and Edit**: Create new warehouse locations or update existing ones (Name, Description, and Active Status) through a dedicated card form.
- **Active/Inactive Status**: Toggling a warehouse to inactive hides/prevents it from active transaction selections while preserving all historical stock movement and ledger data for complete compliance and auditing.

### Multi-Warehouse Integration in Operations
DERP supports localized warehouse assignment across your transactional flows:
- **Manufacturing Orders**: Select a specific assembly warehouse when planning production. Stock shortages are calculated against that warehouse's inventory balances, components are consumed from it, and finished assemblies are deposited directly into it.
- **Sales Invoices**: Allocate a fulfillment warehouse on a line-item basis when drafting invoices. Stock issue movements are completed at the allocated warehouses, reducing their localized balances.
- **Goods Receipts**: Assign a target receiving warehouse for each item line on a purchase order receipt. Stock receipt movements are generated and stored at those specific locations.

