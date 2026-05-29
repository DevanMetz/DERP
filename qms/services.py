from decimal import Decimal
from django.db import models
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (
    TriggerType, FieldType, InspectionTemplate, InspectionFieldTemplate,
    QualityInspection, InspectionValue, NonConformance, CAPA
)
from inventory.models import Lot, SerialNumber, Location
from purchasing.models import GoodsReceipt
from manufacturing.models import ManufacturingOrder
from core.numbering import next_document_number


@transaction.atomic
def create_pending_inspections_for_receipt(goods_receipt: GoodsReceipt, user=None) -> list[QualityInspection]:
    """
    Hooks into purchasing GoodsReceipt posting.
    Auto-generates draft QualityInspections for received items with active templates.
    """
    inspections = []
    # Loop over all received lines
    for line in goods_receipt.lines.all():
        # Find active templates triggered by receiving that match this product or have product=Null
        templates = InspectionTemplate.objects.filter(
            trigger_type=TriggerType.RECEIVING,
            is_active=True
        ).filter(
            models.Q(product=line.product) | models.Q(product__isnull=True)
        )
        
        for t in templates:
            # Let's see if a lot is linked to the stock movement
            lot = None
            if line.stock_movement and line.stock_movement.lot_id:
                try:
                    lot = Lot.objects.get(product=line.product, lot_number=line.stock_movement.lot_id)
                except Lot.DoesNotExist:
                    # Let's create a Lot if it doesn't exist
                    lot = Lot.objects.create(product=line.product, lot_number=line.stock_movement.lot_id)
            
            # Create the inspection
            inspection = QualityInspection.objects.create(
                template=t,
                status=QualityInspection.Status.DRAFT,
                goods_receipt=goods_receipt,
                lot=lot,
                inspected_by=None
            )
            
            # Create inspection values placeholders
            for f in t.fields.all():
                InspectionValue.objects.create(
                    inspection=inspection,
                    field_template=f
                )
            inspections.append(inspection)
            
    return inspections


@transaction.atomic
def create_pending_inspections_for_mo(mo: ManufacturingOrder, user=None) -> list[QualityInspection]:
    """
    Hooks into manufacturing MO completion.
    Auto-generates draft QualityInspections for finished assemblies with active templates.
    """
    inspections = []
    # Find active templates triggered by manufacturing that match this product or have product=Null
    templates = InspectionTemplate.objects.filter(
        trigger_type=TriggerType.MANUFACTURING,
        is_active=True
    ).filter(
        models.Q(product=mo.product) | models.Q(product__isnull=True)
    )
    
    for t in templates:
        # Create the inspection
        inspection = QualityInspection.objects.create(
            template=t,
            status=QualityInspection.Status.DRAFT,
            manufacturing_order=mo,
            inspected_by=None
        )
        
        # Create inspection values placeholders
        for f in t.fields.all():
            InspectionValue.objects.create(
                inspection=inspection,
                field_template=f
            )
        inspections.append(inspection)
        
    return inspections


@transaction.atomic
def complete_inspection(inspection: QualityInspection, notes: str, values_data: dict, user) -> QualityInspection:
    """
    Fills in test values, evaluates pass/fail status, updates associated lot quarantine flags,
    and returns the completed QualityInspection.
    """
    if inspection.status != QualityInspection.Status.DRAFT:
        raise ValidationError("Only draft/pending inspections can be completed.")
        
    inspection.notes = notes
    inspection.inspected_by = user
    inspection.inspected_at = timezone.now()
    
    # Fill values and check overall pass/fail status
    all_passed = True
    for val in inspection.values.all():
        field_id = str(val.field_template.pk)
        if field_id in values_data:
            v_type = val.field_template.field_type
            raw_val = values_data[field_id]
            if v_type == FieldType.BOOLEAN:
                val.value_boolean = raw_val == True or raw_val == 'True' or raw_val == 'on'
            elif v_type == FieldType.NUMERIC:
                if raw_val is not None and str(raw_val).strip() != '':
                    val.value_numeric = Decimal(str(raw_val))
                else:
                    val.value_numeric = None
            else:
                val.value_text = str(raw_val)
            val.save()
            
            if not val.is_passing:
                all_passed = False
        else:
            if val.field_template.is_required:
                all_passed = False
                
    # Determine overall status
    if all_passed:
        inspection.status = QualityInspection.Status.PASS
        # If lot is marked as quarantined, release it
        if inspection.lot and inspection.lot.is_quarantined:
            inspection.lot.is_quarantined = False
            inspection.lot.save()
    else:
        inspection.status = QualityInspection.Status.FAIL
        # Quarantine the lot
        if inspection.lot:
            inspection.lot.is_quarantined = True
            inspection.lot.save()
            
    # Assign gap-free number
    if not inspection.number:
        inspection.number = next_document_number("QC", year=timezone.localdate().year)
        
    inspection.save()
    
    # If failed, raise a Non-Conformance Report automatically
    if inspection.status == QualityInspection.Status.FAIL:
        create_ncr_from_failed_inspection(inspection, user)
        
    return inspection


@transaction.atomic
def create_ncr_from_failed_inspection(inspection: QualityInspection, user) -> NonConformance:
    """
    Helper to automatically raise a NonConformance for a failed inspection.
    """
    ncr = NonConformance.objects.create(
        inspection=inspection,
        title=f"Failed Inspection: {inspection.template.name}",
        description=f"Auto-generated because Quality Inspection {inspection.number} failed.\nNotes: {inspection.notes}",
        severity=NonConformance.Severity.MAJOR,
        status=NonConformance.Status.OPEN,
        lot=inspection.lot,
        location=None,
        reported_by=user
    )
    ncr.number = next_document_number("NC", year=timezone.localdate().year)
    ncr.save()
    return ncr


@transaction.atomic
def quarantine_lot(lot: Lot, reason: str, user) -> NonConformance:
    """
    Manually quarantines a lot, raises an NCR, and marks lot status.
    """
    lot.is_quarantined = True
    lot.save()
    
    # Create NCR
    ncr = NonConformance.objects.create(
        title=f"Manual Quarantine: Lot {lot.lot_number}",
        description=f"Lot {lot.lot_number} for product {lot.product.sku} was manually quarantined. Reason: {reason}",
        severity=NonConformance.Severity.MAJOR,
        status=NonConformance.Status.OPEN,
        disposition=NonConformance.Disposition.PENDING,
        lot=lot,
        reported_by=user
    )
    ncr.number = next_document_number("NC", year=timezone.localdate().year)
    ncr.save()
    return ncr


@transaction.atomic
def resolve_ncr(ncr: NonConformance, disposition: str, notes: str, user) -> NonConformance:
    """
    Resolves/closes an NCR and applies final disposition.
    """
    if ncr.status in [NonConformance.Status.RESOLVED, NonConformance.Status.CLOSED]:
        raise ValidationError("This Non-Conformance is already closed.")
        
    ncr.disposition = disposition
    ncr.disposition_notes = notes
    ncr.disposition_by = user
    ncr.disposition_at = timezone.now()
    ncr.status = NonConformance.Status.CLOSED
    ncr.save()
    
    # If disposition is USE_AS_IS, release the lot
    if disposition == NonConformance.Disposition.USE_AS_IS and ncr.lot:
        ncr.lot.is_quarantined = False
        ncr.lot.save()
        
    return ncr
