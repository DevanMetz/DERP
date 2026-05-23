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
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.models import (
    Account, JournalEntry, Payment, PaymentApplication, ZERO,
)
from accounting.services import LineSpec, post_transaction
from core.numbering import next_document_number
from inventory.models import ProductType, StockMovement
from inventory.services import post_stock_movement

from .models import (
    Bill, BillLine, GoodsReceipt, GoodsReceiptLine, PurchaseOrder,
    PurchaseOrderLine,
)

AP_ACCOUNT_CODE = "2110"
FALLBACK_EXPENSE_CODE = "6900"


def _po_has_bill(order: PurchaseOrder) -> bool:
    return order.bills.exists()


def _po_has_active_receipts(order: PurchaseOrder) -> bool:
    return order.goods_receipts.filter(is_reversed=False).exists()


def _set_purchase_order_receipt_status(order: PurchaseOrder) -> None:
    if _po_has_bill(order):
        order.status = PurchaseOrder.Status.BILLED
    else:
        lines = list(order.lines.all())
        if lines and all(line.open_qty() <= ZERO for line in lines):
            order.status = PurchaseOrder.Status.RECEIVED
        elif any(line.received_qty() > ZERO for line in lines):
            order.status = PurchaseOrder.Status.PARTIALLY_RECEIVED
        else:
            order.status = PurchaseOrder.Status.ISSUED
    order.save(update_fields=["status"])


def _line_description(*, product, description: str) -> str:
    desc = (description or "").strip()
    if desc:
        return desc
    return f"{product.sku} {product.name}"


def resolve_expense_account(*, product, vendor, explicit=None) -> Account:
    fallback = Account.objects.get(code=FALLBACK_EXPENSE_CODE)
    return (
        explicit
        or (product.default_expense_account if product else None)
        or vendor.default_expense_account
        or fallback
    )


def create_purchase_order_lines(order: PurchaseOrder, cleaned_lines: list[dict]) -> None:
    for line in cleaned_lines:
        product = line.get("product")
        desc = (line.get("description") or "").strip()
        qty = line.get("qty")
        cost = line.get("unit_cost")
        if not (product or desc) or not qty:
            continue
        PurchaseOrderLine.objects.create(
            order=order,
            product=product,
            description=_line_description(product=product, description=desc),
            qty=qty,
            unit_cost=cost,
            expense_account=resolve_expense_account(
                product=product,
                vendor=order.vendor,
                explicit=line.get("expense_account"),
            ),
        )


def create_bill_lines(bill: Bill, cleaned_lines: list[dict]) -> None:
    for line in cleaned_lines:
        product = line.get("product")
        desc = (line.get("description") or "").strip()
        qty = line.get("qty")
        cost = line.get("unit_cost")
        if not (product or desc) or not qty:
            continue
        BillLine.objects.create(
            bill=bill,
            product=product,
            description=_line_description(product=product, description=desc),
            qty=qty,
            unit_cost=cost,
            expense_account=resolve_expense_account(
                product=product,
                vendor=bill.vendor,
                explicit=line.get("expense_account"),
            ),
        )


@transaction.atomic
def issue_purchase_order(order: PurchaseOrder, *, user=None) -> PurchaseOrder:
    if order.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError(f"Purchase order is {order.get_status_display()}, only DRAFT can be issued.")
    if not order.lines.exists():
        raise ValidationError("Purchase order has no lines.")
    if order.total() <= ZERO:
        raise ValidationError("Purchase order total must be positive.")

    order.number = next_document_number("PO", year=order.date.year)
    order.status = PurchaseOrder.Status.ISSUED
    order.issued_at = timezone.now()
    order.issued_by = user
    order.save(update_fields=["number", "status", "issued_at", "issued_by"])
    return order


@transaction.atomic
def undo_issue_purchase_order(order: PurchaseOrder) -> PurchaseOrder:
    if order.status != PurchaseOrder.Status.ISSUED:
        raise ValidationError("Only issued purchase orders with no downstream activity can be moved back to draft.")
    if _po_has_active_receipts(order) or _po_has_bill(order):
        raise ValidationError("Cannot undo issue after receipts or bills exist.")
    order.status = PurchaseOrder.Status.DRAFT
    order.issued_at = None
    order.issued_by = None
    order.save(update_fields=["status", "issued_at", "issued_by"])
    return order


@transaction.atomic
def create_bill_from_purchase_order(order: PurchaseOrder, *, user=None) -> Bill:
    if order.status not in {
        PurchaseOrder.Status.ISSUED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
        PurchaseOrder.Status.RECEIVED,
    }:
        raise ValidationError("Only issued or received purchase orders can be billed.")
    if order.bills.exists():
        raise ValidationError("This purchase order already has a bill.")

    bill = Bill.objects.create(
        vendor=order.vendor,
        purchase_order=order,
        date=order.date,
        due_date=order.date + timedelta(days=order.vendor.payment_terms_days),
        notes=order.notes,
        created_by=user,
    )
    BillLine.objects.bulk_create([
        BillLine(
            bill=bill,
            product=line.product,
            description=line.description,
            qty=line.qty,
            unit_cost=line.unit_cost,
            expense_account=line.expense_account,
        )
        for line in order.lines.select_related("product", "expense_account")
    ])
    order.status = PurchaseOrder.Status.BILLED
    order.save(update_fields=["status"])
    return bill


