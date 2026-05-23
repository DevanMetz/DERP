import re
from django import forms


class TenantSignupForm(forms.Form):
    company_name = forms.CharField(
        max_length=200,
        label="Company name",
        widget=forms.TextInput(attrs={"placeholder": "Acme Inc."}),
    )
    subdomain = forms.SlugField(
        max_length=63,
        label="Subdomain",
        widget=forms.TextInput(attrs={"placeholder": "acme"}),
        help_text="Letters, numbers, and hyphens only. This becomes your URL.",
    )
    email = forms.EmailField(
        label="Admin email",
        widget=forms.EmailInput(attrs={"placeholder": "you@company.com"}),
    )
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput())
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput())

    RESERVED = {"www", "api", "admin", "mail", "public", "static", "media", "app", "dashboard"}

    def clean_subdomain(self):
        slug = self.cleaned_data["subdomain"].lower()
        if slug in self.RESERVED:
            raise forms.ValidationError("That subdomain is reserved. Please choose another.")
        if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", slug) and len(slug) > 1:
            raise forms.ValidationError("Subdomains must start and end with a letter or number.")
        from tenants.models import TenantCompany
        if TenantCompany.objects.filter(schema_name=slug).exists():
            raise forms.ValidationError("That subdomain is already taken.")
        return slug

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned
