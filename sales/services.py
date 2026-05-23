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

from .models import Invoice, InvoiceLine

# Account codes used by sales posting. If you re-code the chart of accounts,
# update these in one place.
AR_ACCOUNT_CODE = "1200"
SALES_TAX_PAYABLE_CODE = "2120"
FALLBACK_REVENUE_CODE = "4100"


@transaction.atomic
def post_invoice(invoice: Invoice, *, user=None) -> Invoice:
    """
    Post a DRAFT invoice: assign number, write the JE, flip to SENT.
    Idempotent guard: posting a non-DRAFT invoice raises.
    """
    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError(f"Invoice is {invoice.get_status_display()}, only DRAFT can be posted.")

    lines = list(invoice.lines.select_related("revenue_account").all())
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

    # Assign invoice number FIRST so the JE memo can reference it.
    invoice.number = next_document_number("INV", year=invoice.date.year)

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
