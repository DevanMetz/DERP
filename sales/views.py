from datetime import date as date_cls
from decimal import Decimal
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.http import FileResponse

from core.models import Company
from core.pdf_service import generate_document_pdf

from .forms import (
    CustomerForm, InvoiceHeaderForm, InvoiceLineFormSet, ReceivePaymentForm,
    SalesOrderHeaderForm, SalesOrderLineFormSet,
)
from .models import Customer, Invoice, SalesOrder
from .services import (
    confirm_sales_order, create_invoice_from_sales_order, create_invoice_lines,
    create_sales_order_lines, post_invoice, receive_payment,
    undo_confirm_sales_order, undo_invoice_from_sales_order,
)


# ----------------------------- Customers -----------------------------

@login_required
def customer_list(request):
    customers = Customer.objects.all()
    return render(request, "sales/customer_list.html", {"customers": customers})


@login_required
def customer_edit(request, pk=None):
    customer = get_object_or_404(Customer, pk=pk) if pk else None
    if request.method == "POST":
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f"Saved {obj.name}.")
            return redirect("customer_list")
    else:
        form = CustomerForm(instance=customer)
    return render(request, "sales/customer_form.html", {"form": form, "customer": customer})


# --------------------------- Sales orders ----------------------------

@login_required
def sales_order_list(request):
    orders = SalesOrder.objects.select_related("customer").all()
    return render(request, "sales/sales_order_list.html", {"orders": orders})


@login_required
def sales_order_create(request):
    if request.method == "POST":
        header = SalesOrderHeaderForm(request.POST)
        formset = SalesOrderLineFormSet(request.POST)
        if header.is_valid() and formset.is_valid():
            order = header.save(commit=False)
            order.created_by = request.user
            order.save()
            create_sales_order_lines(order, formset.cleaned_data)

            if "confirm_now" in request.POST:
                try:
                    confirm_sales_order(order, user=request.user)
                except ValidationError as e:
                    messages.error(request, "; ".join(e.messages))
                    return redirect("sales_order_detail", pk=order.pk)
                messages.success(request, f"Confirmed {order.number}.")
            else:
                messages.success(request, "Draft sales order saved.")
            return redirect("sales_order_detail", pk=order.pk)
    else:
        header = SalesOrderHeaderForm(initial={"date": date_cls.today()})
        formset = SalesOrderLineFormSet()
    return render(request, "sales/sales_order_form.html", {"header": header, "formset": formset})


@login_required
def sales_order_detail(request, pk):
    order = get_object_or_404(
        SalesOrder.objects.select_related("customer").prefetch_related(
            "lines__product", "lines__revenue_account", "invoices",
        ),
        pk=pk,
    )
    return render(request, "sales/sales_order_detail.html", {"order": order})


