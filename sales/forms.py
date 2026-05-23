from decimal import Decimal
from django import forms

from accounting.models import Account
from inventory.models import Product

from .models import Customer, Invoice, InvoiceLine


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = [
            "name", "email", "phone", "billing_address", "shipping_address",
            "payment_terms_days", "tax_rate", "default_revenue_account",
            "is_active", "notes",
        ]


class InvoiceHeaderForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["customer", "date", "due_date", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }


class InvoiceLineForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(is_active=True).order_by("sku"),
        required=False,
    )
    description = forms.CharField(max_length=500, required=False)
    qty = forms.DecimalField(max_digits=14, decimal_places=4, min_value=Decimal("0"), required=False)
    unit_price = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False)
    revenue_account = forms.ModelChoiceField(
        queryset=Account.objects.filter(type="revenue", is_postable=True, is_active=True).order_by("code"),
        required=False,
    )

    def clean(self):
        c = super().clean()
        product = c.get("product")
        qty = c.get("qty")
        desc = c.get("description") or ""
        price = c.get("unit_price")
        if not product and not desc.strip() and not qty:
            return c  # empty row
        if not product and not desc.strip():
            raise forms.ValidationError("Provide a product OR a description.")
        if qty is None or qty <= 0:
            raise forms.ValidationError("Quantity is required and must be > 0.")
        if price is None or price < 0:
            raise forms.ValidationError("Unit price is required and must be >= 0.")
        return c


InvoiceLineFormSet = forms.formset_factory(InvoiceLineForm, extra=4, min_num=1, validate_min=True)


class ReceivePaymentForm(forms.Form):
    """
    Header for a customer payment. The list of invoice rows + amounts is
    built dynamically from the customer's open invoices and posted alongside.
    """
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.filter(is_active=True).order_by("name"),
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
    reference = forms.CharField(max_length=100, required=False, help_text="Check number, ACH ref, etc.")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
