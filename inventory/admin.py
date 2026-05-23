from django.contrib import admin
from .models import Product, StockMovement, StockOnHand, Location, LocationStock


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "type", "uom", "price", "cost", "low_stock_threshold", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("sku", "name")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("product", "movement_type", "qty", "unit_cost", "ref_doc_type", "ref_doc_id", "posted_at")
    list_filter = ("movement_type", "posted_at")
    search_fields = ("product__sku", "product__name", "ref_doc_type", "memo")
    readonly_fields = ("posted_at", "created_by")


@admin.register(StockOnHand)
class StockOnHandAdmin(admin.ModelAdmin):
    list_display = ("product", "qty", "updated_at")
    search_fields = ("product__sku", "product__name")
    readonly_fields = ("updated_at",)


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "description")


@admin.register(LocationStock)
class LocationStockAdmin(admin.ModelAdmin):
    list_display = ("product", "location", "qty", "updated_at")
    list_filter = ("location",)
    search_fields = ("product__sku", "product__name", "location__name")
    readonly_fields = ("updated_at",)
