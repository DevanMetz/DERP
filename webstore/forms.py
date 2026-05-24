from django import forms

from .models import Address


class CheckoutForm(forms.Form):
    """Single-page checkout: email + shipping address.
    Billing defaults to shipping unless `billing_same_as_shipping` is False
    (UI offers it as a checkbox; this v1 always uses shipping for billing).
    """
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "you@example.com"}),
    )
    full_name = forms.CharField(
        max_length=160,
        widget=forms.TextInput(attrs={"autocomplete": "name", "placeholder": "First Last"}),
    )
    company = forms.CharField(
        max_length=160, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
    line1 = forms.CharField(
        max_length=200,
        label="Address",
        widget=forms.TextInput(attrs={"autocomplete": "address-line1", "placeholder": "123 Main Street"}),
    )
    line2 = forms.CharField(
        max_length=200, required=False,
        label="Apt / Suite",
        widget=forms.TextInput(attrs={"autocomplete": "address-line2", "placeholder": "Apt 4B (optional)"}),
    )
    city = forms.CharField(
        max_length=120,
        widget=forms.TextInput(attrs={"autocomplete": "address-level2"}),
    )
    region = forms.CharField(
        max_length=120,
        label="State / Province",
        widget=forms.TextInput(attrs={"autocomplete": "address-level1"}),
    )
    postal_code = forms.CharField(
        max_length=24,
        label="ZIP / Postal code",
        widget=forms.TextInput(attrs={"autocomplete": "postal-code"}),
    )
    country = forms.CharField(
        max_length=2, initial="US",
        widget=forms.TextInput(attrs={"autocomplete": "country", "maxlength": 2}),
        help_text="2-letter ISO code (e.g. US, CA, GB).",
    )
    phone = forms.CharField(
        max_length=40, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "tel", "placeholder": "(555) 123-4567"}),
    )

    def build_address(self) -> Address:
        d = self.cleaned_data
        return Address.objects.create(
            full_name=d["full_name"],
            company=d.get("company", ""),
            line1=d["line1"],
            line2=d.get("line2", ""),
            city=d["city"],
            region=d["region"],
            postal_code=d["postal_code"],
            country=d["country"].upper(),
            phone=d.get("phone", ""),
        )
