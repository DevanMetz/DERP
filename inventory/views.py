from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ProductForm
from .models import Product, StockMovement


@login_required
def product_list(request):
    products = Product.objects.select_related("stock_on_hand").all()
    return render(request, "inventory/product_list.html", {"products": products})


@login_required
def product_edit(request, pk=None):
    product = get_object_or_404(Product, pk=pk) if pk else None
    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f"Saved {obj.sku}.")
            return redirect("product_list")
    else:
        form = ProductForm(instance=product)
    return render(request, "inventory/product_form.html", {"form": form, "product": product})


@login_required
def product_detail(request, pk):
    product = get_object_or_404(
        Product.objects.select_related("stock_on_hand", "default_revenue_account", "default_expense_account"),
        pk=pk,
    )
    movements = product.stock_movements.select_related("created_by").order_by("-posted_at", "-id")[:10]
    
    margin_pct = Decimal("0.0")
    if product.price > 0:
        margin_pct = (((product.price - product.cost) / product.price) * 100).quantize(Decimal("0.1"))
        
    return render(
        request,
        "inventory/product_detail.html",
        {
            "product": product,
            "movements": movements,
            "margin_pct": margin_pct,
        },
    )


@login_required
def stock_movement_list(request):
    movements = StockMovement.objects.select_related("product", "created_by").order_by("-posted_at", "-id")[:200]
    return render(request, "inventory/stock_movement_list.html", {"movements": movements})
