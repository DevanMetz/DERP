from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Product, ProductType, StockMovement, StockOnHand


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
    user=None,
) -> StockMovement:
    if product.type != ProductType.STOCK:
        raise ValidationError("Only stock items can have stock movements.")
    if qty <= 0:
        raise ValidationError("Stock movement quantity must be positive.")
    if unit_cost < 0:
        raise ValidationError("Stock movement unit cost cannot be negative.")

    on_hand, _ = StockOnHand.objects.select_for_update().get_or_create(product=product)
    if movement_type == StockMovement.MovementType.RECEIPT:
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
        if on_hand.qty < qty:
            raise ValidationError(f"Insufficient stock for {product.sku}.")
        on_hand.qty -= qty
    elif movement_type == StockMovement.MovementType.ADJUSTMENT:
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
        pass
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
        created_by=user,
    )
    on_hand.save(update_fields=["qty", "updated_at"])
    return movement
