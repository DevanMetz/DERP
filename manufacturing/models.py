from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from django.db.models import Q
from simple_history.models import HistoricalRecords


class BillOfMaterials(models.Model):
    product = models.OneToOneField(
        "inventory.Product",
        on_delete=models.PROTECT,
        related_name="bom",
        limit_choices_to={"type": "stock", "is_active": True},
        help_text="The finished product produced by this BOM."
    )
    name = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_boms"
    )
    
    history = HistoricalRecords()

    class Meta:
        verbose_name = "Bill of Materials"
        verbose_name_plural = "Bills of Materials"

    def __str__(self):
        return self.name or f"BOM - {self.product.sku}"

    @property
    def total_cost_rollup(self) -> Decimal:
        """
        Calculates the standard rollup cost by summing component standard costs.
        """
        total = Decimal("0.00")
        for comp in self.components.all():
            total += comp.qty * comp.product.cost
        return total


class BOMComponent(models.Model):
    bom = models.ForeignKey(
        BillOfMaterials,
        on_delete=models.CASCADE,
        related_name="components"
    )
    product = models.ForeignKey(
        "inventory.Product",
        on_delete=models.PROTECT,
        related_name="bom_components",
        limit_choices_to={"type": "stock", "is_active": True},
        help_text="The raw material component."
    )
    qty = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        help_text="Quantity of component required to make 1 unit of finished product."
    )

    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.CheckConstraint(
                name="bomcomponent_qty_positive",
                check=models.Q(qty__gt=0)
            ),
            models.UniqueConstraint(
                fields=["bom", "product"],
                name="unique_bom_product_component"
            )
        ]

    @property
    def extended_cost(self) -> Decimal:
        return (self.qty * self.product.cost).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.product.sku} x {self.qty}"

    def clean(self):
        if self.bom_id and self.bom.product == self.product:
            raise ValidationError("A BOM cannot contain the finished product as a component.")


class ManufacturingOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    product = models.ForeignKey(
        "inventory.Product",
        on_delete=models.PROTECT,
        related_name="manufacturing_orders",
        limit_choices_to={"type": "stock", "is_active": True}
    )
    bom = models.ForeignKey(
        BillOfMaterials,
        on_delete=models.PROTECT,
        related_name="manufacturing_orders"
    )
    qty_target = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        help_text="Quantity of finished goods to produce."
    )
    qty_produced = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Quantity actually completed."
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT
    )
    
    date_planned = models.DateField(help_text="Scheduled production date.")
    date_completed = models.DateTimeField(null=True, blank=True)
    
    journal_entry = models.ForeignKey(
        "accounting.JournalEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="manufacturing_orders"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_mos"
    )
    completed_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="completed_mos"
    )
    production_location = models.ForeignKey(
        "inventory.Location",
        on_delete=models.PROTECT,
        related_name="manufacturing_orders",
        null=True,
        blank=True,
        help_text="Warehouse location where assembly takes place."
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-date_planned", "-id"]
        constraints = [
            models.CheckConstraint(
                name="mo_qty_target_positive",
                check=models.Q(qty_target__gt=0)
            )
        ]

    def __str__(self):
        return self.number or f"MO-DRAFT-{self.pk}"

    def clean(self):
        if self.bom_id and self.bom.product != self.product:
            raise ValidationError("Selected BOM does not match the product to be manufactured.")
