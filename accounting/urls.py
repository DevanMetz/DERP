from django.urls import path
from . import views

urlpatterns = [
    path("journal/", views.journal_list, name="journal_list"),
    path("journal/new/", views.journal_create, name="journal_create"),
    path("journal/<int:pk>/", views.journal_detail, name="journal_detail"),
    path("reports/trial-balance/", views.trial_balance_view, name="trial_balance"),
    path("reports/general-ledger/", views.general_ledger_view, name="general_ledger"),
]