@login_required
def sales_order_confirm(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    if request.method != "POST":
        return redirect("sales_order_detail", pk=pk)
    try:
        confirm_sales_order(order, user=request.user)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    else:
        messages.success(request, f"Confirmed {order.number}.")
    return redirect("sales_order_detail", pk=pk)


@login_required
def sales_order_unconfirm(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    if request.method != "POST":
        return redirect("sales_order_detail", pk=pk)
    try:
        undo_confirm_sales_order(order)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    else:
        messages.success(request, "Sales order moved back to draft.")
    return redirect("sales_order_detail", pk=pk)


@login_required
def sales_order_invoice(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    if request.method != "POST":
        return redirect("sales_order_detail", pk=pk)
    try:
        invoice = create_invoice_from_sales_order(order, user=request.user)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
        return redirect("sales_order_detail", pk=pk)
    messages.success(request, f"Created draft invoice from {order.number}.")
    return redirect("invoice_detail", pk=invoice.pk)


@login_required
def sales_order_undo_invoice(request, pk):
    order = get_object_or_404(SalesOrder, pk=pk)
    if request.method != "POST":
        return redirect("sales_order_detail", pk=pk)
    try:
        undo_invoice_from_sales_order(order)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    else:
        messages.success(request, "Draft invoice removed and sales order reopened.")
    return redirect("sales_order_detail", pk=pk)


# ----------------------------- Invoices ------------------------------

@login_required
def invoice_list(request):
    invoices = Invoice.objects.select_related("customer").all()
    return render(request, "sales/invoice_list.html", {"invoices": invoices})


@login_required
def invoice_create(request):
    """
    Draft creation. The header form + line formset post together.
    Use the 'post_now' submit value to immediately post to GL; otherwise save as DRAFT.
    """
    if request.method == "POST":
        header = InvoiceHeaderForm(request.POST)
        formset = InvoiceLineFormSet(request.POST)
        if header.is_valid() and formset.is_valid():
            invoice = header.save(commit=False)
            invoice.created_by = request.user
            # Snapshot the customer's tax rate on the draft.
            invoice.tax_rate = invoice.customer.tax_rate
            invoice.save()

            create_invoice_lines(invoice, formset.cleaned_data)

            if "post_now" in request.POST:
                try:
                    post_invoice(invoice, user=request.user)
                except ValidationError as e:
                    messages.error(request, "; ".join(e.messages))
                    return redirect("invoice_detail", pk=invoice.pk)
                messages.success(request, f"Posted {invoice.number}.")
            else:
                messages.success(request, "Draft saved.")
            return redirect("invoice_detail", pk=invoice.pk)
    else:
        header = InvoiceHeaderForm(initial={
            "date": date_cls.today(),
            "due_date": date_cls.today(),
        })
        formset = InvoiceLineFormSet()
    return render(request, "sales/invoice_form.html", {"header": header, "formset": formset})


@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.select_related("customer", "journal_entry", "sales_order").prefetch_related("lines__product", "lines__revenue_account"),
        pk=pk,
    )
    return render(request, "sales/invoice_detail.html", {"invoice": invoice})


@login_required
def payment_create(request):
    """
    Receive a customer payment. Workflow:
      1. Pick a customer.
      2. Page reloads (GET ?customer=<id>) showing that customer's open invoices
         and an "apply" amount field next to each.
      3. POST submits header + per-invoice apply amounts.
    """
    customer = None
    open_invoices = []
    customer_id = request.GET.get("customer") or request.POST.get("customer")
    if customer_id:
        try:
            customer = Customer.objects.get(pk=customer_id, is_active=True)
            open_invoices = [
                inv for inv in Invoice.objects.filter(
                    customer=customer,
                    status__in=[Invoice.Status.SENT, Invoice.Status.PAID],
                ).order_by("date", "id")
                if inv.amount_due() > Decimal("0")
            ]
        except Customer.DoesNotExist:
            pass

    if request.method == "POST":
        header = ReceivePaymentForm(request.POST)
        if header.is_valid():
            applications: list[tuple[Invoice, Decimal]] = []
            for inv in open_invoices:
                raw = request.POST.get(f"apply_{inv.pk}", "").strip()
                if not raw:
                    continue
                try:
                    amt = Decimal(raw)
                except Exception:
                    messages.error(request, f"Invalid apply amount for invoice {inv.number}.")
                    return redirect(request.get_full_path())
                if amt > 0:
                    applications.append((inv, amt))
            try:
                payment = receive_payment(
                    customer=header.cleaned_data["customer"],
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
                return redirect("invoice_list")
    else:
        header = ReceivePaymentForm(initial={
            "date": date_cls.today(),
            "customer": customer.pk if customer else None,
        })

    return render(request, "sales/payment_form.html", {
        "header": header, "customer": customer, "open_invoices": open_invoices,
    })


@login_required
def invoice_post(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method != "POST":
        return redirect("invoice_detail", pk=pk)
    try:
        post_invoice(invoice, user=request.user)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    else:
        messages.success(request, f"Posted {invoice.number}.")
    return redirect("invoice_detail", pk=pk)


@login_required
def invoice_void(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method != "POST":
        return redirect("invoice_detail", pk=pk)
    if not request.user.can_void:
        messages.error(request, "Only Administrators can void invoices.")
        return redirect("invoice_detail", pk=pk)
    from .services import void_invoice
    try:
        void_invoice(invoice, user=request.user)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    else:
        messages.success(request, f"Successfully voided invoice {invoice.number}.")
    return redirect("invoice_detail", pk=pk)


@login_required
def sales_order_pdf(request, pk):
    order = get_object_or_404(
        SalesOrder.objects.select_related("customer").prefetch_related("lines__product"),
        pk=pk,
    )
    company = Company.get()
    
    extra_meta = []
    if order.requested_date:
        extra_meta.append(("Requested Date", order.requested_date.strftime("%Y-%m-%d")))
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
        "name": order.customer.name,
        "email": order.customer.email,
        "phone": order.customer.phone,
        "address": order.customer.billing_address,
    }

    lines_data = []
    for line in order.lines.all():
        lines_data.append({
            "description": line.description,
            "qty": line.qty,
            "unit_price": f"${line.unit_price:.2f}",
            "total": f"${line.line_total():.2f}",
        })

    totals_data = [
        ("Total", f"${order.subtotal():.2f}"),
    ]

    pdf_bytes = generate_document_pdf(
        filename_prefix="so",
        title="Sales Order",
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
    filename = f"SO-{order.number or order.pk}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def invoice_pdf(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.select_related("customer", "sales_order").prefetch_related("lines__product"),
        pk=pk,
    )
    company = Company.get()
    
    extra_meta = [
        ("Due Date", invoice.due_date.strftime("%Y-%m-%d")),
        ("Status", invoice.get_status_display()),
    ]
    if invoice.sales_order:
        extra_meta.append(("Sales Order", invoice.sales_order.number or str(invoice.sales_order.pk)))

    company_data = {
        "name": company.name,
        "legal_name": company.legal_name,
        "email": company.email,
        "phone": company.phone,
        "address": company.address,
        "tax_id": company.tax_id,
    }

    partner_data = {
        "name": invoice.customer.name,
        "email": invoice.customer.email,
        "phone": invoice.customer.phone,
        "address": invoice.customer.billing_address,
    }

    lines_data = []
    for line in invoice.lines.all():
        lines_data.append({
            "description": line.description,
            "qty": line.qty,
            "unit_price": f"${line.unit_price:.2f}",
            "total": f"${line.line_total():.2f}",
        })

    totals_data = [
        ("Subtotal", f"${invoice.subtotal():.2f}"),
        (f"Sales Tax ({invoice.tax_rate}%)", f"${invoice.tax_total():.2f}"),
        ("Total", f"${invoice.total():.2f}"),
        ("Amount Paid", f"${invoice.amount_paid():.2f}"),
        ("Amount Due", f"${invoice.amount_due():.2f}"),
    ]

    pdf_bytes = generate_document_pdf(
        filename_prefix="inv",
        title="Invoice",
        doc_number=invoice.number or f"DRAFT-{invoice.pk}",
        doc_date=invoice.date.strftime("%Y-%m-%d"),
        extra_meta=extra_meta,
        company=company_data,
        partner=partner_data,
        lines=lines_data,
        totals=totals_data,
        notes=invoice.notes,
    )

    response = FileResponse(io.BytesIO(pdf_bytes), content_type="application/pdf")
    filename = f"INV-{invoice.number or invoice.pk}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
