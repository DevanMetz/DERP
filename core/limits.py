"""
Per-tenant row caps to prevent abuse (e.g. someone dumping millions of journal entries).

Limits are enforced via pre_save signals. Since django-tenants scopes queries to the
active schema, sender.objects.count() returns the current tenant's row count.
"""
from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save


TENANT_ROW_LIMITS = {
    "accounting.JournalEntry": 50_000,
    "accounting.JournalLine": 200_000,
    "accounting.Payment": 50_000,
    "accounting.PaymentApplication": 100_000,
    "sales.Customer": 10_000,
    "sales.SalesOrder": 50_000,
    "sales.SalesOrderLine": 200_000,
    "sales.Invoice": 50_000,
    "sales.InvoiceLine": 200_000,
    "purchasing.Vendor": 10_000,
    "purchasing.PurchaseOrder": 50_000,
    "purchasing.PurchaseOrderLine": 200_000,
    "purchasing.GoodsReceipt": 50_000,
    "purchasing.GoodsReceiptLine": 200_000,
    "purchasing.Bill": 50_000,
    "purchasing.BillLine": 200_000,
    "inventory.Product": 50_000,
    "inventory.StockMovement": 500_000,
    "manufacturing.BillOfMaterials": 10_000,
    "manufacturing.BOMComponent": 100_000,
    "manufacturing.ManufacturingOrder": 50_000,
}


WRITE_RATE_PER_MINUTE = 100


def _enforce_limit(sender, instance, **kwargs):
    if instance.pk is not None:
        return  # only check on create, not update

    key = f"{sender._meta.app_label}.{sender.__name__}"
    limit = TENANT_ROW_LIMITS.get(key)
    if limit is None:
        return

    # Per-user / per-IP write rate limit
    from .middleware import get_current_request
    from .models import WriteAttempt

    request = get_current_request()
    if request is not None:
        ip = request.META.get(
            "HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")
        ).split(",")[0].strip() or None
        user = getattr(request, "user", None)
        if WriteAttempt.is_limited(user=user, ip=ip, max_per_minute=WRITE_RATE_PER_MINUTE):
            raise ValidationError(
                f"Too many writes per minute. Please slow down "
                f"(limit: {WRITE_RATE_PER_MINUTE}/min)."
            )
        WriteAttempt.record(user=user, ip=ip)
        # Probabilistic prune to keep the rate-limit table small
        import random
        if random.random() < 0.01:
            WriteAttempt.prune()

    # Per-tenant hard cap
    if sender.objects.count() >= limit:
        raise ValidationError(
            f"This workspace has reached its limit of {limit:,} "
            f"{sender._meta.verbose_name_plural}. "
            "Contact support@inventorymanager.xyz to raise the cap."
        )


def install_limits():
    from django.apps import apps
    for key in TENANT_ROW_LIMITS:
        app_label, model_name = key.split(".")
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            continue
        pre_save.connect(
            _enforce_limit, sender=model, weak=False,
            dispatch_uid=f"tenant_row_limit_{key}",
        )
