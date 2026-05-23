"""
Inventory models.

Phase 1 ships Product only — the bare master-data needed for sales and
purchasing line items. Stock tracking (StockMovement, on-hand) lands in
Phase 2 along with COGS posting on shipment.

The schema reserves columns for things we're not building yet (cost,
default accounts) so adding them later doesn't require backfill churn.
"""

from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from simple_history.models import HistoricalRecords


def _validate_image_size(file):
    if file.size > 5 * 1024 * 1024:
        raise ValidationError("Image must be 5 MB or smaller.")


class ProductType(models.TextChoices):
    STOCK = "stock", "Stock item"
    SERVICE = "service", "Service"
    # KIT (BOM-driven) lands with manufacturing in Phase 3.


class Product(models.Model):
    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    type = models.CharField(max_length=20, choices=ProductType.choices, default=ProductType.STOCK)
    uom = models.CharField(max_length=20, default="ea", help_text="Unit of measure: ea, kg, hr, etc.")
    image = models.ImageField(
        upload_to="products/",
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "gif", "webp"]),
            _validate_image_size,
        ],
        help_text="Product thumbnail or icon (JPG/PNG/GIF/WebP, max 5 MB).",
    )

    # Pricing. Cost is what we paid (avg cost in Phase 2); price is default sell price.
    cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    price = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    low_stock_threshold = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Alert when stock on hand is at or below this quantity.",
    )

    # Default GL accounts. Invoice/Bill lines copy these on create so historical
    # documents are immune to later changes to the product master.
    default_revenue_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="default_revenue_for_products",
        limit_choices_to={"type": "revenue", "is_postable": True},
    )
    default_expense_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
        related_name="default_expense_for_products",
        limit_choices_to={"type": "expense", "is_postable": True},
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["sku"]

    @property
    def is_low_stock(self) -> bool:
        if self.type != ProductType.STOCK:
            return False
        qty = self.stock_on_hand.qty if hasattr(self, "stock_on_hand") else Decimal("0.0000")
        return qty <= self.low_stock_threshold

    def __str__(self):
        return f"{self.sku} - {self.name}"


class StockMovement(models.Model):
    class MovementType(models.TextChoices):
        RECEIPT = "receipt", "Receipt"
        ISSUE = "issue", "Issue"
        ADJUSTMENT = "adjustment", "Adjustment"
        TRANSFER = "transfer", "Transfer"

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="stock_movements")
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    qty = models.DecimalField(max_digits=14, decimal_places=4)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    ref_doc_type = models.CharField(max_length=50, blank=True)
    ref_doc_id = models.PositiveBigIntegerField(null=True, blank=True)
    memo = models.CharField(max_length=500, blank=True)
    lot_id = models.CharField(max_length=100, blank=True)
    serial_no = models.CharField(max_length=100, blank=True)
    posted_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="created_stock_movements",
    )

    class Meta:
        ordering = ["-posted_at", "-id"]
        indexes = [
            models.Index(fields=["product", "posted_at"]),
            models.Index(fields=["ref_doc_type", "ref_doc_id"]),
        ]
        constraints = [
            models.CheckConstraint(name="stockmovement_qty_positive", check=models.Q(qty__gt=0)),
            models.CheckConstraint(name="stockmovement_unit_cost_nonneg", check=models.Q(unit_cost__gte=0)),
        ]

    def __str__(self):
        return f"{self.product.sku} {self.get_movement_type_display()} {self.qty}"


class StockOnHand(models.Model):
    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="stock_on_hand")
    qty = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0.0000"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(name="stockonhand_qty_nonneg", check=models.Q(qty__gte=0)),
        ]

    def __str__(self):
        return f"{self.product.sku}: {self.qty}"
