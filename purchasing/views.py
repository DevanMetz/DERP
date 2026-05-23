from datetime import date as date_cls
from decimal import Decimal
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.http import FileResponse

from core.models import Company
from core.permissions import write_required
from core.pdf_service import generate_document_pdf

from .forms import (
    BillHeaderForm, BillLineFormSet, GoodsReceiptHeaderForm, PayVendorForm,
    PurchaseOrderHeaderForm, PurchaseOrderLineFormSet, VendorForm,
)
from .models import Bill, GoodsReceipt, PurchaseOrder, Vendor
from .services import (
    create_bill_from_purchase_order, create_bill_lines, create_purchase_order_lines,
    issue_purchase_order, pay_vendor, post_bill, receive_purchase_order,
    reverse_goods_receipt, undo_bill_from_purchase_order,
    undo_issue_purchase_order, create_bill_from_receipt,
)


# ----------------------------- Vendors -------------------------------

@login_required
def vendor_list(request):
    vendors = Vendor.objects.all()
    return render(request, "purchasing/vendor_list.html", {"vendors": vendors})


@login_required
@write_required
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


@login_required
def vendor_detail(request, pk):
    vendor = get_object_or_404(Vendor, pk=pk)
    orders = PurchaseOrder.objects.filter(vendor=vendor).select_related("vendor").order_by("-date", "-id")
    bills = Bill.objects.filter(vendor=vendor).select_related("vendor").order_by("-date", "-id")
    
    # Calculate lifetime values
    total_purchases = sum((bill.total() for bill in bills if bill.status != Bill.Status.VOID), Decimal("0.00"))
    total_due = sum((bill.amount_due() for bill in bills if bill.status in [Bill.Status.ENTERED, Bill.Status.PAID]), Decimal("0.00"))
    
    return render(
        request,
        "purchasing/vendor_detail.html",
        {
            "vendor": vendor,
            "orders": orders,
            "bills": bills,
            "total_purchases": total_purchases,
            "total_due": total_due,
        },
    )


# ------------------------- Purchase orders ---------------------------

@login_required
def purchase_order_list(request):
    from core.views import apply_filters
    qs = PurchaseOrder.objects.select_related("vendor").all()

    status = request.GET.get("status", "")
    name = request.GET.get("name", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    if status:
        qs = qs.filter(status=status)
    if name:
        qs = qs.filter(vendor__name__icontains=name)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)

    SORT_FIELDS = ["date", "vendor__name", "status", "number"]
    qs, sort, direction = apply_filters(qs, request, SORT_FIELDS)

    active_filters = []
    if status:
        active_filters.append({"label": f"Status: {dict(PurchaseOrder.Status.choices).get(status, status)}", "remove": "status"})
    if name:
        active_filters.append({"label": f"Vendor: {name}", "remove": "name"})
    if date_from:
        active_filters.append({"label": f"From: {date_from}", "remove": "date_from"})
    if date_to:
        active_filters.append({"label": f"To: {date_to}", "remove": "date_to"})

    return render(request, "purchasing/purchase_order_list.html", {
        "orders": qs,
        "status_choices": PurchaseOrder.Status.choices,
        "filters": {"status": status, "name": name, "date_from": date_from, "date_to": date_to},
        "active_filters": active_filters,
        "sort": sort,
        "dir": direction,
    })



@login_required
@write_required
def purchase_order_create(request):
    if request.method == "POST":
        header = PurchaseOrderHeaderForm(request.POST)
        formset = PurchaseOrderLineFormSet(request.POST)
        if header.is_valid() and formset.is_valid():
            order = header.save(commit=False)
            order.created_by = request.user
            order.save()
            create_purchase_order_lines(order, formset.cleaned_data)

            if "issue_now" in request.POST:
                try:
                    issue_purchase_order(order, user=request.user)
                except ValidationError as e:
                    messages.error(request, "; ".join(e.messages))
                    return redirect("purchase_order_detail", pk=order.pk)
                messages.success(request, f"Issued {order.number}.")
            else:
                messages.success(request, "Draft purchase order saved.")
            return redirect("purchase_order_detail", pk=order.pk)
    else:
        header = PurchaseOrderHeaderForm(initial={"date": date_cls.today()})
        formset = PurchaseOrderLineFormSet()
    return render(request, "purchasing/purchase_order_form.html", {"header": header, "formset": formset})


@login_required
def purchase_order_detail(request, pk):
    order = get_object_or_404(
        PurchaseOrder.objects.select_related("vendor").prefetch_related(
            "lines__product", "lines__expense_account", "bills",
        ),
        pk=pk,
    )
    return render(request, "purchasing/purchase_order_detail.html", {"order": order})


