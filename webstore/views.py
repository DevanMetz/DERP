import logging
from decimal import Decimal

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

from core.models import Company, PublicPage, Role, WebsiteSettings

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
            if ws.stripe_account_id and stripe_service.platform_is_configured():
                # Build a single combined "line item" representing the
                # whole cart. (For a richer UX, expand into one Stripe
                # line item per cart line — left as a future improvement
                # so the sample matches the V2 cart-as-single-charge
                # pattern from the spec.)
                try:
                    success_url = request.build_absolute_uri(
                        reverse("shop_checkout_success") + f"?token={checkout.token}"
                    )
                    cancel_url = request.build_absolute_uri(
                        reverse("shop_checkout_cancel") + f"?token={checkout.token}"
                    )
                    session = stripe_service.create_direct_charge_checkout_session(
                        account_id=ws.stripe_account_id,
                        product_name=f"Order #{checkout.token}",
                        unit_amount=int(checkout.grand_total * 100),
                        currency=checkout.currency.lower(),
                        quantity=1,
                        # No platform fee on the real-store flow by
                        # default. Set to a non-zero value to take a cut.
                        application_fee_amount=0,
                        success_url=success_url,
                        cancel_url=cancel_url,
                        customer_email=checkout.email,
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
        "stripe_configured": bool(WebsiteSettings.get().stripe_account_id and stripe_service.platform_is_configured()),
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


# ---------------------------------------------------------------------------
# Stripe webhook — thin events
# ---------------------------------------------------------------------------
@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Receive thin webhook events from Stripe.

    One webhook destination on the platform handles events from every
    connected account. The thin event payload contains only an event ID
    and minimal metadata; if our handler needs the full event we fetch
    it with `retrieve_full_event(event_id)`.

    Set this endpoint's URL as the destination in your Stripe Dashboard
    (Developers → Webhooks → Add destination) and subscribe to:
      • v2.core.account[requirements].updated
      • v2.core.account[configuration.merchant].capability_status_updated
      • v2.core.account[configuration.customer].capability_status_updated
    Plus, for V1-shaped events we still need:
      • checkout.session.completed                 (real-store flow)
    """
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    if not stripe_service.platform_is_configured():
        # Platform-level Stripe isn't set up yet; reject so mis-routed
        # webhooks don't blow up logs.
        return HttpResponse(status=503)

    try:
        notification = stripe_service.parse_thin_event(payload, sig_header)
    except Exception as exc:
        log.warning("Stripe webhook signature verification failed: %s", exc)
        return HttpResponse(status=400)

    event_type = getattr(notification, "type", None) or notification.get("type", "")
    log.info("Stripe webhook %s", event_type)

    # ─── V2 thin events: fetch the full event before acting ──────────
    if event_type.startswith("v2.core.account"):
        try:
            full = stripe_service.retrieve_full_event(notification.id)
        except Exception:
            log.exception("Failed to retrieve full V2 event %s", notification.id)
            return HttpResponse(status=500)

        # For requirements-updated and capability-status-updated, the
        # action is the same: re-fetch the account status so any UI
        # bound to it shows fresh state. We do not cache status —
        # `retrieve_account_status` always hits the API.
        related = getattr(full, "related_object", None) or {}
        acct_id = getattr(related, "id", None) or (related.get("id") if isinstance(related, dict) else None)
        if acct_id:
            try:
                stripe_service.retrieve_account_status(acct_id)
                log.info("Refreshed status for connected account %s", acct_id)
            except Exception:
                log.exception("Status refresh failed for %s", acct_id)
        return HttpResponse(status=200)

    # ─── V1 checkout.session.completed — real-store fulfillment ──────
    if event_type == "checkout.session.completed":
        # The thin event still includes the data we need for V1 events;
        # for V1 the `data.object` is the full session.
        data = (
            (getattr(notification, "data", None) or {}).get("object")
            if hasattr(notification, "data")
            else (notification.get("data") or {}).get("object")
        ) or {}
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
        return HttpResponse(status=200)

    # Any other event type — silently ack so Stripe stops retrying.
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
    ws = WebsiteSettings.get()
    if ws.stripe_account_id and stripe_service.platform_is_configured():
        return redirect("shop_index")
    token = request.GET.get("token")
    checkout = get_object_or_404(Checkout, token=token)
    return render(request, "webstore/checkout_dev.html", {
        "checkout": checkout,
        "public_pages": _public_nav_pages(),
    })


# ===========================================================================
# Stripe Connect (V2 Accounts) — admin views
# ===========================================================================
def _require_admin_or_manager(user):
    return user.is_authenticated and getattr(user, "role", None) in {Role.ADMIN, Role.MANAGER}


@login_required
def stripe_onboard(request):
    """Create a V2 connected account (if none exists for this tenant)
    and redirect to the Stripe-hosted onboarding flow.

    This is the entry point for the "Onboard to collect payments"
    button on the Website Settings panel. Idempotent in the sense
    that re-clicking it for a tenant that already has an account just
    generates a fresh Account Link.

    DB note: we store a mapping of `tenant (via WebsiteSettings) →
    stripe_account_id`. The `stripe_account_id` field is Fernet-
    encrypted at rest (see webstore.fields.EncryptedCharField).
    """
    if not _require_admin_or_manager(request.user):
        return HttpResponseForbidden("Only administrators and managers can manage Stripe Connect.")
    if not stripe_service.platform_is_configured():
        messages.error(
            request,
            "STRIPE_SECRET_KEY is not set on the platform. Add it to your environment "
            "and restart the server before onboarding a Stripe account.",
        )
        return redirect("website_settings")

    ws = WebsiteSettings.get()
    try:
        # Create the account on first onboard. Subsequent clicks reuse
        # the existing acct_… so the tenant doesn't accumulate
        # abandoned accounts on Stripe.
        if not ws.stripe_account_id:
            account = stripe_service.create_connected_account(
                # ───── PLACEHOLDER: in a real signup you'd collect a
                # separate display name; the brand name from Website
                # Settings is a good default.
                display_name=ws.brand_name or "DERP Merchant",
                # ───── PLACEHOLDER: pull from the tenant owner's user
                # record. `request.user.email` is fine here.
                contact_email=request.user.email or "owner@example.com",
            )
            ws.stripe_account_id = account.id
            ws.stripe_connected_at = timezone.now()
            ws.save(update_fields=["stripe_account_id", "stripe_connected_at"])

        # Build the absolute return / refresh URLs Stripe redirects
        # to when the onboarding flow ends (or expires).
        return_url = request.build_absolute_uri(reverse("stripe_onboarding_return"))
        refresh_url = request.build_absolute_uri(reverse("stripe_onboard"))

        link = stripe_service.create_onboarding_link(
            account_id=ws.stripe_account_id,
            refresh_url=refresh_url,
            return_url=return_url,
        )
    except Exception as exc:
        log.exception("Stripe onboarding setup failed")
        messages.error(request, f"Could not start Stripe onboarding: {exc}")
        return redirect("website_settings")

    # `link.url` is short-lived (typically a few minutes). If the user
    # bounces around and comes back later, they'll hit refresh_url and
    # we'll mint a new one — same account, fresh link.
    return redirect(link.url)


@login_required
def stripe_onboarding_return(request):
    """Stripe redirects back here when the onboarding flow ends.

    DO NOT trust this as proof of success — the user could simply
    type the URL. Always verify by retrieving the account from the
    API (which we do on the settings page).
    """
    if not _require_admin_or_manager(request.user):
        return HttpResponseForbidden()
    messages.success(
        request,
        "Welcome back from Stripe. Your live account status is shown below — "
        "it may take a moment for capability changes to propagate.",
    )
    return redirect("website_settings")


@login_required
@require_POST
def stripe_disconnect(request):
    """Clear the local mapping to the connected account.

    In V2 there is no programmatic 'deauthorize' equivalent — the
    Stripe account itself continues to exist independently of the
    platform. Disconnecting just forgets the link on our side so
    we stop routing the storefront through it.
    """
    if not _require_admin_or_manager(request.user):
        return HttpResponseForbidden("Only administrators and managers can manage Stripe Connect.")
    ws = WebsiteSettings.get()
    ws.stripe_account_id = ""
    ws.stripe_publishable_key = ""
    ws.stripe_webhook_secret = ""
    ws.stripe_connected_at = None
    ws.save()
    messages.success(request, "Disconnected the Stripe account from this tenant.")
    return redirect("website_settings")


# ===========================================================================
# Sample storefront — minimal flow per the integration spec
# ===========================================================================
# This is a self-contained example of the canonical Connect flow:
#   1. Admin creates a product on the connected account
#      → POST /derp/stripe/sample/products/new/
#   2. Customer browses the connected account's storefront
#      → GET  /sample/store/<account_id>/
#   3. Customer pays via hosted Checkout (direct charge + app fee)
#      → POST /sample/store/<account_id>/checkout/<product_id>/
#   4. Customer lands on success/cancel pages
#      → GET  /sample/store/<account_id>/success/
#
# In a real app you'd:
#   • Use a slug or UUID instead of the raw acct_… in the URL.
#   • Verify the connected account is the tenant's before allowing
#     product creation (we do that here via WebsiteSettings).
#   • Cache the product list briefly if it gets large.


@login_required
def sample_product_create(request):
    """Admin form to create a product on this tenant's connected
    account using the Stripe-Account header pattern."""
    if not _require_admin_or_manager(request.user):
        return HttpResponseForbidden("Only administrators and managers can create products.")

    ws = WebsiteSettings.get()
    if not ws.stripe_account_id:
        messages.error(request, "Connect a Stripe account first before adding products.")
        return redirect("website_settings")

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        description = (request.POST.get("description") or "").strip()
        currency = (request.POST.get("currency") or "usd").strip().lower()
        try:
            # We accept a dollar amount in the form and convert to
            # cents server-side. NEVER trust a client-supplied minor
            # unit value — the floating-point conversion belongs here.
            price_dollars = Decimal((request.POST.get("price") or "0").strip())
            price_in_cents = int((price_dollars * 100).quantize(Decimal("1")))
        except Exception:
            messages.error(request, "Price must be a valid number.")
            return redirect("stripe_sample_product_create")

        if not name or price_in_cents <= 0:
            messages.error(request, "Product name and a positive price are required.")
            return redirect("stripe_sample_product_create")

        try:
            product = stripe_service.create_product(
                account_id=ws.stripe_account_id,
                name=name,
                description=description,
                price_in_cents=price_in_cents,
                currency=currency,
            )
        except Exception as exc:
            log.exception("Product creation failed")
            messages.error(request, f"Stripe rejected the product: {exc}")
            return redirect("stripe_sample_product_create")

        messages.success(request, f"Created product '{product.name}' (Stripe ID {product.id}).")
        return redirect("stripe_sample_storefront", account_id=ws.stripe_account_id)

    return render(request, "webstore/stripe_sample_product_form.html", {
        "company": Company.get(),
        "stripe_account_id": ws.stripe_account_id,
    })


def sample_storefront(request, account_id):
    """Public per-account storefront — lists products from Stripe.

    The URL embeds the raw `acct_…` ID for simplicity in this sample.
    In production, use a stable, friendly identifier instead (e.g. a
    tenant slug or a separate UUID stored on your tenant model) so
    rotating the connected account doesn't change customer-facing
    URLs.
    """
    if not stripe_service.platform_is_configured():
        return HttpResponse("Stripe is not configured.", status=503)
    try:
        products = stripe_service.list_products(account_id=account_id)
    except Exception:
        log.exception("Failed to list products for account %s", account_id)
        products = []

    return render(request, "webstore/stripe_sample_storefront.html", {
        "products": products,
        "account_id": account_id,
        "public_pages": _public_nav_pages(),
    })


@require_POST
def sample_storefront_checkout(request, account_id, product_id):
    """Start a hosted Checkout Session for one product on the
    connected account.

    This is a direct charge: funds settle into the connected
    account's balance, and we skim `application_fee_amount` cents
    into the platform's balance.
    """
    if not stripe_service.platform_is_configured():
        return HttpResponse("Stripe is not configured.", status=503)

    # Pull live product + price from Stripe so a client can't tamper
    # with the amount via the form.
    try:
        product = stripe_service.retrieve_product(
            account_id=account_id, product_id=product_id,
        )
    except Exception as exc:
        log.exception("Failed to retrieve product for checkout")
        return HttpResponse(f"Product unavailable: {exc}", status=404)

    price = getattr(product, "default_price", None)
    if not price:
        return HttpResponse("This product has no price set.", status=400)
    unit_amount = price.unit_amount
    currency = price.currency

    success_url = request.build_absolute_uri(
        reverse("stripe_sample_storefront_success", args=[account_id])
        + "?session_id={CHECKOUT_SESSION_ID}"
    )
    cancel_url = request.build_absolute_uri(
        reverse("stripe_sample_storefront", args=[account_id])
    )

    # ───── PLACEHOLDER: choose your fee model ─────────────────────
    # `application_fee_amount` is in minor units (cents for USD).
    # For example, a 5% platform fee on a $20 sale would be 100.
    # Here we take a flat 5% rounded down to cents.
    application_fee = max(0, int(unit_amount * 0.05))

    try:
        session = stripe_service.create_direct_charge_checkout_session(
            account_id=account_id,
            product_name=product.name,
            unit_amount=unit_amount,
            currency=currency,
            quantity=1,
            application_fee_amount=application_fee,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except Exception as exc:
        log.exception("Checkout session creation failed")
        return HttpResponse(f"Could not start checkout: {exc}", status=500)

    return redirect(session.url)


def sample_storefront_success(request, account_id):
    """Landing page after Stripe-hosted Checkout."""
    session_id = request.GET.get("session_id", "")
    session = None
    if session_id:
        try:
            session = stripe_service.retrieve_checkout_session(
                account_id=account_id, session_id=session_id,
            )
        except Exception:
            log.exception("Failed to retrieve session %s", session_id)
    return render(request, "webstore/stripe_sample_storefront_success.html", {
        "session": session,
        "account_id": account_id,
        "public_pages": _public_nav_pages(),
    })
