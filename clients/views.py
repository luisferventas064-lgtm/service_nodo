from datetime import timedelta
import random

from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from core.auth_session import require_role
from core.services.sms_service import send_sms
from jobs.activity_service import build_activity_view_context, export_activity_csv
from jobs.models import Job
from ui.models import PasswordResetCode

from .forms import ClientProfileForm, ClientRegisterForm
from .models import Client, ClientTicket


PASSWORD_CODE_WINDOW = timedelta(minutes=10)
PASSWORD_CODE_PHONE_LIMIT = 3
PASSWORD_CODE_IP_LIMIT = 10
CLIENT_DASHBOARD_ACTIVE_JOB_STATUSES = (
    Job.JobStatus.POSTED,
    Job.JobStatus.WAITING_PROVIDER_RESPONSE,
    Job.JobStatus.PENDING_CLIENT_DECISION,
    Job.JobStatus.HOLD,
    Job.JobStatus.PENDING_PROVIDER_CONFIRMATION,
    Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
    Job.JobStatus.ASSIGNED,
    Job.JobStatus.IN_PROGRESS,
    Job.JobStatus.CONFIRMED,
)


def _split_full_name(full_name: str) -> tuple[str, str]:
    normalized = " ".join((full_name or "").strip().split())
    if not normalized:
        return "Client", "User"

    parts = normalized.split(" ", 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else "User"
    return first_name, last_name


def _get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def client_register(request):
    if request.method == "POST":
        form = ClientRegisterForm(request.POST)

        if form.is_valid():
            first_name, last_name = _split_full_name(form.cleaned_data["full_name"])
            ip = _get_client_ip(request)
            window_start = timezone.now() - PASSWORD_CODE_WINDOW
            recent_phone = PasswordResetCode.objects.filter(
                phone_number=form.cleaned_data["phone_number"],
                created_at__gte=window_start,
            ).count()
            recent_ip = 0
            if ip:
                recent_ip = PasswordResetCode.objects.filter(
                    ip_address=ip,
                    created_at__gte=window_start,
                ).count()

            if recent_phone >= PASSWORD_CODE_PHONE_LIMIT:
                form.add_error(None, "Too many attempts. Try later.")
            elif ip and recent_ip >= PASSWORD_CODE_IP_LIMIT:
                form.add_error(None, "Too many attempts from this network.")
            else:
                with transaction.atomic():
                    client = Client.objects.create(
                        first_name=first_name,
                        last_name=last_name,
                        email=form.cleaned_data["email"],
                        phone_number=form.cleaned_data["phone_number"],
                        languages_spoken=form.cleaned_data.get("languages_spoken", ""),
                        password=make_password(form.cleaned_data["password"]),
                        is_phone_verified=False,
                        profile_completed=False,
                        country=form.cleaned_data["country_name"],
                        province="QC",
                        city="Pending",
                        postal_code="PENDING",
                        address_line1="Pending profile completion",
                    )

                    code = str(random.randint(100000, 999999))
                    PasswordResetCode.objects.filter(
                        phone_number=client.phone_number,
                        purpose="verify",
                        used=False,
                    ).update(used=True)
                    PasswordResetCode.objects.create(
                        phone_number=client.phone_number,
                        code=code,
                        purpose="verify",
                        ip_address=ip,
                    )
                    send_sms(
                        client.phone_number,
                        f"Your NODO verification code is: {code}",
                    )

                request.session["verify_phone"] = client.phone_number
                request.session["verify_role"] = "client"
                request.session["verify_actor_type"] = "client"
                request.session["verify_actor_id"] = client.pk
                return redirect("verify_phone")
    else:
        form = ClientRegisterForm()

    return render(request, "clients/register.html", {"form": form})


def client_dashboard(request):
    client_id = request.session.get("client_id")
    if not client_id:
        return redirect("client_register")

    client = Client.objects.filter(pk=client_id).first()
    if not client:
        request.session.pop("client_id", None)
        return redirect("client_register")

    client.evaluate_profile_completion()

    jobs_queryset = (
        Job.objects.filter(client=client)
        .select_related("service_type", "selected_provider")
        .order_by("-created_at")
    )
    recent_active_jobs = jobs_queryset.filter(
        job_status__in=CLIENT_DASHBOARD_ACTIVE_JOB_STATUSES
    )
    stats = jobs_queryset.aggregate(
        total_jobs=Count("job_id"),
        open_jobs=Count(
            "job_id",
            filter=Q(
                job_status__in=[
                    Job.JobStatus.POSTED,
                    Job.JobStatus.WAITING_PROVIDER_RESPONSE,
                    Job.JobStatus.PENDING_CLIENT_DECISION,
                    Job.JobStatus.HOLD,
                    Job.JobStatus.PENDING_PROVIDER_CONFIRMATION,
                    Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
                    Job.JobStatus.ASSIGNED,
                    Job.JobStatus.IN_PROGRESS,
                ]
            ),
        ),
        pending_client_action_jobs=Count(
            "job_id",
            filter=Q(
                job_status__in=[
                    Job.JobStatus.PENDING_CLIENT_DECISION,
                    Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
                ]
            ),
        ),
        completed_jobs=Count(
            "job_id",
            filter=Q(
                job_status__in=[
                    Job.JobStatus.COMPLETED,
                    Job.JobStatus.CONFIRMED,
                ]
            ),
        ),
        cancelled_jobs=Count(
            "job_id",
            filter=Q(job_status=Job.JobStatus.CANCELLED),
        ),
    )
    billed_total_cents = (
        ClientTicket.objects.filter(
            client=client,
            status=ClientTicket.Status.FINALIZED,
        ).aggregate(total_cents=Sum("total_cents"))["total_cents"]
        or 0
    )

    languages = [
        language.strip()
        for language in (client.languages_spoken or "").split(",")
        if language.strip()
    ]

    return render(
        request,
        "portal/client_dashboard.html",
        {
            "client": client,
            "nav_identity": f"{client.first_name} {client.last_name}".strip(),
            "languages": languages,
            "stats": stats,
            "billed_total_amount": billed_total_cents / 100,
            "recent_jobs": recent_active_jobs[:8],
        },
    )


def client_profile(request):
    client_id = request.session.get("client_id")
    if not client_id:
        return redirect("client_register")

    client = Client.objects.filter(pk=client_id).first()
    if not client:
        request.session.pop("client_id", None)
        return redirect("client_register")

    client.evaluate_profile_completion()

    return render(request, "clients/profile.html", {"client": client})


@require_role("client")
def client_activity(request):
    client = getattr(request, "client_profile", None)
    if client is None:
        client_id = request.session.get("nodo_profile_id") or request.session.get("client_id")
        if not client_id:
            return redirect("client_register")
        client = Client.objects.filter(pk=client_id).first()
        if client is None:
            request.session.pop("client_id", None)
            request.session.pop("nodo_profile_id", None)
            return redirect("client_register")

    if request.GET.get("export") == "csv":
        return export_activity_csv(
            "client",
            client,
            request.GET,
        )

    return render(
        request,
        "clients/activity.html",
        {
            "client": client,
            "activity_page_title": _("Activity History"),
            **build_activity_view_context(
                "client",
                client,
                params=request.GET,
            ),
        },
    )


@require_role("client")
def client_billing(request):
    return render(request, "clients/billing.html")


@require_role("client")
def client_edit(request):
    return render(request, "clients/account.html")


def client_complete_profile(request):
    client_id = request.session.get("client_id") or request.session.get("verify_actor_id")
    if not client_id:
        return redirect("client_register")

    client = Client.objects.filter(pk=client_id).first()
    if not client:
        request.session.pop("client_id", None)
        request.session.pop("verify_actor_type", None)
        request.session.pop("verify_actor_id", None)
        return redirect("client_register")

    if not client.is_phone_verified:
        return redirect("verify_phone")

    client.evaluate_profile_completion()
    if client.profile_completed:
        request.session["client_id"] = client.pk
        request.session.pop("verify_actor_type", None)
        request.session.pop("verify_actor_id", None)
        return redirect("client_dashboard")

    if request.method == "POST":
        form = ClientProfileForm(request.POST, instance=client)
        if form.is_valid():
            form.save()
            client.accepts_terms = form.cleaned_data["accepts_terms"]
            client.save(update_fields=["accepts_terms", "updated_at"])
            if client.evaluate_profile_completion():
                request.session["client_id"] = client.pk
                request.session.pop("verify_actor_type", None)
                request.session.pop("verify_actor_id", None)
                return redirect("client_dashboard")

            form.add_error(None, "Complete all required profile fields.")
    else:
        form = ClientProfileForm(instance=client)

    return render(request, "clients/complete_profile.html", {"form": form, "client": client})
