from django.http import JsonResponse
from django.views.decorators.http import require_GET

from providers.models import ServiceZone
from providers.services_marketplace import search_provider_services


@require_GET
def zone_list(request):
    province = request.GET.get("province")
    city = request.GET.get("city")

    if not province or not city:
        return JsonResponse([], safe=False)

    zones = list(
        ServiceZone.objects.filter(
            province=province,
            city=city,
        ).values("id", "name")
    )
    return JsonResponse(zones, safe=False)


@require_GET
def marketplace_search(request):
    try:
        service_category_id_raw = request.GET.get("service_category_id")
        if not service_category_id_raw:
            return JsonResponse(
                {"detail": "service_category_id is required"},
                status=400,
            )

        service_category_id = int(service_category_id_raw)
        province = request.GET.get("province")
        city = request.GET.get("city")
        zone_id_raw = request.GET.get("zone_id")
        limit = int(request.GET.get("limit", 20))
        offset = int(request.GET.get("offset", 0))
        debug = request.GET.get("debug") == "1"

        if not province or not city:
            return JsonResponse(
                {"detail": "province and city are required"},
                status=400,
            )

        zone_id = int(zone_id_raw) if zone_id_raw else None

        rows = list(
            search_provider_services(
                service_category_id=service_category_id,
                province=province,
                city=city,
                zone_id=zone_id,
                limit=limit,
                offset=offset,
            )
        )

        if debug:
            for row in rows:
                print(
                    "[marketplace_search]",
                    "provider_id=",
                    row.get("provider_id"),
                    "hybrid_score=",
                    row.get("hybrid_score"),
                    "cancellation_rate=",
                    row.get("cancellation_rate"),
                    "safe_completed=",
                    row.get("safe_completed"),
                    "safe_cancelled=",
                    row.get("safe_cancelled"),
                    "volume_score=",
                    row.get("volume_score"),
                    "verified_bonus=",
                    row.get("verified_bonus"),
                )

        data = [
            {
                "provider_id": row.get("provider_id"),
                "price_cents": row.get("price_cents"),
                "safe_rating": row.get("safe_rating"),
                "hybrid_score": row.get("hybrid_score"),
            }
            for row in rows
        ]

        return JsonResponse({"results": data})
    except (TypeError, ValueError) as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
