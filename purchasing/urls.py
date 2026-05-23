from django.urls import path
from . import views

urlpatterns = [
    path("vendors/", views.vendor_list, name="vendor_list"),
    path("vendors/new/", views.vendor_edit, name="vendor_create"),
    path("vendors/<int:pk>/edit/", views.vendor_edit, name="vendor_edit"),
    path("bills/", views.bill_list, name="bill_list"),
    path("bills/new/", views.bill_create, name="bill_create"),
    path("bills/<int:pk>/", views.bill_detail, name="bill_detail"),
    path("bills/<int:pk>/post/", views.bill_post, name="bill_post"),
    path("vendor-payments/new/", views.vendor_payment_create, name="vendor_payment_create"),
]
