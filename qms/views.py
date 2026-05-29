from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.permissions import write_required
from .models import (
    TriggerType, FieldType, InspectionTemplate, InspectionFieldTemplate,
    QualityInspection, InspectionValue, NonConformance, CAPA
)
from .forms import (
    InspectionTemplateForm, InspectionFieldTemplateForm, NonConformanceForm, CAPAForm
)
from .services import (
    complete_inspection, quarantine_lot, resolve_ncr
)
from inventory.models import Lot, Product, Location


# ---------------------------- Dashboard -----------------------------

@login_required
def qms_dashboard(request):
    total = QualityInspection.objects.count()
    pending = QualityInspection.objects.filter(status=QualityInspection.Status.DRAFT).count()
    passed = QualityInspection.objects.filter(status=QualityInspection.Status.PASS).count()
    failed = QualityInspection.objects.filter(status=QualityInspection.Status.FAIL).count()
    quarantined = QualityInspection.objects.filter(status=QualityInspection.Status.QUARANTINED).count()
    
    open_ncrs = NonConformance.objects.filter(status=NonConformance.Status.OPEN).count()
    active_capas = CAPA.objects.exclude(status=CAPA.Status.CLOSED).count()
    
    # First Pass Yield (FPY)
    total_completed = passed + failed
    fpy = (passed / total_completed * 100) if total_completed > 0 else 100.0
    
    # Recent items
    recent_inspections = QualityInspection.objects.all().select_related("template", "lot", "inspected_by")[:5]
    recent_ncrs = NonConformance.objects.all().select_related("reported_by", "lot")[:5]
    recent_capas = CAPA.objects.all().select_related("assigned_to", "non_conformance")[:5]
    
    # Quarantined Lots count
    quarantined_lots = Lot.objects.filter(is_quarantined=True).select_related("product")
    
    context = {
        "total": total,
        "pending": pending,
        "passed": passed,
        "failed": failed,
        "quarantined": quarantined,
        "open_ncrs": open_ncrs,
        "active_capas": active_capas,
        "fpy": round(fpy, 1),
        "recent_inspections": recent_inspections,
        "recent_ncrs": recent_ncrs,
        "recent_capas": recent_capas,
        "quarantined_lots": quarantined_lots,
    }
    return render(request, "qms/dashboard.html", context)


# --------------------------- Templates ------------------------------

@login_required
def template_list(request):
    templates = InspectionTemplate.objects.all().select_related("product")
    return render(request, "qms/template_list.html", {"templates": templates})


@login_required
def template_detail(request, pk):
    template = get_object_or_404(InspectionTemplate, pk=pk)
    fields = template.fields.all()
    return render(request, "qms/template_detail.html", {"template": template, "fields": fields})


@login_required
@write_required
def template_edit(request, pk=None):
    template = get_object_or_404(InspectionTemplate, pk=pk) if pk else None
    if request.method == "POST":
        form = InspectionTemplateForm(request.POST, instance=template)
        if form.is_valid():
            obj = form.save(commit=False)
            if not pk:
                obj.created_by = request.user
            obj.save()
            messages.success(request, f"Saved template {obj.name}.")
            return redirect("qms_template_detail", pk=obj.pk)
    else:
        form = InspectionTemplateForm(instance=template)
    return render(request, "qms/template_form.html", {"form": form, "template": template})


@login_required
@write_required
def field_edit(request, template_pk, pk=None):
    template = get_object_or_404(InspectionTemplate, pk=template_pk)
    field = get_object_or_404(InspectionFieldTemplate, template=template, pk=pk) if pk else None
    if request.method == "POST":
        form = InspectionFieldTemplateForm(request.POST, instance=field)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.template = template
            obj.save()
            messages.success(request, f"Saved field {obj.name} for template {template.name}.")
            return redirect("qms_template_detail", pk=template.pk)
    else:
        form = InspectionFieldTemplateForm(instance=field)
    return render(request, "qms/field_form.html", {"form": form, "template": template, "field": field})


@login_required
@write_required
def field_delete(request, template_pk, pk):
    template = get_object_or_404(InspectionTemplate, pk=template_pk)
    field = get_object_or_404(InspectionFieldTemplate, template=template, pk=pk)
    if request.method == "POST":
        field.delete()
        messages.success(request, "Field deleted successfully.")
    return redirect("qms_template_detail", pk=template.pk)


# -------------------------- Inspections -----------------------------

@login_required
def inspection_list(request):
    inspections = QualityInspection.objects.all().select_related("template", "lot", "inspected_by", "template__product")
    return render(request, "qms/inspection_list.html", {"inspections": inspections})


@login_required
def inspection_detail(request, pk):
    inspection = get_object_or_404(
        QualityInspection.objects.select_related("template", "lot", "inspected_by", "goods_receipt", "manufacturing_order").prefetch_related("values__field_template"),
        pk=pk
    )
    non_conformances = inspection.non_conformances.all()
    return render(request, "qms/inspection_detail.html", {"inspection": inspection, "non_conformances": non_conformances})


