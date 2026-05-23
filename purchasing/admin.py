from django.contrib import admin
from .models import Bill, BillLine, Vendor


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
