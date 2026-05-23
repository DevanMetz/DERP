import json
import urllib.parse
import urllib.request

from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django_tenants.utils import remove_www
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


def _send_email_via_resend(to_email, subject, text_body):
    """Send email through Resend's HTTPS API (Railway blocks outbound SMTP)."""
    api_key = getattr(settings, "RESEND_API_KEY", "") or __import__("os").environ.get("RESEND_API_KEY", "")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY not configured")
    payload = json.dumps({
        "from": settings.DEFAULT_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "text": text_body,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"DERP/1.0 (+{_configured_public_base_url()})",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            if resp.status >= 400:
                raise RuntimeError(f"Resend API error {resp.status}: {body!r}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Resend API error {e.code}: {body}") from e

from .forms import TenantSignupForm
from .models import Domain, PendingTenant, SignupAttempt, TenantCompany


def _external_scheme(request=None):
    if request is not None and request.is_secure():
        return "https"
    if not settings.DEBUG:
        return "https"
    return request.scheme if request is not None else "http"


def _configured_public_base_url(request=None):
    return f"{_external_scheme(request)}://{settings.BASE_DOMAIN}".rstrip("/")


def _request_public_url(request, path="/"):
    return request.build_absolute_uri(path)


def _public_page_context(request, path="/"):
    return {
        "base_domain": settings.BASE_DOMAIN,
        "canonical_url": _request_public_url(request, path),
        "public_base_url": _request_public_url(request, "/").rstrip("/"),
        "workspace_login_base_url": _configured_public_base_url(request),
    }


def _pending_subdomain_from_hostname(hostname):
    base_domain = settings.BASE_DOMAIN.lower().split(":")[0]
    hostname = hostname.lower().strip(".")
    suffix = f".{base_domain}"

    if hostname == base_domain or not hostname.endswith(suffix):
        return None

    subdomain = hostname[: -len(suffix)]
    if not subdomain or "." in subdomain:
        return None
    return subdomain


def tenant_not_found(request):
    hostname = remove_www(request.get_host().split(":")[0])
    subdomain = _pending_subdomain_from_hostname(hostname)
    pending = PendingTenant.objects.filter(subdomain=subdomain).first() if subdomain else None
    public_base_url = _configured_public_base_url(request)

    if pending:
        return render(request, "tenants/workspace_pending.html", {
            "base": settings.BASE_DOMAIN,
            "is_expired": pending.is_expired(),
            "public_base_url": public_base_url,
            "subdomain": pending.subdomain,
        }, status=404)

    return render(request, "tenants/workspace_not_found.html", {
        "hostname": hostname,
        "public_base_url": public_base_url,
    }, status=404)


def landing(request):
    return render(request, "tenants/landing.html", _public_page_context(request, "/"))


def features(request):
    return render(request, "tenants/features.html", _public_page_context(request, "/features/"))


def robots_txt(request):
    from django.http import HttpResponse
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /signup/confirm/\n"
        f"Sitemap: {_request_public_url(request, '/sitemap.xml')}\n"
    )
    return HttpResponse(content, content_type="text/plain")


def sitemap_xml(request):
    from django.http import HttpResponse
    urls = [
        (_request_public_url(request, "/"), "1.0"),
        (_request_public_url(request, "/features/"), "0.9"),
        (_request_public_url(request, "/signup/"), "0.8"),
    ]
    body = '<?xml version="1.0" encoding="UTF-8"?>\n'
    body += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for loc, priority in urls:
        body += f"  <url><loc>{loc}</loc><priority>{priority}</priority></url>\n"
    body += "</urlset>\n"
    return HttpResponse(body, content_type="application/xml")


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

        confirm_url = _request_public_url(request, f"/signup/confirm/{pending.token}/")
        try:
            _send_email_via_resend(
                to_email=data["email"],
                subject="Confirm your DERP workspace",
                text_body=(
                    f"Hi,\n\n"
                    f"You requested a workspace at {data['subdomain']}.{base}.\n\n"
                    f"Click the link below to confirm and activate it:\n{confirm_url}\n\n"
                    f"This link expires in 24 hours. If you didn't sign up, you can ignore this email.\n\n"
                    f"— The DERP team"
                ),
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("send_mail failed: %s", exc)
            pending.delete()
            return render(request, "tenants/signup.html", {
                "form": form,
                "captcha_error": "Failed to send confirmation email. Please try again later or contact support.",
                "turnstile_site_key": getattr(settings, "TURNSTILE_SITE_KEY", ""),
                "base_domain": base,
                "public_base_url": _request_public_url(request, "/").rstrip("/"),
            })

        return render(request, "tenants/signup_pending.html", {
            "email": data["email"],
            "subdomain": data["subdomain"],
            "base": base,
        })

    return render(request, "tenants/signup.html", {
        "form": form,
        "captcha_error": captcha_error,
        "turnstile_site_key": getattr(settings, "TURNSTILE_SITE_KEY", ""),
        "base_domain": settings.BASE_DOMAIN,
        "public_base_url": _request_public_url(request, "/").rstrip("/"),
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

    if request.method != "POST":
        return render(request, "tenants/confirm_pending.html", {
            "company_name": pending.company_name,
            "subdomain": pending.subdomain,
            "workspace_url": f"{_external_scheme(request)}://{pending.subdomain}.{base}/",
        })

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

    return render(request, "tenants/confirm_success.html", {
        "subdomain": tenant.schema_name,
        "login_url": f"{_external_scheme(request)}://{tenant.schema_name}.{base}/accounts/login/",
    })
