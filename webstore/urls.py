from django.urls import path

from . import views

urlpatterns = [
    # --- Real storefront (cart, ProductStorefront-backed) -------------
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

    # --- One platform-wide Stripe webhook destination -----------------
    # All thin events for all connected accounts land here.
    path("shop/webhooks/stripe/", views.stripe_webhook, name="shop_stripe_webhook"),

    # --- Stripe Connect (V2 Accounts) admin -------------------------
    path("derp/stripe/onboard/", views.stripe_onboard, name="stripe_onboard"),
    path("derp/stripe/return/", views.stripe_onboarding_return, name="stripe_onboarding_return"),
    path("derp/stripe/disconnect/", views.stripe_disconnect, name="stripe_disconnect"),

    # --- Stripe sample storefront (per the integration spec) -------
    # `sample_product_create` is the admin-side "create product on
    # connected account" form. The public storefront is at
    # `/sample/store/<account_id>/`.
    path(
        "derp/stripe/sample/products/new/",
        views.sample_product_create,
        name="stripe_sample_product_create",
    ),
    # Using the raw acct_… ID in the public URL keeps this sample
    # self-contained. In production, swap for a stable tenant slug
    # or UUID so the URL isn't tied to a specific Stripe account.
    path(
        "sample/store/<str:account_id>/",
        views.sample_storefront,
        name="stripe_sample_storefront",
    ),
    path(
        "sample/store/<str:account_id>/checkout/<str:product_id>/",
        views.sample_storefront_checkout,
        name="stripe_sample_storefront_checkout",
    ),
    path(
        "sample/store/<str:account_id>/success/",
        views.sample_storefront_success,
        name="stripe_sample_storefront_success",
    ),
]
