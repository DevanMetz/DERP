from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("company/", views.company_setup, name="company_setup"),
]
