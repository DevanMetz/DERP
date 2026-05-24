from decimal import Decimal
from uuid import uuid4

from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.text import slugify
from simple_history.models import HistoricalRecords


ZERO = Decimal("0.00")


class Category(models.Model):
    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    image = models.ImageField(
        upload_to="webstore/categories/",
        null=True, blank=True,
        validators=[FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "gif", "webp"])],
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="children",
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:80]
        super().save(*args, **kwargs)


class ProductStorefront(models.Model):
    """Storefront-facing layer over inventory.Product.

    Keeps marketing/online concerns (slug, long copy, gallery, online price)
    separate from the operational product master so internal SKU edits don't
    rewrite the public catalog by accident.
    """
    product = models.OneToOneField(
        "inventory.Product", on_delete=models.CASCADE,
        related_name="storefront",
        limit_choices_to={"is_sellable": True},
    )
    slug = models.SlugField(max_length=120, unique=True)
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="products",
    )
    short_tagline = models.CharField(
        max_length=160, blank=True,
        help_text="One-liner shown under the title on cards and the PDP.",
    )
    online_description = models.TextField(
        blank=True,
        help_text="Marketing copy (HTML allowed). Separate from the internal product description.",
    )
    online_price = models.DecimalField(
        max_digits=14, decimal_places=2, default=ZERO,
        help_text="Price charged on the storefront. Leave 0 to fall back to Product.price.",
    )
    compare_at_price = models.DecimalField(
        max_digits=14, decimal_places=2, default=ZERO,
        help_text="Strike-through price for showing a discount (e.g. $40 crossed out). Set 0 to hide.",
    )
    is_online_active = models.BooleanField(
        default=True,
        help_text="If unchecked, the product is hidden from the storefront catalog.",
    )
    is_featured = models.BooleanField(
        default=False,
        help_text="Highlighted in Featured Product blocks and 'featured' product grids.",
    )
    sort_order = models.PositiveIntegerField(default=0)
    seo_title = models.CharField(max_length=160, blank=True)
    seo_description = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["sort_order", "product__name"]

    def __str__(self):
        return f"{self.product.name} (online)"

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.product.name)[:115] or f"product-{self.product_id}"
            self.slug = base
        super().save(*args, **kwargs)

    @property
    def effective_price(self) -> Decimal:
        return self.online_price if self.online_price > 0 else self.product.price

    @property
    def is_on_sale(self) -> bool:
        return self.compare_at_price > 0 and self.compare_at_price > self.effective_price


class ProductImage(models.Model):
    storefront = models.ForeignKey(
        ProductStorefront, on_delete=models.CASCADE, related_name="images",
    )
    image = models.ImageField(
        upload_to="webstore/products/",
        validators=[
            FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "gif", "webp"]),
        ],
    )
    alt_text = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"Image for {self.storefront.product.name}"


class Address(models.Model):
    """Snapshot address used at checkout. Not tied to Customer so guest
    checkouts and edits don't mutate historical orders."""
    full_name = models.CharField(max_length=160)
    company = models.CharField(max_length=160, blank=True)
    line1 = models.CharField(max_length=200)
    line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=120)
    region = models.CharField(max_length=120, help_text="State / Province")
    postal_code = models.CharField(max_length=24)
    country = models.CharField(max_length=2, default="US", help_text="ISO 3166-1 alpha-2")
    phone = models.CharField(max_length=40, blank=True)

    def __str__(self):
        return f"{self.full_name}, {self.city} {self.region} {self.postal_code}"

    def one_line(self) -> str:
        bits = [self.line1, self.line2, f"{self.city}, {self.region} {self.postal_code}", self.country]
        return ", ".join(b for b in bits if b)


class Checkout(models.Model):
    """A pending checkout. Becomes a SalesOrder + Invoice on payment success.

    The cart snapshot is stored as JSON so the order can be reconstructed
    server-side, independent of session state.
    """
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        AWAITING_PAYMENT = "awaiting_payment", "Awaiting Payment"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    token = models.UUIDField(default=uuid4, unique=True, editable=False)
    session_key = models.CharField(max_length=64, blank=True, db_index=True)
    customer = models.ForeignKey(
        "sales.Customer", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="webstore_checkouts",
    )
    email = models.EmailField()
    shipping_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="shipping_checkouts",
    )
    billing_address = models.ForeignKey(
        Address, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="billing_checkouts",
    )

    # Cart snapshot. List of {product_id, sku, name, qty, unit_price, line_total}
    cart_items = models.JSONField(default=list)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    shipping_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    currency = models.CharField(max_length=3, default="USD")

    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    stripe_session_id = models.CharField(max_length=200, blank=True, db_index=True)
    stripe_payment_intent = models.CharField(max_length=200, blank=True)

    sales_order = models.ForeignKey(
        "sales.SalesOrder", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="webstore_checkouts",
    )
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"Checkout {self.token} ({self.status})"
