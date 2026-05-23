"""
Inventory models.

Phase 1 ships Product only — the bare master-data needed for sales and
purchasing line items. Stock tracking (StockMovement, on-hand) lands in
Phase 2 along with COGS posting on shipment.

The schema reserves columns for things we're not building yet (cost,
default accounts) so adding them later doesn't require backfill churn.
"""

from decimal import Decimal
from django.db import models
from simple_history.models import HistoricalRecords


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

    # Pricing. Cost is what we paid (avg cost in Phase 2); price is default sell price.
    cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    price = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

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

    def __str__(self):
        return f"{self.sku} — {self.name}"
