from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from .models import Job
from providers.models import Provider


def match_providers(request, job_id: int):
    job = get_object_or_404(Job, pk=job_id)

    # Versión 1 (simple): traer todos los providers activos y disponibles
    qs = Provider.objects.filter(is_active=True)

    # Si tu Provider tiene is_available_now, úsalo también
    if hasattr(Provider, "is_available_now"):
        qs = qs.filter(is_available_now=True)

    data = {
        "job_id": job.job_id,
        "job_status": job.job_status,
        "service_type_id": job.service_type_id,
        "city": job.city,
        "providers_found": qs.count(),
        "providers": [
            {
                "provider_id": p.provider_id,
                "company_name": getattr(p, "company_name", None),
                "email": getattr(p, "email", None),
            }
            for p in qs[:20]
        ],
    }
    return JsonResponse(data)
from django.views.decorators.http import require_http_methods

@require_http_methods(["POST", "GET"])
def assign_provider(request, job_id: int, provider_id: int):
    job = get_object_or_404(Job, pk=job_id)
    provider = get_object_or_404(Provider, pk=provider_id)

    # Asignación (versión 1 simple)
    job.selected_provider = provider
    job.job_status = "assigned"
    job.save(update_fields=["selected_provider", "job_status", "updated_at"])

    return JsonResponse({
        "ok": True,
        "job_id": job.job_id,
        "job_status": job.job_status,
        "selected_provider_id": job.selected_provider_id,
    })
