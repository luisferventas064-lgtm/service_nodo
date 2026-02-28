from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from clients.models import ClientTicket
from clients.models import Client
from jobs import services as job_services
from jobs.models import Job, PlatformLedgerEntry
from providers.models import Provider
from providers.models import ServiceCategory
from providers.models import ProviderService
from providers.models import ProviderTicket
from providers.services_analytics import (
    marketplace_analytics_snapshot,
    marketplace_analytics_to_csv,
)
from providers.services_marketplace import marketplace_ranked_queryset
from service_type.models import ServiceType


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


@login_required
def portal_view(request):
    return render(request, "portal/index.html")


def marketplace_search_view(request):
    categories = ServiceCategory.objects.filter(is_active=True).order_by("name")
    return render(
        request,
        "marketplace/search.html",
        {
            "categories": categories,
        },
    )


def marketplace_results_view(request):
    if request.method != "POST":
        return redirect("ui:marketplace_search")

    category_id = request.POST.get("category_id")
    province = (request.POST.get("province") or "").strip()
    city = (request.POST.get("city") or "").strip()
    zone_id_raw = (request.POST.get("zone_id") or "").strip()

    results = []
    error = None
    zone_id = zone_id_raw

    try:
        service_category_id = int(category_id)
    except (TypeError, ValueError):
        service_category_id = None
        error = "Categoria invalida."

    parsed_zone_id = None
    if zone_id_raw:
        try:
            parsed_zone_id = int(zone_id_raw)
        except (TypeError, ValueError):
            error = "Zona invalida."

    if error is None and service_category_id and province and city:
        results = list(
            marketplace_ranked_queryset(
                service_category_id=service_category_id,
                province=province,
                city=city,
                zone_id=parsed_zone_id,
            )
            .order_by("-hybrid_score", "-safe_rating", "price_cents", "provider_id")[:20]
        )
        for provider in results:
            provider.display_price = provider.price_cents / 100
            provider.is_verified_badge = bool(provider.verified_bonus)
    elif error is None:
        error = "Complete categoria, provincia y ciudad."

    return render(
        request,
        "marketplace/results.html",
        {
            "results": results,
            "error": error,
            "category_id": category_id,
            "province": province,
            "city": city,
            "zone_id": zone_id,
        },
    )


