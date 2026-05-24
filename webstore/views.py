import json
import logging
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models import PublicPage, Role, WebsiteSettings

from . import services, stripe_service
from .cart import Cart
from .forms import CheckoutForm
from .models import Category, Checkout, ProductImage, ProductStorefront


log = logging.getLogger(__name__)


def _public_nav_pages():
    return PublicPage.objects.filter(is_published=True).exclude(is_homepage=True).order_by("title")


def _active_products_qs():
    return (
        ProductStorefront.objects
        .filter(is_online_active=True, product__is_active=True, product__is_sellable=True)
        .select_related("product", "category")
        .prefetch_related(Prefetch("images", queryset=ProductImage.objects.order_by("sort_order", "id")))
    )


# ---------- Catalog ----------
def catalog(request):
    products = _active_products_qs()
    featured = products.filter(is_featured=True)[:4]
    categories = Category.objects.filter(is_active=True)
    return render(request, "webstore/catalog.html", {
        "products": products,
        "featured": featured,
        "categories": categories,
        "public_pages": _public_nav_pages(),
    })


def category_detail(request, slug):
    category = get_object_or_404(Category, slug=slug, is_active=True)
    products = _active_products_qs().filter(category=category)
    return render(request, "webstore/category.html", {
        "category": category,
        "products": products,
        "categories": Category.objects.filter(is_active=True),
        "public_pages": _public_nav_pages(),
    })


def product_detail(request, slug):
    storefront = get_object_or_404(_active_products_qs(), slug=slug)
    related = (
        _active_products_qs()
        .filter(category=storefront.category)
        .exclude(pk=storefront.pk)[:4]
    ) if storefront.category_id else _active_products_qs().exclude(pk=storefront.pk)[:4]
    return render(request, "webstore/product_detail.html", {
        "storefront": storefront,
        "product": storefront.product,
        "images": list(storefront.images.all()),
        "related": related,
        "public_pages": _public_nav_pages(),
    })


# ---------- Cart ----------
def cart_view(request):
    return render(request, "webstore/cart.html", {
        "cart": Cart(request),
        "public_pages": _public_nav_pages(),
    })


@require_POST
def cart_add(request, product_id):
    try:
        qty = int(request.POST.get("qty", 1))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid qty")
    if not ProductStorefront.objects.filter(
        product_id=product_id, is_online_active=True,
        product__is_active=True, product__is_sellable=True,
    ).exists():
        messages.error(request, "That product is no longer available.")
        return redirect("shop_cart")

    Cart(request).add(product_id, qty)

    if request.htmx:
        return render(request, "webstore/_cart_summary.html", {"cart": Cart(request)})

    messages.success(request, "Added to cart.")
    next_url = request.POST.get("next") or "shop_cart"
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect(next_url)


@require_POST
def cart_update(request, product_id):
    try:
        qty = int(request.POST.get("qty", 0))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid qty")
    Cart(request).set_qty(product_id, qty)
    if request.htmx:
        return render(request, "webstore/_cart_body.html", {"cart": Cart(request)})
    return redirect("shop_cart")


@require_POST
def cart_remove(request, product_id):
    Cart(request).remove(product_id)
    if request.htmx:
        return render(request, "webstore/_cart_body.html", {"cart": Cart(request)})
    return redirect("shop_cart")


@require_POST
def cart_clear(request):
    Cart(request).clear()
    return redirect("shop_cart")


# ---------- Checkout ----------
def checkout_view(request):
    cart = Cart(request)
    if not cart.lines:
        messages.info(request, "Your cart is empty.")
        return redirect("shop_cart")

    if request.method == "POST":
        form = CheckoutForm(request.POST)
        if form.is_valid():
            ship = form.build_address()
            checkout = Checkout.objects.create(
                session_key=request.session.session_key or "",
                email=form.cleaned_data["email"],
                shipping_address=ship,
                billing_address=ship,
                cart_items=services.snapshot_cart_for_checkout(cart),
                subtotal=cart.subtotal,
                grand_total=cart.subtotal,
                currency=getattr(settings, "DEFAULT_CURRENCY", "USD"),
                status=Checkout.Status.PENDING,
            )

            ws = WebsiteSettings.get()
            if stripe_service.tenant_is_connected(ws):
                try:
                    session = stripe_service.create_checkout_session(
                        checkout=checkout, request=request, ws=ws,
                    )
                except Exception as exc:
                    log.exception("Stripe session creation failed")
                    checkout.status = Checkout.Status.FAILED
                    checkout.notes = f"Stripe error: {exc}"
                    checkout.save()
                    messages.error(request, "Payment provider error. Please try again.")
                    return redirect("shop_checkout")

                checkout.stripe_session_id = session.id
                checkout.status = Checkout.Status.AWAITING_PAYMENT
                checkout.save()
                return redirect(session.url)
            else:
                # Dev fallback: tenant hasn't connected Stripe yet. Land on a
                # page that exercises the full ERP wiring without a charge.
                checkout.status = Checkout.Status.AWAITING_PAYMENT
                checkout.save()
                return redirect(reverse("shop_checkout_dev") + f"?token={checkout.token}")
    else:
        form = CheckoutForm()

    return render(request, "webstore/checkout.html", {
        "form": form,
        "cart": cart,
        "stripe_configured": stripe_service.tenant_is_connected(),
        "public_pages": _public_nav_pages(),
    })


