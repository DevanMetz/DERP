from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Product, ProductType, StockMovement, StockOnHand, Lot, SerialNumber, Location, LocationStock


@transaction.atomic
def post_stock_movement(
    *,
    product: Product,
    movement_type: str,
    qty: Decimal,
    unit_cost: Decimal = Decimal("0.00"),
    ref_doc_type: str = "",
    ref_doc_id: int | None = None,
    memo: str = "",
    lot_id: str = "",
    serial_no: str = "",
    location: Location | str | None = None,
    to_location: Location | str | None = None,
    user=None,
) -> StockMovement:
    if product.type != ProductType.STOCK:
        raise ValidationError("Only stock items can have stock movements.")
    if qty <= 0:
        raise ValidationError("Stock movement quantity must be positive.")
    if unit_cost < 0:
        raise ValidationError("Stock movement unit cost cannot be negative.")

    # Validate quantity for serial numbers
    if serial_no and qty != Decimal("1.0000"):
        raise ValidationError("Serial-tracked stock movements must have a quantity of exactly 1.")

    # Resolve locations
    if isinstance(location, str) and location.strip():
        location, _ = Location.objects.get_or_create(name=location.strip())
    if isinstance(to_location, str) and to_location.strip():
        to_location, _ = Location.objects.get_or_create(name=to_location.strip())

    if not location and movement_type != StockMovement.MovementType.TRANSFER:
        location, _ = Location.objects.get_or_create(
            name="Main Warehouse",
            defaults={"description": "Default warehouse location."}
        )

    # Validate for Transfers
    if movement_type == StockMovement.MovementType.TRANSFER:
        if not location or not to_location:
            raise ValidationError("Stock transfers require both source and destination locations.")
        if location == to_location:
            raise ValidationError("Source and destination locations must be different.")

    # Resolve Lot
    lot = None
    if lot_id:
        lot, _ = Lot.objects.get_or_create(product=product, lot_number=lot_id)

    # Validate and update SerialNumber status and location
    if serial_no:
        if movement_type in (StockMovement.MovementType.RECEIPT, StockMovement.MovementType.ADJUSTMENT):
            sn_record, created = SerialNumber.objects.get_or_create(
                product=product,
                serial_number=serial_no,
                defaults={"status": SerialNumber.Status.IN_STOCK, "lot": lot, "location": location}
            )
            if not created:
                if sn_record.status == SerialNumber.Status.IN_STOCK:
                    raise ValidationError(f"Serial number '{serial_no}' for product {product.sku} is already in stock.")
                sn_record.status = SerialNumber.Status.IN_STOCK
                sn_record.location = location
                if lot:
                    sn_record.lot = lot
                sn_record.save(update_fields=["status", "location", "lot"])
        elif movement_type == StockMovement.MovementType.ISSUE:
            try:
                sn_record = SerialNumber.objects.get(product=product, serial_number=serial_no)
            except SerialNumber.DoesNotExist:
                raise ValidationError(f"Serial number '{serial_no}' for product {product.sku} is not in stock.")
            
            if sn_record.status != SerialNumber.Status.IN_STOCK:
                raise ValidationError(f"Serial number '{serial_no}' for product {product.sku} is not in stock.")
            
            sn_record.status = SerialNumber.Status.ISSUED
            sn_record.location = None
            sn_record.save(update_fields=["status", "location"])
        elif movement_type == StockMovement.MovementType.TRANSFER:
            try:
                sn_record = SerialNumber.objects.get(product=product, serial_number=serial_no)
            except SerialNumber.DoesNotExist:
                raise ValidationError(f"Serial number '{serial_no}' for product {product.sku} is not in stock.")
            
            if sn_record.status != SerialNumber.Status.IN_STOCK or sn_record.location != location:
                raise ValidationError(f"Serial number '{serial_no}' for product {product.sku} is not at source location {location.name}.")
            
            sn_record.location = to_location
            sn_record.save(update_fields=["location"])

    on_hand, _ = StockOnHand.objects.select_for_update().get_or_create(product=product)

    # Sync unallocated legacy stock to Main Warehouse
    if location and location.name == "Main Warehouse":
        loc_stock, _ = LocationStock.objects.select_for_update().get_or_create(product=product, location=location)
        unallocated_qty = on_hand.qty - loc_stock.qty
        if unallocated_qty > 0:
            loc_stock.qty += unallocated_qty
            loc_stock.save(update_fields=["qty"])

    if movement_type == StockMovement.MovementType.RECEIPT:
        loc_stock, _ = LocationStock.objects.select_for_update().get_or_create(product=product, location=location)
        loc_stock.qty += qty
        loc_stock.save(update_fields=["qty"])

        current_qty = on_hand.qty
        current_cost = product.cost
        new_qty = current_qty + qty
        if new_qty > 0:
            new_cost = ((current_qty * current_cost) + (qty * unit_cost)) / new_qty
            product.cost = new_cost.quantize(Decimal("0.01"))
        else:
            product.cost = unit_cost
        product.save(update_fields=["cost"])
        
        on_hand.qty += qty
    elif movement_type == StockMovement.MovementType.ISSUE:
        loc_stock, _ = LocationStock.objects.select_for_update().get_or_create(product=product, location=location)
        if loc_stock.qty < qty:
            raise ValidationError(f"Insufficient stock for {product.sku} at location {location.name}.")
        loc_stock.qty -= qty
        loc_stock.save(update_fields=["qty"])

        if on_hand.qty < qty:
            raise ValidationError(f"Insufficient stock for {product.sku}.")
        on_hand.qty -= qty
    elif movement_type == StockMovement.MovementType.ADJUSTMENT:
        loc_stock, _ = LocationStock.objects.select_for_update().get_or_create(product=product, location=location)
        loc_stock.qty += qty
        loc_stock.save(update_fields=["qty"])

        current_qty = on_hand.qty
        current_cost = product.cost
        new_qty = current_qty + qty
        if new_qty > 0:
            cost_to_use = unit_cost if unit_cost > 0 else current_cost
            new_cost = ((current_qty * current_cost) + (qty * cost_to_use)) / new_qty
            product.cost = new_cost.quantize(Decimal("0.01"))
        else:
            product.cost = unit_cost
        product.save(update_fields=["cost"])
        
        on_hand.qty += qty
    elif movement_type == StockMovement.MovementType.TRANSFER:
        source_stock, _ = LocationStock.objects.select_for_update().get_or_create(product=product, location=location)
        if source_stock.qty < qty:
            raise ValidationError(f"Insufficient stock for {product.sku} at source location {location.name}.")
        dest_stock, _ = LocationStock.objects.select_for_update().get_or_create(product=product, location=to_location)
        
        source_stock.qty -= qty
        dest_stock.qty += qty
        
        source_stock.save(update_fields=["qty"])
        dest_stock.save(update_fields=["qty"])
    else:
        raise ValidationError("Invalid stock movement type.")

    movement = StockMovement.objects.create(
        product=product,
        movement_type=movement_type,
        qty=qty,
        unit_cost=unit_cost,
        ref_doc_type=ref_doc_type,
        ref_doc_id=ref_doc_id,
        memo=memo,
        lot_id=lot_id,
        serial_no=serial_no,
        location=location,
        to_location=to_location,
        created_by=user,
    )
    on_hand.save(update_fields=["qty", "updated_at"])
    return movement
