from .cart import Cart


def cart_summary(request):
    """Expose `cart_count` and `cart_subtotal` to all templates so the
    header badge stays in sync without each view having to pass it.
    """
    if not hasattr(request, "session"):
        return {}
    cart = Cart(request)
    return {
        "cart_count": cart.item_count,
        "cart_subtotal": cart.subtotal,
    }