def checkout_success(request):
    token = request.GET.get("token")
    checkout = get_object_or_404(Checkout, token=token) if token else None
    # The order is normally finalized by the Stripe webhook. If the user
    # lands here before the webhook fires, the page just shows a "we're
    # finalizing" state; refresh confirms.
    if checkout and checkout.status == Checkout.Status.PAID:
        # Clear the cart now that the order is locked in.
        Cart(request).clear()
    return render(request, "webstore/checkout_success.html", {
        "checkout": checkout,
        "public_pages": _public_nav_pages(),
    })


def checkout_cancel(request):
    token = request.GET.get("token")
    checkout = Checkout.objects.filter(token=token).first() if token else None
    if checkout and checkout.status == Checkout.Status.AWAITING_PAYMENT:
        checkout.status = Checkout.Status.CANCELLED
        checkout.save(update_fields=["status"])
    return render(request, "webstore/checkout_cancel.html", {
        "checkout": checkout,
        "public_pages": _public_nav_pages(),
    })


# ---------- Stripe webhook ----------
@csrf_exempt
@require_POST
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    ws = WebsiteSettings.get()
    if not stripe_service.tenant_is_connected(ws):
        # Tenant hasn't finished Connect setup yet. Reject silently so
        # mis-routed webhooks don't blow up logs.
        return HttpResponse(status=503)
    try:
        event = stripe_service.verify_webhook(payload, sig_header, ws=ws)
    except Exception as exc:
        log.warning("Stripe webhook signature verification failed: %s", exc)
        return HttpResponse(status=400)

    event_type = event["type"]
    log.info("Stripe webhook %s id=%s", event_type, event.get("id"))

    if event_type == "checkout.session.completed":
        data = event["data"]["object"]
        token = (data.get("metadata") or {}).get("checkout_token")
        if not token:
            return HttpResponse(status=200)
        checkout = Checkout.objects.filter(token=token).first()
        if not checkout:
            log.warning("Webhook for unknown checkout token %s", token)
            return HttpResponse(status=200)
        try:
            services.complete_checkout(
                checkout,
                stripe_payment_intent=data.get("payment_intent", "") or "",
            )
        except Exception:
            log.exception("Failed to complete checkout %s", checkout.pk)
            return HttpResponse(status=500)
    # Silently ack any other event types we don't subscribe to.
    return HttpResponse(status=200)


# ---------- Dev-only manual completion (when Stripe isn't configured) ----------
@require_POST
def checkout_dev_complete(request):
    """Manually mark a checkout paid without Stripe.

    Active ONLY when STRIPE_SECRET_KEY is unset — lets you exercise the
    full ERP wiring (SalesOrder + Invoice + Payment) locally before you
    have Stripe keys.
    """
    if stripe_service.tenant_is_connected():
        return HttpResponse("Disabled while Stripe is configured.", status=403)
    token = request.POST.get("token") or request.GET.get("token")
    checkout = get_object_or_404(Checkout, token=token)
    if checkout.status not in {Checkout.Status.AWAITING_PAYMENT, Checkout.Status.PENDING}:
        return redirect(reverse("shop_checkout_success") + f"?token={checkout.token}")
    services.complete_checkout(checkout, stripe_payment_intent="dev-manual")
    return redirect(reverse("shop_checkout_success") + f"?token={checkout.token}")


def checkout_dev_landing(request):
    """Landing page used in dev mode when Stripe is not configured."""
    if stripe_service.tenant_is_connected():
        return redirect("shop_index")
    token = request.GET.get("token")
    checkout = get_object_or_404(Checkout, token=token)
    return render(request, "webstore/checkout_dev.html", {
        "checkout": checkout,
        "public_pages": _public_nav_pages(),
    })


