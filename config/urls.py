"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.timezone import now
from django.urls import path, include

from clients.models import Client
from jobs.models import Job
from payments.views import stripe_webhook
from providers.models import Provider
from workers.models import Worker


def health_view(request):
    db_status = "up"
    try:
        connection.ensure_connection()
    except Exception:
        db_status = "down"

    return JsonResponse(
        {
            "status": "OK",
            "db": db_status,
            "timestamp": now().isoformat(),
        }
    )


def health_business_view(request):
    return JsonResponse(
        {
            "providers": Provider.objects.count(),
            "jobs": Job.objects.count(),
            "clients": Client.objects.count(),
            "workers": Worker.objects.count(),
            "timestamp": now().isoformat(),
            "status": "CORE_OK",
        }
    )


@staff_member_required
def dashboard_view(request):
    context = {
        "providers": Provider.objects.count(),
        "jobs": Job.objects.count(),
        "clients": Client.objects.count(),
        "workers": Worker.objects.count(),
    }
    return render(request, "dashboard/index.html", context)


health_business_view = staff_member_required(health_business_view)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health_view),
    path("dashboard/", dashboard_view),
    path("", include("ui.urls")),
    path("", include("jobs.urls")),
    path("settlements/", include("settlements.urls")),
    path("api/", include("providers.urls")),
    path("api/stripe/webhook/", stripe_webhook),
]

if settings.DEBUG:
    urlpatterns += [
        path("health/business/", health_business_view),
    ]

