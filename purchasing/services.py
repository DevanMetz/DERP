"""
Purchasing workflow services.

Posting a bill:
  DR Expense (per line, by expense_account) = line totals
  CR Accounts Payable                        = total

Paying a vendor:
  DR AP                                      = amount
  CR Cash (the payment's cash_account)       = amount
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.models import (
    Account, JournalEntry, Payment, PaymentApplication, ZERO,
)
from accounting.services import LineSpec, post_transaction
from core.numbering import next_document_number

from .models import Bill, BillLine

AP_ACCOUNT_CODE = "2110"


@transaction.atomic
def post_bill(bill: Bill, *, user=None) -> Bill:
    if bill.status != Bill.Status.DRAFT:
        raise ValidationError(f"Bill is {bill.get_status_display()}, only DRAFT can be posted.")

    lines = list(bill.lines.select_related("expense_account").all())
    if not lines:
        raise ValidationError("Bill has no lines.")

    total = sum((l.line_total() for l in lines), ZERO)
    if total <= 0:
        raise ValidationError("Bill total must be positive.")

    by_account: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for line in lines:
        by_account[line.expense_account.code] += line.line_total()

    specs: list[LineSpec] = []
    for code, amount in sorted(by_account.items()):
        specs.append(LineSpec(account_code=code, debit=amount, memo=f"Expense {code}"))
    specs.append(LineSpec(
        account_code=AP_ACCOUNT_CODE, credit=total,
        memo=f"Bill from {bill.vendor.name}",
    ))

    bill.number = next_document_number("BILL", year=bill.date.year)

    je = post_transaction(
        date=bill.date,
        memo=f"Bill {bill.number} — {bill.vendor.name}",
        lines=specs,
        user=user,
        source_doc_type="Bill",
        source_doc_id=bill.pk,
    )

    bill.journal_entry = je
    bill.status = Bill.Status.ENTERED
    bill.posted_at = timezone.now()
    bill.posted_by = user
    bill.save(update_fields=[
        "number", "journal_entry", "status", "posted_at", "posted_by",
    ])
    return bill


@transaction.atomic
def pay_vendor(
    *,
    vendor,
    date,
    amount: Decimal,
    cash_account: Account,
    method: str,
    reference: str = "",
    applications: list[tuple[Bill, Decimal]],
    notes: str = "",
    user=None,
) -> Payment:
    """
    Record a vendor payment and apply to bills.
    Posts: DR AP / CR cash_account.
    """
    if amount <= 0:
        raise ValidationError("Payment amount must be positive.")
    if not applications:
        raise ValidationError("Payment must be applied to at least one bill.")

    applied_total = sum((a for _, a in applications), ZERO)
    if applied_total != amount:
        raise ValidationError(
            f"Applications total {applied_total} != payment amount {amount}."
        )

    for bill, apply_amount in applications:
        if bill.vendor_id != vendor.id:
            raise ValidationError(f"Bill {bill} is not for vendor {vendor}.")
        if bill.status not in {Bill.Status.ENTERED, Bill.Status.PAID}:
            raise ValidationError(f"Bill {bill} is {bill.get_status_display()}, can't apply.")
        if apply_amount <= 0:
            raise ValidationError(f"Apply amount for {bill} must be positive.")
        if apply_amount > bill.amount_due():
            raise ValidationError(
                f"Apply amount {apply_amount} for {bill} exceeds amount due {bill.amount_due()}."
            )

    number = next_document_number("PAY", year=date.year)

    je = post_transaction(
        date=date,
        memo=f"{number} to {vendor.name}",
        lines=[
            LineSpec(account_code=AP_ACCOUNT_CODE, debit=amount, memo=f"Pay {vendor.name}"),
            LineSpec(account_code=cash_account.code, credit=amount, memo=f"Payment to {vendor.name}"),
        ],
        user=user,
        source_doc_type="Payment",
        source_doc_id=None,
    )

    payment = Payment.objects.create(
        number=number, date=date,
        direction=Payment.Direction.SENT,
        method=method, amount=amount, reference=reference,
        vendor=vendor, cash_account=cash_account,
        journal_entry=je, notes=notes, created_by=user,
    )

    PaymentApplication.objects.bulk_create([
        PaymentApplication(
            payment=payment, doc_type="Bill", doc_id=bill.pk, amount=apply_amount,
        )
        for bill, apply_amount in applications
    ])

    for bill, _ in applications:
        bill.refresh_from_db()
        if bill.amount_due() <= ZERO:
            bill.status = Bill.Status.PAID
            bill.save(update_fields=["status"])

    return payment
