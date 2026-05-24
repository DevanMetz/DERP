from django.urls import path

from . import views

urlpatterns = [
    path("shop/", views.catalog, name="shop_index"),
    path("shop/c/<slug:slug>/", views.category_detail, name="shop_category"),
    path("shop/p/<slug:slug>/", views.product_detail, name="shop_product"),

    path("shop/cart/", views.cart_view, name="shop_cart"),
    path("shop/cart/add/<int:product_id>/", views.cart_add, name="shop_cart_add"),
    path("shop/cart/update/<int:product_id>/", views.cart_update, name="shop_cart_update"),
    path("shop/cart/remove/<int:product_id>/", views.cart_remove, name="shop_cart_remove"),
    path("shop/cart/clear/", views.cart_clear, name="shop_cart_clear"),

    path("shop/checkout/", views.checkout_view, name="shop_checkout"),
    path("shop/checkout/success/", views.checkout_success, name="shop_checkout_success"),
    path("shop/checkout/cancel/", views.checkout_cancel, name="shop_checkout_cancel"),
    path("shop/checkout/dev/", views.checkout_dev_landing, name="shop_checkout_dev"),
    path("shop/checkout/dev/complete/", views.checkout_dev_complete, name="shop_checkout_dev_complete"),
    path("shop/webhooks/stripe/", views.stripe_webhook, name="shop_stripe_webhook"),

    # Stripe Connect (admin-only OAuth)
    path("derp/stripe/connect/", views.stripe_connect_start, name="stripe_connect_start"),
    path("derp/stripe/callback/", views.stripe_connect_callback, name="stripe_connect_callback"),
    path("derp/stripe/disconnect/", views.stripe_disconnect, name="stripe_disconnect"),
    path("derp/stripe/webhook-secret/", views.stripe_save_webhook_secret, name="stripe_save_webhook_secret"),
]
