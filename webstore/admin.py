from django.contrib import admin

from .models import Address, Category, Checkout, ProductImage, ProductStorefront


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "parent", "sort_order", "is_active")
    list_filter = ("is_active", "parent")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ProductStorefront)
class ProductStorefrontAdmin(admin.ModelAdmin):
    list_display = (
        "product", "slug", "category",
        "online_price", "compare_at_price",
        "is_online_active", "is_featured", "sort_order",
    )
    list_filter = ("is_online_active", "is_featured", "category")
    search_fields = ("product__sku", "product__name", "slug")
    autocomplete_fields = ("product", "category")
    inlines = [ProductImageInline]


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("full_name", "city", "region", "postal_code", "country")
    search_fields = ("full_name", "city", "postal_code", "company")


@admin.register(Checkout)
class CheckoutAdmin(admin.ModelAdmin):
    list_display = (
        "token", "email", "status", "grand_total", "currency",
        "sales_order", "created_at", "paid_at",
    )
    list_filter = ("status", "currency", "created_at")
    search_fields = ("token", "email", "stripe_session_id", "stripe_payment_intent")
    readonly_fields = (
        "token", "session_key", "stripe_session_id", "stripe_payment_intent",
        "cart_items", "subtotal", "shipping_total", "tax_total", "grand_total",
        "created_at", "updated_at", "paid_at", "sales_order",
    )
    fieldsets = (
        ("Identity", {"fields": ("token", "status", "session_key", "customer", "email")}),
        ("Addresses", {"fields": ("shipping_address", "billing_address")}),
        ("Cart & Totals", {"fields": ("cart_items", "subtotal", "shipping_total", "tax_total", "grand_total", "currency")}),
        ("Stripe", {"fields": ("stripe_session_id", "stripe_payment_intent")}),
        ("Result", {"fields": ("sales_order", "notes")}),
        ("Timestamps", {"fields": ("created_at", "updated_at", "paid_at")}),
    )
