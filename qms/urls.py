from django.urls import path
from . import views

urlpatterns = [
    # Dashboard
    path("qms/", views.qms_dashboard, name="qms_dashboard"),
    
    # Templates
    path("qms/templates/", views.template_list, name="qms_template_list"),
    path("qms/templates/new/", views.template_edit, name="qms_template_create"),
    path("qms/templates/<int:pk>/", views.template_detail, name="qms_template_detail"),
    path("qms/templates/<int:pk>/edit/", views.template_edit, name="qms_template_edit"),
    path("qms/templates/<int:template_pk>/fields/new/", views.field_edit, name="qms_field_create"),
    path("qms/templates/<int:template_pk>/fields/<int:pk>/edit/", views.field_edit, name="qms_field_edit"),
    path("qms/templates/<int:template_pk>/fields/<int:pk>/delete/", views.field_delete, name="qms_field_delete"),
    
    # Inspections
    path("qms/inspections/", views.inspection_list, name="qms_inspection_list"),
    path("qms/inspections/<int:pk>/", views.inspection_detail, name="qms_inspection_detail"),
    path("qms/inspections/<int:pk>/complete/", views.inspection_complete, name="qms_inspection_complete"),
    
    # Non-Conformances (NCR)
    path("qms/ncrs/", views.ncr_list, name="qms_ncr_list"),
    path("qms/ncrs/new/", views.ncr_edit, name="qms_ncr_create"),
    path("qms/ncrs/<int:pk>/", views.ncr_detail, name="qms_ncr_detail"),
    path("qms/ncrs/<int:pk>/edit/", views.ncr_edit, name="qms_ncr_edit"),
    
    # CAPA
    path("qms/capas/", views.capa_list, name="qms_capa_list"),
    path("qms/capas/new/", views.capa_edit, name="qms_capa_create"),
    path("qms/capas/<int:pk>/", views.capa_detail, name="qms_capa_detail"),
    path("qms/capas/<int:pk>/edit/", views.capa_edit, name="qms_capa_edit"),
    
    # Manual Quarantine Action
    path("qms/quarantine/", views.quarantine_action, name="qms_quarantine_action"),
]
