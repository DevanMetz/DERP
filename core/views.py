from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect, render
from django import forms
from django.utils import timezone
from datetime import date
from decimal import Decimal
from django.db.models import Sum

from .models import Company
from accounting.models import Account, AccountType, JournalEntry, JournalLine
from inventory.models import Product, ProductType

ZERO = Decimal("0.00")


@login_required
def home(request):
    company = Company.get()
    all_stock_products = Product.objects.filter(type=ProductType.STOCK, is_active=True).select_related("stock_on_hand")
    low_stock_products = [p for p in all_stock_products if p.is_low_stock]
    return render(request, "core/home.html", {
        "company": company,
        "low_stock_products": low_stock_products,
    })


@login_required
def dashboard_view(request):
    company = Company.get()
    today = timezone.localdate()
    current_year = today.year
    start_date = date(current_year, 1, 1)
    end_date = date(current_year, 12, 31)

    # 1. YTD Revenue: Sum credit minus debit for REVENUE in current year
    rev_totals = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__gte=start_date,
        entry__date__lte=end_date,
        account__type=AccountType.REVENUE
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    ytd_revenue = (rev_totals["c"] or ZERO) - (rev_totals["d"] or ZERO)

    # 2. YTD Expenses: Sum debit minus credit for EXPENSE in current year
    exp_totals = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__gte=start_date,
        entry__date__lte=end_date,
        account__type=AccountType.EXPENSE
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    ytd_expenses = (exp_totals["d"] or ZERO) - (exp_totals["c"] or ZERO)

    # 3. Net Profit
    net_profit = ytd_revenue - ytd_expenses

    # 4. Outstanding AR (Asset: Debit - Credit for code '1200')
    ar_totals = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__lte=today,
        account__code="1200"
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    outstanding_ar = (ar_totals["d"] or ZERO) - (ar_totals["c"] or ZERO)

    # 5. Outstanding AP (Liability: Credit - Debit for code '2110')
    ap_totals = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__lte=today,
        account__code="2110"
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    outstanding_ap = (ap_totals["c"] or ZERO) - (ap_totals["d"] or ZERO)

    # 6. GL Inventory Valuation (Asset: Debit - Credit for code '1300')
    gl_inv_totals = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__lte=today,
        account__code="1300"
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    gl_inventory_val = (gl_inv_totals["d"] or ZERO) - (gl_inv_totals["c"] or ZERO)

    # 7. Operational Inventory Valuation
    products = Product.objects.filter(type=ProductType.STOCK, is_active=True).select_related("stock_on_hand")
    operational_inventory_val = ZERO
    for p in products:
        qty = p.stock_on_hand.qty if hasattr(p, "stock_on_hand") else ZERO
        operational_inventory_val += qty * p.cost

    # 8. Monthly Revenue & Expenses
    monthly_revenue = [ZERO] * 12
    monthly_expenses = [ZERO] * 12

    rev_by_month = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__gte=start_date,
        entry__date__lte=end_date,
        account__type=AccountType.REVENUE
    ).values("entry__date__month").annotate(d=Sum("debit"), c=Sum("credit"))

    for item in rev_by_month:
        m = item["entry__date__month"]
        if m and 1 <= m <= 12:
            monthly_revenue[m - 1] = (item["c"] or ZERO) - (item["d"] or ZERO)

    exp_by_month = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__gte=start_date,
        entry__date__lte=end_date,
        account__type=AccountType.EXPENSE
    ).values("entry__date__month").annotate(d=Sum("debit"), c=Sum("credit"))

    for item in exp_by_month:
        m = item["entry__date__month"]
        if m and 1 <= m <= 12:
            monthly_expenses[m - 1] = (item["d"] or ZERO) - (item["c"] or ZERO)

    monthly_revenue_float = [float(v) for v in monthly_revenue]
    monthly_expenses_float = [float(v) for v in monthly_expenses]

    # 9. Top 5 Products for Doughnut Chart
    product_valuations = []
    for p in products:
        qty = p.stock_on_hand.qty if hasattr(p, "stock_on_hand") else ZERO
        val = qty * p.cost
        if val > ZERO:
            product_valuations.append({
                "sku": p.sku,
                "name": p.name,
                "val": float(val)
            })

    product_valuations.sort(key=lambda x: x["val"], reverse=True)
    top_products = product_valuations[:5]
    other_sum = sum(x["val"] for x in product_valuations[5:])

    doughnut_labels = [f"{x['sku']} - {x['name']}" for x in top_products]
    doughnut_data = [x["val"] for x in top_products]

    if other_sum > 0:
        doughnut_labels.append("Other Items")
        doughnut_data.append(other_sum)

    return render(request, "core/dashboard.html", {
        "company": company,
        "ytd_revenue": ytd_revenue,
        "ytd_expenses": ytd_expenses,
        "net_profit": net_profit,
        "outstanding_ar": outstanding_ar,
        "outstanding_ap": outstanding_ap,
        "gl_inventory_val": gl_inventory_val,
        "operational_inventory_val": operational_inventory_val,
        "monthly_revenue_json": monthly_revenue_float,
        "monthly_expenses_json": monthly_expenses_float,
        "doughnut_labels_json": doughnut_labels,
        "doughnut_data_json": doughnut_data,
        "current_year": current_year,
    })


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            "name", "legal_name", "email", "phone", "address", "tax_id",
            "fiscal_year_start_month", "fiscal_year_start_day",
        ]


@login_required
def company_setup(request):
    company = Company.get()
    if request.method == "POST":
        form = CompanyForm(request.POST, instance=company)
        if form.is_valid():
            form.save()
            messages.success(request, "Company saved.")
            return redirect("company_setup")
    else:
        form = CompanyForm(instance=company)
    return render(request, "core/company_setup.html", {"form": form})
