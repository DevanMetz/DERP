from django.urls import path
from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("features/", views.features, name="features"),
    path("signup/", views.signup, name="tenant_signup"),
    path("signup/confirm/<uuid:token>/", views.confirm, name="tenant_confirm"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap_xml"),
]
