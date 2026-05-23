from django.contrib import admin
from .models import (
    Bill, BillLine, GoodsReceipt, GoodsReceiptLine, PurchaseOrder,
    PurchaseOrderLine, Vendor,
)


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "payment_terms_days", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "email")


class BillLineInline(admin.TabularInline):
    model = BillLine
    extra = 0


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ("number", "vendor", "date", "due_date", "status")
    list_filter = ("status",)
    search_fields = ("number", "vendor__name", "vendor_ref")
    inlines = [BillLineInline]
    readonly_fields = ("number", "journal_entry", "posted_at", "posted_by", "created_by", "created_at")


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("number", "vendor", "date", "expected_date", "status")
    list_filter = ("status",)
    search_fields = ("number", "vendor__name")
    inlines = [PurchaseOrderLineInline]
    readonly_fields = ("number", "issued_at", "issued_by", "created_by", "created_at")


class GoodsReceiptLineInline(admin.TabularInline):
    model = GoodsReceiptLine
    extra = 0
    readonly_fields = ("stock_movement",)


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(admin.ModelAdmin):
    list_display = ("number", "purchase_order", "date", "posted_by", "posted_at")
    list_filter = ("date",)
    search_fields = ("number", "purchase_order__number", "purchase_order__vendor__name")
    inlines = [GoodsReceiptLineInline]
    readonly_fields = ("number", "posted_by", "posted_at")
