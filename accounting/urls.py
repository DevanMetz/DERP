from django.urls import path
from . import views

urlpatterns = [
    path("journal/", views.journal_list, name="journal_list"),
    path("journal/new/", views.journal_create, name="journal_create"),
    path("journal/<int:pk>/", views.journal_detail, name="journal_detail"),
    path("journal/<int:pk>/reverse/", views.journal_reverse, name="journal_reverse"),
    path("reports/trial-balance/", views.trial_balance_view, name="trial_balance"),
    path("reports/income-statement/", views.income_statement_view, name="income_statement"),
    path("reports/balance-sheet/", views.balance_sheet_view, name="balance_sheet"),
    path("reports/general-ledger/", views.general_ledger_view, name="general_ledger"),
]
