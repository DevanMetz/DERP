"""
Purchasing: Vendor, PurchaseOrder, PurchaseOrderLine, Bill, BillLine.

Mirror of sales on the AP side. Lifecycle: DRAFT -> ENTERED -> PAID | VOID.
Posting a bill: DR expense (per line) / CR AP.

Tax handling on bills is intentionally bare-bones in MVP. US small-business
vendors typically charge sales tax that you expense to the underlying
category. If a bill has a separate tax line item, add it as its own line
to whatever account (e.g. 6900 Misc). Header-level use-tax handling lands
later if needed.
"""

from decimal import Decimal
from django.db import models
from simple_history.models import HistoricalRecords

ZERO = Decimal("0.00")


class Vendor(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)

    payment_terms_days = models.PositiveSmallIntegerField(default=30)
    default_expense_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="default_expense_for_vendors",
        limit_choices_to={"type": "expense", "is_postable": True},
    )

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PARTIALLY_RECEIVED = "partially_received", "Partially received"
        RECEIVED = "received", "Received"
        BILLED = "billed", "Billed"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="purchase_orders")
    date = models.DateField()
    expected_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="created_purchase_orders",
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="issued_purchase_orders",
    )
    history = HistoricalRecords()

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["status", "date"]),
            models.Index(fields=["vendor", "status"]),
        ]

    def __str__(self):
        return self.number or f"PO-DRAFT-{self.pk}"

    def total(self) -> Decimal:
        return sum((l.line_total() for l in self.lines.all()), ZERO)


class PurchaseOrderLine(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(
        "inventory.Product", null=True, blank=True, on_delete=models.PROTECT,
        related_name="purchase_order_lines",
    )
    description = models.CharField(max_length=500)
    qty = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("1"))
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    expense_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, related_name="purchase_order_lines",
        limit_choices_to={"type__in": ["expense", "asset"], "is_postable": True},
    )

    class Meta:
        constraints = [
            models.CheckConstraint(name="purchaseorderline_qty_positive", check=models.Q(qty__gt=0)),
            models.CheckConstraint(name="purchaseorderline_cost_nonneg", check=models.Q(unit_cost__gte=0)),
        ]

    def line_total(self) -> Decimal:
        return (self.qty * self.unit_cost).quantize(Decimal("0.01"))

    def received_qty(self) -> Decimal:
        from django.db.models import Sum
        agg = GoodsReceiptLine.objects.filter(
            po_line=self,
            receipt__is_reversed=False,
        ).aggregate(s=Sum("qty_received"))
        return agg["s"] or ZERO

    def open_qty(self) -> Decimal:
        return self.qty - self.received_qty()


class GoodsReceipt(models.Model):
    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="goods_receipts")
    date = models.DateField()
    notes = models.TextField(blank=True)
    posted_at = models.DateTimeField(auto_now_add=True)
    posted_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="posted_goods_receipts",
    )
    is_reversed = models.BooleanField(default=False)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="reversed_goods_receipts",
    )
    history = HistoricalRecords()

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["purchase_order", "date"]),
        ]

    def __str__(self):
        return self.number or f"GR-DRAFT-{self.pk}"


class GoodsReceiptLine(models.Model):
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    po_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipt_lines")
    product = models.ForeignKey(
        "inventory.Product", on_delete=models.PROTECT, related_name="goods_receipt_lines",
    )
    qty_received = models.DecimalField(max_digits=14, decimal_places=4)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    stock_movement = models.ForeignKey(
        "inventory.StockMovement", null=True, blank=True, on_delete=models.PROTECT,
        related_name="goods_receipt_lines",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(name="goodsreceiptline_qty_positive", check=models.Q(qty_received__gt=0)),
            models.CheckConstraint(name="goodsreceiptline_cost_nonneg", check=models.Q(unit_cost__gte=0)),
        ]

    def line_total(self) -> Decimal:
        return (self.qty_received * self.unit_cost).quantize(Decimal("0.01"))


class Bill(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ENTERED = "entered", "Entered"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    # Vendor's reference number (their invoice #), distinct from our internal number.
    vendor_ref = models.CharField(max_length=64, blank=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="bills")
    purchase_order = models.ForeignKey(
        PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT,
        related_name="bills",
    )
    goods_receipt = models.ForeignKey(
        "GoodsReceipt", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="bills",
        help_text="The Goods Receipt this bill was drafted from.",
    )
    date = models.DateField()
    due_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    journal_entry = models.ForeignKey(
        "accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
        related_name="bills",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="posted_bills",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="created_bills",
    )
    history = HistoricalRecords()

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["status", "date"]),
            models.Index(fields=["vendor", "status"]),
        ]

    def __str__(self):
        return self.number or f"DRAFT-{self.pk}"

    def total(self) -> Decimal:
        return sum((l.line_total() for l in self.lines.all()), ZERO)

    def amount_paid(self) -> Decimal:
        from accounting.models import PaymentApplication
        agg = PaymentApplication.objects.filter(
            doc_type="Bill", doc_id=self.pk,
        ).aggregate(s=models.Sum("amount"))
        return agg["s"] or ZERO

    def amount_due(self) -> Decimal:
        return self.total() - self.amount_paid()


class BillLine(models.Model):
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(
        "inventory.Product", null=True, blank=True, on_delete=models.PROTECT,
        related_name="bill_lines",
    )
    description = models.CharField(max_length=500)
    qty = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("1"))
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    expense_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, related_name="bill_lines",
        limit_choices_to={"type__in": ["expense", "asset"], "is_postable": True},
    )

    class Meta:
        constraints = [
            models.CheckConstraint(name="billline_qty_positive", check=models.Q(qty__gt=0)),
            models.CheckConstraint(name="billline_cost_nonneg", check=models.Q(unit_cost__gte=0)),
        ]

    def line_total(self) -> Decimal:
        return (self.qty * self.unit_cost).quantize(Decimal("0.01"))
