from django import forms
from .models import Product, Location


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "sku", "name", "description", "type", "uom", "image",
            "cost", "price", "low_stock_threshold",
            "default_revenue_account", "default_expense_account",
            "is_active",
        ]


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ["name", "description", "is_active"]