@transaction.atomic
def create_bill_from_receipt(receipt: GoodsReceipt, *, user=None) -> Bill:
    if receipt.is_reversed:
        raise ValidationError("Cannot create a bill from a reversed goods receipt.")
    if receipt.bills.exists():
        raise ValidationError("This goods receipt already has a bill.")

    order = receipt.purchase_order
    bill = Bill.objects.create(
        vendor=order.vendor,
        purchase_order=order,
        goods_receipt=receipt,
        date=receipt.date,
        due_date=receipt.date + timedelta(days=order.vendor.payment_terms_days),
        notes=receipt.notes,
        created_by=user,
    )
    BillLine.objects.bulk_create([
        BillLine(
            bill=bill,
            product=line.product,
            description=line.po_line.description,
            qty=line.qty_received,
            unit_cost=line.unit_cost,
            expense_account=line.po_line.expense_account,
        )
        for line in receipt.lines.select_related("product", "po_line__expense_account")
    ])
    _set_purchase_order_receipt_status(order)
    return bill


@transaction.atomic
def undo_bill_from_purchase_order(order: PurchaseOrder) -> PurchaseOrder:
    bills = list(order.bills.all())
    if len(bills) != 1:
        raise ValidationError("Can only undo a purchase-order bill when exactly one bill exists.")
    bill = bills[0]
    if bill.status != Bill.Status.DRAFT:
        raise ValidationError("Only draft bills can be undone.")
    bill.delete()
    _set_purchase_order_receipt_status(order)
    return order


@transaction.atomic
def receive_purchase_order(
    *,
    order: PurchaseOrder,
    date,
    receipts: list[tuple[PurchaseOrderLine, Decimal]],
    notes: str = "",
    user=None,
) -> GoodsReceipt:
    if order.status not in {
        PurchaseOrder.Status.ISSUED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
        PurchaseOrder.Status.BILLED,
    }:
        raise ValidationError("Only issued purchase orders can be received.")
    if not receipts:
        raise ValidationError("Receipt must include at least one line.")

    # Lock the order lines while validating open quantities.
    locked_lines = {
        line.id: line
        for line in PurchaseOrderLine.objects.select_for_update()
        .filter(order=order)
    }

    number = next_document_number("GR", year=date.year)
    receipt = GoodsReceipt.objects.create(
        number=number,
        purchase_order=order,
        date=date,
        notes=notes,
        posted_by=user,
    )

    for original_line, qty in receipts:
        line = locked_lines.get(original_line.id)
        if line is None:
            raise ValidationError("Receipt line does not belong to this purchase order.")
        if qty <= 0:
            raise ValidationError("Received quantity must be positive.")
        if qty > line.open_qty():
            raise ValidationError(
                f"Received quantity for {line.description} exceeds open quantity."
            )
        if line.product is None or line.product.type != ProductType.STOCK:
            continue

        movement = post_stock_movement(
            product=line.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=qty,
            unit_cost=line.unit_cost,
            ref_doc_type="GoodsReceipt",
            ref_doc_id=receipt.pk,
            memo=f"Receipt {number} for {order.number}",
            user=user,
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt,
            po_line=line,
            product=line.product,
            qty_received=qty,
            unit_cost=line.unit_cost,
            stock_movement=movement,
        )

    if not receipt.lines.exists():
        raise ValidationError("Receipt did not include any stock items.")

    _set_purchase_order_receipt_status(order)
    return receipt


@transaction.atomic
def reverse_goods_receipt(receipt: GoodsReceipt, *, user=None) -> GoodsReceipt:
    if receipt.is_reversed:
        raise ValidationError("Goods receipt is already reversed.")

    for line in receipt.lines.select_related("product"):
        post_stock_movement(
            product=line.product,
            movement_type=StockMovement.MovementType.ISSUE,
            qty=line.qty_received,
            unit_cost=line.unit_cost,
            ref_doc_type="GoodsReceiptReversal",
            ref_doc_id=receipt.pk,
            memo=f"Reverse receipt {receipt.number}",
            user=user,
        )

    receipt.is_reversed = True
    receipt.reversed_at = timezone.now()
    receipt.reversed_by = user
    receipt.save(update_fields=["is_reversed", "reversed_at", "reversed_by"])
    _set_purchase_order_receipt_status(receipt.purchase_order)
    return receipt


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
def void_bill(bill: Bill, *, user=None) -> Bill:
    """
    Void a posted (ENTERED) vendor bill:
      1. Check status is ENTERED.
      2. Check amount_paid is zero.
      3. Call reverse_entry on its journal_entry.
      4. If this bill was created from a PurchaseOrder, reset the PO status.
      5. Set status to VOID.
    """
    if bill.status != Bill.Status.ENTERED:
        raise ValidationError(
            f"Bill is in status {bill.get_status_display()}, only ENTERED bills can be voided."
        )

    if bill.amount_paid() > ZERO:
        raise ValidationError(
            "Cannot void a bill that has payments applied. Please reverse the payments first."
        )

    if not bill.journal_entry:
        raise ValidationError("Bill has no associated journal entry.")

    from accounting.services import reverse_entry

    # Reversal of the journal entry
    reverse_entry(
        bill.journal_entry,
        date=timezone.now().date(),
        memo=f"Void Bill {bill.number}",
        user=user,
    )

    bill.status = Bill.Status.VOID
    bill.save(update_fields=["status"])

    if bill.purchase_order:
        _set_purchase_order_receipt_status(bill.purchase_order)

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
