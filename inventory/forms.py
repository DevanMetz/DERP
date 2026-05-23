from django import forms
from .models import Product


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "sku", "name", "description", "type", "uom",
            "cost", "price",
            "default_revenue_account", "default_expense_account",
            "is_active",
        ]
