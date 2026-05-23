from decimal import Decimal
from django import forms

from accounting.models import Account
from inventory.models import Product

from .models import Bill, Vendor


class VendorForm(forms.ModelForm):
    class Meta:
        model = Vendor
        fields = [
            "name", "email", "phone", "address",
            "payment_terms_days", "default_expense_account",
            "is_active", "notes",
        ]


class BillHeaderForm(forms.ModelForm):
    class Meta:
        model = Bill
        fields = ["vendor", "vendor_ref", "date", "due_date", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }


class BillLineForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(is_active=True).order_by("sku"),
        required=False,
    )
    description = forms.CharField(max_length=500, required=False)
    qty = forms.DecimalField(max_digits=14, decimal_places=4, min_value=Decimal("0"), required=False)
    unit_cost = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False)
    expense_account = forms.ModelChoiceField(
        queryset=Account.objects.filter(
            type__in=["expense", "asset"], is_postable=True, is_active=True,
        ).order_by("code"),
        required=False,
    )

    def clean(self):
        c = super().clean()
        product = c.get("product")
        qty = c.get("qty")
        desc = c.get("description") or ""
        cost = c.get("unit_cost")
        if not product and not desc.strip() and not qty:
            return c
        if not product and not desc.strip():
            raise forms.ValidationError("Provide a product OR a description.")
        if qty is None or qty <= 0:
            raise forms.ValidationError("Quantity is required and must be > 0.")
        if cost is None or cost < 0:
            raise forms.ValidationError("Unit cost is required and must be >= 0.")
        return c


BillLineFormSet = forms.formset_factory(BillLineForm, extra=4, min_num=1, validate_min=True)


class PayVendorForm(forms.Form):
    vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.filter(is_active=True).order_by("name"),
    )
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    cash_account = forms.ModelChoiceField(
        queryset=Account.objects.filter(type="asset", is_postable=True, is_active=True).order_by("code"),
    )
    method = forms.ChoiceField(choices=[
        ("cash", "Cash"), ("check", "Check"), ("ach", "ACH / bank transfer"),
        ("card", "Card"), ("other", "Other"),
    ], initial="check")
    reference = forms.CharField(max_length=100, required=False)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
