import json

from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods

from providers.models import Provider

from .models import ApiIdempotencyKey, Job
from .services import confirm_service_closed_by_client, start_service_by_provider
from .services_extras import add_extra_line_for_job


def _json(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def match_providers(request, job_id: int):
    job = get_object_or_404(Job, pk=job_id)

    qs = Provider.objects.filter(is_active=True)
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


@require_http_methods(["POST", "GET"])
def assign_provider(request, job_id: int, provider_id: int):
    job = get_object_or_404(Job, pk=job_id)
    provider = get_object_or_404(Provider, pk=provider_id)

    job.selected_provider = provider
    job.job_status = "assigned"
    job.save(update_fields=["selected_provider", "job_status", "updated_at"])

    return JsonResponse(
        {
            "ok": True,
            "job_id": job.job_id,
            "job_status": job.job_status,
            "selected_provider_id": job.selected_provider_id,
        }
    )


@csrf_exempt
@require_POST
def api_job_start(request, job_id: int):
    data = _json(request)
    if data is None:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    provider_id = data.get("provider_id")
    if not provider_id:
        return JsonResponse({"ok": False, "error": "provider_id_required"}, status=400)

    try:
        result = start_service_by_provider(job_id=job_id, provider_id=int(provider_id))
        return JsonResponse({"ok": True, "result": result}, safe=False)
    except PermissionError:
        return JsonResponse({"ok": False, "error": "provider_not_allowed"}, status=403)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


@csrf_exempt
@require_POST
def api_job_close(request, job_id: int):
    data = _json(request)
    if data is None:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    client_id = data.get("client_id")
    if not client_id:
        return JsonResponse({"ok": False, "error": "client_id_required"}, status=400)

    try:
        result = confirm_service_closed_by_client(job_id=job_id, client_id=int(client_id))
        return JsonResponse({"ok": True, "result": result}, safe=False)
    except PermissionError:
        return JsonResponse({"ok": False, "error": "client_not_allowed"}, status=403)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


@csrf_exempt
@require_POST
def api_job_add_extra(request, job_id: int):
    data = _json(request)
    if data is None:
        return JsonResponse({"error": "bad_request"}, status=400)

    provider_id = int(data.get("provider_id") or 0)
    description = (data.get("description") or "").strip()
    amount_cents = int(data.get("amount_cents") or 0)

    if not provider_id or not description or amount_cents <= 0:
        return JsonResponse({"error": "bad_request"}, status=400)

    idem = request.headers.get("Idempotency-Key", "").strip()
    if idem:
        existing = ApiIdempotencyKey.objects.filter(key=idem).first()
        if existing:
            return JsonResponse(existing.response_json, status=200)

    try:
        resp = add_extra_line_for_job(
            job_id=job_id,
            provider_id=provider_id,
            description=description,
            amount_cents=amount_cents,
        )
    except PermissionError as e:
        return JsonResponse({"error": str(e)}, status=403)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

    if idem:
        try:
            ApiIdempotencyKey.objects.create(key=idem, response_json=resp)
        except IntegrityError:
            existing = ApiIdempotencyKey.objects.filter(key=idem).first()
            if existing:
                return JsonResponse(existing.response_json, status=200)

    return JsonResponse(resp, status=200)
