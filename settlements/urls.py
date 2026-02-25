from django.urls import path

from settlements.views import provider_financial_dashboard

urlpatterns = [
    path(
        "provider/<int:provider_id>/financial-dashboard/",
        provider_financial_dashboard,
        name="provider_financial_dashboard",
    ),
]
