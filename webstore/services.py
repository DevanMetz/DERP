"""Webstore business logic. Splits out so views stay thin."""
from __future__ import annotations

from datetime import date as date_cls
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounting.models import Account
from sales.models import Customer, Invoice, SalesOrder
from sales.services import (
    confirm_sales_order,
    create_invoice_from_sales_order,
    create_sales_order_lines,
    post_invoice,
    receive_payment,
    resolve_revenue_account,
)

from .cart import Cart
from .models import Checkout, ProductStorefront


ZERO = Decimal("0.00")


def snapshot_cart_for_checkout(cart: Cart) -> list[dict]:
    """Build the JSON cart snapshot saved on Checkout.cart_items."""
    items = []
    for line in cart.lines:
        items.append({
            "product_id": line.storefront.product_id,
            "storefront_id": line.storefront.pk,
            "sku": line.storefront.product.sku,
            "name": line.storefront.product.name,
            "qty": line.qty,
            "unit_price": str(line.unit_price),
            "line_total": str(line.line_total),
        })
    return items


def _get_or_create_customer(*, email: str, name: str, address) -> Customer:
    """Match by email (case-insensitive); otherwise create a new Customer."""
    qs = Customer.objects.filter(email__iexact=email)
    customer = qs.first()
    if customer:
        return customer
    return Customer.objects.create(
        name=name or email.split("@")[0],
        email=email,
        billing_address=address.one_line() if address else "",
        shipping_address=address.one_line() if address else "",
    )


def _cash_account() -> Account:
    code = getattr(settings, "WEBSTORE_CASH_ACCOUNT_CODE", "1010")
    try:
        return Account.objects.get(code=code, type="asset", is_postable=True)
    except Account.DoesNotExist:
        # Fall back to the first postable asset account.
        acc = Account.objects.filter(type="asset", is_postable=True).order_by("code").first()
        if not acc:
            raise RuntimeError(
                "No postable asset account is available for webstore deposits. "
                "Seed the chart of accounts or set WEBSTORE_CASH_ACCOUNT_CODE."
            )
        return acc


@transaction.atomic
def complete_checkout(checkout: Checkout, *, stripe_payment_intent: str = "") -> Checkout:
    """Convert a paid Stripe checkout into a SalesOrder + posted Invoice + Payment.

    Idempotent: re-running with the same checkout returns it unchanged.
    """
    if checkout.status == Checkout.Status.PAID:
        return checkout

    if not checkout.cart_items:
        raise ValueError("Checkout has no line items.")

    customer = _get_or_create_customer(
        email=checkout.email,
        name=(checkout.shipping_address.full_name if checkout.shipping_address else checkout.email),
        address=checkout.shipping_address,
    )

    # Build cleaned line dicts in the shape sales.services expects.
    cleaned_lines = []
    pids = [int(i["product_id"]) for i in checkout.cart_items]
    storefronts = {
        sf.product_id: sf for sf in
        ProductStorefront.objects.filter(product_id__in=pids).select_related("product")
    }
    for item in checkout.cart_items:
        sf = storefronts.get(int(item["product_id"]))
        if not sf:
            raise ValueError(f"Product {item['product_id']} from cart is no longer present.")
        revenue_acc = resolve_revenue_account(product=sf.product, customer=customer)
        cleaned_lines.append({
            "product": sf.product,
            "description": sf.product.name,
            "qty": Decimal(str(item["qty"])),
            "unit_price": Decimal(str(item["unit_price"])),
            "revenue_account": revenue_acc,
        })

    today = timezone.localdate()
    order = SalesOrder.objects.create(
        customer=customer,
        date=today,
        status=SalesOrder.Status.DRAFT,
        notes=f"Webstore order — Checkout {checkout.token}",
    )
    create_sales_order_lines(order, cleaned_lines)
    confirm_sales_order(order)
    invoice = create_invoice_from_sales_order(order)
    post_invoice(invoice)

    receive_payment(
        customer=customer,
        date=today,
        amount=Decimal(str(invoice.total())),
        cash_account=_cash_account(),
        method="card",
        reference=stripe_payment_intent or checkout.stripe_payment_intent or f"stripe:{checkout.stripe_session_id}",
        applications=[(invoice, Decimal(str(invoice.total())))],
        notes=f"Stripe checkout {checkout.token}",
    )

    checkout.status = Checkout.Status.PAID
    checkout.paid_at = timezone.now()
    checkout.sales_order = order
    if stripe_payment_intent:
        checkout.stripe_payment_intent = stripe_payment_intent
    checkout.customer = customer
    checkout.save()
    return checkout
