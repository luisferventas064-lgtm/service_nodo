from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import random
from types import SimpleNamespace
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.hashers import check_password
from django.contrib.auth import logout as auth_logout
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.db import transaction
from django.db.models import (
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Cast, Coalesce, Greatest, Least
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date, parse_time
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.views.decorators.http import require_POST

from clients.models import ClientTicket
from clients.models import Client
from core.auth_session import clear_session, require_role, set_session
from core.legal_disclaimers import build_financial_disclaimer_context
from core.utils.phone import best_effort_normalize_phone, phone_lookup_candidates
from jobs import services as job_services
from jobs.events import create_job_event, get_visible_job_status_label
from jobs.models import (
    Job,
    JobDispute,
    JobEvent,
    JobLocation,
    JobProviderExclusion,
    JobRequestedExtra,
    PlatformLedgerEntry,
)
from jobs.services_pricing_snapshot import apply_provider_service_snapshot_to_job
from jobs.services_state_transitions import (
    transition_assignment_status,
    transition_job_status,
)
from notifications.models import PushDevice, PushDispatchAttempt
from notifications.services import register_push_device_for_user
from jobs.taxes import TAX_RULES_BY_REGION, compute_tax_cents, get_tax_rule_for_region
from providers.models import Provider, ProviderLocation, ProviderMetrics, ProviderService, ProviderServiceArea, ProviderServiceExtra
from providers.models import ProviderTicket
from providers.services_analytics import (
    marketplace_analytics_snapshot,
    marketplace_analytics_to_csv,
)
from providers.services_geocode import extract_province, geocode_address
from providers.services_marketplace import Log10, marketplace_ranked_queryset
from providers.utils_distance import haversine_distance_km
from providers.utils_ranking import provider_ranking_score
from service_type.models import ServiceType
from workers.models import Worker

from core.services.sms_service import send_sms

from .forms import ForgotPasswordForm, ResetPasswordConfirmForm, RoleLoginForm
from .models import PasswordResetCode

PASSWORD_CODE_WINDOW = timedelta(minutes=10)
PASSWORD_CODE_PHONE_LIMIT = 3
PASSWORD_CODE_IP_LIMIT = 10
PASSWORD_CODE_MAX_ATTEMPTS = 5
VERIFY_RESEND_COOLDOWN = timedelta(seconds=60)
MARKETPLACE_DEFAULT_ORDER = ("-hybrid_score", "-safe_rating", "price_cents", "provider_id")
MARKETPLACE_ORDER_MAP = {
    "rating_desc": ("-safe_rating", "price_cents", "provider_id"),
    "price_asc": ("price_cents", "-safe_rating", "provider_id"),
    "price_desc": ("-price_cents", "-safe_rating", "provider_id"),
}
MARKETPLACE_SERVICE_TIMING_CHOICES = (
    (
        "emergency",
        _lazy("Emergency"),
        _lazy("less than 2 hours"),
    ),
    (
        "urgent",
        _lazy("Urgent"),
        _lazy("2 to 24 hours"),
    ),
    (
        "scheduled",
        _lazy("Scheduled"),
        _lazy("more than 24 hours"),
    ),
)
MARKETPLACE_SERVICE_TIMING_VALUES = {
    choice[0] for choice in MARKETPLACE_SERVICE_TIMING_CHOICES
}
REQUEST_SERVICE_TIMING_VALUES = set(MARKETPLACE_SERVICE_TIMING_VALUES)
REQUEST_SERVICE_TIMING_LABELS = {
    value: label for value, label, _help_text in MARKETPLACE_SERVICE_TIMING_CHOICES
}
REQUEST_SERVICE_TIMING_TO_JOB_MODE = {
    "emergency": Job.JobMode.ON_DEMAND,
    "urgent": Job.JobMode.ON_DEMAND,
    "scheduled": Job.JobMode.SCHEDULED,
}
JOB_CREATED_NEXT_STEP_LABELS = {
    "emergency": _lazy("Waiting for provider response"),
    "scheduled": _lazy("Request submitted"),
    "urgent": _lazy("Pending confirmation"),
}
MARKETPLACE_LANGUAGE_CHOICES = (
    _lazy("English"),
    _lazy("French"),
    _lazy("Spanish"),
    _lazy("Arabic"),
    _lazy("Mandarin"),
    _lazy("Italian"),
    _lazy("Portuguese"),
    _lazy("Russian"),
    _lazy("Punjabi"),
    _lazy("Vietnamese"),
)
REQUEST_MONEY_Q = Decimal("0.01")
REQUEST_AREA_UNAVAILABLE_ERROR = _lazy(
    "Service not available in this area. Please choose another address."
)
FRIENDLY_JOB_STATUS_LABELS = {
    Job.JobStatus.DRAFT: _lazy("Draft"),
    Job.JobStatus.POSTED: _lazy("Looking for a provider"),
    Job.JobStatus.SCHEDULED_PENDING_ACTIVATION: _lazy("Request submitted"),
    Job.JobStatus.WAITING_PROVIDER_RESPONSE: _lazy("Waiting for provider reply"),
    Job.JobStatus.PENDING_CLIENT_DECISION: _lazy("Waiting for your decision"),
    Job.JobStatus.HOLD: _lazy("Temporarily on hold"),
    Job.JobStatus.PENDING_PROVIDER_CONFIRMATION: _lazy("Waiting for provider confirmation"),
    Job.JobStatus.PENDING_CLIENT_CONFIRMATION: _lazy("Waiting for your confirmation"),
    Job.JobStatus.ASSIGNED: _lazy("Provider assigned"),
    Job.JobStatus.IN_PROGRESS: _lazy("Service in progress"),
    Job.JobStatus.COMPLETED: _lazy("Completed by provider"),
    Job.JobStatus.CONFIRMED: _lazy("Service closed"),
    Job.JobStatus.CANCELLED: _lazy("Cancelled"),
    Job.JobStatus.EXPIRED: _lazy("Expired"),
}


def _marketplace_service_types(*, limit=20):
    return (
        ServiceType.objects.filter(
            is_active=True,
            provider_services__is_active=True,
            provider_services__provider__is_active=True,
        )
        .annotate(
            provider_count=Count("provider_services__provider", distinct=True)
        )
        .order_by("-provider_count", "name")[:limit]
    )


def home(request):
    role = _get_session_role(request)
    service_types = _marketplace_service_types(limit=20)
    return render(
        request,
        "ui/home.html",
        {
            "service_types": service_types,
            "nav_identity": (
                _("Client")
                if role == "client"
                else _("Provider")
                if role == "provider"
                else _("Worker")
                if role == "worker"
                else None
            ),
        },
    )


def terms_and_conditions(request):
    return render(
        request,
        "ui/terms_and_conditions.html",
        build_financial_disclaimer_context(),
    )


def portal_view(request):
    role = _get_session_role(request)

    if role == "client":
        return redirect("portal:client_dashboard")

    if role == "provider":
        return redirect("portal:provider_dashboard")

    if role == "worker":
        return redirect("portal:worker_dashboard")

    return redirect("ui:root_login")


def logout_view(request):
    clear_session(request)
    auth_logout(request)
    return redirect("ui:root_login")


def signup(request):
    return render(request, "ui/signup.html")


def login_choice(request):
    if _get_session_role(request):
        return redirect("ui:home")

    return render(request, "ui/login.html")


def login_selector(request):
    return login_choice(request)


def _get_session_role(request):
    role = request.session.get("nodo_role")
    if role:
        return role

    legacy_session_keys = (
        ("client", "client_id"),
        ("provider", "provider_id"),
        ("worker", "worker_id"),
    )
    for legacy_role, legacy_key in legacy_session_keys:
        legacy_id = request.session.get(legacy_key)
        if legacy_id:
            set_session(request, role=legacy_role, profile_id=legacy_id)
            return legacy_role

    return None


def get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _get_recent_password_code_counts(*, phone, ip):
    window_start = timezone.now() - PASSWORD_CODE_WINDOW
    recent_phone = PasswordResetCode.objects.filter(
        phone_number=phone,
        created_at__gte=window_start,
    ).count()
    recent_ip = 0
    if ip:
        recent_ip = PasswordResetCode.objects.filter(
            ip_address=ip,
            created_at__gte=window_start,
        ).count()
    return recent_phone, recent_ip


def _password_code_rate_limit_error(*, phone, ip):
    recent_phone, recent_ip = _get_recent_password_code_counts(phone=phone, ip=ip)
    if recent_phone >= PASSWORD_CODE_PHONE_LIMIT:
        return _("Too many attempts. Try later.")
    if ip and recent_ip >= PASSWORD_CODE_IP_LIMIT:
        return _("Too many attempts from this network.")
    return None


def _resolve_phone_for_lookup(raw_phone):
    candidates = phone_lookup_candidates(raw_phone)
    if not candidates:
        return best_effort_normalize_phone(raw_phone)

    for model in (Client, Provider, Worker):
        matched_phone = (
            model.objects.filter(phone_number__in=candidates)
            .values_list("phone_number", flat=True)
            .first()
        )
        if matched_phone:
            return matched_phone

    return best_effort_normalize_phone(raw_phone)


def _get_logged_client(request):
    client_id = request.session.get("client_id")
    if not client_id:
        return None

    client = Client.objects.filter(pk=client_id).first()
    if client is None:
        request.session.pop("client_id", None)
        return None

    return client


def _issue_verify_code(*, phone, ip=None, allow_existing_active=False):
    verify_window_start = timezone.now() - PASSWORD_CODE_WINDOW

    if allow_existing_active:
        active_code_exists = PasswordResetCode.objects.filter(
            phone_number=phone,
            purpose="verify",
            used=False,
            created_at__gte=verify_window_start,
        ).exists()
        if active_code_exists:
            return None

    recent_phone = PasswordResetCode.objects.filter(
        phone_number=phone,
        purpose="verify",
        created_at__gte=verify_window_start,
    ).count()
    if recent_phone >= PASSWORD_CODE_PHONE_LIMIT:
        return _("Too many attempts. Try later.")

    recent_ip = 0
    if ip:
        recent_ip = PasswordResetCode.objects.filter(
            ip_address=ip,
            purpose="verify",
            created_at__gte=verify_window_start,
        ).count()
    if ip and recent_ip >= PASSWORD_CODE_IP_LIMIT:
        return _("Too many attempts from this network.")

    code = str(random.randint(100000, 999999))
    PasswordResetCode.objects.filter(
        phone_number=phone,
        purpose="verify",
        used=False,
    ).update(used=True)
    PasswordResetCode.objects.create(
        phone_number=phone,
        code=code,
        purpose="verify",
        ip_address=ip,
    )
    send_sms(
        phone,
        _("Your NODO verification code is: %(code)s") % {"code": code},
    )
    return None


def _redirect_after_role_login(actor, *, role):
    if hasattr(actor, "evaluate_profile_completion"):
        actor.evaluate_profile_completion()
    return "ui:portal"


def _marketplace_top_results(*, queryset, limit=10):
    ranked_rows = []
    seen_provider_ids = set()

    for offer in queryset[:100]:
        if offer.provider_id in seen_provider_ids:
            continue

        seen_provider_ids.add(offer.provider_id)
        offer.display_price = offer.price_cents / 100
        offer.is_verified_badge = bool(offer.verified_bonus)
        ranked_rows.append(offer)

        if len(ranked_rows) >= limit:
            break

    return ranked_rows


def _apply_marketplace_search_query(*, queryset, search_q=""):
    normalized_query = (search_q or "").strip()
    if not normalized_query:
        return queryset

    return queryset.filter(
        Q(provider_display_name__icontains=normalized_query)
        | Q(provider__company_name__icontains=normalized_query)
        | Q(provider__contact_first_name__icontains=normalized_query)
        | Q(provider__contact_last_name__icontains=normalized_query)
        | Q(provider__legal_name__icontains=normalized_query)
        | Q(provider__email__icontains=normalized_query)
        | Q(service_type__name__icontains=normalized_query)
        | Q(custom_name__icontains=normalized_query)
        | Q(subservices__is_active=True, subservices__name__icontains=normalized_query)
        | Q(extras__is_active=True, extras__name__icontains=normalized_query)
    ).distinct()


def _provider_matches_nearby_service_filter(provider_offer, search_term_lower):
    provider_name = (
        (getattr(provider_offer, "provider_display_name", "") or "").strip().lower()
        or str(getattr(provider_offer, "provider", "") or "").strip().lower()
    )
    subservice_names = [
        (subservice.name or "").strip().lower()
        for subservice in provider_offer.subservices.all()
        if subservice.is_active
    ]
    extra_names = [
        (extra.name or "").strip().lower()
        for extra in provider_offer.extras.all()
        if extra.is_active
    ]
    haystack = [provider_name] + subservice_names + extra_names
    return any(search_term_lower in item for item in haystack)


def _provider_name_priority_key(card, provider_name_lower):
    display_name = (getattr(card, "card_display_name", "") or "").strip().lower()
    match_index = display_name.find(provider_name_lower)
    if match_index < 0:
        match_index = len(display_name) + 1
    return (
        0 if display_name == provider_name_lower else 1,
        0 if display_name.startswith(provider_name_lower) else 1,
        match_index,
    )


def _prepare_marketplace_provider_cards(offers):
    prepared = []

    for offer in offers:
        offer.display_price = Decimal(offer.price_cents) / Decimal("100")
        offer.card_display_name = (
            getattr(offer, "provider_display_name", "").strip()
            or str(offer.provider)
        )

        provider_logo = getattr(offer.provider, "logo", None)
        offer.card_logo_url = getattr(provider_logo, "url", "") if provider_logo else ""

        subservices = sorted(
            [subservice for subservice in offer.subservices.all() if subservice.is_active],
            key=lambda subservice: (subservice.sort_order, subservice.pk),
        )

        # Locked marketplace card rule:
        # - one visible card per provider
        # - card source = first ranked ProviderService row for that provider
        # - visible service text = base service of the visible offer
        # - extras are not shown on the card
        service_type_name = getattr(getattr(offer, "service_type", None), "name", "") or ""
        offer.card_primary_service = service_type_name.strip()

        # Optional fallback only if service_type name is unexpectedly empty.
        if not offer.card_primary_service and subservices:
            offer.card_primary_service = subservices[0].name

        # Extras intentionally hidden in marketplace card.
        offer.card_extra_preview = ""
        offer.card_primary_subservice = ""
        prepared.append(offer)

    return prepared


def _select_marketplace_card_rows(provider_services):
    """
    Select one visible marketplace card row per provider.

    Current locked rule:
    - preserve the incoming ranking/order
    - visible card = first ranked row for each provider

    Important:
    ProviderService does not currently expose a field that distinguishes
    a main/base service from secondary/extra-like rows. Because of that,
    the marketplace intentionally uses the first ranked row per provider
    as the visible card source.
    """
    seen_provider_ids = set()
    selected_rows = []

    for row in provider_services:
        provider_id = getattr(row, "provider_id", None)
        if not provider_id or provider_id in seen_provider_ids:
            continue

        seen_provider_ids.add(provider_id)
        selected_rows.append(row)

    return selected_rows


def _build_marketplace_results(*, client=None, service_type_id="", provider_type="", order="", province="", city="", zone_id=""):
    error = None
    selected_service_type = (service_type_id or "").strip()
    selected_type = (provider_type or "").strip()
    selected_order = (order or "").strip()
    selected_province = (province or "").strip()
    selected_city = (city or "").strip()
    selected_zone = (zone_id or "").strip()

    service_type = None
    if selected_service_type:
        service_type = ServiceType.objects.filter(
            pk=selected_service_type,
            is_active=True,
        ).first()
        if service_type is None:
            return {
                "results": [],
                "error": "Invalid service type.",
                "selected_service_type": selected_service_type,
                "selected_service_type_id": "",
                "selected_type": selected_type,
                "selected_order": selected_order,
                "selected_province": selected_province,
                "selected_city": selected_city,
                "selected_zone": selected_zone,
            }

    parsed_zone_id = None
    if selected_zone:
        try:
            parsed_zone_id = int(selected_zone)
        except (TypeError, ValueError):
            error = "Invalid zone."

    queryset = marketplace_ranked_queryset(
        service_type_id=service_type.pk if service_type else None,
    )

    if error is None:
        # Apply provider-type filtering before the geographic fallback so the
        # "city -> province" sequence stays coherent for the chosen segment.
        if selected_type in dict(Provider.PROVIDER_TYPE_CHOICES):
            queryset = queryset.filter(provider__provider_type=selected_type)

        target_city = selected_city or getattr(client, "city", "")
        target_province = selected_province or getattr(client, "province", "")

        city_queryset = queryset.none()
        if target_city:
            city_queryset = queryset.filter(
                provider__providerservicearea__city__iexact=target_city
            )

        province_queryset = queryset.none()
        if target_province:
            province_queryset = queryset.filter(
                provider__providerservicearea__province__iexact=target_province
            )

        if city_queryset.exists():
            queryset = city_queryset
        elif province_queryset.exists():
            queryset = province_queryset

        order_fields = MARKETPLACE_ORDER_MAP.get(selected_order, MARKETPLACE_DEFAULT_ORDER)
        queryset = queryset.order_by(*order_fields)

    results = [] if error else _marketplace_top_results(queryset=queryset, limit=10)

    return {
        "results": results,
        "error": error,
        "selected_service_type": selected_service_type,
        "selected_service_type_id": service_type.pk if service_type else "",
        "selected_type": selected_type,
        "selected_order": selected_order,
        "selected_province": selected_province,
        "selected_city": selected_city,
        "selected_zone": selected_zone,
    }


def _login_for_role(request, *, model, template_name, role_label):
    form = RoleLoginForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        identifier = form.cleaned_data["identifier"].strip()
        raw_password = form.cleaned_data["password"]
        role_key = role_label.lower()
        phone_candidates = phone_lookup_candidates(identifier)
        actor = (
            model.objects.filter(
                Q(email__iexact=identifier) | Q(phone_number__in=phone_candidates),
                is_active=True,
            )
            .order_by("pk")
            .first()
        )

        if actor and actor.password and check_password(raw_password, actor.password):
            if not getattr(actor, "is_phone_verified", False):
                _issue_verify_code(
                    phone=actor.phone_number,
                    ip=get_client_ip(request),
                    allow_existing_active=True,
                )
                request.session.flush()
                request.session["verify_phone"] = actor.phone_number
                request.session["verify_role"] = role_key
                request.session["verify_actor_type"] = role_key
                request.session["verify_actor_id"] = actor.pk
                return redirect("verify_phone")

            request.session.flush()
            set_session(request, role=role_key, profile_id=actor.pk)
            return redirect(_redirect_after_role_login(actor, role=role_key))

        form.add_error(None, _("Invalid credentials."))

    form.apply_error_styles()

    return render(
        request,
        template_name,
        {
            "form": form,
            "role_label": role_label,
        },
    )


def login_client(request):
    return _login_for_role(
        request,
        model=Client,
        template_name="auth/login_client.html",
        role_label="Client",
    )


def login_provider(request):
    return _login_for_role(
        request,
        model=Provider,
        template_name="auth/login_provider.html",
        role_label="Provider",
    )


def login_worker(request):
    return _login_for_role(
        request,
        model=Worker,
        template_name="auth/login_worker.html",
        role_label="Worker",
    )


def verify_phone(request):
    phone = request.session.get("verify_phone")
    role = request.session.get("verify_role")

    if (not phone or not role) and request.session.get("verify_actor_type") and request.session.get("verify_actor_id"):
        legacy_role = request.session.get("verify_actor_type")
        legacy_actor_id = request.session.get("verify_actor_id")
        model_map = {
            "client": Client,
            "provider": Provider,
            "worker": Worker,
        }
        legacy_model = model_map.get(legacy_role)
        actor = legacy_model.objects.filter(pk=legacy_actor_id).first() if legacy_model else None
        if actor is not None:
            phone = actor.phone_number
            role = legacy_role
            request.session["verify_phone"] = phone
            request.session["verify_role"] = role

    if not phone or not role:
        return redirect("ui:root_login")

    error = None
    if request.method == "POST":
        code_input = (request.POST.get("code") or "").strip()
        record = (
            PasswordResetCode.objects.filter(
                phone_number=phone,
                purpose="verify",
                used=False,
            )
            .order_by("-created_at")
            .first()
        )

        if not record:
            error = _("Verification code not found.")
        elif not record.is_valid():
            record.used = True
            record.save(update_fields=["used"])
            error = _("Code expired.")
        elif record.code != code_input:
            record.attempts += 1
            if record.attempts >= PASSWORD_CODE_MAX_ATTEMPTS:
                record.used = True
                record.save(update_fields=["attempts", "used"])
                error = _("Code expired.")
            else:
                record.save(update_fields=["attempts"])
                error = _("Invalid verification code.")
        else:
            model_map = {
                "client": Client,
                "provider": Provider,
                "worker": Worker,
            }
            model_info = model_map.get(role)
            if not model_info:
                return redirect("ui:root_login")

            model = model_info
            user = model.objects.filter(phone_number=phone).order_by("pk").first()
            if user is None:
                error = _("No account is linked to this phone number.")
            else:
                update_fields = []
                if hasattr(user, "is_phone_verified"):
                    user.is_phone_verified = True
                    update_fields.append("is_phone_verified")
                if hasattr(user, "phone_verified_at"):
                    user.phone_verified_at = timezone.now()
                    update_fields.append("phone_verified_at")
                if hasattr(user, "phone_verification_attempts"):
                    user.phone_verification_attempts = 0
                    update_fields.append("phone_verification_attempts")
                if hasattr(user, "updated_at"):
                    update_fields.append("updated_at")
                if update_fields:
                    user.save(update_fields=update_fields)

                PasswordResetCode.objects.filter(
                    phone_number=phone,
                    purpose="verify",
                ).update(used=True)
                request.session.flush()
                set_session(request, role=role, profile_id=user.pk)
                return redirect(_redirect_after_role_login(user, role=role))

    return render(
        request,
        "auth/verify_phone.html",
        {
            "error": error,
        },
    )


def resend_code(request):
    if request.method != "POST":
        return JsonResponse({"error": _("Invalid request")}, status=400)

    phone = request.session.get("verify_phone")
    role = request.session.get("verify_role")

    if not phone or not role:
        return JsonResponse({"error": _("Session expired")}, status=400)

    ip = get_client_ip(request)
    recent_verify = PasswordResetCode.objects.filter(
        phone_number=phone,
        purpose="verify",
        created_at__gte=timezone.now() - VERIFY_RESEND_COOLDOWN,
    ).exists()
    if recent_verify:
        return JsonResponse(
            {"error": _("Please wait before requesting again.")},
            status=429,
        )

    verify_window_start = timezone.now() - PASSWORD_CODE_WINDOW
    recent_phone = PasswordResetCode.objects.filter(
        phone_number=phone,
        purpose="verify",
        created_at__gte=verify_window_start,
    ).count()
    if recent_phone >= PASSWORD_CODE_PHONE_LIMIT:
        return JsonResponse({"error": _("Too many attempts. Try later.")}, status=429)

    recent_ip = 0
    if ip:
        recent_ip = PasswordResetCode.objects.filter(
            ip_address=ip,
            purpose="verify",
            created_at__gte=verify_window_start,
        ).count()
    if ip and recent_ip >= PASSWORD_CODE_IP_LIMIT:
        return JsonResponse(
            {"error": _("Too many attempts from this network.")},
            status=429,
        )

    error = _issue_verify_code(phone=phone, ip=ip)
    if error:
        return JsonResponse({"error": error}, status=429)

    return JsonResponse({"success": True})


def forgot_password(request):
    form = ForgotPasswordForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        phone = _resolve_phone_for_lookup(form.cleaned_data["phone"])
        ip = get_client_ip(request)
        rate_limit_error = _password_code_rate_limit_error(phone=phone, ip=ip)
        if rate_limit_error:
            form.add_error(None, rate_limit_error)
            return render(
                request,
                "auth/reset_password_request.html",
                {
                    "form": form,
                },
            )

        code = str(random.randint(100000, 999999))

        PasswordResetCode.objects.filter(
            phone_number=phone,
            purpose="reset",
            used=False,
        ).update(used=True)
        PasswordResetCode.objects.create(
            phone_number=phone,
            code=code,
            purpose="reset",
            ip_address=ip,
        )

        print("=== ABOUT TO SEND SMS ===")
        print("PHONE:", phone)
        send_sms(
            phone,
            _("Your NODO reset code is: %(code)s") % {"code": code},
        )

        request.session["reset_phone"] = phone
        return redirect("ui:reset_password_confirm")

    return render(
        request,
        "auth/reset_password_request.html",
        {
            "form": form,
        },
    )


def reset_password_confirm(request):
    phone = request.session.get("reset_phone")
    if not phone:
        return redirect("ui:forgot_password")

    form = ResetPasswordConfirmForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        phone_candidates = phone_lookup_candidates(phone)
        reset_code = (
            PasswordResetCode.objects.filter(
                phone_number=phone,
                purpose="reset",
                used=False,
            )
            .order_by("-created_at")
            .first()
        )

        if not reset_code:
            form.add_error("code", _("Reset code expired."))
        elif not reset_code.is_valid():
            reset_code.used = True
            reset_code.save(update_fields=["used"])
            form.add_error("code", _("Reset code expired."))
        elif reset_code.code != form.cleaned_data["code"].strip():
            reset_code.attempts += 1
            if reset_code.attempts >= PASSWORD_CODE_MAX_ATTEMPTS:
                reset_code.used = True
                reset_code.save(update_fields=["attempts", "used"])
                form.add_error("code", _("Reset code expired."))
            else:
                reset_code.save(update_fields=["attempts"])
                form.add_error("code", _("Invalid reset code."))
        else:
            password_hash = make_password(form.cleaned_data["new_password"])
            updated = 0
            updated += Client.objects.filter(phone_number__in=phone_candidates).update(password=password_hash)
            updated += Provider.objects.filter(phone_number__in=phone_candidates).update(password=password_hash)
            updated += Worker.objects.filter(phone_number__in=phone_candidates).update(password=password_hash)
            PasswordResetCode.objects.filter(
                phone_number=phone,
                purpose="reset",
            ).update(used=True)

            request.session.pop("reset_phone", None)

            if not updated:
                messages.warning(
                    request,
                    _("No account is linked to that phone number."),
                )
            else:
                messages.success(
                    request,
                    _("Password updated. You can log in now."),
                )

            return redirect("ui:login")

    return render(
        request,
        "auth/reset_password_confirm.html",
        {
            "form": form,
            "phone": phone,
        },
    )


@require_role("client")
def marketplace_search_view(request):
    client = _get_logged_client(request)
    if client is None:
        return redirect("ui:root_login")
    if not client.profile_completed:
        return redirect("client_complete_profile")

    service_type_id = (request.GET.get("service_type") or "").strip()
    province = (request.GET.get("province") or "").strip()
    postal_code = (request.GET.get("postal_code") or "").strip()
    city = (request.GET.get("city") or "").strip()
    language = (request.GET.get("language") or "").strip()
    search_q = (request.GET.get("q") or "").strip()
    provider_name = (request.GET.get("provider_name") or "").strip()
    provider_name_lower = provider_name.lower()
    only_certified = request.GET.get("only_certified")
    only_insured = request.GET.get("only_insured")
    use_profile_address_raw = request.GET.get("use_profile_address")
    if use_profile_address_raw is None:
        use_profile_address = not any((province, city, postal_code))
    else:
        use_profile_address = str(use_profile_address_raw).strip().lower() not in {
            "",
            "0",
            "false",
            "off",
            "no",
        }
    service_timing = (request.GET.get("service_timing") or "").strip().lower()
    if service_timing not in MARKETPLACE_SERVICE_TIMING_VALUES:
        service_timing = ""

    service_types = _marketplace_service_types(limit=20)
    profile_address = {
        "province": (getattr(client, "province", "") or "").strip(),
        "city": (getattr(client, "city", "") or "").strip(),
        "postal_code": (getattr(client, "postal_code", "") or "").strip(),
    }
    form_data = {
        "province": profile_address["province"] if use_profile_address else province,
        "city": profile_address["city"] if use_profile_address else city,
        "postal_code": profile_address["postal_code"] if use_profile_address else postal_code,
        "use_profile_address": use_profile_address,
    }
    link_province = form_data["province"] if use_profile_address else province
    link_city = form_data["city"] if use_profile_address else city
    link_postal_code = form_data["postal_code"] if use_profile_address else postal_code
    search_province = form_data["province"] or profile_address["province"]
    search_city = form_data["city"] or profile_address["city"]

    context = {
        "service_types": service_types,
        "form_data": form_data,
        "profile_address": profile_address,
        "selected_province": province,
        "selected_postal_code": postal_code,
        "selected_city": city,
        "link_province": link_province,
        "link_postal_code": link_postal_code,
        "link_city": link_city,
        "selected_language": language,
        "selected_query": search_q,
        "provider_name": provider_name,
        "selected_service_timing": service_timing,
        "service_timing_choices": MARKETPLACE_SERVICE_TIMING_CHOICES,
        "timing_required": False,
        "language_options": MARKETPLACE_LANGUAGE_CHOICES,
        "only_certified": bool(only_certified),
        "only_insured": bool(only_insured),
    }

    selected_service_type = None
    if service_type_id:
        selected_service_type = get_object_or_404(
            ServiceType,
            pk=service_type_id,
            is_active=True,
        )
        context["selected_service_type"] = selected_service_type

    if not service_type_id or not service_timing:
        context["timing_required"] = bool(service_type_id and not service_timing)
        context["mode"] = "services"
        return render(request, "marketplace/index.html", context)

    target_postal_prefix = _normalize_postal_prefix(link_postal_code)

    if service_timing == "emergency":
        emergency_cta_url = ""
        emergency_postal_code = (link_postal_code or profile_address["postal_code"] or "").strip()
        emergency_city = (link_city or profile_address["city"] or "").strip()
        emergency_province = (link_province or profile_address["province"] or "").strip()
        emergency_postal_prefix = target_postal_prefix or _normalize_postal_prefix(
            client.postal_code
        )
        if emergency_postal_prefix:
            emergency_params = {"fsa": emergency_postal_prefix}
            if emergency_postal_code:
                emergency_params["postal_code"] = emergency_postal_code
            if emergency_city:
                emergency_params["city"] = emergency_city
            if emergency_province:
                emergency_params["province"] = emergency_province
            emergency_params["service_type"] = service_type_id
            emergency_params["service_timing"] = service_timing
            emergency_cta_url = (
                f"{reverse('ui:providers_nearby')}?"
                f"{urlencode(emergency_params)}"
            )
        context.update(
            {
                "mode": "emergency",
                "selected_service_type": selected_service_type,
                "emergency_cta_url": emergency_cta_url,
            }
        )
        return render(request, "marketplace/index.html", context)

    providers = marketplace_ranked_queryset(
        province=search_province,
        city=search_city,
        service_type_id=service_type_id,
    ).prefetch_related("subservices", "extras")

    providers = _apply_marketplace_search_query(
        queryset=providers,
        search_q=search_q,
    )

    qs = providers
    if language:
        qs = qs.filter(provider__languages_spoken__icontains=language)

    if only_insured:
        qs = qs.filter(has_verified_insurance=True)

    if only_certified:
        qs = [ps for ps in qs if ps.is_compliant]

    visible_provider_services = _select_marketplace_card_rows(qs)
    provider_cards = _prepare_marketplace_provider_cards(visible_provider_services)
    if provider_name_lower:
        provider_cards = [
            card
            for card in provider_cards
            if provider_name_lower in (getattr(card, "card_display_name", "") or "").lower()
        ]
        provider_cards = sorted(
            provider_cards,
            key=lambda card: _provider_name_priority_key(card, provider_name_lower),
        )
    provider_cards = provider_cards[:20]

    context.update(
        {
            "providers": provider_cards,
            "mode": "providers",
            "selected_service_type": selected_service_type,
        }
    )
    return render(request, "marketplace/index.html", context)


def marketplace_results_view(request):
    if request.method != "POST":
        return redirect("ui:marketplace_search")

    client_id = request.session.get("client_id")
    if client_id:
        client = Client.objects.filter(pk=client_id).first()
        if client and not client.profile_completed:
            return redirect("client_complete_profile")

    service_type_id = request.POST.get("service_type")
    province = (request.POST.get("province") or "").strip()
    city = (request.POST.get("city") or "").strip()
    zone_id_raw = (request.POST.get("zone_id") or "").strip()

    results = []
    error = None
    zone_id = zone_id_raw

    try:
        parsed_service_type_id = int(service_type_id)
    except (TypeError, ValueError):
        parsed_service_type_id = None
        error = "Tipo de servicio invalido."

    parsed_zone_id = None
    if zone_id_raw:
        try:
            parsed_zone_id = int(zone_id_raw)
        except (TypeError, ValueError):
            error = "Zona invalida."

    if error is None and parsed_service_type_id and province and city:
        results = list(
            marketplace_ranked_queryset(
                service_type_id=parsed_service_type_id,
                province=province,
                city=city,
            )
            .order_by("-hybrid_score", "-safe_rating", "price_cents", "provider_id")[:20]
        )
        for provider in results:
            provider.display_price = provider.price_cents / 100
            provider.is_verified_badge = bool(provider.verified_bonus)
    elif error is None:
        error = "Complete tipo de servicio, provincia y ciudad."

    return render(
        request,
        "marketplace/results.html",
        {
            "results": results,
            "error": error,
            "service_type_id": service_type_id,
            "province": province,
            "city": city,
            "zone_id": zone_id,
        },
    )


def providers_nearby_view(request, job_id=None):
    if job_id is not None:
        job = get_object_or_404(
            Job.objects.select_related("location", "service_type"),
            pk=job_id,
        )
        job_location = getattr(job, "location", None)

        providers = []
        error = None
        selected_service_type = getattr(job, "service_type", None)
        service_type_id = str(job.service_type_id or "")
        fsa = _normalize_postal_prefix(job.postal_code)
        rating = ""
        search = ""

        if job_location is None:
            error = _("Job has no location.")
        elif selected_service_type is None:
            error = _("Job has no service type.")
        else:
            provider_offers = (
                marketplace_ranked_queryset(service_type_id=job.service_type_id)
                .exclude(
                    provider_id__in=JobProviderExclusion.objects.filter(job=job).values_list(
                        "provider_id",
                        flat=True,
                    )
                )
                .filter(provider__location__isnull=False)
                .select_related("provider", "provider__location", "provider__metrics", "service_type")
                .order_by("provider_id", "price_cents")
            )

            seen_provider_ids = set()
            for provider_offer in provider_offers:
                if provider_offer.provider_id in seen_provider_ids:
                    continue
                seen_provider_ids.add(provider_offer.provider_id)

                if not _provider_services_request_area(
                    provider=provider_offer.provider,
                    city=job.city,
                    province=job.province,
                    postal_code=job.postal_code,
                ):
                    continue

                try:
                    provider_location = provider_offer.provider.location
                except ProviderLocation.DoesNotExist:
                    continue

                provider_offer.distance_km = haversine_distance_km(
                    job_location.latitude,
                    job_location.longitude,
                    provider_location.latitude,
                    provider_location.longitude,
                )
                try:
                    provider_metrics = provider_offer.provider.metrics
                except ProviderMetrics.DoesNotExist:
                    provider_metrics = None
                provider_offer.score = provider_ranking_score(
                    distance_km=provider_offer.distance_km,
                    rating=provider_offer.safe_rating or 0,
                    response_minutes=getattr(provider_metrics, "avg_response_time", None),
                    acceptance_rate=getattr(provider_metrics, "acceptance_rate", None),
                    completion_rate=getattr(provider_metrics, "completion_rate", None),
                    last_job_assigned_at=provider_offer.provider.last_job_assigned_at,
                )
                provider_offer.display_price = provider_offer.price_cents / 100
                provider_logo = getattr(provider_offer.provider, "logo", None)
                provider_offer.card_logo_url = (
                    getattr(provider_logo, "url", "") if provider_logo else ""
                )
                providers.append(provider_offer)

            providers.sort(
                key=lambda provider_offer: (
                    -provider_offer.score,
                    provider_offer.distance_km,
                    provider_offer.provider_id,
                )
            )

        return render(
            request,
            "providers/nearby.html",
            {
                "providers": providers,
                "error": error,
                "fsa": fsa,
                "rating": rating,
                "search": search,
                "service_type_id": service_type_id,
                "selected_service_type": selected_service_type,
                "job": job,
            },
        )

    postal_code = (request.GET.get("postal_code") or "").strip()
    city = (request.GET.get("city") or "").strip()
    province = (request.GET.get("province") or "").strip()
    fsa = _normalize_postal_prefix(postal_code or request.GET.get("fsa"))
    service_type_id = (request.GET.get("service_type") or "").strip()
    rating = (request.GET.get("rating") or "").strip()
    search_term = (request.GET.get("search") or "").strip()
    search_term_lower = search_term.lower()
    service_timing = (request.GET.get("service_timing") or "").strip().lower()
    if service_timing not in MARKETPLACE_SERVICE_TIMING_VALUES:
        service_timing = ""

    providers = []
    error = None
    selected_service_type = None

    try:
        parsed_service_type_id = int(service_type_id)
    except (TypeError, ValueError):
        parsed_service_type_id = None
        error = _("Invalid service type.")

    if error is None and not fsa:
        error = _("Postal area is required.")

    if error is None:
        selected_service_type = ServiceType.objects.filter(
            pk=parsed_service_type_id,
            is_active=True,
        ).first()
        if selected_service_type is None:
            error = _("Invalid service type.")

    if error is None:
        providers_qs = (
            marketplace_ranked_queryset(service_type_id=parsed_service_type_id)
            .filter(
                provider__providerservicearea__is_active=True,
                provider__providerservicearea__postal_prefix__iexact=fsa,
            )
            .distinct()
            .prefetch_related("subservices", "extras")
        )

        if rating:
            try:
                minimum_rating = Decimal(rating)
            except (TypeError, ValueError, InvalidOperation):
                error = _("Invalid rating filter.")
            else:
                providers_qs = providers_qs.filter(safe_rating__gte=float(minimum_rating))

        if error is None:
            provider_rows = list(
                providers_qs.order_by(
                    "-hybrid_score",
                    "-safe_rating",
                    "price_cents",
                    "provider_id",
                )
            )

            if search_term_lower:
                provider_rows = [
                    row
                    for row in provider_rows
                    if _provider_matches_nearby_service_filter(row, search_term_lower)
                ]

            providers = _select_marketplace_card_rows(provider_rows)[:20]
            for provider_offer in providers:
                provider_offer.display_price = provider_offer.price_cents / 100
                provider_logo = getattr(provider_offer.provider, "logo", None)
                provider_offer.card_logo_url = (
                    getattr(provider_logo, "url", "") if provider_logo else ""
                )

    return render(
        request,
        "providers/nearby.html",
        {
            "providers": providers,
            "error": error,
            "fsa": fsa,
            "postal_code": postal_code,
            "city": city,
            "province": province,
            "rating": rating,
            "search": search_term,
            "service_timing": service_timing,
            "service_type_id": service_type_id,
            "selected_service_type": selected_service_type,
        },
    )


def _resolve_request_offer(*, provider, service_type_id="", provider_service_id=""):
    offers_qs = (
        ProviderService.objects.select_related("service_type")
        .filter(provider=provider, is_active=True, service_type__is_active=True)
    )
    if provider_service_id:
        return offers_qs.filter(pk=provider_service_id).first()
    if service_type_id:
        return offers_qs.filter(service_type_id=service_type_id).order_by("price_cents", "id").first()
    return offers_qs.order_by("price_cents", "id").first()


def _get_request_catalog(*, selected_offer):
    if selected_offer is None:
        return [], []

    subservices = list(
        selected_offer.subservices.filter(is_active=True).order_by("sort_order", "id")
    )

    real_extras = list(
        selected_offer.extras.filter(is_active=True).order_by("sort_order", "id")
    )

    if real_extras:
        return subservices, real_extras

    addon_offers = list(
        ProviderService.objects.filter(
            provider=selected_offer.provider,
            service_type=selected_offer.service_type,
            is_active=True,
            custom_name__istartswith="ADDON:",
        )
        .exclude(pk=selected_offer.pk)
        .order_by("price_cents", "id")
    )

    fallback_extras = []
    for addon in addon_offers:
        fallback_extras.append(
            SimpleNamespace(
                id=addon.pk,
                pk=addon.pk,
                name=addon.custom_name.replace("ADDON:", "", 1).strip(),
                unit_price=(Decimal(addon.price_cents) / Decimal("100")),
                is_active=True,
                allows_quantity=True,
                min_qty=1,
                max_qty=10,
                sort_order=0,
                is_provider_service_fallback=True,
                provider_service=addon,
            )
        )

    return subservices, fallback_extras


def _build_request_extra_options(*, extras, selected_ids=None, selected_quantities=None):
    selected_ids = set(selected_ids or [])
    selected_quantities = selected_quantities or {}
    options = []
    for extra in extras:
        extra_id = str(extra.pk)
        options.append(
            {
                "extra": extra,
                "selected": extra_id in selected_ids,
                "quantity": selected_quantities.get(extra_id, "1"),
            }
        )
    return options


def _normalize_postal_prefix(raw_postal_code):
    normalized = (raw_postal_code or "").replace(" ", "").strip().upper()
    if not normalized:
        return ""
    return normalized[:3]


def _redirect_to_nearby_providers(
    *,
    postal_code="",
    service_type_id="",
    city="",
    province="",
    search="",
):
    fsa = _normalize_postal_prefix(postal_code)
    if not fsa:
        return None

    params = {"fsa": fsa}
    if postal_code:
        params["postal_code"] = str(postal_code).strip()
    if city:
        params["city"] = str(city).strip()
    if province:
        params["province"] = str(province).strip()
    if service_type_id:
        params["service_type"] = str(service_type_id).strip()
    if search:
        params["search"] = str(search).strip()

    return redirect(f"{reverse('ui:providers_nearby')}?{urlencode(params)}")


def _provider_services_request_area(*, provider, city="", province="", postal_code=""):
    active_areas = ProviderServiceArea.objects.filter(provider=provider, is_active=True)
    if not active_areas.exists():
        return True

    postal_prefix = _normalize_postal_prefix(postal_code)
    normalized_city = (city or "").strip()
    normalized_province = (province or "").strip()

    active_areas_with_postal_prefix = active_areas.exclude(
        postal_prefix__isnull=True
    ).exclude(postal_prefix__exact="")
    if active_areas_with_postal_prefix.exists():
        if not postal_prefix:
            return False
        return active_areas_with_postal_prefix.filter(
            postal_prefix__iexact=postal_prefix
        ).exists()

    if not normalized_city:
        return False

    area_filters = Q(city__iexact=normalized_city)
    if normalized_province:
        area_filters &= Q(province__iexact=normalized_province)

    return active_areas.filter(area_filters).exists()


def _request_money(value) -> Decimal:
    return Decimal(value).quantize(REQUEST_MONEY_Q, rounding=ROUND_HALF_UP)


def _provider_service_money(provider_service) -> Decimal:
    return _request_money(Decimal(provider_service.price_cents) / Decimal("100"))


def _resolve_requested_base_price(*, selected_offer, selected_subservice) -> Decimal:
    if selected_subservice is not None:
        subservice_base_price = _request_money(selected_subservice.base_price or Decimal("0.00"))
        if subservice_base_price > Decimal("0.00"):
            return subservice_base_price
    if selected_offer is None:
        return _request_money(Decimal("0.00"))
    return _provider_service_money(selected_offer)


def _build_request_pricing_snapshot(
    *,
    selected_offer,
    selected_subservice,
    requested_quantity,
    selected_requested_extras,
    tax_region_code="",
):
    base_unit_price = _resolve_requested_base_price(
        selected_offer=selected_offer,
        selected_subservice=selected_subservice,
    )
    base_line_total = _request_money(base_unit_price * requested_quantity)
    priced_extras = []
    extras_total = Decimal("0.00")

    for extra, quantity in selected_requested_extras:
        unit_price = _request_money(extra.unit_price or Decimal("0.00"))
        line_total = _request_money(unit_price * quantity)
        extras_total += line_total
        priced_extras.append(
            {
                "extra": extra,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
            }
        )

    subtotal = _request_money(base_line_total + extras_total)
    subtotal_cents = int((subtotal * 100).quantize(Decimal("1")))
    province = (tax_region_code or "").strip().upper()
    tax_rule = get_tax_rule_for_region(province)
    tax_cents = compute_tax_cents(subtotal_cents, tax_rule)
    total_cents = subtotal_cents + tax_cents
    tax_amount = Decimal(tax_cents) / Decimal("100")
    total = Decimal(total_cents) / Decimal("100")
    return {
        "base_unit_price": base_unit_price,
        "requested_quantity": requested_quantity,
        "base_line_total": base_line_total,
        "extras_total": _request_money(extras_total),
        "subtotal": subtotal,
        "subtotal_cents": subtotal_cents,
        "tax": tax_amount,
        "tax_cents": tax_cents,
        "tax_rate_bps": tax_rule.rate_bps,
        "tax_region": province,
        "tax_region_code": province,
        "total": total,
        "total_cents": total_cents,
        "priced_extras": priced_extras,
    }


def _friendly_job_status_label(status: str) -> str:
    return FRIENDLY_JOB_STATUS_LABELS.get(status, status.replace("_", " ").title())


def _job_status_label(job) -> str:
    return get_visible_job_status_label(job)


def _job_event_type_label(event_type: str) -> str:
    return str(event_type or "").replace("_", " ").title()


def _build_job_event_timeline(job):
    events = list(job.events.order_by("created_at", "id"))
    for event in events:
        event.event_type_label = _job_event_type_label(event.event_type)
        event.actor_role_label = event.get_actor_role_display() or str(
            event.actor_role or ""
        ).title()
    return events


def _billing_unit_display(value: str) -> str:
    if not value:
        return ""
    return dict(ProviderService.BILLING_UNIT_CHOICES).get(value, value)


def _normalize_request_service_timing(raw_value: str, *, fallback_job_mode: str = "") -> str:
    normalized = (raw_value or "").strip().lower()
    if normalized in REQUEST_SERVICE_TIMING_VALUES:
        return normalized

    if fallback_job_mode == Job.JobMode.SCHEDULED:
        return "scheduled"

    # Preserve existing request links and tests that still post legacy on-demand mode.
    return "urgent"


def _request_create_search_context(
    *,
    service_type_id="",
    postal_code="",
    city="",
    province="",
    service_timing="",
    search="",
    provider_name="",
):
    return {
        "service_type_id": str(service_type_id or "").strip(),
        "search": search,
        "provider_name": provider_name,
        "postal_code": postal_code,
        "city": city,
        "province": province,
        "service_timing": service_timing,
    }


def _request_flow_query_params(
    *,
    service_type_id="",
    postal_code="",
    city="",
    province="",
    service_timing="",
    search="",
    provider_name="",
):
    params = {}
    if service_timing:
        params["service_timing"] = str(service_timing).strip()
    if service_type_id:
        params["service_type"] = str(service_type_id).strip()
    if postal_code:
        params["postal_code"] = str(postal_code).strip()
    if city:
        params["city"] = str(city).strip()
    if province:
        params["province"] = str(province).strip()
    if search:
        params["search"] = str(search).strip()
    if provider_name:
        params["provider_name"] = str(provider_name).strip()
    return params


def _job_created_query_params(
    *,
    service_timing="",
    search="",
    provider_name="",
):
    params = {}
    if service_timing:
        params["service_timing"] = str(service_timing).strip()
    if search:
        params["search"] = str(search).strip()
    if provider_name:
        params["provider_name"] = str(provider_name).strip()
    return params


def _marketplace_query_params_from_request_context(request_context):
    params = {}
    if request_context.get("service_timing"):
        params["service_timing"] = request_context["service_timing"]
    if request_context.get("service_type_id"):
        params["service_type"] = request_context["service_type_id"]
    if request_context.get("postal_code"):
        params["postal_code"] = request_context["postal_code"]
    if request_context.get("city"):
        params["city"] = request_context["city"]
    if request_context.get("province"):
        params["province"] = request_context["province"]
    if request_context.get("search"):
        params["q"] = request_context["search"]
    if request_context.get("provider_name"):
        params["provider_name"] = request_context["provider_name"]
    return params


def _url_with_query(base_url: str, params: dict) -> str:
    filtered_params = {
        key: value
        for key, value in (params or {}).items()
        if str(value or "").strip()
    }
    if not filtered_params:
        return base_url
    return f"{base_url}?{urlencode(filtered_params)}"


def _job_timing_context(*, job, raw_service_timing=""):
    service_timing = _normalize_request_service_timing(
        raw_service_timing,
        fallback_job_mode=getattr(job, "job_mode", ""),
    )
    return {
        "service_timing": service_timing,
        "service_timing_label": REQUEST_SERVICE_TIMING_LABELS.get(
            service_timing,
            service_timing.replace("_", " ").title(),
        ),
        "next_step_label": JOB_CREATED_NEXT_STEP_LABELS.get(
            service_timing,
            "Request submitted",
        ),
    }


def request_create_view(request, provider_id):
    provider = get_object_or_404(Provider, pk=provider_id, is_active=True)
    provider_offers = list(
        ProviderService.objects.select_related("service_type")
        .filter(provider=provider, is_active=True, service_type__is_active=True)
        .order_by("service_type__name", "price_cents", "id")
    )
    service_options = []
    offers_by_service_type = {}

    for offer in provider_offers:
        service_type_key = str(offer.service_type_id)
        if service_type_key not in offers_by_service_type:
            offers_by_service_type[service_type_key] = {
                "service_type": offer.service_type,
                "offers": [],
            }

        offers_by_service_type[service_type_key]["offers"].append(
            {
                "id": str(offer.pk),
                "custom_name": offer.custom_name,
                "price_display": offer.price_cents / 100,
                "billing_unit": (
                    offer.get_billing_unit_display()
                    if hasattr(offer, "get_billing_unit_display")
                    else offer.billing_unit
                ),
            }
        )

    service_options = list(offers_by_service_type.values())
    service_type_id = (
        request.POST.get("service_type")
        or request.POST.get("service_type_id")
        or request.GET.get("service_type_id")
        or ""
    ).strip()
    provider_service_id = (
        request.POST.get("provider_service_id")
        or request.GET.get("provider_service_id")
        or ""
    ).strip()
    requested_service_timing = _normalize_request_service_timing(
        request.GET.get("service_timing") or "",
        fallback_job_mode=(request.GET.get("job_mode") or "").strip(),
    )
    search_term = (request.POST.get("search") or request.GET.get("search") or "").strip()
    provider_name = (
        request.GET.get("provider_name")
        or request.POST.get("provider_name")
        or ""
    ).strip()
    client_id = request.session.get("client_id")
    session_client = Client.objects.filter(pk=client_id).first() if client_id else None
    if client_id and session_client is None:
        request.session.pop("client_id", None)
    client_authenticated = bool(session_client)
    postal_code = (
        request.GET.get("postal_code")
        or getattr(session_client, "postal_code", None)
        or ""
    ).strip()
    city = (
        request.GET.get("city")
        or getattr(session_client, "city", None)
        or ""
    ).strip()
    province = (
        request.GET.get("province")
        or getattr(session_client, "province", None)
        or ""
    ).strip()
    # Freeze the request location contract early so downstream logic shares one source.
    location = {
        "postal_code": postal_code,
        "city": city,
        "province": province,
    }
    provider_postal_prefixes = sorted(
        {
            (prefix or "").strip().upper()
            for prefix in ProviderServiceArea.objects.filter(
                provider=provider,
                is_active=True,
            )
            .exclude(postal_prefix__isnull=True)
            .exclude(postal_prefix__exact="")
            .values_list("postal_prefix", flat=True)
            if (prefix or "").strip()
        }
    )

    selected_offer = _resolve_request_offer(
        provider=provider,
        service_type_id=service_type_id,
        provider_service_id=provider_service_id,
    )
    if selected_offer is not None:
        if not service_type_id:
            service_type_id = str(selected_offer.service_type_id)
        provider_service_id = str(selected_offer.pk)
        selected_offer.display_price = selected_offer.price_cents / 100
    request_subservices, request_extras = _get_request_catalog(selected_offer=selected_offer)
    request_extra_options = _build_request_extra_options(extras=request_extras)
    request_tax_rates = {
        region_code: rule.rate_bps
        for region_code, rule in TAX_RULES_BY_REGION.items()
    }

    compliance_blocked = bool(selected_offer and not selected_offer.is_compliant)
    compliance_error = (
        _("This service cannot be requested until provider compliance is complete.")
        if compliance_blocked
        else None
    )

    default_form_data = {
        "country": getattr(session_client, "country", None) or "CA",
        "province": location["province"] or provider.province,
        "city": location["city"] or provider.city,
        "postal_code": location["postal_code"] or "",
        "address_line1": getattr(session_client, "address_line1", None) or "",
        "use_other_address": False,
        "service_timing": requested_service_timing,
        "job_mode": REQUEST_SERVICE_TIMING_TO_JOB_MODE[requested_service_timing],
        "scheduled_date": "",
        "scheduled_start_time": "",
        "service_type": service_type_id,
        "provider_service_id": provider_service_id,
        "requested_quantity": "1",
        "requested_subservice_id": (request.GET.get("requested_subservice_id") or "").strip(),
        "selected_extra_ids": [],
        "selected_extra_quantities": {},
    }
    if session_client is None:
        default_form_data.update(
            {
                "first_name": "",
                "last_name": "",
                "phone_number": "",
                "email": "",
            }
        )

    request_area_error = None
    if default_form_data["postal_code"] and not _provider_services_request_area(
        provider=provider,
        city=default_form_data["city"],
        province=default_form_data["province"],
        postal_code=default_form_data["postal_code"],
    ):
        request_area_error = REQUEST_AREA_UNAVAILABLE_ERROR
        if session_client is not None:
            default_form_data["use_other_address"] = True

    default_pricing_snapshot = None
    default_selected_subservice = None
    if selected_offer is not None:
        if default_form_data["requested_subservice_id"]:
            default_selected_subservice = next(
                (
                    subservice
                    for subservice in request_subservices
                    if str(subservice.pk) == default_form_data["requested_subservice_id"]
                ),
                None,
            )
            if default_selected_subservice is None:
                default_form_data["requested_subservice_id"] = ""
        try:
            default_pricing_snapshot = _build_request_pricing_snapshot(
                selected_offer=selected_offer,
                selected_subservice=default_selected_subservice,
                requested_quantity=_request_money(
                    Decimal(default_form_data["requested_quantity"] or "1")
                ),
                selected_requested_extras=[],
                tax_region_code=default_form_data["province"],
            )
        except (InvalidOperation, TypeError, ValueError):
            default_pricing_snapshot = None

    if request.method == "GET":
        context = {
            "provider": provider,
            "service_options": service_options,
            "selected_offer": selected_offer,
            "service_type_id": service_type_id,
            "form_data": default_form_data,
            "client": session_client,
            "client_authenticated": client_authenticated,
            "compliance_blocked": compliance_blocked,
            "error": request_area_error or compliance_error,
            "request_subservices": request_subservices,
            "request_extra_options": request_extra_options,
            "provider_postal_prefixes": provider_postal_prefixes,
            "pricing": default_pricing_snapshot,
            "request_tax_rates": request_tax_rates,
            "show_service_address_editor": bool(
                client_authenticated and default_form_data["use_other_address"]
            ),
        }
        context.update(
            _request_create_search_context(
                postal_code=postal_code,
                city=city,
                province=province,
                service_timing=default_form_data["service_timing"],
                search=search_term,
                provider_name=provider_name,
            )
        )
        return render(request, "request/create.html", context)

    selected_extra_quantities = {
        key.replace("extra_qty_", "", 1): (value or "").strip()
        for key, value in request.POST.items()
        if key.startswith("extra_qty_")
    }
    selected_extra_ids = [
        value.strip()
        for value in request.POST.getlist("selected_extras")
        if (value or "").strip()
    ]
    raw_requested_quantity = request.POST.get("requested_quantity")

    if session_client is not None:
        use_other_address = bool(request.POST.get("use_other_address"))
        posted_service_timing = (request.POST.get("service_timing") or "").strip().lower()
        use_posted_location_contract = (
            use_other_address or posted_service_timing in REQUEST_SERVICE_TIMING_VALUES
        )
        posted_province = (request.POST.get("province") or "").strip()
        posted_city = (request.POST.get("city") or "").strip()
        posted_postal_code = (request.POST.get("postal_code") or "").strip()
        posted_address_line1 = (request.POST.get("address_line1") or "").strip()
        form_data = {
            "first_name": session_client.first_name,
            "last_name": session_client.last_name,
            "phone_number": session_client.phone_number,
            "email": session_client.email,
            "country": session_client.country,
            "province": (
                posted_province
                if use_posted_location_contract and posted_province
                else session_client.province
            ),
            "city": (
                posted_city
                if use_posted_location_contract and posted_city
                else session_client.city
            ),
            "postal_code": (
                posted_postal_code
                if use_posted_location_contract and posted_postal_code
                else session_client.postal_code
            ),
            "address_line1": (
                posted_address_line1
                if use_posted_location_contract and posted_address_line1
                else session_client.address_line1
            ),
            "use_other_address": use_other_address,
            "service_type": (request.POST.get("service_type") or "").strip(),
            "provider_service_id": (request.POST.get("provider_service_id") or "").strip(),
            "requested_quantity": (
                raw_requested_quantity if raw_requested_quantity is not None else "1"
            ).strip(),
            "requested_subservice_id": (request.POST.get("requested_subservice_id") or "").strip(),
            "selected_extra_ids": selected_extra_ids,
            "selected_extra_quantities": selected_extra_quantities,
            "service_timing": (request.POST.get("service_timing") or "").strip().lower(),
            "scheduled_date": (request.POST.get("scheduled_date") or "").strip(),
            "scheduled_start_time": (request.POST.get("scheduled_time") or "").strip(),
        }
    else:
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
            "use_other_address": False,
            "service_type": (request.POST.get("service_type") or "").strip(),
            "provider_service_id": (request.POST.get("provider_service_id") or "").strip(),
            "requested_quantity": (
                raw_requested_quantity if raw_requested_quantity is not None else "1"
            ).strip(),
            "requested_subservice_id": (request.POST.get("requested_subservice_id") or "").strip(),
            "selected_extra_ids": selected_extra_ids,
            "selected_extra_quantities": selected_extra_quantities,
            "service_timing": (request.POST.get("service_timing") or "").strip().lower(),
            "scheduled_date": (request.POST.get("scheduled_date") or "").strip(),
            "scheduled_start_time": (request.POST.get("scheduled_time") or "").strip(),
        }

    form_data["service_timing"] = _normalize_request_service_timing(
        form_data.get("service_timing", ""),
        fallback_job_mode=(request.POST.get("job_mode") or "").strip(),
    )
    form_data["job_mode"] = REQUEST_SERVICE_TIMING_TO_JOB_MODE[form_data["service_timing"]]

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
        error = _("Complete all required fields.")
    elif form_data["service_timing"] not in REQUEST_SERVICE_TIMING_VALUES:
        error = _("Invalid service timing.")
    elif form_data["service_timing"] == "scheduled" and (
        not form_data["scheduled_date"] or not form_data["scheduled_start_time"]
    ):
        error = _("Scheduled mode requires date and time.")

    scheduled_date_value = None
    scheduled_start_time_value = None
    if error is None and form_data["service_timing"] == "scheduled":
        scheduled_date_value = parse_date(form_data["scheduled_date"])
        scheduled_start_time_value = parse_time(form_data["scheduled_start_time"])
        if scheduled_date_value is None or scheduled_start_time_value is None:
            error = _("Scheduled mode requires a valid date and time.")
        elif scheduled_date_value <= timezone.localdate():
            error = _("Scheduled mode requires a future date.")

    requested_quantity_decimal = None
    if error is None:
        if not form_data["requested_quantity"]:
            error = _("Quantity is required.")
        else:
            try:
                requested_quantity_decimal = Decimal(form_data["requested_quantity"])
            except (TypeError, ValueError, InvalidOperation):
                error = _("Invalid quantity.")

    if error is None and requested_quantity_decimal <= Decimal("0"):
        error = _("Quantity must be greater than zero.")

    if error is None:
        requested_quantity_decimal = _request_money(requested_quantity_decimal)

    service_type = None
    if error is None:
        service_type = ServiceType.objects.filter(
            pk=form_data["service_type"],
            is_active=True,
        ).first()
        if service_type is None:
            error = _("Invalid service type.")

    if error is None:
        if form_data["provider_service_id"]:
            selected_offer = _resolve_request_offer(
                provider=provider,
                provider_service_id=form_data["provider_service_id"],
            )
            if selected_offer is None:
                error = _("Invalid provider service.")
            elif str(selected_offer.service_type_id) != form_data["service_type"]:
                error = _("Invalid provider service for this service type.")
        else:
            selected_offer = _resolve_request_offer(
                provider=provider,
                service_type_id=form_data["service_type"],
            )

        if error is None:
            if selected_offer is None:
                error = _("Provider must have an active priced service for this service type.")
            else:
                selected_offer.display_price = selected_offer.price_cents / 100
                form_data["provider_service_id"] = str(selected_offer.pk)
            if error is None and not selected_offer.is_compliant:
                error = _("This service cannot be requested until provider compliance is complete.")

    geo = None
    if error is None:
        geo = geocode_address(
            form_data["postal_code"],
            city=form_data["city"],
            province=form_data["province"],
        )
        if geo:
            real_province = (extract_province(geo["components"]) or "").strip().upper()
            requested_province = (form_data["province"] or "").strip().upper()
            if real_province and real_province != requested_province:
                error = _("The postal code does not belong to the selected province.")

    if error is None and not _provider_services_request_area(
        provider=provider,
        city=form_data["city"],
        province=form_data["province"],
        postal_code=form_data["postal_code"],
    ):
        nearby_redirect = _redirect_to_nearby_providers(
            postal_code=form_data["postal_code"],
            service_type_id=form_data["service_type"],
            city=form_data["city"],
            province=form_data["province"],
            search=search_term,
        )
        if nearby_redirect is not None:
            return nearby_redirect
        error = REQUEST_AREA_UNAVAILABLE_ERROR

    request_subservices, request_extras = _get_request_catalog(selected_offer=selected_offer)
    request_extra_options = _build_request_extra_options(
        extras=request_extras,
        selected_ids=form_data["selected_extra_ids"],
        selected_quantities=form_data["selected_extra_quantities"],
    )

    selected_subservice = None
    selected_requested_extras = []
    request_pricing_snapshot = None
    if error is None:
        subservices_by_id = {str(item.pk): item for item in request_subservices}
        extras_by_id = {str(item.pk): item for item in request_extras}

        if form_data["requested_subservice_id"]:
            selected_subservice = subservices_by_id.get(form_data["requested_subservice_id"])
            if selected_subservice is None:
                error = _("Invalid subservice.")

        if error is None:
            seen_extra_ids = set()
            for extra_id in form_data["selected_extra_ids"]:
                if extra_id in seen_extra_ids:
                    continue
                seen_extra_ids.add(extra_id)

                extra = extras_by_id.get(extra_id)
                if extra is None:
                    error = _("Invalid extra selection.")
                    break

                quantity_raw = form_data["selected_extra_quantities"].get(extra_id, "")
                if extra.allows_quantity:
                    if quantity_raw == "":
                        quantity = 1
                    else:
                        try:
                            quantity = int(quantity_raw)
                        except (TypeError, ValueError):
                            error = _("Invalid quantity for %(name)s.") % {"name": extra.name}
                            break

                    if quantity < 0:
                        error = _("Invalid quantity for %(name)s.") % {"name": extra.name}
                        break
                else:
                    quantity = 1

                selected_requested_extras.append((extra, quantity))

        if error is None:
            request_pricing_snapshot = _build_request_pricing_snapshot(
                selected_offer=selected_offer,
                selected_subservice=selected_subservice,
                requested_quantity=requested_quantity_decimal,
                selected_requested_extras=selected_requested_extras,
                tax_region_code=form_data["province"],
            )

    compliance_blocked = bool(selected_offer and not selected_offer.is_compliant)

    if error is None:
        client = session_client
        if client is None:
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
            if not client.is_phone_verified:
                raise PermissionError("PHONE_NOT_VERIFIED")
            if not client.profile_completed:
                messages.warning(
                    request,
                    _("You must complete your profile before creating a job."),
                )
                request.session["client_id"] = client.pk
                return redirect("client_complete_profile")
            with transaction.atomic():
                created_job_status = (
                    Job.JobStatus.SCHEDULED_PENDING_ACTIVATION
                    if form_data["job_mode"] == Job.JobMode.SCHEDULED
                    else Job.JobStatus.WAITING_PROVIDER_RESPONSE
                )
                created_job = Job.objects.create(
                    selected_provider=provider,
                    client=client,
                    service_type=service_type,
                    job_mode=form_data["job_mode"],
                    scheduled_date=(
                        scheduled_date_value
                        if form_data["job_mode"] == Job.JobMode.SCHEDULED
                        else None
                    ),
                    scheduled_start_time=(
                        scheduled_start_time_value
                        if form_data["job_mode"] == Job.JobMode.SCHEDULED
                        else None
                    ),
                    is_asap=form_data["job_mode"] == Job.JobMode.ON_DEMAND,
                    country=form_data["country"],
                    province=form_data["province"],
                    city=form_data["city"],
                    postal_code=form_data["postal_code"],
                    address_line1=form_data["address_line1"],
                    job_status=created_job_status,
                    provider_service_name_snapshot=(
                        selected_offer.custom_name if selected_offer else ""
                    ),
                    requested_subservice_name=(
                        selected_subservice.name if selected_subservice else ""
                    ),
                    requested_subservice_id_snapshot=(
                        int(selected_subservice.pk) if selected_subservice else None
                    ),
                    requested_subservice_base_price_snapshot=(
                        request_pricing_snapshot["base_unit_price"] if request_pricing_snapshot else None
                    ),
                    requested_quantity_snapshot=(
                        request_pricing_snapshot["requested_quantity"] if request_pricing_snapshot else None
                    ),
                    requested_unit_price_snapshot=(
                        request_pricing_snapshot["base_unit_price"] if request_pricing_snapshot else None
                    ),
                    requested_billing_unit_snapshot=(
                        selected_offer.billing_unit if selected_offer else ""
                    ),
                    requested_base_line_total_snapshot=(
                        request_pricing_snapshot["base_line_total"] if request_pricing_snapshot else None
                    ),
                    requested_subtotal_snapshot=(
                        request_pricing_snapshot["subtotal"] if request_pricing_snapshot else None
                    ),
                    requested_tax_snapshot=(
                        request_pricing_snapshot["tax"] if request_pricing_snapshot else None
                    ),
                    requested_tax_rate_bps_snapshot=(
                        request_pricing_snapshot["tax_rate_bps"] if request_pricing_snapshot else None
                    ),
                    requested_tax_region_code_snapshot=(
                        request_pricing_snapshot["tax_region_code"] if request_pricing_snapshot else ""
                    ),
                    requested_total_snapshot=(
                        request_pricing_snapshot["total"] if request_pricing_snapshot else None
                    ),
                )
                if geo:
                    JobLocation.objects.create(
                        job=created_job,
                        latitude=Decimal(str(geo["lat"])),
                        longitude=Decimal(str(geo["lng"])),
                        postal_code=form_data["postal_code"],
                        city=form_data["city"],
                        province=form_data["province"],
                        country=form_data["country"] or "Canada",
                    )
                apply_provider_service_snapshot_to_job(
                    job=created_job,
                    provider_service=selected_offer,
                )
                if request_pricing_snapshot and request_pricing_snapshot["priced_extras"]:
                    JobRequestedExtra.objects.bulk_create(
                        [
                            JobRequestedExtra(
                                job=created_job,
                                provider_service_extra=(
                                    item["extra"]
                                    if isinstance(item["extra"], ProviderServiceExtra)
                                    else None
                                ),
                                extra_name_snapshot=item["extra"].name,
                                quantity=item["quantity"],
                                unit_price_snapshot=item["unit_price"],
                                line_total_snapshot=item["line_total"],
                            )
                            for item in request_pricing_snapshot["priced_extras"]
                        ]
                    )
                create_job_event(
                    job=created_job,
                    event_type=JobEvent.EventType.JOB_CREATED,
                    actor_role=JobEvent.ActorRole.CLIENT,
                    payload={
                        "source": "request_create",
                        "service_timing": form_data["service_timing"],
                    },
                    provider_id=provider.provider_id,
                    unique_per_job=True,
                )
                if created_job_status == Job.JobStatus.WAITING_PROVIDER_RESPONSE:
                    create_job_event(
                        job=created_job,
                        event_type=JobEvent.EventType.WAITING_PROVIDER_RESPONSE,
                        actor_role=JobEvent.ActorRole.SYSTEM,
                        payload={
                            "source": "request_create",
                            "service_timing": form_data["service_timing"],
                        },
                        provider_id=provider.provider_id,
                        note="request created and waiting for provider response",
                    )
        except PermissionError as exc:
            if str(exc) == "PHONE_NOT_VERIFIED":
                return HttpResponseForbidden(
                    _("Phone verification is required before creating a request.")
                )
            return HttpResponseForbidden(str(exc))
        except ValidationError as exc:
            error = "; ".join(exc.messages) or _("The request could not be created.")

    if error is not None:
        if session_client is not None and error == REQUEST_AREA_UNAVAILABLE_ERROR:
            form_data["use_other_address"] = True
        context = {
            "provider": provider,
            "service_options": service_options,
            "selected_offer": selected_offer,
            "service_type_id": service_type_id,
            "form_data": form_data,
            "error": error,
            "client": session_client,
            "client_authenticated": client_authenticated,
            "compliance_blocked": compliance_blocked,
            "request_subservices": request_subservices,
            "request_extra_options": request_extra_options,
            "provider_postal_prefixes": provider_postal_prefixes,
            "pricing": request_pricing_snapshot,
            "request_tax_rates": request_tax_rates,
            "show_service_address_editor": bool(
                client_authenticated and form_data["use_other_address"]
            ),
        }
        context.update(
            _request_create_search_context(
                postal_code=form_data["postal_code"],
                city=form_data["city"],
                province=form_data["province"],
                service_timing=form_data["service_timing"],
                service_type_id=form_data["service_type"],
                search=search_term,
                provider_name=provider_name,
            )
        )
        return render(request, "request/create.html", context)

    return redirect(
        _url_with_query(
            reverse("ui:job_created", args=[created_job.job_id]),
            _job_created_query_params(
                service_timing=form_data["service_timing"],
                search=search_term,
                provider_name=provider_name,
            ),
        )
    )