@login_required
@write_required
def purchase_order_issue(request, pk):
    order = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method != "POST":
        return redirect("purchase_order_detail", pk=pk)
    try:
        issue_purchase_order(order, user=request.user)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    else:
        messages.success(request, f"Issued {order.number}.")
    return redirect("purchase_order_detail", pk=pk)


@login_required
@write_required
def purchase_order_unissue(request, pk):
    order = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method != "POST":
        return redirect("purchase_order_detail", pk=pk)
    try:
        undo_issue_purchase_order(order)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    else:
        messages.success(request, "Purchase order moved back to draft.")
    return redirect("purchase_order_detail", pk=pk)


@login_required
@write_required
def purchase_order_bill(request, pk):
    order = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method != "POST":
        return redirect("purchase_order_detail", pk=pk)
    try:
        bill = create_bill_from_purchase_order(order, user=request.user)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
        return redirect("purchase_order_detail", pk=pk)
    messages.success(request, f"Created draft bill from {order.number}.")
    return redirect("bill_detail", pk=bill.pk)


@login_required
@write_required
def purchase_order_undo_bill(request, pk):
    order = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method != "POST":
        return redirect("purchase_order_detail", pk=pk)
    try:
        undo_bill_from_purchase_order(order)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    else:
        messages.success(request, "Draft bill removed and purchase order reopened.")
    return redirect("purchase_order_detail", pk=pk)


@login_required
@write_required
def purchase_order_receive(request, pk):
    order = get_object_or_404(
        PurchaseOrder.objects.select_related("vendor").prefetch_related("lines__product"),
        pk=pk,
    )
    if request.method == "POST":
        header = GoodsReceiptHeaderForm(request.POST)
        if header.is_valid():
            receipts = []
            for line in order.lines.all():
                raw = request.POST.get(f"receive_{line.pk}", "").strip()
                if not raw:
                    continue
                try:
                    qty = Decimal(raw)
                except Exception:
                    messages.error(request, f"Invalid received quantity for {line.description}.")
                    return redirect("purchase_order_receive", pk=pk)
                if qty > 0:
                    location = None
                    loc_id = request.POST.get(f"receive_location_{line.pk}", "").strip()
                    if loc_id:
                        from inventory.models import Location
                        try:
                            location = Location.objects.get(pk=loc_id, is_active=True)
                        except Location.DoesNotExist:
                            pass
                    receipts.append((line, qty, location))
            try:
                receipt = receive_purchase_order(
                    order=order,
                    date=header.cleaned_data["date"],
                    receipts=receipts,
                    notes=header.cleaned_data.get("notes", ""),
                    user=request.user,
                )
            except ValidationError as e:
                messages.error(request, "; ".join(e.messages))
            else:
                messages.success(request, f"Posted {receipt.number}.")
                return redirect("purchase_order_detail", pk=pk)
    else:
        header = GoodsReceiptHeaderForm(initial={"date": date_cls.today()})

    from inventory.models import Location
    locations = Location.objects.filter(is_active=True).order_by("name")

    return render(request, "purchasing/goods_receipt_form.html", {
        "header": header,
        "order": order,
        "locations": locations,
    })



@login_required
@write_required
def goods_receipt_reverse(request, pk):
    receipt = get_object_or_404(GoodsReceipt.objects.select_related("purchase_order"), pk=pk)
    order_pk = receipt.purchase_order_id
    if request.method != "POST":
        return redirect("purchase_order_detail", pk=order_pk)
    try:
        reverse_goods_receipt(receipt, user=request.user)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    else:
        messages.success(request, f"Reversed {receipt.number}.")
    return redirect("purchase_order_detail", pk=order_pk)


@login_required
def goods_receipt_detail(request, pk):
    receipt = get_object_or_404(
        GoodsReceipt.objects.select_related("purchase_order", "purchase_order__vendor").prefetch_related(
            "lines__product", "lines__po_line", "bills"
        ),
        pk=pk,
    )
    bill = receipt.bills.first()
    return render(
        request,
        "purchasing/goodsreceipt_detail.html",
        {"receipt": receipt, "bill": bill},
    )


@login_required
@write_required
def bill_create_from_receipt(request, pk):
    receipt = get_object_or_404(GoodsReceipt, pk=pk)
    if request.method != "POST":
        return redirect("goods_receipt_detail", pk=pk)
    try:
        bill = create_bill_from_receipt(receipt, user=request.user)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
        return redirect("goods_receipt_detail", pk=pk)
    messages.success(request, f"Created draft bill from {receipt.number}.")
    return redirect("bill_detail", pk=bill.pk)


