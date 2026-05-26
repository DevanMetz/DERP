from .cart import Cart


def cart_summary(request):
    """Expose cart totals to templates for this installation's storefront."""
    if not hasattr(request, "session"):
        return {}
    cart = Cart(request)
    return {
        "cart_count": cart.item_count,
        "cart_subtotal": cart.subtotal,
    }
