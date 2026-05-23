from django.urls import path
from . import views

urlpatterns = [
    path("products/", views.product_list, name="product_list"),
    path("products/new/", views.product_edit, name="product_create"),
    path("products/<int:pk>/", views.product_detail, name="product_detail"),
    path("products/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("products/ledger/", views.stock_movement_list, name="stock_movement_list"),
    path("products/transfers/", views.stock_transfer_list, name="stock_transfer_list"),
]