# ------------------------------ Bills --------------------------------

@login_required
def bill_list(request):
    from core.views import apply_filters
    qs = Bill.objects.select_related("vendor").all()

    status = request.GET.get("status", "")
    name = request.GET.get("name", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    due_from = request.GET.get("due_from", "")
    due_to = request.GET.get("due_to", "")

    if status:
        qs = qs.filter(status=status)
    if name:
        qs = qs.filter(vendor__name__icontains=name)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if due_from:
        qs = qs.filter(due_date__gte=due_from)
    if due_to:
        qs = qs.filter(due_date__lte=due_to)

    SORT_FIELDS = ["date", "due_date", "vendor__name", "status", "number"]
    qs, sort, direction = apply_filters(qs, request, SORT_FIELDS)

    active_filters = []
    if status:
        active_filters.append({"label": f"Status: {dict(Bill.Status.choices).get(status, status)}", "remove": "status"})
    if name:
        active_filters.append({"label": f"Vendor: {name}", "remove": "name"})
    if date_from:
        active_filters.append({"label": f"From: {date_from}", "remove": "date_from"})
    if date_to:
        active_filters.append({"label": f"To: {date_to}", "remove": "date_to"})
    if due_from:
        active_filters.append({"label": f"Due from: {due_from}", "remove": "due_from"})
    if due_to:
        active_filters.append({"label": f"Due to: {due_to}", "remove": "due_to"})

    return render(request, "purchasing/bill_list.html", {
        "bills": qs,
        "status_choices": Bill.Status.choices,
        "filters": {"status": status, "name": name, "date_from": date_from, "date_to": date_to,
                    "due_from": due_from, "due_to": due_to},
        "active_filters": active_filters,
        "sort": sort,
        "dir": direction,
    })



@login_required
@write_required
def bill_create(request):
    if request.method == "POST":
        header = BillHeaderForm(request.POST)
        formset = BillLineFormSet(request.POST)
        if header.is_valid() and formset.is_valid():
            bill = header.save(commit=False)
            bill.created_by = request.user
            bill.save()

            create_bill_lines(bill, formset.cleaned_data)

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
        Bill.objects.select_related("vendor", "journal_entry", "purchase_order").prefetch_related("lines__product", "lines__expense_account"),
        pk=pk,
    )
    return render(request, "purchasing/bill_detail.html", {"bill": bill})


@login_required
@write_required
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
@write_required
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


@login_required
@write_required
def bill_void(request, pk):
    bill = get_object_or_404(Bill, pk=pk)
    if request.method != "POST":
        return redirect("bill_detail", pk=pk)
    if not request.user.can_void:
        messages.error(request, "Only Administrators can void bills.")
        return redirect("bill_detail", pk=pk)
    from .services import void_bill
    try:
        void_bill(bill, user=request.user)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    else:
        messages.success(request, f"Successfully voided bill {bill.number}.")
    return redirect("bill_detail", pk=pk)


@login_required
def purchase_order_pdf(request, pk):
    order = get_object_or_404(
        PurchaseOrder.objects.select_related("vendor").prefetch_related("lines__product"),
        pk=pk,
    )
    company = Company.get()

    extra_meta = []
    if order.expected_date:
        extra_meta.append(("Expected Date", order.expected_date.strftime("%Y-%m-%d")))
    extra_meta.append(("Status", order.get_status_display()))

    company_data = {
        "name": company.name,
        "legal_name": company.legal_name,
        "email": company.email,
        "phone": company.phone,
        "address": company.address,
        "tax_id": company.tax_id,
    }

    partner_data = {
        "name": order.vendor.name,
        "email": order.vendor.email,
        "phone": order.vendor.phone,
        "address": order.vendor.address,
    }

    lines_data = []
    for line in order.lines.all():
        lines_data.append({
            "description": line.description,
            "qty": line.qty,
            "unit_price": f"${line.unit_cost:.2f}",
            "total": f"${line.line_total():.2f}",
        })

    totals_data = [
        ("Total", f"${order.total():.2f}"),
    ]

    pdf_bytes = generate_document_pdf(
        filename_prefix="po",
        title="Purchase Order",
        doc_number=order.number or f"DRAFT-{order.pk}",
        doc_date=order.date.strftime("%Y-%m-%d"),
        extra_meta=extra_meta,
        company=company_data,
        partner=partner_data,
        lines=lines_data,
        totals=totals_data,
        notes=order.notes,
    )

    response = FileResponse(io.BytesIO(pdf_bytes), content_type="application/pdf")
    filename = f"PO-{order.number or order.pk}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
