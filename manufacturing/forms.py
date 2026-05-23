from django import forms
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.db.models import Q

from .models import BillOfMaterials, BOMComponent, ManufacturingOrder
from inventory.models import Product, ProductType


class BOMForm(forms.ModelForm):
    class Meta:
        model = BillOfMaterials
        fields = ["product", "name", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Limit product selection to active stock products that don't already have a BOM
        qs = Product.objects.filter(type=ProductType.STOCK, is_active=True)
        if self.instance.pk:
            # If editing, allow the current product
            qs = qs.filter(Q(bom__isnull=True) | Q(pk=self.instance.product_id))
        else:
            qs = qs.filter(bom__isnull=True)
        
        self.fields["product"].queryset = qs


class BOMComponentForm(forms.ModelForm):
    class Meta:
        model = BOMComponent
        fields = ["product", "qty"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Limit component products to active stock products
        self.fields["product"].queryset = Product.objects.filter(
            type=ProductType.STOCK, is_active=True
        )


BOMComponentFormSet = inlineformset_factory(
    BillOfMaterials,
    BOMComponent,
    form=BOMComponentForm,
    fields=["product", "qty"],
    extra=1,
    can_delete=True
)


class ManufacturingOrderForm(forms.ModelForm):
    class Meta:
        model = ManufacturingOrder
        fields = ["product", "qty_target", "date_planned"]
        widgets = {
            "qty_target": forms.NumberInput(attrs={"step": "any", "placeholder": "0.0000"}),
            "date_planned": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only allow products that have an active BOM
        self.fields["product"].queryset = Product.objects.filter(
            type=ProductType.STOCK, is_active=True, bom__isnull=False, bom__is_active=True
        )

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get("product")
        if product:
            if not hasattr(product, "bom") or not product.bom.is_active:
                raise ValidationError(
                    f"Product '{product.name}' does not have an active Bill of Materials defined."
                )
        return cleaned_data
