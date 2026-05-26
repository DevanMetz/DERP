from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("company/", views.company_setup, name="company_setup"),
    path("ai/chat/", views.ai_chat, name="ai_chat"),
    path("ai/confirm/", views.ai_confirm, name="ai_confirm"),
    path("agents/", views.agent_hub, name="agent_hub"),
    path("agents/new/", views.agent_routine_create, name="agent_routine_create"),
    path("agents/<int:pk>/edit/", views.agent_routine_edit, name="agent_routine_edit"),
    path("agents/<int:pk>/toggle/", views.agent_routine_toggle, name="agent_routine_toggle"),
    path("agents/<int:pk>/delete/", views.agent_routine_delete, name="agent_routine_delete"),
    path("docs/", views.docs_index, name="docs_index"),
    path("docs/<slug:slug>/", views.docs_page, name="docs_page"),
    path("export/", views.export_view, name="data_export"),
    path("import/", views.import_view, name="data_import"),
    path("search/", views.search_view, name="search"),
    path("users/", views.user_list, name="user_list"),
    path("users/add/", views.user_create, name="user_create"),
    path("users/<int:pk>/edit/", views.user_edit, name="user_edit"),
    path("website/", views.website_editor, name="website_editor"),
    path("website/settings/", views.website_settings_view, name="website_settings"),
    path("website/add/", views.page_create, name="page_create"),
    path("website/<int:pk>/edit/", views.page_edit, name="page_edit"),
    path("website/<int:pk>/delete/", views.page_delete, name="page_delete"),
]
