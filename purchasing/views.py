from datetime import date as date_cls
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from accounting.models import Account

from .forms import BillHeaderForm, BillLineFormSet, PayVendorForm, VendorForm
from .models import Bill, BillLine, Vendor
from .services import pay_vendor, post_bill


# ----------------------------- Vendors -------------------------------

@login_required
def vendor_list(request):
    vendors = Vendor.objects.all()
    return render(request, "purchasing/vendor_list.html", {"vendors": vendors})


@login_required
def vendor_edit(request, pk=None):
    vendor = get_object_or_404(Vendor, pk=pk) if pk else None
    if request.method == "POST":
        form = VendorForm(request.POST, instance=vendor)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f"Saved {obj.name}.")
            return redirect("vendor_list")
    else:
        form = VendorForm(instance=vendor)
    return render(request, "purchasing/vendor_form.html", {"form": form, "vendor": vendor})


# ------------------------------ Bills --------------------------------

@login_required
def bill_list(request):
    bills = Bill.objects.select_related("vendor").all()
    return render(request, "purchasing/bill_list.html", {"bills": bills})


@login_required
def bill_create(request):
    if request.method == "POST":
        header = BillHeaderForm(request.POST)
        formset = BillLineFormSet(request.POST)
        if header.is_valid() and formset.is_valid():
            bill = header.save(commit=False)
            bill.created_by = request.user
            bill.save()

            fallback_expense = Account.objects.filter(code="6900").first()  # Miscellaneous
            for line in formset.cleaned_data:
                product = line.get("product")
                desc = (line.get("description") or "").strip()
                qty = line.get("qty")
                cost = line.get("unit_cost")
                if not (product or desc) or not qty:
                    continue
                if not desc:
                    desc = f"{product.sku} {product.name}"
                expense_account = (
                    line.get("expense_account")
                    or (product.default_expense_account if product else None)
                    or bill.vendor.default_expense_account
                    or fallback_expense
                )
                BillLine.objects.create(
                    bill=bill, product=product, description=desc,
                    qty=qty, unit_cost=cost, expense_account=expense_account,
                )

            if "post_now" in request.POST:
                try:
                    post_bill(bill, user=request.user)
                except ValidationError as e:
                    messages.error(request, "; ".join(e.messages))
                    return redirect("bill_detail", pk=bill.pk)
                messages.success(request, f"Posted {bill.number}.")
            else:
                messages.success(request, "Draft saved.")
            return redirect("bill_detail", pk=bill.pk)
    else:
        header = BillHeaderForm(initial={
            "date": date_cls.today(),
            "due_date": date_cls.today(),
        })
        formset = BillLineFormSet()
    return render(request, "purchasing/bill_form.html", {"header": header, "formset": formset})


@login_required
def bill_detail(request, pk):
    bill = get_object_or_404(
        Bill.objects.select_related("vendor", "journal_entry").prefetch_related("lines__product", "lines__expense_account"),
        pk=pk,
    )
    return render(request, "purchasing/bill_detail.html", {"bill": bill})


@login_required
def bill_post(request, pk):
    bill = get_object_or_404(Bill, pk=pk)
    if request.method != "POST":
        return redirect("bill_detail", pk=pk)
    try:
        post_bill(bill, user=request.user)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    else:
        messages.success(request, f"Posted {bill.number}.")
    return redirect("bill_detail", pk=pk)


# ------------------------- Vendor payments ---------------------------

@login_required
def vendor_payment_create(request):
    vendor = None
    open_bills = []
    vendor_id = request.GET.get("vendor") or request.POST.get("vendor")
    if vendor_id:
        try:
            vendor = Vendor.objects.get(pk=vendor_id, is_active=True)
            open_bills = [
                b for b in Bill.objects.filter(
                    vendor=vendor,
                    status__in=[Bill.Status.ENTERED, Bill.Status.PAID],
                ).order_by("date", "id")
                if b.amount_due() > Decimal("0")
            ]
        except Vendor.DoesNotExist:
            pass

    if request.method == "POST":
        header = PayVendorForm(request.POST)
        if header.is_valid():
            applications: list[tuple[Bill, Decimal]] = []
            for bill in open_bills:
                raw = request.POST.get(f"apply_{bill.pk}", "").strip()
                if not raw:
                    continue
                try:
                    amt = Decimal(raw)
                except Exception:
                    messages.error(request, f"Invalid apply amount for bill {bill.number}.")
                    return redirect(request.get_full_path())
                if amt > 0:
                    applications.append((bill, amt))
            try:
                payment = pay_vendor(
                    vendor=header.cleaned_data["vendor"],
                    date=header.cleaned_data["date"],
                    amount=header.cleaned_data["amount"],
                    cash_account=header.cleaned_data["cash_account"],
                    method=header.cleaned_data["method"],
                    reference=header.cleaned_data.get("reference", ""),
                    notes=header.cleaned_data.get("notes", ""),
                    applications=applications,
                    user=request.user,
                )
            except ValidationError as e:
                messages.error(request, "; ".join(e.messages))
            else:
                messages.success(request, f"Recorded {payment.number} (${payment.amount}).")
                return redirect("bill_list")
    else:
        header = PayVendorForm(initial={
            "date": date_cls.today(),
            "vendor": vendor.pk if vendor else None,
        })

    return render(request, "purchasing/payment_form.html", {
        "header": header, "vendor": vendor, "open_bills": open_bills,
    })
