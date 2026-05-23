from django.urls import path
from . import views

urlpatterns = [
    path("customers/", views.customer_list, name="customer_list"),
    path("customers/new/", views.customer_edit, name="customer_create"),
    path("customers/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("customers/<int:pk>/edit/", views.customer_edit, name="customer_edit"),
    path("sales-orders/", views.sales_order_list, name="sales_order_list"),
    path("sales-orders/new/", views.sales_order_create, name="sales_order_create"),
    path("sales-orders/<int:pk>/", views.sales_order_detail, name="sales_order_detail"),
    path("sales-orders/<int:pk>/pdf/", views.sales_order_pdf, name="sales_order_pdf"),
    path("sales-orders/<int:pk>/confirm/", views.sales_order_confirm, name="sales_order_confirm"),
    path("sales-orders/<int:pk>/unconfirm/", views.sales_order_unconfirm, name="sales_order_unconfirm"),
    path("sales-orders/<int:pk>/invoice/", views.sales_order_invoice, name="sales_order_invoice"),
    path("sales-orders/<int:pk>/undo-invoice/", views.sales_order_undo_invoice, name="sales_order_undo_invoice"),
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/new/", views.invoice_create, name="invoice_create"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("invoices/<int:pk>/post/", views.invoice_post, name="invoice_post"),
    path("invoices/<int:pk>/void/", views.invoice_void, name="invoice_void"),
    path("payments/new/", views.payment_create, name="payment_create"),
]
