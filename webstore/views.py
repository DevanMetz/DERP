import logging

from django.conf import settings
from django.contrib import messages
from django.db.models import Prefetch
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models import PublicPage

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


def catalog(request):
    products = _active_products_qs()
    return render(request, "webstore/catalog.html", {
        "products": products,
        "featured": products.filter(is_featured=True)[:4],
        "categories": Category.objects.filter(is_active=True),
        "public_pages": _public_nav_pages(),
    })


def category_detail(request, slug):
    category = get_object_or_404(Category, slug=slug, is_active=True)
    return render(request, "webstore/category.html", {
        "category": category,
        "products": _active_products_qs().filter(category=category),
        "categories": Category.objects.filter(is_active=True),
        "public_pages": _public_nav_pages(),
    })


def product_detail(request, slug):
    storefront = get_object_or_404(_active_products_qs(), slug=slug)
    products = _active_products_qs().exclude(pk=storefront.pk)
    related = products.filter(category=storefront.category)[:4] if storefront.category_id else products[:4]
    return render(request, "webstore/product_detail.html", {
        "storefront": storefront,
        "product": storefront.product,
        "images": list(storefront.images.all()),
        "related": related,
        "public_pages": _public_nav_pages(),
    })


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
        product_id=product_id,
        is_online_active=True,
        product__is_active=True,
        product__is_sellable=True,
    ).exists():
        messages.error(request, "That product is no longer available.")
        return redirect("shop_cart")

    Cart(request).add(product_id, qty)
    if request.htmx:
        return render(request, "webstore/_cart_summary.html", {"cart": Cart(request)})
    messages.success(request, "Added to cart.")
    next_url = request.POST.get("next") or "shop_cart"
    return redirect(next_url if next_url.startswith("/") else next_url)


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


def checkout_view(request):
    cart = Cart(request)
    if not cart.lines:
        messages.info(request, "Your cart is empty.")
        return redirect("shop_cart")

    if request.method == "POST":
        form = CheckoutForm(request.POST)
        if form.is_valid():
            shipping_address = form.build_address()
            checkout = Checkout.objects.create(
                session_key=request.session.session_key or "",
                email=form.cleaned_data["email"],
                shipping_address=shipping_address,
                billing_address=shipping_address,
                cart_items=services.snapshot_cart_for_checkout(cart),
                subtotal=cart.subtotal,
                grand_total=cart.subtotal,
                currency=getattr(settings, "DEFAULT_CURRENCY", "USD"),
                status=Checkout.Status.PENDING,
            )
            if stripe_service.is_configured():
                try:
                    success_url = request.build_absolute_uri(
                        reverse("shop_checkout_success") + f"?token={checkout.token}"
                    )
                    cancel_url = request.build_absolute_uri(
                        reverse("shop_checkout_cancel") + f"?token={checkout.token}"
                    )
                    session = stripe_service.create_checkout_session(
                        product_name=f"Order #{checkout.token}",
                        unit_amount=int(checkout.grand_total * 100),
                        currency=checkout.currency.lower(),
                        quantity=1,
                        success_url=success_url,
                        cancel_url=cancel_url,
                        checkout_token=str(checkout.token),
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

            checkout.status = Checkout.Status.AWAITING_PAYMENT
            checkout.save()
            return redirect(reverse("shop_checkout_dev") + f"?token={checkout.token}")
    else:
        form = CheckoutForm()

    return render(request, "webstore/checkout.html", {
        "form": form,
        "cart": cart,
        "stripe_configured": stripe_service.is_configured(),
        "public_pages": _public_nav_pages(),
    })


def checkout_success(request):
    token = request.GET.get("token")
    checkout = get_object_or_404(Checkout, token=token) if token else None
    if checkout and checkout.status == Checkout.Status.PAID:
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


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Complete paid storefront checkouts from Stripe's signed event."""
    if not stripe_service.is_configured():
        return HttpResponse(status=503)
    try:
        event = stripe_service.verify_webhook_event(
            request.body,
            request.META.get("HTTP_STRIPE_SIGNATURE", ""),
        )
    except Exception as exc:
        log.warning("Stripe webhook signature verification failed: %s", exc)
        return HttpResponse(status=400)

    if event.get("type", "") != "checkout.session.completed":
        return HttpResponse(status=200)

    data = (event.get("data") or {}).get("object") or {}
    token = (data.get("metadata") or {}).get("checkout_token")
    checkout = Checkout.objects.filter(token=token).first() if token else None
    if not checkout:
        log.warning("Webhook did not identify an existing checkout.")
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


@require_POST
def checkout_dev_complete(request):
    """Manually complete checkout locally when Stripe is not configured."""
    if stripe_service.is_configured():
        return HttpResponse("Disabled while Stripe is configured.", status=403)
    token = request.POST.get("token") or request.GET.get("token")
    checkout = get_object_or_404(Checkout, token=token)
    if checkout.status not in {Checkout.Status.AWAITING_PAYMENT, Checkout.Status.PENDING}:
        return redirect(reverse("shop_checkout_success") + f"?token={checkout.token}")
    services.complete_checkout(checkout, stripe_payment_intent="dev-manual")
    return redirect(reverse("shop_checkout_success") + f"?token={checkout.token}")


def checkout_dev_landing(request):
    """Display the local-only completion screen when Stripe is unconfigured."""
    if stripe_service.is_configured():
        return redirect("shop_index")
    token = request.GET.get("token")
    checkout = get_object_or_404(Checkout, token=token)
    return render(request, "webstore/checkout_dev.html", {
        "checkout": checkout,
        "public_pages": _public_nav_pages(),
    })