@login_required
@write_required
def inspection_complete(request, pk):
    inspection = get_object_or_404(QualityInspection.objects.prefetch_related("values__field_template"), pk=pk)
    if inspection.status != QualityInspection.Status.DRAFT:
        messages.warning(request, "This inspection is already completed.")
        return redirect("qms_inspection_detail", pk=pk)
        
    if request.method == "POST":
        values_data = {}
        for val in inspection.values.all():
            field_id = val.field_template.pk
            if val.field_template.field_type == FieldType.BOOLEAN:
                # Checkbox sends 'on' if checked, else omitted
                values_data[str(field_id)] = request.POST.get(f"field_{field_id}") == "on"
            else:
                values_data[str(field_id)] = request.POST.get(f"field_{field_id}", "").strip()
                
        notes = request.POST.get("notes", "").strip()
        try:
            completed_inspection = complete_inspection(
                inspection=inspection,
                notes=notes,
                values_data=values_data,
                user=request.user
            )
            if completed_inspection.status == QualityInspection.Status.PASS:
                messages.success(request, f"Quality Inspection {completed_inspection.number} completed successfully and passed.")
            else:
                messages.error(request, f"Quality Inspection {completed_inspection.number} failed! A Non-Conformance Report has been auto-generated.")
            return redirect("qms_inspection_detail", pk=pk)
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        except Exception as e:
            messages.error(request, f"Failed to save inspection: {str(e)}")
            
    return render(request, "qms/inspection_form.html", {"inspection": inspection})


# ------------------------- Non-Conformances --------------------------

@login_required
def ncr_list(request):
    ncrs = NonConformance.objects.all().select_related("inspection", "lot", "reported_by", "lot__product")
    return render(request, "qms/ncr_list.html", {"ncrs": ncrs})


@login_required
def ncr_detail(request, pk):
    ncr = get_object_or_404(
        NonConformance.objects.select_related("inspection", "lot", "reported_by", "disposition_by", "lot__product"),
        pk=pk
    )
    capas = ncr.capas.all()
    return render(request, "qms/ncr_detail.html", {"ncr": ncr, "capas": capas})


@login_required
@write_required
def ncr_edit(request, pk=None):
    ncr = get_object_or_404(NonConformance, pk=pk) if pk else None
    if request.method == "POST":
        form = NonConformanceForm(request.POST, instance=ncr)
        if form.is_valid():
            obj = form.save(commit=False)
            if not pk:
                obj.reported_by = request.user
                from core.numbering import next_document_number
                obj.number = next_document_number("NC", year=timezone.localdate().year)
            
            # If changing disposition, assign who and when
            if "disposition" in form.changed_data and obj.disposition != NonConformance.Disposition.PENDING:
                obj.disposition_by = request.user
                obj.disposition_at = timezone.now()
                obj.status = NonConformance.Status.CLOSED
                # If disposition is USE_AS_IS, release lot
                if obj.disposition == NonConformance.Disposition.USE_AS_IS and obj.lot:
                    obj.lot.is_quarantined = False
                    obj.lot.save()
            obj.save()
            messages.success(request, f"Saved Non-Conformance {obj.number}.")
            return redirect("qms_ncr_detail", pk=obj.pk)
    else:
        form = NonConformanceForm(instance=ncr)
    return render(request, "qms/ncr_form.html", {"form": form, "ncr": ncr})


# ----------------------------- CAPA ---------------------------------

@login_required
def capa_list(request):
    capas = CAPA.objects.all().select_related("non_conformance", "assigned_to")
    return render(request, "qms/capa_list.html", {"capas": capas})


@login_required
def capa_detail(request, pk):
    capa = get_object_or_404(
        CAPA.objects.select_related("non_conformance", "assigned_to", "closed_by"),
        pk=pk
    )
    return render(request, "qms/capa_detail.html", {"capa": capa})


@login_required
@write_required
def capa_edit(request, pk=None):
    capa = get_object_or_404(CAPA, pk=pk) if pk else None
    if request.method == "POST":
        form = CAPAForm(request.POST, instance=capa)
        if form.is_valid():
            obj = form.save(commit=False)
            if not pk:
                from core.numbering import next_document_number
                obj.number = next_document_number("CAPA", year=timezone.localdate().year)
            
            # Handle closing
            if obj.status == CAPA.Status.CLOSED and "status" in form.changed_data:
                obj.closed_by = request.user
                obj.closed_at = timezone.now()
            obj.save()
            messages.success(request, f"Saved CAPA Action {obj.number}.")
            return redirect("qms_capa_detail", pk=obj.pk)
    else:
        form = CAPAForm(instance=capa)
    return render(request, "qms/capa_form.html", {"form": form, "capa": capa})


# -------------------------- Manual Quarantine -------------------------

@login_required
@write_required
def quarantine_action(request):
    if request.method == "POST":
        lot_id = request.POST.get("lot_id")
        reason = request.POST.get("reason", "").strip()
        lot = get_object_or_404(Lot, pk=lot_id)
        if not reason:
            messages.error(request, "A reason must be provided to quarantine a lot.")
            return redirect("stock_movement_list") # fallback
            
        ncr = quarantine_lot(lot, reason, request.user)
        messages.success(request, f"Lot {lot.lot_number} successfully quarantined. Non-Conformance Report {ncr.number} has been created.")
        return redirect("qms_ncr_detail", pk=ncr.pk)
    return redirect("qms_dashboard")
