from django.contrib import admin
from .models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "type", "uom", "price", "cost", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("sku", "name")
