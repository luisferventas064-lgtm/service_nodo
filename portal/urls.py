from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.home, name="home"),
    path("client/dashboard/", views.client_dashboard_alias, name="client_dashboard"),
    path("provider/dashboard/", views.provider_dashboard_view, name="provider_dashboard"),
    path("provider/services/", views.provider_services_view, name="provider_services"),
    path(
        "provider/services/<int:service_id>/edit/",
        views.provider_service_edit_view,
        name="provider_service_edit",
    ),
    path(
        "provider/services/<int:service_id>/toggle/",
        views.provider_service_toggle_view,
        name="provider_service_toggle",
    ),
    path(
        "provider/services/categories/",
        views.provider_service_categories_view,
        name="provider_service_categories",
    ),
    path(
        "provider/services/add/<int:service_type_id>/",
        views.provider_service_add_view,
        name="provider_service_add",
    ),
    path("worker/dashboard/", views.worker_dashboard_alias, name="worker_dashboard"),
    path("internal/", views.internal, name="internal"),
]
