from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("company/", views.company_setup, name="company_setup"),
    path("export/", views.export_view, name="data_export"),
    path("import/", views.import_view, name="data_import"),
    path("search/", views.search_view, name="search"),
]
