from decimal import Decimal
from django.db import models
from django.conf import settings
from simple_history.models import HistoricalRecords


class TriggerType(models.TextChoices):
    MANUAL = "manual", "Manual"
    RECEIVING = "receiving", "On Goods Receipt"
    MANUFACTURING = "manufacturing", "On MO Completion"


class FieldType(models.TextChoices):
    BOOLEAN = "boolean", "Pass / Fail"
    NUMERIC = "numeric", "Numeric Value"
    TEXT = "text", "Text / Notes"


class InspectionTemplate(models.Model):
    name = models.CharField(max_length=200)
    product = models.ForeignKey(
        "inventory.Product",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inspection_templates",
        help_text="Optional. Link this template to a specific product."
    )
    trigger_type = models.CharField(
        max_length=20,
        choices=TriggerType.choices,
        default=TriggerType.MANUAL
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_inspection_templates"
    )
    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_trigger_type_display()})"


class InspectionFieldTemplate(models.Model):
    template = models.ForeignKey(
        InspectionTemplate,
        on_delete=models.CASCADE,
        related_name="fields"
    )
    name = models.CharField(max_length=150)
    field_type = models.CharField(
        max_length=20,
        choices=FieldType.choices,
        default=FieldType.BOOLEAN
    )
    min_value = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="For numeric checks: minimum acceptable value."
    )
    max_value = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="For numeric checks: maximum acceptable value."
    )
    is_required = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.name} ({self.get_field_type_display()})"


class QualityInspection(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Pending"
        PASS = "pass", "Passed"
        FAIL = "fail", "Failed"
        QUARANTINED = "quarantined", "Quarantined"

    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    template = models.ForeignKey(
        InspectionTemplate,
        on_delete=models.PROTECT,
        related_name="inspections"
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT
    )
    notes = models.TextField(blank=True)
    
    # Traceability links
    goods_receipt = models.ForeignKey(
        "purchasing.GoodsReceipt",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inspections"
    )
    manufacturing_order = models.ForeignKey(
        "manufacturing.ManufacturingOrder",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inspections"
    )
    lot = models.ForeignKey(
        "inventory.Lot",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inspections"
    )
    serial_number = models.ForeignKey(
        "inventory.SerialNumber",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inspections"
    )
    
    inspected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inspections"
    )
    inspected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.number or f"QC-PENDING-{self.pk}"


class InspectionValue(models.Model):
    inspection = models.ForeignKey(
        QualityInspection,
        on_delete=models.CASCADE,
        related_name="values"
    )
    field_template = models.ForeignKey(
        InspectionFieldTemplate,
        on_delete=models.PROTECT
    )
    value_boolean = models.BooleanField(null=True, blank=True)
    value_numeric = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True
    )
    value_text = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["inspection", "field_template"],
                name="unique_inspection_field_value"
            )
        ]

    def __str__(self):
        return f"{self.field_template.name}: {self.value}"

    @property
    def value(self):
        if self.field_template.field_type == FieldType.BOOLEAN:
            return self.value_boolean
        elif self.field_template.field_type == FieldType.NUMERIC:
            return self.value_numeric
        return self.value_text

    @property
    def is_passing(self) -> bool:
        ft = self.field_template
        if ft.field_type == FieldType.BOOLEAN:
            return self.value_boolean is True
        elif ft.field_type == FieldType.NUMERIC:
            val = self.value_numeric
            if val is None:
                return not ft.is_required
            if ft.min_value is not None and val < ft.min_value:
                return False
            if ft.max_value is not None and val > ft.max_value:
                return False
            return True
        return True


class NonConformance(models.Model):
    class Severity(models.TextChoices):
        MINOR = "minor", "Minor"
        MAJOR = "major", "Major"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Under Investigation"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Disposition(models.TextChoices):
        PENDING = "pending", "Pending Disposition"
        USE_AS_IS = "use_as_is", "Use As-Is"
        REWORK = "rework", "Rework"
        SCRAP = "scrap", "Scrap / Disposal"
        RETURN = "return_to_vendor", "Return to Vendor"

    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    inspection = models.ForeignKey(
        QualityInspection,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="non_conformances"
    )
    title = models.CharField(max_length=200)
    description = models.TextField()
    severity = models.CharField(
        max_length=20,
        choices=Severity.choices,
        default=Severity.MAJOR
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN
    )
    disposition = models.CharField(
        max_length=30,
        choices=Disposition.choices,
        default=Disposition.PENDING
    )
    
    # Lot & Location tracking
    lot = models.ForeignKey(
        "inventory.Lot",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="non_conformances"
    )
    location = models.ForeignKey(
        "inventory.Location",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="non_conformances"
    )
    
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reported_ncrs"
    )
    reported_at = models.DateTimeField(auto_now_add=True)
    disposition_notes = models.TextField(blank=True)
    disposition_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="disposed_ncrs"
    )
    disposition_at = models.DateTimeField(null=True, blank=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["-reported_at", "-id"]

    def __str__(self):
        return self.number or f"NC-PENDING-{self.pk}"


class CAPA(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active / In Progress"
        VERIFICATION = "verification", "Under Verification"
        CLOSED = "closed", "Closed"

    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    non_conformance = models.ForeignKey(
        NonConformance,
        on_delete=models.PROTECT,
        related_name="capas"
    )
    title = models.CharField(max_length=200)
    root_cause_analysis = models.TextField()
    corrective_action = models.TextField()
    preventive_action = models.TextField()
    
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_capas"
    )
    target_date = models.DateField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="closed_capas"
    )
    history = HistoricalRecords()

    class Meta:
        ordering = ["-target_date", "-id"]

    def __str__(self):
        return self.number or f"CAPA-DRAFT-{self.pk}"
