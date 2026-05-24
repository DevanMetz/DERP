from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),

    # Public tenant website on the root domain
    path("", core_views.public_home, name="public_home"),
    path("p/<slug:slug>/", core_views.public_page, name="public_page"),

    # Public storefront
    path("", include("webstore.urls")),

    # Administrative ERP modules prefixed with /derp/
    path("derp/", include("core.urls")),
    path("derp/", include("accounting.urls")),
    path("derp/", include("sales.urls")),
    path("derp/", include("inventory.urls")),
    path("derp/", include("purchasing.urls")),
    path("derp/", include("manufacturing.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
