from django.urls import path
from . import views

urlpatterns = [
    # Bill of Materials
    path("boms/", views.bom_list, name="bom_list"),
    path("boms/create/", views.bom_create, name="bom_create"),
    path("boms/<int:pk>/", views.bom_detail, name="bom_detail"),
    path("boms/<int:pk>/edit/", views.bom_edit, name="bom_edit"),

    # Manufacturing Orders
    path("manufacturing-orders/", views.mo_list, name="mo_list"),
    path("manufacturing-orders/create/", views.mo_create, name="mo_create"),
    path("manufacturing-orders/<int:pk>/", views.mo_detail, name="mo_detail"),
    path("manufacturing-orders/<int:pk>/confirm/", views.mo_confirm, name="mo_confirm"),
    path("manufacturing-orders/<int:pk>/complete/", views.mo_complete, name="mo_complete"),
    path("manufacturing-orders/<int:pk>/cancel/", views.mo_cancel, name="mo_cancel"),
]
