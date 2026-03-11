from datetime import timedelta
import random

from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from core.auth_session import require_role
from core.services.sms_service import send_sms
from jobs.activity_service import build_activity_view_context, export_activity_csv
from ui.models import PasswordResetCode

from .forms import WorkerProfileForm, WorkerRegisterForm
from .models import Worker

PASSWORD_CODE_WINDOW = timedelta(minutes=10)
PASSWORD_CODE_PHONE_LIMIT = 3
PASSWORD_CODE_IP_LIMIT = 10


def _split_full_name(full_name: str) -> tuple[str, str]:
    normalized = " ".join((full_name or "").strip().split())
    if not normalized:
        return "Worker", "User"

    parts = normalized.split(" ", 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else "User"
    return first_name, last_name


def _get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _get_logged_worker(request):
    worker_id = request.session.get("worker_id")
    if not worker_id:
        return None
    return Worker.objects.filter(pk=worker_id).first()


def worker_register(request):
    if request.method == "POST":
        form = WorkerRegisterForm(request.POST)

        if form.is_valid():
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
                first_name, last_name = _split_full_name(form.cleaned_data["full_name"])

                with transaction.atomic():
                    worker = Worker.objects.create(
                        first_name=first_name,
                        last_name=last_name,
                        email=form.cleaned_data["email"],
                        phone_number=form.cleaned_data["phone_number"],
                        languages_spoken=form.cleaned_data.get("languages_spoken", ""),
                        password=make_password(form.cleaned_data["password"]),
                        is_phone_verified=False,
                        profile_completed=False,
                        country=form.cleaned_data["country_name"],
                        province=None,
                        city=None,
                        postal_code=None,
                        address_line1=None,
                    )

                    code = str(random.randint(100000, 999999))
                    PasswordResetCode.objects.filter(
                        phone_number=worker.phone_number,
                        purpose="verify",
                        used=False,
                    ).update(used=True)
                    PasswordResetCode.objects.create(
                        phone_number=worker.phone_number,
                        code=code,
                        purpose="verify",
                        ip_address=ip,
                    )
                    send_sms(
                        worker.phone_number,
                        f"Your NODO verification code is: {code}",
                    )

                request.session["verify_phone"] = worker.phone_number
                request.session["verify_role"] = "worker"
                request.session["verify_actor_type"] = "worker"
                request.session["verify_actor_id"] = worker.pk
                return redirect("verify_phone")
    else:
        form = WorkerRegisterForm()

    return render(request, "workers/register.html", {"form": form})


def worker_profile(request):
    worker = _get_logged_worker(request)
    if worker is None:
        request.session.pop("worker_id", None)
        return redirect("ui:login")

    if request.method == "POST":
        form = WorkerProfileForm(request.POST, instance=worker)
        if form.is_valid():
            worker = form.save(commit=False)
            worker.accepts_terms = form.cleaned_data["accepts_terms"]
            worker.save(
                update_fields=[
                    "first_name",
                    "last_name",
                    "languages_spoken",
                    "accepts_terms",
                    "updated_at",
                ]
            )

            if worker.evaluate_profile_completion():
                return redirect("worker_jobs")

            form.add_error(None, "Complete all required profile fields.")
    else:
        if worker.evaluate_profile_completion():
            return redirect("worker_jobs")
        form = WorkerProfileForm(instance=worker)

    return render(
        request,
        "workers/profile.html",
        {
            "form": form,
            "worker": worker,
        },
    )


def worker_jobs(request):
    worker = _get_logged_worker(request)
    if worker is None:
        request.session.pop("worker_id", None)
        return redirect("ui:login")

    worker.evaluate_profile_completion()
    if not worker.profile_completed:
        return redirect("worker_profile")

    return render(request, "workers/jobs.html")


@require_role("worker")
def worker_activity(request):
    worker = getattr(request, "worker_profile", None) or _get_logged_worker(request)
    if worker is None:
        request.session.pop("worker_id", None)
        request.session.pop("nodo_profile_id", None)
        return redirect("ui:login")

    if request.GET.get("export") == "csv":
        return export_activity_csv(
            "worker",
            worker,
            request.GET,
        )

    return render(
        request,
        "workers/activity.html",
        {
            "worker": worker,
            "activity_page_title": "Activity History",
            **build_activity_view_context(
                "worker",
                worker,
                params=request.GET,
            ),
        },
    )


@require_role("worker")
def worker_edit(request):
    return render(request, "workers/account.html")
