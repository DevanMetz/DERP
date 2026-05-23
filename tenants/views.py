import urllib.parse
import urllib.request
import json

from django.conf import settings
from django.core.mail import send_mail
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django_tenants.utils import schema_context


def _verify_turnstile(token, ip):
    """Return True if Cloudflare Turnstile token is valid."""
    secret = getattr(settings, "TURNSTILE_SECRET_KEY", "")
    if not secret:
        return True  # skip check if not configured (local dev)
    data = urllib.parse.urlencode({
        "secret": secret,
        "response": token,
        "remoteip": ip,
    }).encode()
    try:
        with urllib.request.urlopen(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify", data, timeout=5
        ) as resp:
            result = json.loads(resp.read())
            return result.get("success", False)
    except Exception:
        return False

from .forms import TenantSignupForm
from .models import Domain, PendingTenant, SignupAttempt, TenantCompany


def landing(request):
    return render(request, "tenants/landing.html")


def signup(request):
    ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip()

    if request.method == "POST" and SignupAttempt.is_limited(ip):
        return HttpResponseForbidden("Too many signup attempts. Please try again in an hour.")

    form = TenantSignupForm(request.POST or None)
    captcha_error = None
    if request.method == "POST" and form.is_valid():
        token = request.POST.get("cf-turnstile-response", "")
        if not _verify_turnstile(token, ip):
            captcha_error = "CAPTCHA verification failed. Please try again."
    if request.method == "POST" and form.is_valid() and not captcha_error:
        SignupAttempt.record(ip)
        data = form.cleaned_data
        base = settings.BASE_DOMAIN

        pending = PendingTenant.create_for(
            company_name=data["company_name"],
            subdomain=data["subdomain"],
            email=data["email"],
            raw_password=data["password1"],
        )

        confirm_url = f"https://{base}/signup/confirm/{pending.token}/"
        send_mail(
            subject="Confirm your DERP workspace",
            message=(
                f"Hi,\n\n"
                f"You requested a workspace at {data['subdomain']}.{base}.\n\n"
                f"Click the link below to confirm and activate it:\n{confirm_url}\n\n"
                f"This link expires in 24 hours. If you didn't sign up, you can ignore this email.\n\n"
                f"— The DERP team"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[data["email"]],
            fail_silently=False,
        )

        return render(request, "tenants/signup_pending.html", {
            "email": data["email"],
            "subdomain": data["subdomain"],
            "base": base,
        })

    return render(request, "tenants/signup.html", {
        "form": form,
        "captcha_error": captcha_error,
        "turnstile_site_key": getattr(settings, "TURNSTILE_SITE_KEY", ""),
    })


def confirm(request, token):
    pending = get_object_or_404(PendingTenant, token=token)

    if pending.is_expired():
        pending.delete()
        return render(request, "tenants/confirm_error.html", {
            "reason": "This confirmation link has expired. Please sign up again.",
        })

    # Subdomain might have been claimed while pending
    if TenantCompany.objects.filter(schema_name=pending.subdomain).exists():
        pending.delete()
        return render(request, "tenants/confirm_error.html", {
            "reason": "That subdomain was claimed by someone else while your confirmation was pending. Please sign up again with a different subdomain.",
        })

    base = settings.BASE_DOMAIN

    tenant = TenantCompany(schema_name=pending.subdomain, name=pending.company_name)
    tenant.save()
    Domain.objects.create(domain=f"{pending.subdomain}.{base}", tenant=tenant, is_primary=True)

    with schema_context(pending.subdomain):
        from django.contrib.auth import get_user_model
        from django.core.management import call_command

        User = get_user_model()
        user = User(username=pending.email, email=pending.email, is_staff=True, is_superuser=True)
        user.password = pending.password_hash
        user.save()
        call_command("seed_chart_of_accounts", verbosity=0)

    pending.delete()

    protocol = "https" if not settings.DEBUG else "http"
    return render(request, "tenants/confirm_success.html", {
        "subdomain": tenant.schema_name,
        "login_url": f"{protocol}://{tenant.schema_name}.{base}/accounts/login/",
    })
