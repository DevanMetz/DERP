from datetime import date as date_cls
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from accounting.models import Account

from .forms import CustomerForm, InvoiceHeaderForm, InvoiceLineFormSet, ReceivePaymentForm
from .models import Customer, Invoice, InvoiceLine
from .services import default_due_date, post_invoice, receive_payment


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

            # Build lines. Fall back: product's revenue account, then customer's,
            # then code 4100. The form already validated qty/price.
            fallback_revenue = Account.objects.filter(code="4100").first()
            for line in formset.cleaned_data:
                product = line.get("product")
                desc = (line.get("description") or "").strip()
                qty = line.get("qty")
                price = line.get("unit_price")
                if not (product or desc) or not qty:
                    continue
                if not desc:
                    desc = f"{product.sku} {product.name}"
                revenue_account = (
                    line.get("revenue_account")
                    or (product.default_revenue_account if product else None)
                    or invoice.customer.default_revenue_account
                    or fallback_revenue
                )
                InvoiceLine.objects.create(
                    invoice=invoice, product=product, description=desc,
                    qty=qty, unit_price=price, revenue_account=revenue_account,
                )

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
        Invoice.objects.select_related("customer", "journal_entry").prefetch_related("lines__product", "lines__revenue_account"),
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