def request_create_view(request, provider_id):
    provider = get_object_or_404(Provider, pk=provider_id, is_active=True)
    service_types = ServiceType.objects.filter(is_active=True).order_by("name")
    category_id = request.GET.get("category_id") or request.POST.get("category_id")

    selected_offer = (
        ProviderService.objects.select_related("category")
        .filter(
            provider=provider,
            is_active=True,
        )
        .order_by("price_cents", "id")
        .first()
    )
    if category_id:
        selected_offer = (
            ProviderService.objects.select_related("category")
            .filter(
                provider=provider,
                category_id=category_id,
                is_active=True,
            )
            .order_by("price_cents", "id")
            .first()
            or selected_offer
        )
    if selected_offer is not None:
        selected_offer.display_price = selected_offer.price_cents / 100

    if request.method == "GET":
        return render(
            request,
            "request/create.html",
            {
                "provider": provider,
                "service_types": service_types,
                "selected_offer": selected_offer,
                "category_id": category_id,
                "form_data": {
                    "country": "CA",
                    "province": provider.province,
                    "city": provider.city,
                    "job_mode": Job.JobMode.ON_DEMAND,
                },
            },
        )

    form_data = {
        "first_name": (request.POST.get("first_name") or "").strip(),
        "last_name": (request.POST.get("last_name") or "").strip(),
        "phone_number": (request.POST.get("phone_number") or "").strip(),
        "email": (request.POST.get("email") or "").strip(),
        "country": (request.POST.get("country") or "CA").strip(),
        "province": (request.POST.get("province") or "").strip(),
        "city": (request.POST.get("city") or "").strip(),
        "postal_code": (request.POST.get("postal_code") or "").strip(),
        "address_line1": (request.POST.get("address_line1") or "").strip(),
        "service_type": (request.POST.get("service_type") or "").strip(),
        "job_mode": (request.POST.get("job_mode") or Job.JobMode.ON_DEMAND).strip(),
        "scheduled_date": (request.POST.get("scheduled_date") or "").strip(),
        "scheduled_start_time": (request.POST.get("scheduled_time") or "").strip(),
    }

    required_fields = [
        "first_name",
        "last_name",
        "phone_number",
        "email",
        "country",
        "province",
        "city",
        "postal_code",
        "address_line1",
        "service_type",
        "job_mode",
    ]
    missing_required = [field for field in required_fields if not form_data[field]]

    error = None
    if missing_required:
        error = "Complete todos los campos obligatorios."
    elif form_data["job_mode"] not in {Job.JobMode.ON_DEMAND, Job.JobMode.SCHEDULED}:
        error = "Modo de servicio invalido."
    elif form_data["job_mode"] == Job.JobMode.SCHEDULED and (
        not form_data["scheduled_date"] or not form_data["scheduled_start_time"]
    ):
        error = "Scheduled requiere fecha y hora."

    service_type = None
    if error is None:
        service_type = ServiceType.objects.filter(
            pk=form_data["service_type"],
            is_active=True,
        ).first()
        if service_type is None:
            error = "Service type invalido."

    if error is None:
        client = Client.objects.filter(email=form_data["email"]).order_by("client_id").first()
        if client is None:
            client = Client.objects.create(
                first_name=form_data["first_name"],
                last_name=form_data["last_name"],
                phone_number=form_data["phone_number"],
                email=form_data["email"],
                country=form_data["country"],
                province=form_data["province"],
                city=form_data["city"],
                postal_code=form_data["postal_code"],
                address_line1=form_data["address_line1"],
            )

        created_job = None
        try:
            created_job = Job.objects.create(
                selected_provider=provider,
                client=client,
                service_type=service_type,
                job_mode=form_data["job_mode"],
                scheduled_date=(
                    form_data["scheduled_date"]
                    if form_data["job_mode"] == Job.JobMode.SCHEDULED
                    else None
                ),
                scheduled_start_time=(
                    form_data["scheduled_start_time"]
                    if form_data["job_mode"] == Job.JobMode.SCHEDULED
                    else None
                ),
                is_asap=form_data["job_mode"] == Job.JobMode.ON_DEMAND,
                country=form_data["country"],
                province=form_data["province"],
                city=form_data["city"],
                postal_code=form_data["postal_code"],
                address_line1=form_data["address_line1"],
                job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            )
        except ValidationError as exc:
            error = "; ".join(exc.messages) or "No se pudo crear la solicitud."

    if error is not None:
        return render(
            request,
            "request/create.html",
            {
                "provider": provider,
                "service_types": service_types,
                "selected_offer": selected_offer,
                "category_id": category_id,
                "form_data": form_data,
                "error": error,
            },
        )

    return redirect("ui:request_status", job_id=created_job.job_id)


def request_status_lookup_view(request):
    job_id = request.GET.get("job_id")

    try:
        job_id_int = int(job_id)
    except (TypeError, ValueError):
        return redirect("ui:portal")

    return redirect("ui:request_status", job_id=job_id_int)


def request_status_view(request, job_id):
    job = get_object_or_404(
        Job.objects.select_related("selected_provider", "client", "service_type"),
        pk=job_id,
    )

    return render(
        request,
        "request/status.html",
        {
            "job": job,
        },
    )


@login_required
def provider_jobs_view(request):
    provider_ids = list(
        request.user.provider_roles
        .filter(is_active=True)
        .values_list("provider_id", flat=True)
    )

    jobs = (
        Job.objects.filter(
            selected_provider_id__in=provider_ids,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
        )
        .select_related("client", "service_type")
        .order_by("created_at")
    )

    return render(
        request,
        "provider/jobs.html",
        {
            "jobs": jobs,
        },
    )


@login_required
def provider_job_action_view(request, job_id):
    if request.method != "POST":
        return redirect("ui:provider_jobs")

    job = get_object_or_404(Job, pk=job_id)
    provider_ids = set(
        request.user.provider_roles
        .filter(is_active=True)
        .values_list("provider_id", flat=True)
    )

    if job.selected_provider_id not in provider_ids:
        return HttpResponseForbidden("No autorizado.")

    if job.job_status != Job.JobStatus.WAITING_PROVIDER_RESPONSE:
        return HttpResponseForbidden("Estado invalido.")

    action = request.POST.get("action")

    if action == "accept":
        job.job_status = Job.JobStatus.ASSIGNED
        job.save(update_fields=["job_status", "updated_at"])
    elif action == "reject":
        job.job_status = Job.JobStatus.POSTED
        job.selected_provider = None
        job.save(update_fields=["job_status", "selected_provider", "updated_at"])
    else:
        return HttpResponseBadRequest("Accion invalida.")

    return redirect("ui:provider_jobs")


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