def job_created_view(request, job_id):
    job = get_object_or_404(
        Job.objects.select_related(
            "selected_provider",
            "client",
            "service_type",
            "provider_service",
        ).prefetch_related("requested_extras"),
        pk=job_id,
    )
    _attach_job_lifecycle_details(job)
    timing_context = _job_timing_context(
        job=job,
        raw_service_timing=(request.GET.get("service_timing") or "").strip(),
    )
    request_context = _request_create_search_context(
        service_type_id=(request.GET.get("service_type") or job.service_type_id or ""),
        postal_code=(request.GET.get("postal_code") or job.postal_code or ""),
        city=(request.GET.get("city") or job.city or ""),
        province=(request.GET.get("province") or job.province or ""),
        service_timing=timing_context["service_timing"],
        search=(request.GET.get("search") or "").strip(),
        provider_name=(request.GET.get("provider_name") or "").strip(),
    )
    return render(
        request,
        "jobs/created.html",
        {
            "job": job,
            "job_status_label": _job_status_label(job),
            "request_status_url": _url_with_query(
                reverse("ui:request_status", args=[job.job_id]),
                _request_flow_query_params(**request_context),
            ),
            **request_context,
            **timing_context,
        },
    )


def request_status_lookup_view(request):
    job_id = request.GET.get("job_id")

    try:
        job_id_int = int(job_id)
    except (TypeError, ValueError):
        return redirect("ui:portal")

    return redirect("ui:request_status", job_id=job_id_int)


