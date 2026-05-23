from django.urls import path
from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("signup/", views.signup, name="tenant_signup"),
    path("signup/confirm/<uuid:token>/", views.confirm, name="tenant_confirm"),
]
