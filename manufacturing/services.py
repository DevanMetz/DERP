from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import ManufacturingOrder
from inventory.models import StockMovement
from inventory.services import post_stock_movement
from accounting.services import post_transaction, LineSpec


@transaction.atomic
def confirm_manufacturing_order(mo: ManufacturingOrder, user) -> ManufacturingOrder:
    """
    Transition a manufacturing order from Draft to Confirmed,
    assigning a gap-free MO number if not already assigned.
    """
    if mo.status != ManufacturingOrder.Status.DRAFT:
        raise ValidationError("Only Draft orders can be confirmed.")
    
    mo.status = ManufacturingOrder.Status.CONFIRMED
    if not mo.number:
        from core.numbering import next_document_number
        mo.number = next_document_number("MO", year=mo.date_planned.year)
    mo.save(update_fields=["status", "number"])
    return mo


@transaction.atomic
def complete_manufacturing_order(mo: ManufacturingOrder, user) -> ManufacturingOrder:
    """
    Complete a manufacturing order. Issues raw components from inventory,
    receives the finished assembly, recalculates its average cost, and posts
    a balanced DR/CR journal entry to Inventory Asset.
    """
    if mo.status != ManufacturingOrder.Status.CONFIRMED:
        raise ValidationError("Only Confirmed orders can be completed.")
    
    bom = mo.bom
    components = bom.components.all()
    if not components.exists():
        raise ValidationError(f"BOM '{bom}' has no components defined.")

    # 1. Validation & shortage checks
    shortages = []
    required_components = []
    
    for comp in components:
        req_qty = comp.qty * mo.qty_target
        on_hand_qty = Decimal("0.0000")
        if hasattr(comp.product, "stock_on_hand"):
            on_hand_qty = comp.product.stock_on_hand.qty
        
        if on_hand_qty < req_qty:
            shortages.append(
                f"{comp.product.sku} ({comp.product.name}): Required {req_qty:.4f}, On Hand {on_hand_qty:.4f}"
            )
        required_components.append((comp, req_qty))

    if shortages:
        raise ValidationError(
            "Insufficient raw materials in stock:\n" + "\n".join(shortages)
        )

    # 2. Compute costings and perform inventory / GL postings
    total_material_cost = Decimal("0.00")
    line_specs = []
    
    # Issue raw materials
    for comp, req_qty in required_components:
        comp_cost = comp.product.cost
        comp_total_cost = (req_qty * comp_cost).quantize(Decimal("0.01"))
        total_material_cost += comp_total_cost
        
        # Post operational stock movement: ISSUE component
        post_stock_movement(
            product=comp.product,
            movement_type=StockMovement.MovementType.ISSUE,
            qty=req_qty,
            unit_cost=comp_cost,
            ref_doc_type="ManufacturingOrder",
            ref_doc_id=mo.id,
            memo=f"Consumed in MO {mo.number}",
            user=user
        )
        
        # Credit raw materials in GL
        line_specs.append(
            LineSpec(
                account_code="1300",
                credit=comp_total_cost,
                debit=Decimal("0.00"),
                memo=f"MO {mo.number} Consumed: {comp.product.sku}"
            )
        )

    # Calculate finished goods unit cost
    finished_unit_cost = (total_material_cost / mo.qty_target).quantize(Decimal("0.01"))
    
    # Post operational stock movement: RECEIPT finished product (automatically updates WAC)
    post_stock_movement(
        product=mo.product,
        movement_type=StockMovement.MovementType.RECEIPT,
        qty=mo.qty_target,
        unit_cost=finished_unit_cost,
        ref_doc_type="ManufacturingOrder",
        ref_doc_id=mo.id,
        memo=f"Produced in MO {mo.number}",
        user=user
    )
    
    # Debit finished goods in GL
    line_specs.append(
        LineSpec(
            account_code="1300",
            debit=total_material_cost,
            credit=Decimal("0.00"),
            memo=f"MO {mo.number} Finished: {mo.product.sku}"
        )
    )

    # Post balanced transaction to general ledger (only if non-zero value)
    je = None
    if total_material_cost > Decimal("0.00"):
        je = post_transaction(
            date=timezone.localdate(),
            memo=f"Completion of MO {mo.number}",
            lines=line_specs,
            user=user,
            source_doc_type="ManufacturingOrder",
            source_doc_id=mo.id
        )

    # 3. Finalize order status
    mo.qty_produced = mo.qty_target
    mo.status = ManufacturingOrder.Status.COMPLETED
    mo.date_completed = timezone.now()
    mo.completed_by = user
    mo.journal_entry = je
    mo.save()

    return mo


@transaction.atomic
def cancel_manufacturing_order(mo: ManufacturingOrder, user) -> ManufacturingOrder:
    """
    Cancel a manufacturing order. Only allowed if it is in Draft or Confirmed status.
    """
    if mo.status not in [ManufacturingOrder.Status.DRAFT, ManufacturingOrder.Status.CONFIRMED]:
        raise ValidationError("Only Draft or Confirmed orders can be cancelled.")
    
    mo.status = ManufacturingOrder.Status.CANCELLED
    mo.save(update_fields=["status"])
    return mo
