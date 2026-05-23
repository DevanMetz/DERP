from django.contrib import admin
from .models import Customer, Invoice, InvoiceLine


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "tax_rate", "payment_terms_days", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "email")


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "customer", "date", "due_date", "status")
    list_filter = ("status",)
    search_fields = ("number", "customer__name")
    inlines = [InvoiceLineInline]
    readonly_fields = ("number", "journal_entry", "posted_at", "posted_by", "created_by", "created_at")
