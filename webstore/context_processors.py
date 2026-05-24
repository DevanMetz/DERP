from django.db import connection
from django.db.utils import DatabaseError

from .cart import Cart


def cart_summary(request):
    """Expose `cart_count` and `cart_subtotal` to all templates so the
    header badge stays in sync without each view having to pass it.

    Webstore tables only live in tenant schemas (the app is in TENANT_APPS).
    On the public schema — the platform's landing/signup pages — the tables
    don't exist, so we short-circuit before any DB access. The try/except
    is a belt-and-suspenders defense for edge cases like a tenant with the
    webstore migration not yet applied.
    """
    if not hasattr(request, "session"):
        return {}
    tenant = getattr(connection, "tenant", None)
    if tenant is None or getattr(tenant, "schema_name", "public") == "public":
        return {"cart_count": 0, "cart_subtotal": 0}
    try:
        cart = Cart(request)
        return {
            "cart_count": cart.item_count,
            "cart_subtotal": cart.subtotal,
        }
    except DatabaseError:
        return {"cart_count": 0, "cart_subtotal": 0}
