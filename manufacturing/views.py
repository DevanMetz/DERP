from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import BillOfMaterials, BOMComponent, ManufacturingOrder
from .forms import BOMForm, BOMComponentFormSet, ManufacturingOrderForm
from .services import confirm_manufacturing_order, complete_manufacturing_order, cancel_manufacturing_order


@login_required
def bom_list(request):
    boms = BillOfMaterials.objects.all().select_related("product").prefetch_related("components__product")
    return render(request, "manufacturing/bom_list.html", {"boms": boms})


@login_required
def bom_detail(request, pk):
    bom = get_object_or_404(
        BillOfMaterials.objects.select_related("product").prefetch_related("components__product"), pk=pk
    )
    return render(request, "manufacturing/bom_detail.html", {"bom": bom})


@login_required
@transaction.atomic
def bom_create(request):
    if request.method == "POST":
        form = BOMForm(request.POST)
        formset = BOMComponentFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            bom = form.save(commit=False)
            bom.created_by = request.user
            bom.save()
            
            formset.instance = bom
            formset.save()
            
            messages.success(request, "Bill of Materials created.")
            return redirect("bom_detail", pk=bom.pk)
    else:
        form = BOMForm()
        formset = BOMComponentFormSet()
    
    return render(request, "manufacturing/bom_form.html", {
        "form": form,
        "formset": formset,
        "is_create": True
    })


@login_required
@transaction.atomic
def bom_edit(request, pk):
    bom = get_object_or_404(BillOfMaterials, pk=pk)
    if request.method == "POST":
        form = BOMForm(request.POST, instance=bom)
        formset = BOMComponentFormSet(request.POST, instance=bom)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, "Bill of Materials saved.")
            return redirect("bom_detail", pk=bom.pk)
    else:
        form = BOMForm(instance=bom)
        formset = BOMComponentFormSet(instance=bom)
    
    return render(request, "manufacturing/bom_form.html", {
        "form": form,
        "formset": formset,
        "is_create": False,
        "bom": bom
    })


@login_required
def mo_list(request):
    mos = ManufacturingOrder.objects.all().select_related("product", "bom")
    return render(request, "manufacturing/mo_list.html", {"mos": mos})


@login_required
def mo_create(request):
    if request.method == "POST":
        form = ManufacturingOrderForm(request.POST)
        if form.is_valid():
            mo = form.save(commit=False)
            mo.bom = mo.product.bom
            mo.created_by = request.user
            mo.save()
            messages.success(request, "Manufacturing Order created.")
            return redirect("mo_detail", pk=mo.pk)
    else:
        form = ManufacturingOrderForm()
    
    return render(request, "manufacturing/mo_form.html", {"form": form})


@login_required
def mo_detail(request, pk):
    mo = get_object_or_404(
        ManufacturingOrder.objects.select_related("product", "bom__product", "journal_entry"), pk=pk
    )
    
    # Calculate required components and shortages
    components_info = []
    has_shortages = False
    
    for comp in mo.bom.components.all().select_related("product"):
        required_qty = comp.qty * mo.qty_target
        on_hand_qty = Decimal("0.0000")
        if hasattr(comp.product, "stock_on_hand"):
            on_hand_qty = comp.product.stock_on_hand.qty
        
        shortage = Decimal("0.0000")
        if on_hand_qty < required_qty:
            shortage = required_qty - on_hand_qty
            has_shortages = True
            
        components_info.append({
            "product": comp.product,
            "required_qty": required_qty,
            "on_hand_qty": on_hand_qty,
            "shortage": shortage,
            "is_short": shortage > 0
        })
        
    return render(request, "manufacturing/mo_detail.html", {
        "mo": mo,
        "components_info": components_info,
        "has_shortages": has_shortages
    })


@login_required
def mo_confirm(request, pk):
    mo = get_object_or_404(ManufacturingOrder, pk=pk)
    if request.method == "POST":
        try:
            confirm_manufacturing_order(mo, request.user)
            messages.success(request, f"Manufacturing Order {mo.number} confirmed.")
        except ValidationError as e:
            messages.error(request, ", ".join(e.messages) if hasattr(e, "messages") else str(e))
    return redirect("mo_detail", pk=mo.pk)


@login_required
def mo_complete(request, pk):
    mo = get_object_or_404(ManufacturingOrder, pk=pk)
    if request.method == "POST":
        try:
            complete_manufacturing_order(mo, request.user)
            messages.success(request, f"Manufacturing Order {mo.number} completed successfully.")
        except ValidationError as e:
            messages.error(request, ", ".join(e.messages) if hasattr(e, "messages") else str(e))
    return redirect("mo_detail", pk=mo.pk)


@login_required
def mo_cancel(request, pk):
    mo = get_object_or_404(ManufacturingOrder, pk=pk)
    if request.method == "POST":
        try:
            cancel_manufacturing_order(mo, request.user)
            messages.success(request, f"Manufacturing Order {mo.number or mo.pk} cancelled.")
        except ValidationError as e:
            messages.error(request, ", ".join(e.messages) if hasattr(e, "messages") else str(e))
    return redirect("mo_detail", pk=mo.pk)
