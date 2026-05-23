from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", include("core.urls")),
    path("", include("accounting.urls")),
    path("", include("sales.urls")),
    path("", include("inventory.urls")),
    path("", include("purchasing.urls")),
]
