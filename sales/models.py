"""
Sales models: Customer, SalesOrder, SalesOrderLine, Invoice, InvoiceLine.

Tax model: flat per-customer rate (spec §2.2). Each Customer carries a
single tax_rate percentage; invoices compute tax = subtotal * tax_rate / 100.
Per-line tax overrides can be added later without schema change by adding a
nullable override column to InvoiceLine.

Invoice lifecycle: DRAFT -> SENT -> (PAID | VOID).
- DRAFT: editable, no journal entry, no number assigned.
- SENT: posted to GL, document number issued (gap-free), immutable.
- PAID: fully applied by Payments. Computed, not user-set.
- VOID: only before SENT, OR after SENT via a reversing JE (Phase 1b).
"""

from decimal import Decimal
from django.db import models
from simple_history.models import HistoricalRecords

ZERO = Decimal("0.00")


class Customer(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    billing_address = models.TextField(blank=True)
    shipping_address = models.TextField(blank=True)

    payment_terms_days = models.PositiveSmallIntegerField(
        default=30, help_text="Net terms, e.g. 30 = Net 30.",
    )
    # Flat per-customer sales tax rate as a percentage (e.g. 8.25 for 8.25%).
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3, default=ZERO)

    default_revenue_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="default_revenue_for_customers",
        limit_choices_to={"type": "revenue", "is_postable": True},
    )

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SalesOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        INVOICED = "invoiced", "Invoiced"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="sales_orders")
    date = models.DateField()
    requested_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="created_sales_orders",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="confirmed_sales_orders",
    )
    history = HistoricalRecords()

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["status", "date"]),
            models.Index(fields=["customer", "status"]),
        ]

    def __str__(self):
        return self.number or f"SO-DRAFT-{self.pk}"

    def subtotal(self) -> Decimal:
        return sum((l.line_total() for l in self.lines.all()), ZERO)


class SalesOrderLine(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(
        "inventory.Product", null=True, blank=True, on_delete=models.PROTECT,
        related_name="sales_order_lines",
    )
    description = models.CharField(max_length=500)
    qty = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    revenue_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, related_name="sales_order_lines",
        limit_choices_to={"type": "revenue", "is_postable": True},
    )

    class Meta:
        constraints = [
            models.CheckConstraint(name="salesorderline_qty_positive", check=models.Q(qty__gt=0)),
            models.CheckConstraint(name="salesorderline_price_nonneg", check=models.Q(unit_price__gte=0)),
        ]

    def line_total(self) -> Decimal:
        return (self.qty * self.unit_price).quantize(Decimal("0.01"))


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")
    sales_order = models.ForeignKey(
        SalesOrder, null=True, blank=True, on_delete=models.PROTECT,
        related_name="invoices",
    )
    date = models.DateField()
    due_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    # Snapshot of the customer's tax_rate at posting time. Invoices are
    # historical documents — if the customer's rate changes later, posted
    # invoices keep the rate they were issued under.
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3, default=ZERO)

    notes = models.TextField(blank=True)

    # Set when the invoice is posted (status -> SENT).
    journal_entry = models.ForeignKey(
        "accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT,
        related_name="invoices",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="posted_invoices",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="created_invoices",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["status", "date"]),
            models.Index(fields=["customer", "status"]),
        ]

    def __str__(self):
        return self.number or f"DRAFT-{self.pk}"

    def subtotal(self) -> Decimal:
        return sum((l.line_total() for l in self.lines.all()), ZERO)

    def tax_total(self) -> Decimal:
        return (self.subtotal() * (self.tax_rate / Decimal("100"))).quantize(Decimal("0.01"))

    def total(self) -> Decimal:
        return self.subtotal() + self.tax_total()

    def amount_paid(self) -> Decimal:
        from accounting.models import PaymentApplication
        agg = PaymentApplication.objects.filter(
            doc_type="Invoice", doc_id=self.pk,
        ).aggregate(s=models.Sum("amount"))
        return agg["s"] or ZERO

    def amount_due(self) -> Decimal:
        return self.total() - self.amount_paid()


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(
        "inventory.Product", null=True, blank=True, on_delete=models.PROTECT,
        related_name="invoice_lines",
    )
    description = models.CharField(max_length=500)
    qty = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    # Snapshot of the revenue account at posting time. Defaults pulled from
    # product or customer at line creation but stored here so the historical
    # entry is unaffected by master-data edits.
    revenue_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, related_name="invoice_lines",
        limit_choices_to={"type": "revenue", "is_postable": True},
    )

    class Meta:
        constraints = [
            models.CheckConstraint(name="invoiceline_qty_positive", check=models.Q(qty__gt=0)),
            models.CheckConstraint(name="invoiceline_price_nonneg", check=models.Q(unit_price__gte=0)),
        ]

    def line_total(self) -> Decimal:
        return (self.qty * self.unit_price).quantize(Decimal("0.01"))
