from django.urls import path
from . import views

urlpatterns = [
    path("vendors/", views.vendor_list, name="vendor_list"),
    path("vendors/new/", views.vendor_edit, name="vendor_create"),
    path("vendors/<int:pk>/edit/", views.vendor_edit, name="vendor_edit"),
    path("purchase-orders/", views.purchase_order_list, name="purchase_order_list"),
    path("purchase-orders/new/", views.purchase_order_create, name="purchase_order_create"),
    path("purchase-orders/<int:pk>/", views.purchase_order_detail, name="purchase_order_detail"),
    path("purchase-orders/<int:pk>/pdf/", views.purchase_order_pdf, name="purchase_order_pdf"),
    path("purchase-orders/<int:pk>/issue/", views.purchase_order_issue, name="purchase_order_issue"),
    path("purchase-orders/<int:pk>/unissue/", views.purchase_order_unissue, name="purchase_order_unissue"),
    path("purchase-orders/<int:pk>/receive/", views.purchase_order_receive, name="purchase_order_receive"),
    path("purchase-orders/<int:pk>/bill/", views.purchase_order_bill, name="purchase_order_bill"),
    path("purchase-orders/<int:pk>/undo-bill/", views.purchase_order_undo_bill, name="purchase_order_undo_bill"),
    path("goods-receipts/<int:pk>/reverse/", views.goods_receipt_reverse, name="goods_receipt_reverse"),
    path("goods-receipts/<int:pk>/", views.goods_receipt_detail, name="goods_receipt_detail"),
    path("goods-receipts/<int:pk>/bill/", views.bill_create_from_receipt, name="bill_create_from_receipt"),
    path("bills/", views.bill_list, name="bill_list"),
    path("bills/new/", views.bill_create, name="bill_create"),
    path("bills/<int:pk>/", views.bill_detail, name="bill_detail"),
    path("bills/<int:pk>/post/", views.bill_post, name="bill_post"),
    path("bills/<int:pk>/void/", views.bill_void, name="bill_void"),
    path("vendor-payments/new/", views.vendor_payment_create, name="vendor_payment_create"),
]
