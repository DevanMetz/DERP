"""
Sales workflow services.

Posting an invoice flips it from DRAFT to SENT and writes the journal entry:
  DR Accounts Receivable                      = total (subtotal + tax)
  CR Revenue (per line, by revenue_account)   = line totals
  CR Sales Tax Payable                        = tax_total (if > 0)

Applying a payment to an invoice writes:
  DR Cash (the payment's cash_account)        = amount
  CR Accounts Receivable                      = amount

Account codes are looked up by code string (not FK) so changes to the chart
of accounts don't break this code path. The codes themselves are settings:
"""

from collections import defaultdict
from decimal import Decimal
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.models import (
    Account, JournalEntry, Payment, PaymentApplication, ZERO,
)
from accounting.services import LineSpec, post_transaction
from core.numbering import next_document_number

from .models import Invoice, InvoiceLine, SalesOrder, SalesOrderLine

# Account codes used by sales posting. If you re-code the chart of accounts,
# update these in one place.
AR_ACCOUNT_CODE = "1200"
SALES_TAX_PAYABLE_CODE = "2120"
FALLBACK_REVENUE_CODE = "4100"


def _line_description(*, product, description: str) -> str:
    desc = (description or "").strip()
    if desc:
        return desc
    return f"{product.sku} {product.name}"


def resolve_revenue_account(*, product, customer, explicit=None) -> Account:
    fallback = Account.objects.get(code=FALLBACK_REVENUE_CODE)
    return (
        explicit
        or (product.default_revenue_account if product else None)
        or customer.default_revenue_account
        or fallback
    )


def create_sales_order_lines(order: SalesOrder, cleaned_lines: list[dict]) -> None:
    for line in cleaned_lines:
        product = line.get("product")
        desc = (line.get("description") or "").strip()
        qty = line.get("qty")
        price = line.get("unit_price")
        if not (product or desc) or not qty:
            continue
        SalesOrderLine.objects.create(
            order=order,
            product=product,
            description=_line_description(product=product, description=desc),
            qty=qty,
            unit_price=price,
            revenue_account=resolve_revenue_account(
                product=product,
                customer=order.customer,
                explicit=line.get("revenue_account"),
            ),
        )


def create_invoice_lines(invoice: Invoice, cleaned_lines: list[dict]) -> None:
    for line in cleaned_lines:
        product = line.get("product")
        desc = (line.get("description") or "").strip()
        qty = line.get("qty")
        price = line.get("unit_price")
        if not (product or desc) or not qty:
            continue
        InvoiceLine.objects.create(
            invoice=invoice,
            product=product,
            description=_line_description(product=product, description=desc),
            qty=qty,
            unit_price=price,
            revenue_account=resolve_revenue_account(
                product=product,
                customer=invoice.customer,
                explicit=line.get("revenue_account"),
            ),
            location=line.get("location"),
        )



@transaction.atomic
def confirm_sales_order(order: SalesOrder, *, user=None) -> SalesOrder:
    if order.status != SalesOrder.Status.DRAFT:
        raise ValidationError(f"Sales order is {order.get_status_display()}, only DRAFT can be confirmed.")
    if not order.lines.exists():
        raise ValidationError("Sales order has no lines.")
    if order.subtotal() <= ZERO:
        raise ValidationError("Sales order total must be positive.")

    order.number = next_document_number("SO", year=order.date.year)
    order.status = SalesOrder.Status.CONFIRMED
    order.confirmed_at = timezone.now()
    order.confirmed_by = user
    order.save(update_fields=["number", "status", "confirmed_at", "confirmed_by"])

    # Automate draft Invoice and Stock Shipment immediately upon confirming
    invoice = create_invoice_from_sales_order(order, user=user)

    from inventory.models import ProductType
    from inventory.services import post_stock_movement

    for line in invoice.lines.select_related("product").all():
        if line.product and line.product.type == ProductType.STOCK:
            post_stock_movement(
                product=line.product,
                movement_type="issue",
                qty=line.qty,
                unit_cost=line.product.cost,
                location=line.location,
                ref_doc_type="Invoice",
                ref_doc_id=invoice.pk,
                memo=f"Auto stock shipment for SO {order.number} via Invoice {invoice.number or 'DRAFT'}",
                user=user,
            )

    return order


@transaction.atomic
def undo_confirm_sales_order(order: SalesOrder) -> SalesOrder:
    if order.status != SalesOrder.Status.CONFIRMED:
        raise ValidationError("Only confirmed sales orders with no invoice can be moved back to draft.")
    if order.invoices.exists():
        raise ValidationError("Cannot undo confirmation after an invoice exists.")
    order.status = SalesOrder.Status.DRAFT
    order.confirmed_at = None
    order.confirmed_by = None
    order.save(update_fields=["status", "confirmed_at", "confirmed_by"])
    return order


