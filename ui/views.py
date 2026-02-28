from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from clients.models import ClientTicket
from jobs import services as job_services
from jobs.models import Job, PlatformLedgerEntry
from providers.models import ProviderTicket
from providers.models import ServiceZone
from providers.services_analytics import (
    marketplace_analytics_snapshot,
    marketplace_analytics_to_csv,
)
from providers.services_marketplace import search_provider_services


@staff_member_required
def home(request):
    now = timezone.now()
    today = now.date()
    week_start = today - timedelta(days=today.weekday())

    open_jobs = Job.objects.exclude(
        job_status__in=[
            Job.JobStatus.CONFIRMED,
            Job.JobStatus.CANCELLED,
            Job.JobStatus.EXPIRED,
        ]
    ).count()

    closed_today = Job.objects.filter(
        job_status=Job.JobStatus.CONFIRMED,
        updated_at__date=today,
    ).count()

    total_billed_today_cents = (
        PlatformLedgerEntry.objects.filter(
            created_at__date=today,
            is_final=True,
            is_adjustment=False,
        ).aggregate(total=Sum("gross_cents"))["total"] or 0
    )
    total_billed_today = total_billed_today_cents / 100

    platform_week_cents = (
        PlatformLedgerEntry.objects.filter(
            created_at__date__gte=week_start,
            is_final=True,
            is_adjustment=False,
        ).aggregate(total=Sum("fee_cents"))["total"] or 0
    )
    platform_week = platform_week_cents / 100

    provider_week_cents = (
        PlatformLedgerEntry.objects.filter(
            created_at__date__gte=week_start,
            is_final=True,
            is_adjustment=False,
        ).aggregate(total=Sum("net_provider_cents"))["total"] or 0
    )
    provider_week = provider_week_cents / 100

    context = {
        "open_jobs": open_jobs,
        "closed_today": closed_today,
        "total_billed_today": total_billed_today,
        "platform_week": platform_week,
        "provider_week": provider_week,
    }
    return render(request, "ui/home.html", context)


def marketplace_view(request):
    service_category_id = request.GET.get("service_category_id")
    province = request.GET.get("province")
    city = request.GET.get("city")
    zone_id_raw = request.GET.get("zone_id")

    providers = []
    error = None
    zones = []
    selected_zone_id = ""

    if province and city:
        zones = list(
            ServiceZone.objects.filter(
                province=province,
                city=city,
            ).values("id", "name")
        )

    zone_id = None
    if zone_id_raw:
        try:
            zone_id = int(zone_id_raw)
            selected_zone_id = str(zone_id)
        except (TypeError, ValueError):
            error = "Zona invalida."

    if service_category_id and province:
        try:
            search_kwargs = {
                "service_category_id": int(service_category_id),
                "province": province,
                "city": city,
            }
            if zone_id is not None:
                search_kwargs["zone_id"] = zone_id

            providers = list(search_provider_services(**search_kwargs))
            for provider in providers:
                provider["display_price"] = provider["price_cents"] / 100
        except Exception as exc:
            error = str(exc)
    else:
        error = "Seleccione categoria y provincia para buscar."

    return render(
        request,
        "marketplace/index.html",
        {
            "providers": providers,
            "error": error,
            "service_category_id": service_category_id,
            "province": province,
            "city": city,
            "zones": zones,
            "selected_zone_id": selected_zone_id,
            "debug_mode": settings.DEBUG,
        },
    )


@staff_member_required
def marketplace_analytics_api_view(request):
    limit_raw = request.GET.get("limit")
    limit = None
    if limit_raw:
        try:
            limit = max(1, min(int(limit_raw), 100))
        except (TypeError, ValueError):
            limit = None

    snapshot = marketplace_analytics_snapshot(limit=limit)

    if request.GET.get("format") == "csv":
        csv_string = marketplace_analytics_to_csv(snapshot)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        response = HttpResponse(csv_string, content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="marketplace_analytics_{timestamp}.csv"'
        )
        return response

    return JsonResponse(snapshot)


@staff_member_required
def marketplace_analytics_dashboard_view(request):
    limit_raw = request.GET.get("limit")
    limit = None
    if limit_raw:
        try:
            limit = max(1, min(int(limit_raw), 100))
        except (TypeError, ValueError):
            limit = None

    snapshot = marketplace_analytics_snapshot(limit=limit)
    return render(
        request,
        "dashboard/marketplace.html",
        {
            "analytics": snapshot,
            "limit": limit or "",
        },
    )


@staff_member_required
def jobs_list(request):
    jobs = Job.objects.order_by("-job_id")[:100]
    return render(request, "ui/jobs_list.html", {"jobs": jobs})


@staff_member_required
def job_detail(request, job_id: int):
    job = get_object_or_404(Job, pk=job_id)

    client_ticket_qs = ClientTicket.objects.filter(
        ref_type="job",
        ref_id=job.job_id,
    ).order_by("-created_at")
    if job.client_id:
        client_ticket_qs = client_ticket_qs.filter(client_id=job.client_id)
    client_ticket = client_ticket_qs.first()

    provider_ticket_qs = ProviderTicket.objects.filter(
        ref_type="job",
        ref_id=job.job_id,
    ).order_by("-created_at")
    if job.selected_provider_id:
        provider_ticket_qs = provider_ticket_qs.filter(provider_id=job.selected_provider_id)
    provider_ticket = provider_ticket_qs.first()

    ledger_entries = PlatformLedgerEntry.objects.filter(job=job).order_by("created_at")

    def cents_to_money(value):
        if value is None:
            return None
        return value / 100

    latest_ledger = ledger_entries.filter(is_final=True).order_by("-created_at").first()
    if latest_ledger is None:
        latest_ledger = ledger_entries.order_by("-created_at").first()

    provider_net_cents = getattr(provider_ticket, "net_cents", None)
    if provider_net_cents is None and latest_ledger is not None:
        provider_net_cents = getattr(latest_ledger, "net_provider_cents", None)

    platform_fee_cents = getattr(provider_ticket, "platform_fee_cents", None)
    if platform_fee_cents is None and latest_ledger is not None:
        platform_fee_cents = getattr(latest_ledger, "fee_cents", None)

    financial_snapshot = {
        "client_total": cents_to_money(
            getattr(client_ticket, "total_cents", None)
        ),
        "provider_net": cents_to_money(provider_net_cents),
        "platform_fee": cents_to_money(platform_fee_cents),
    }

    context = {
        "job": job,
        "client_ticket": client_ticket,
        "provider_ticket": provider_ticket,
        "ledger_entries": ledger_entries,
        "financial_snapshot": financial_snapshot,
    }
    return render(request, "ui/job_detail.html", context)


@require_POST
@staff_member_required
def confirm_closed(request, job_id: int):
    job = get_object_or_404(Job, pk=job_id)

    if not job.client_id:
        messages.error(request, "No se puede confirmar: el job no tiene client_id.")
        return redirect("ui:job_detail", job_id=job.job_id)

    try:
        result = job_services.confirm_service_closed_by_client(
            job_id=job.job_id,
            client_id=job.client_id,
        )
    except job_services.MarketplaceDecisionConflict as exc:
        messages.error(request, f"No se pudo cerrar: {exc}")
    except PermissionError as exc:
        messages.error(request, f"Permiso denegado: {exc}")
    else:
        messages.success(request, f"Cierre procesado: {result}")

    return redirect("ui:job_detail", job_id=job.job_id)
