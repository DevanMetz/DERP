from decimal import Decimal
from django import forms

from .models import Account, ZERO


class JournalEntryHeaderForm(forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    memo = forms.CharField(max_length=500, required=False)


class JournalLineForm(forms.Form):
    account = forms.ModelChoiceField(
        queryset=Account.objects.filter(is_active=True, is_postable=True).order_by("code"),
        required=False,
    )
    debit = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False)
    credit = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False)
    memo = forms.CharField(max_length=500, required=False)

    def clean(self):
        cleaned = super().clean()
        acct = cleaned.get("account")
        d = cleaned.get("debit") or ZERO
        c = cleaned.get("credit") or ZERO
        if not acct and d == ZERO and c == ZERO:
            return cleaned  # empty row, will be skipped
        if not acct:
            raise forms.ValidationError("Account is required.")
        if (d > 0) == (c > 0):
            raise forms.ValidationError("Each line must have either a debit OR a credit, not both or neither.")
        return cleaned


JournalLineFormSet = forms.formset_factory(JournalLineForm, extra=4, min_num=2, validate_min=True)


class GLFilterForm(forms.Form):
    account = forms.ModelChoiceField(
        queryset=Account.objects.filter(is_postable=True).order_by("code"),
    )
    start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))


class TrialBalanceFilterForm(forms.Form):
    as_of = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        label="As of",
    )