@transaction.atomic
def create_invoice_from_sales_order(order: SalesOrder, *, user=None) -> Invoice:
    if order.status != SalesOrder.Status.CONFIRMED:
        raise ValidationError("Only confirmed sales orders can be invoiced.")
    if order.invoices.exists():
        raise ValidationError("This sales order already has an invoice.")

    invoice = Invoice.objects.create(
        customer=order.customer,
        sales_order=order,
        date=order.date,
        due_date=default_due_date(order.date, order.customer),
        tax_rate=order.customer.tax_rate,
        notes=order.notes,
        created_by=user,
    )
    InvoiceLine.objects.bulk_create([
        InvoiceLine(
            invoice=invoice,
            product=line.product,
            description=line.description,
            qty=line.qty,
            unit_price=line.unit_price,
            revenue_account=line.revenue_account,
        )
        for line in order.lines.select_related("product", "revenue_account")
    ])
    order.status = SalesOrder.Status.INVOICED
    order.save(update_fields=["status"])
    return invoice


@transaction.atomic
def undo_invoice_from_sales_order(order: SalesOrder) -> SalesOrder:
    invoices = list(order.invoices.all())
    if len(invoices) != 1:
        raise ValidationError("Can only undo a sales-order invoice when exactly one invoice exists.")
    invoice = invoices[0]
    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError("Only draft invoices can be undone.")

    # Return any stock issued for this draft invoice to inventory
    from inventory.models import StockMovement
    from inventory.services import post_stock_movement

    movements = StockMovement.objects.filter(
        ref_doc_type="Invoice",
        ref_doc_id=invoice.pk,
        movement_type=StockMovement.MovementType.ISSUE,
    )
    for move in movements:
        post_stock_movement(
            product=move.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=move.qty,
            unit_cost=move.unit_cost,
            location=move.location,
            ref_doc_type="InvoiceUndo",
            ref_doc_id=invoice.pk,
            memo=f"Stock return due to deleted draft invoice from SO {order.number}",
        )


    invoice.delete()
    order.status = SalesOrder.Status.CONFIRMED
    order.save(update_fields=["status"])
    return order


@transaction.atomic
def post_invoice(invoice: Invoice, *, user=None) -> Invoice:
    """
    Post a DRAFT invoice: assign number, write the JE, flip to SENT.
    Idempotent guard: posting a non-DRAFT invoice raises.
    """
    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError(f"Invoice is {invoice.get_status_display()}, only DRAFT can be posted.")

    lines = list(invoice.lines.select_related("revenue_account", "product").all())
    if not lines:
        raise ValidationError("Invoice has no lines.")

    # Snapshot the tax rate from the customer at post time. The form may have
    # already done this, but enforce it here so direct API callers can't skip it.
    invoice.tax_rate = invoice.customer.tax_rate
    subtotal = sum((l.line_total() for l in lines), ZERO)
    tax = (subtotal * (invoice.tax_rate / Decimal("100"))).quantize(Decimal("0.01"))
    total = subtotal + tax

    if total <= 0:
        raise ValidationError("Invoice total must be positive.")

    # Assign invoice number FIRST so the JE memo and stock movements can reference it.
    invoice.number = next_document_number("INV", year=invoice.date.year)

    # Perform stock issues and aggregate COGS
    from inventory.models import ProductType, StockMovement
    from inventory.services import post_stock_movement

    COGS_ACCOUNT_CODE = "5100"
    INVENTORY_ACCOUNT_CODE = "1300"
    cogs_total = ZERO

    stock_already_shipped = StockMovement.objects.filter(
        ref_doc_type="Invoice",
        ref_doc_id=invoice.pk,
        movement_type=StockMovement.MovementType.ISSUE,
    ).exists()

    for line in lines:
        if line.product and line.product.type == ProductType.STOCK:
            cost_amount = (line.qty * line.product.cost).quantize(Decimal("0.01"))
            cogs_total += cost_amount

            if not stock_already_shipped:
                post_stock_movement(
                    product=line.product,
                    movement_type="issue",
                    qty=line.qty,
                    unit_cost=line.product.cost,
                    location=line.location,
                    ref_doc_type="Invoice",
                    ref_doc_id=invoice.pk,
                    memo=f"Invoice {invoice.number} issue",
                    user=user,
                )


    # Aggregate by revenue account so we emit one credit line per account.
    by_account: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for line in lines:
        by_account[line.revenue_account.code] += line.line_total()

    specs: list[LineSpec] = [
        LineSpec(account_code=AR_ACCOUNT_CODE, debit=total, memo=f"Invoice for {invoice.customer.name}"),
    ]
    for code, amount in sorted(by_account.items()):
        specs.append(LineSpec(account_code=code, credit=amount, memo=f"Revenue {code}"))
    if tax > 0:
        specs.append(LineSpec(
            account_code=SALES_TAX_PAYABLE_CODE, credit=tax,
            memo=f"Sales tax {invoice.tax_rate}%",
        ))

    if cogs_total > ZERO:
        specs.append(LineSpec(
            account_code=COGS_ACCOUNT_CODE, debit=cogs_total,
            memo=f"COGS for Invoice {invoice.number}",
        ))
        specs.append(LineSpec(
            account_code=INVENTORY_ACCOUNT_CODE, credit=cogs_total,
            memo=f"Inventory reduction",
        ))

    je = post_transaction(
        date=invoice.date,
        memo=f"Invoice {invoice.number} — {invoice.customer.name}",
        lines=specs,
        user=user,
        source_doc_type="Invoice",
        source_doc_id=invoice.pk,
    )

    invoice.journal_entry = je
    invoice.status = Invoice.Status.SENT
    invoice.posted_at = timezone.now()
    invoice.posted_by = user
    invoice.save(update_fields=[
        "number", "tax_rate", "journal_entry", "status", "posted_at", "posted_by",
    ])
    return invoice


@transaction.atomic
def void_invoice(invoice: Invoice, *, user=None) -> Invoice:
    """
    Void a posted (SENT) invoice:
      1. Check status is SENT.
      2. Check amount_paid is zero.
      3. Call reverse_entry on its journal_entry.
      4. Release/return any stock issued by this invoice.
      5. Set status to VOID.
    """
    if invoice.status != Invoice.Status.SENT:
        raise ValidationError(
            f"Invoice is in status {invoice.get_status_display()}, only SENT invoices can be voided."
        )

    if invoice.amount_paid() > ZERO:
        raise ValidationError(
            "Cannot void an invoice that has payments applied. Please reverse the payments first."
        )

    if not invoice.journal_entry:
        raise ValidationError("Invoice has no associated journal entry.")

    from accounting.services import reverse_entry

    # Reversal of the journal entry
    reverse_entry(
        invoice.journal_entry,
        date=timezone.now().date(),
        memo=f"Void Invoice {invoice.number}",
        user=user,
    )

    # Return any stock issued by this invoice to inventory
    from inventory.models import StockMovement
    from inventory.services import post_stock_movement

    movements = StockMovement.objects.filter(ref_doc_type="Invoice", ref_doc_id=invoice.pk)
    for move in movements:
        if move.movement_type == StockMovement.MovementType.ISSUE:
            post_stock_movement(
                product=move.product,
                movement_type=StockMovement.MovementType.RECEIPT,
                qty=move.qty,
                unit_cost=move.unit_cost,
                location=move.location,
                ref_doc_type="InvoiceVoid",
                ref_doc_id=invoice.pk,
                memo=f"Stock return due to voided invoice {invoice.number}",
                user=user,
            )


    invoice.status = Invoice.Status.VOID
    invoice.save(update_fields=["status"])
    return invoice


@transaction.atomic
def receive_payment(
    *,
    customer,
    date,
    amount: Decimal,
    cash_account: Account,
    method: str,
    reference: str = "",
    applications: list[tuple[Invoice, Decimal]],
    notes: str = "",
    user=None,
) -> Payment:
    """
    Record a customer payment and apply it to one or more invoices.
    `applications` is a list of (invoice, amount_to_apply) — sums must equal `amount`.

    Posts:
      DR cash_account = amount
      CR AR           = amount
    """
    if amount <= 0:
        raise ValidationError("Payment amount must be positive.")
    if not applications:
        raise ValidationError("Payment must be applied to at least one invoice.")

    applied_total = sum((a for _, a in applications), ZERO)
    if applied_total != amount:
        raise ValidationError(
            f"Applications total {applied_total} != payment amount {amount}."
        )

    # Validate each application: invoice belongs to the customer, is SENT,
    # and the apply amount doesn't exceed amount_due.
    for inv, apply_amount in applications:
        if inv.customer_id != customer.id:
            raise ValidationError(f"Invoice {inv} is not for customer {customer}.")
        if inv.status not in {Invoice.Status.SENT, Invoice.Status.PAID}:
            raise ValidationError(f"Invoice {inv} is {inv.get_status_display()}, can't apply.")
        if apply_amount <= 0:
            raise ValidationError(f"Apply amount for {inv} must be positive.")
        if apply_amount > inv.amount_due():
            raise ValidationError(
                f"Apply amount {apply_amount} for {inv} exceeds amount due {inv.amount_due()}."
            )

    number = next_document_number("PAY", year=date.year)

    je = post_transaction(
        date=date,
        memo=f"{number} from {customer.name}",
        lines=[
            LineSpec(account_code=cash_account.code, debit=amount, memo=f"Receipt from {customer.name}"),
            LineSpec(account_code=AR_ACCOUNT_CODE, credit=amount, memo=f"Apply to AR"),
        ],
        user=user,
        source_doc_type="Payment",
        source_doc_id=None,  # set after creation
    )

    payment = Payment.objects.create(
        number=number, date=date,
        direction=Payment.Direction.RECEIVED,
        method=method, amount=amount, reference=reference,
        customer=customer, cash_account=cash_account,
        journal_entry=je, notes=notes, created_by=user,
    )

    PaymentApplication.objects.bulk_create([
        PaymentApplication(
            payment=payment, doc_type="Invoice", doc_id=inv.pk, amount=apply_amount,
        )
        for inv, apply_amount in applications
    ])

    # Mark invoices PAID if fully covered.
    for inv, _ in applications:
        inv.refresh_from_db()
        if inv.amount_due() <= ZERO:
            inv.status = Invoice.Status.PAID
            inv.save(update_fields=["status"])

    return payment


def default_due_date(invoice_date, customer):
    return invoice_date + timedelta(days=customer.payment_terms_days)