# ---------- Stripe Connect (admin-only OAuth) ----------
def _require_admin_or_manager(user):
    return user.is_authenticated and getattr(user, "role", None) in {Role.ADMIN, Role.MANAGER}


@login_required
def stripe_connect_start(request):
    """Kick off the Connect OAuth flow. Builds a CSRF-bound `state` token
    in the session so the callback can verify the redirect is ours."""
    if not _require_admin_or_manager(request.user):
        return HttpResponseForbidden("Only administrators and managers can manage Stripe Connect.")
    if not stripe_service.platform_is_configured():
        messages.error(request, "Stripe Connect is not configured at the platform level. Set STRIPE_SECRET_KEY and STRIPE_CONNECT_CLIENT_ID in the platform .env.")
        return redirect("website_settings")

    state = secrets.token_urlsafe(32)
    request.session["stripe_oauth_state"] = state
    redirect_uri = request.build_absolute_uri(reverse("stripe_connect_callback"))
    return redirect(stripe_service.oauth_authorize_url(state=state, redirect_uri=redirect_uri))


@login_required
def stripe_connect_callback(request):
    """Stripe redirects here with ?code=...&state=...; we exchange code for acct_…."""
    if not _require_admin_or_manager(request.user):
        return HttpResponseForbidden("Only administrators and managers can manage Stripe Connect.")

    received_state = request.GET.get("state", "")
    expected_state = request.session.pop("stripe_oauth_state", "")
    if not received_state or received_state != expected_state:
        messages.error(request, "Stripe Connect handshake failed (state mismatch). Please try again.")
        return redirect("website_settings")

    if "error" in request.GET:
        messages.error(request, f"Stripe Connect declined: {request.GET.get('error_description') or request.GET.get('error')}")
        return redirect("website_settings")

    code = request.GET.get("code", "")
    if not code:
        messages.error(request, "Stripe Connect callback missing authorization code.")
        return redirect("website_settings")

    try:
        result = stripe_service.oauth_exchange_code(code)
    except Exception as exc:
        log.exception("Stripe OAuth token exchange failed")
        messages.error(request, f"Could not complete Stripe connection: {exc}")
        return redirect("website_settings")

    ws = WebsiteSettings.get()
    ws.stripe_account_id = result.get("stripe_user_id", "")
    ws.stripe_publishable_key = result.get("stripe_publishable_key", "")
    ws.stripe_connected_at = timezone.now()
    ws.save()

    messages.success(request, "Stripe account connected. Next step: register a webhook in your Stripe dashboard and paste the signing secret below.")
    return redirect("website_settings")


@login_required
@require_POST
def stripe_disconnect(request):
    """Revoke the platform's access to the tenant's Stripe account and
    clear the locally stored credentials."""
    if not _require_admin_or_manager(request.user):
        return HttpResponseForbidden("Only administrators and managers can manage Stripe Connect.")
    ws = WebsiteSettings.get()
    if ws.stripe_account_id:
        try:
            stripe_service.oauth_revoke(ws.stripe_account_id)
        except Exception:
            # Even if revocation fails on Stripe's side (e.g. account
            # already disconnected upstream), clear our local copy.
            log.exception("Stripe OAuth revoke failed; clearing local fields anyway.")
    ws.stripe_account_id = ""
    ws.stripe_publishable_key = ""
    ws.stripe_webhook_secret = ""
    ws.stripe_connected_at = None
    ws.save()
    messages.success(request, "Stripe account disconnected.")
    return redirect("website_settings")


@login_required
@require_POST
def stripe_save_webhook_secret(request):
    """Tenant pastes their per-account `whsec_…` from the Stripe dashboard."""
    if not _require_admin_or_manager(request.user):
        return HttpResponseForbidden("Only administrators and managers can manage Stripe Connect.")
    secret = (request.POST.get("webhook_secret") or "").strip()
    if not secret.startswith("whsec_"):
        messages.error(request, "That doesn't look like a Stripe webhook secret. It should start with 'whsec_'.")
        return redirect("website_settings")
    ws = WebsiteSettings.get()
    if not ws.stripe_account_id:
        messages.error(request, "Connect a Stripe account first.")
        return redirect("website_settings")
    ws.stripe_webhook_secret = secret
    ws.save()
    messages.success(request, "Webhook signing secret saved.")
    return redirect("website_settings")
