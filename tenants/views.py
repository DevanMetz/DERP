from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django_tenants.utils import schema_context

from .forms import TenantSignupForm
from .models import Domain, SignupAttempt, TenantCompany


def landing(request):
    return render(request, "tenants/landing.html")


def signup(request):
    ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip()

    if request.method == "POST" and SignupAttempt.is_limited(ip):
        return HttpResponseForbidden("Too many signup attempts. Please try again in an hour.")

    form = TenantSignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        SignupAttempt.record(ip)
        data = form.cleaned_data
        slug = data["subdomain"]
        base = settings.BASE_DOMAIN

        # 1. Create the tenant (auto-creates the PG schema + migrates it)
        tenant = TenantCompany(schema_name=slug, name=data["company_name"])
        tenant.save()

        # 2. Bind the subdomain
        Domain.objects.create(domain=f"{slug}.{base}", tenant=tenant, is_primary=True)

        # 3. Inside the new schema: seed COA + create the admin user
        with schema_context(slug):
            from django.contrib.auth import get_user_model
            from django.core.management import call_command

            User = get_user_model()
            User.objects.create_superuser(
                username=data["email"],
                email=data["email"],
                password=data["password1"],
            )
            call_command("seed_chart_of_accounts", verbosity=0)

        protocol = "https" if not settings.DEBUG else "http"
        return redirect(f"{protocol}://{slug}.{base}/accounts/login/")

    return render(request, "tenants/signup.html", {"form": form})
