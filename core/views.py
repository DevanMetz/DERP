from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect, render
from django import forms

from .models import Company


@login_required
def home(request):
    company = Company.get()
    return render(request, "core/home.html", {"company": company})


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            "name", "legal_name", "email", "phone", "address", "tax_id",
            "fiscal_year_start_month", "fiscal_year_start_day",
        ]


@login_required
def company_setup(request):
    company = Company.get()
    if request.method == "POST":
        form = CompanyForm(request.POST, instance=company)
        if form.is_valid():
            form.save()
            messages.success(request, "Company saved.")
            return redirect("company_setup")
    else:
        form = CompanyForm(instance=company)
    return render(request, "core/company_setup.html", {"form": form})