@require_POST
def register_push_device(request):
    if not request.user.is_authenticated:
        return JsonResponse(
            {"ok": False, "error": "Authentication required."},
            status=403,
        )

    if request.content_type and "application/json" in request.content_type:
        try:
            payload = json.loads((request.body or b"{}").decode("utf-8"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return JsonResponse(
                {"ok": False, "error": "Invalid JSON payload."},
                status=400,
            )
    else:
        payload = request.POST

    role = str(payload.get("role") or "").strip().lower()
    platform = str(payload.get("platform") or "").strip().lower()
    token = str(payload.get("token") or "").strip()

    if not role or not platform or not token:
        return JsonResponse(
            {"ok": False, "error": "role, platform and token are required."},
            status=400,
        )

    if role not in PushDevice.Role.values:
        return JsonResponse({"ok": False, "error": "Invalid role."}, status=400)

    if platform not in PushDevice.Platform.values:
        return JsonResponse({"ok": False, "error": "Invalid platform."}, status=400)

    device, created = register_push_device_for_user(
        user=request.user,
        role=role,
        platform=platform,
        token=token,
    )
    return JsonResponse(
        {
            "ok": True,
            "created": created,
            "device_id": device.pk,
            "role": device.role,
            "platform": device.platform,
            "is_active": device.is_active,
        }
    )


def _attach_job_lifecycle_details(job):
    active_assignment = (
        job.assignments.select_related("provider")
        .filter(is_active=True)
        .order_by("-assignment_id")
        .first()
    )
    confirmed_event = (
        job.events.filter(event_type=JobEvent.EventType.CLIENT_CONFIRMED)
        .order_by("-created_at")
        .first()
    )

    job.active_assignment = active_assignment
    job.confirmed_event = confirmed_event
    job.display_provider = job.selected_provider or getattr(active_assignment, "provider", None)
    prefetched_requested_extras = getattr(job, "_prefetched_objects_cache", {}).get(
        "requested_extras"
    )
    if prefetched_requested_extras is None:
        job.requested_extras_list = list(job.requested_extras.all())
    else:
        job.requested_extras_list = list(prefetched_requested_extras)
    for requested_extra in job.requested_extras_list:
        requested_extra.has_price_snapshot = (
            requested_extra.unit_price_snapshot is not None
            or requested_extra.line_total_snapshot is not None
        )
    job.friendly_status_label = _friendly_job_status_label(job.job_status)
    job.provider_service_name_display = (
        (job.provider_service_name_snapshot or "").strip()
        or getattr(getattr(job, "provider_service", None), "custom_name", "")
    )
    job.requested_billing_unit_display = _billing_unit_display(
        job.requested_billing_unit_snapshot
    )
    job.has_requested_pricing_snapshot = any(
        value is not None
        for value in (
            job.requested_subservice_base_price_snapshot,
            job.requested_quantity_snapshot,
            job.requested_unit_price_snapshot,
            job.requested_base_line_total_snapshot,
            job.requested_subtotal_snapshot,
            job.requested_tax_snapshot,
            job.requested_total_snapshot,
        )
    )
    return job


def request_status_view(request, job_id):
    request_source = request.POST if request.method == "POST" else request.GET
    raw_service_timing = (
        request_source.get("service_timing")
    ) or ""
    raw_service_timing = raw_service_timing.strip()
    raw_service_type_id = (request_source.get("service_type") or "").strip()
    raw_postal_code = (request_source.get("postal_code") or "").strip()
    raw_city = (request_source.get("city") or "").strip()
    raw_province = (request_source.get("province") or "").strip()
    raw_search = (request_source.get("search") or "").strip()
    raw_provider_name = (request_source.get("provider_name") or "").strip()
    next_url = (
        request.POST.get("next")
        if request.method == "POST"
        else request.GET.get("next")
    ) or ""
    next_url = next_url.strip()
    if next_url and not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = ""

    def request_status_query_params():
        return _request_flow_query_params(
            service_type_id=raw_service_type_id,
            postal_code=raw_postal_code,
            city=raw_city,
            province=raw_province,
            service_timing=raw_service_timing,
            search=raw_search,
            provider_name=raw_provider_name,
        )

    def redirect_to_request_status():
        request_status_url = reverse("ui:request_status", args=[job.job_id])
        query_params = request_status_query_params()
        if next_url:
            query_params["next"] = next_url
        if query_params:
            request_status_url = f"{request_status_url}?{urlencode(query_params)}"
        return redirect(request_status_url)

    if request.method == "POST":
        job = get_object_or_404(Job, pk=job_id)
        action = request.POST.get("action")

        if action == "confirm_provider":
            try:
                result = job_services.confirm_marketplace_provider(job_id=job.job_id)
            except job_services.MarketplaceDecisionConflict as exc:
                messages.error(
                    request,
                    _("Unable to confirm provider: %(error)s") % {"error": exc},
                )
            else:
                messages.success(
                    request,
                    _("Provider confirmed: %(result)s") % {"result": result},
                )
            return redirect_to_request_status()

        if action == "reject_provider":
            try:
                result = job_services.reject_marketplace_provider(job_id=job.job_id)
            except job_services.MarketplaceDecisionConflict as exc:
                messages.error(
                    request,
                    _("Unable to reject provider: %(error)s") % {"error": exc},
                )
            else:
                messages.success(
                    request,
                    _("Provider rejected: %(result)s") % {"result": result},
                )
            return redirect_to_request_status()

        if action == "cancel_request":
            if job.job_status == Job.JobStatus.PENDING_CLIENT_CONFIRMATION:
                try:
                    result = job_services.apply_client_marketplace_decision(
                        job_id=job.job_id,
                        action=job_services.MARKETPLACE_ACTION_CANCEL_JOB,
                    )
                except job_services.MarketplaceDecisionConflict as exc:
                    messages.error(
                        request,
                        _("Unable to cancel request: %(error)s") % {"error": exc},
                    )
                else:
                    messages.success(
                        request,
                        _("Request cancelled successfully: %(result)s") % {"result": result},
                    )
                return redirect_to_request_status()

            if job.job_status not in {
                Job.JobStatus.POSTED,
                Job.JobStatus.SCHEDULED_PENDING_ACTIVATION,
                Job.JobStatus.WAITING_PROVIDER_RESPONSE,
                Job.JobStatus.ASSIGNED,
            }:
                messages.error(request, _("This request can no longer be cancelled."))
                return redirect_to_request_status()

            with transaction.atomic():
                active_assignment = (
                    job.assignments
                    .filter(is_active=True)
                    .order_by("-created_at")
                    .first()
                )

                if active_assignment:
                    transition_assignment_status(
                        active_assignment,
                        "cancelled",
                        actor=JobEvent.ActorRole.CLIENT,
                        reason="request_status_cancel",
                    )

                transition_job_status(
                    job,
                    Job.JobStatus.CANCELLED,
                    actor=JobEvent.ActorRole.CLIENT,
                    reason="request_status_cancel",
                    allow_legacy=True,
                )
                job.cancelled_by = Job.CancellationActor.CLIENT
                job.cancel_reason = Job.CancelReason.CLIENT_CANCELLED
                job.save(
                    update_fields=["cancelled_by", "cancel_reason", "updated_at"]
                )
                create_job_event(
                    job=job,
                    event_type=JobEvent.EventType.JOB_CANCELLED,
                    actor_role=JobEvent.ActorRole.CLIENT,
                    payload={"source": "request_status_cancel"},
                    provider_id=getattr(job.selected_provider, "provider_id", None),
                    unique_per_job=True,
                )

            messages.success(request, _("Request cancelled successfully."))
            return redirect_to_request_status()

        if action != "confirm_close":
            return HttpResponseBadRequest(_("Invalid action."))

        if not job.client_id:
            messages.error(request, _("This job has no client assigned."))
            return redirect_to_request_status()

        try:
            result = job_services.confirm_service_closed_by_client(
                job_id=job.job_id,
                client_id=job.client_id,
            )
        except job_services.MarketplaceDecisionConflict as exc:
            messages.error(
                request,
                _("Unable to close service: %(error)s") % {"error": exc},
            )
        except PermissionError as exc:
            messages.error(
                request,
                _("Permission denied: %(error)s") % {"error": exc},
            )
        else:
            messages.success(
                request,
                _("Closure processed: %(result)s") % {"result": result},
            )

        return redirect_to_request_status()

    job = get_object_or_404(
        Job.objects.select_related(
            "selected_provider",
            "client",
            "service_type",
            "provider_service",
        ).prefetch_related("requested_extras"),
        pk=job_id,
    )
    _attach_job_lifecycle_details(job)
    timing_context = _job_timing_context(
        job=job,
        raw_service_timing=raw_service_timing,
    )
    job_events = _build_job_event_timeline(job)
    request_context = _request_create_search_context(
        service_type_id=raw_service_type_id or job.service_type_id or "",
        postal_code=raw_postal_code or job.postal_code or "",
        city=raw_city or job.city or "",
        province=raw_province or job.province or "",
        service_timing=timing_context["service_timing"],
        search=raw_search,
        provider_name=raw_provider_name,
    )
    request_status_params = _request_flow_query_params(**request_context)
    marketplace_retry_url = _url_with_query(
        reverse("ui:marketplace_search"),
        _marketplace_query_params_from_request_context(request_context),
    )
    refresh_status_params = dict(request_status_params)
    if next_url:
        refresh_status_params["next"] = next_url
    refresh_status_url = _url_with_query(
        reverse("ui:request_status", args=[job.job_id]),
        refresh_status_params,
    )
    show_push_debug = settings.DEBUG or bool(getattr(request.user, "is_staff", False))
    last_push_attempt = None
    if show_push_debug:
        last_push_attempt = (
            PushDispatchAttempt.objects.filter(job_event__job=job)
            .select_related("job_event", "device")
            .order_by("-created_at")
            .first()
        )

    return render(
        request,
        "request/status.html",
        {
            "job": job,
            "job_events": job_events,
            "job_status_label": _job_status_label(job),
            "next_url": next_url,
            "last_push_attempt": last_push_attempt,
            "marketplace_retry_url": marketplace_retry_url,
            "refresh_status_url": refresh_status_url,
            "search_again_url": marketplace_retry_url,
            "show_push_debug": show_push_debug,
            **request_context,
            **timing_context,
        },
    )


def provider_jobs_view(request):
    provider_id = request.session.get("provider_id")
    if not provider_id:
        return redirect("provider_register")

    jobs = (
        Job.objects.filter(
            Q(selected_provider_id=provider_id)
            | Q(assignments__provider_id=provider_id, assignments__is_active=True)
        )
        .filter(
            job_status__in=[
                Job.JobStatus.WAITING_PROVIDER_RESPONSE,
                Job.JobStatus.ASSIGNED,
                Job.JobStatus.IN_PROGRESS,
                Job.JobStatus.COMPLETED,
                Job.JobStatus.CONFIRMED,
                Job.JobStatus.CANCELLED,
            ]
        )
        .select_related("client", "service_type", "provider_service")
        .prefetch_related("requested_extras")
        .distinct()
        .order_by("created_at")
    )
    jobs = list(jobs)
    for job in jobs:
        _attach_job_lifecycle_details(job)

    return render(
        request,
        "provider/jobs.html",
        {
            "jobs": jobs,
        },
    )


def provider_job_action_view(request, job_id):
    if request.method != "POST":
        return redirect("ui:provider_jobs")

    provider_id = request.session.get("provider_id")
    if not provider_id:
        return redirect("provider_register")

    job = get_object_or_404(Job, pk=job_id)
    action = request.POST.get("action")

    if action in {"start", "complete"}:
        try:
            if action == "start":
                job_services.start_service_by_provider(
                    job_id=job.job_id,
                    provider_id=provider_id,
                )
            else:
                job_services.complete_service_by_provider(
                    job_id=job.job_id,
                    provider_id=provider_id,
                )
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        except (PermissionError, PermissionDenied):
            return HttpResponseForbidden(_("Not authorized."))
        except job_services.MarketplaceDecisionConflict as exc:
            return HttpResponseBadRequest(str(exc))
        else:
            return redirect("ui:provider_jobs")

    if job.selected_provider_id != provider_id:
        return HttpResponseForbidden(_("Not authorized."))

    provider = Provider.objects.filter(pk=provider_id).first()
    if provider is None:
        request.session.pop("provider_id", None)
        return redirect("provider_register")

    if action == "accept":
        from .views_provider import handle_provider_accept_action

        return handle_provider_accept_action(
            request=request,
            job=job,
            provider=provider,
            redirect_name="ui:provider_jobs",
        )
    elif action == "reject":
        from .views_provider import handle_provider_decline_action

        return handle_provider_decline_action(
            request=request,
            job=job,
            provider=provider,
            redirect_name="ui:provider_jobs",
        )
    else:
        return HttpResponseBadRequest(_("Invalid action."))

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
def quality_providers_dashboard_view(request):
    provider_name = (request.GET.get("provider") or "").strip()
    now = timezone.now()
    cutoff = now - timedelta(days=365)
    disputes_subquery = (
        JobDispute.objects.filter(
            provider_id=OuterRef("provider_id"),
            status=JobDispute.DisputeStatus.RESOLVED,
            job__cancel_reason=Job.CancelReason.DISPUTE_APPROVED,
            resolved_at__gte=cutoff,
        )
        .values("provider_id")
        .annotate(total=Count("pk"))
        .values("total")[:1]
    )

    providers = Provider.objects.annotate(
        disputes_last_12m=Coalesce(
            Subquery(disputes_subquery, output_field=IntegerField()),
            Value(0),
        ),
        safe_rating=Coalesce(
            Cast(F("avg_rating"), FloatField()),
            Value(0.0),
            output_field=FloatField(),
        ),
        safe_completed=Coalesce(F("completed_jobs_count"), Value(0)),
        safe_cancelled=Coalesce(F("cancelled_jobs_count"), Value(0)),
    ).annotate(
        cancel_rate=ExpressionWrapper(
            Cast(F("safe_cancelled"), FloatField())
            / (Cast(F("safe_completed"), FloatField()) + Value(1.0)),
            output_field=FloatField(),
        ),
        volume_score=Log10(Cast(F("safe_completed") + Value(1), FloatField())),
        verified_bonus=ExpressionWrapper(
            Cast(F("is_verified"), FloatField()),
            output_field=FloatField(),
        ),
        dispute_penalty_last_12m=ExpressionWrapper(
            Cast(F("disputes_last_12m"), FloatField()) * Value(0.15),
            output_field=FloatField(),
        ),
    ).annotate(
        cancel_rate=Least(
            Greatest(F("cancel_rate"), Value(0.0)),
            Value(1.0),
        ),
    )

    providers = list(providers)
    for provider in providers:
        provider.display_name = str(provider)

    if provider_name:
        normalized_provider_name = provider_name.lower()
        providers = [
            provider
            for provider in providers
            if normalized_provider_name in provider.display_name.lower()
        ]

    providers.sort(
        key=lambda provider: (
            provider.disputes_last_12m,
            1 if provider.quality_warning_active else 0,
            provider.restricted_until.timestamp() if provider.restricted_until else float("-inf"),
        ),
        reverse=True,
    )

    return render(
        request,
        "admin/quality/providers_dashboard.html",
        {
            "providers": providers,
            "provider_name": provider_name,
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
        messages.error(request, _("Cannot confirm: the job has no client_id."))
        return redirect("ui:job_detail", job_id=job.job_id)

    try:
        result = job_services.confirm_service_closed_by_client(
            job_id=job.job_id,
            client_id=job.client_id,
        )
    except job_services.MarketplaceDecisionConflict as exc:
        messages.error(
            request,
            _("Unable to close service: %(error)s") % {"error": exc},
        )
    except PermissionError as exc:
        messages.error(
            request,
            _("Permission denied: %(error)s") % {"error": exc},
        )
    else:
        messages.success(
            request,
            _("Closure processed: %(result)s") % {"result": result},
        )

    return redirect("ui:job_detail", job_id=job.job_id)
