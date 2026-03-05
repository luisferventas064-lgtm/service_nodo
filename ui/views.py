from datetime import datetime, timedelta
import random

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
from django.utils import timezone
from django.views.decorators.http import require_POST

from clients.models import ClientTicket
from clients.models import Client
from core.auth_session import clear_session, require_role, set_session
from core.utils.phone import best_effort_normalize_phone, phone_lookup_candidates
from jobs import services as job_services
from jobs.models import Job, JobDispute, JobEvent, PlatformLedgerEntry
from jobs.services_pricing_snapshot import apply_provider_service_snapshot_to_job
from jobs.services_lifecycle import accept_job_by_provider
from providers.models import Provider
from providers.models import ProviderService
from providers.models import ProviderTicket
from providers.services_analytics import (
    marketplace_analytics_snapshot,
    marketplace_analytics_to_csv,
)
from providers.services_marketplace import Log10, marketplace_ranked_queryset
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
MARKETPLACE_LANGUAGE_CHOICES = (
    "English",
    "French",
    "Spanish",
    "Arabic",
    "Mandarin",
    "Italian",
    "Portuguese",
    "Russian",
    "Punjabi",
    "Vietnamese",
)


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
                "Client"
                if role == "client"
                else "Provider"
                if role == "provider"
                else "Worker"
                if role == "worker"
                else None
            ),
        },
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
        return "Too many attempts. Try later."
    if ip and recent_ip >= PASSWORD_CODE_IP_LIMIT:
        return "Too many attempts from this network."
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
        return "Too many attempts. Try later."

    recent_ip = 0
    if ip:
        recent_ip = PasswordResetCode.objects.filter(
            ip_address=ip,
            purpose="verify",
            created_at__gte=verify_window_start,
        ).count()
    if ip and recent_ip >= PASSWORD_CODE_IP_LIMIT:
        return "Too many attempts from this network."

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
    send_sms(phone, f"Your NODO verification code is: {code}")
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

        form.add_error(None, "Invalid credentials.")

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
            error = "Verification code not found."
        elif not record.is_valid():
            record.used = True
            record.save(update_fields=["used"])
            error = "Code expired."
        elif record.code != code_input:
            record.attempts += 1
            if record.attempts >= PASSWORD_CODE_MAX_ATTEMPTS:
                record.used = True
                record.save(update_fields=["attempts", "used"])
                error = "Code expired."
            else:
                record.save(update_fields=["attempts"])
                error = "Invalid verification code."
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
                error = "No account is linked to this phone number."
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
        return JsonResponse({"error": "Invalid request"}, status=400)

    phone = request.session.get("verify_phone")
    role = request.session.get("verify_role")

    if not phone or not role:
        return JsonResponse({"error": "Session expired"}, status=400)

    ip = get_client_ip(request)
    recent_verify = PasswordResetCode.objects.filter(
        phone_number=phone,
        purpose="verify",
        created_at__gte=timezone.now() - VERIFY_RESEND_COOLDOWN,
    ).exists()
    if recent_verify:
        return JsonResponse({"error": "Please wait before requesting again"}, status=429)

    verify_window_start = timezone.now() - PASSWORD_CODE_WINDOW
    recent_phone = PasswordResetCode.objects.filter(
        phone_number=phone,
        purpose="verify",
        created_at__gte=verify_window_start,
    ).count()
    if recent_phone >= PASSWORD_CODE_PHONE_LIMIT:
        return JsonResponse({"error": "Too many attempts. Try later."}, status=429)

    recent_ip = 0
    if ip:
        recent_ip = PasswordResetCode.objects.filter(
            ip_address=ip,
            purpose="verify",
            created_at__gte=verify_window_start,
        ).count()
    if ip and recent_ip >= PASSWORD_CODE_IP_LIMIT:
        return JsonResponse({"error": "Too many attempts from this network."}, status=429)

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
            f"Your NODO reset code is: {code}",
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
            form.add_error("code", "Reset code expired.")
        elif not reset_code.is_valid():
            reset_code.used = True
            reset_code.save(update_fields=["used"])
            form.add_error("code", "Reset code expired.")
        elif reset_code.code != form.cleaned_data["code"].strip():
            reset_code.attempts += 1
            if reset_code.attempts >= PASSWORD_CODE_MAX_ATTEMPTS:
                reset_code.used = True
                reset_code.save(update_fields=["attempts", "used"])
                form.add_error("code", "Reset code expired.")
            else:
                reset_code.save(update_fields=["attempts"])
                form.add_error("code", "Invalid reset code.")
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
                    "No account is linked to that phone number.",
                )
            else:
                messages.success(
                    request,
                    "Password updated. You can log in now.",
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
    city = (request.GET.get("city") or "").strip()
    zone = (request.GET.get("zone") or "").strip()
    language = (request.GET.get("language") or "").strip()
    available_now = request.GET.get("available_now")
    only_certified = request.GET.get("only_certified")
    only_insured = request.GET.get("only_insured")

    service_types = _marketplace_service_types(limit=20)

    context = {
        "service_types": service_types,
        "selected_city": city,
        "selected_zone": zone,
        "selected_language": language,
        "language_options": MARKETPLACE_LANGUAGE_CHOICES,
        "available_now": bool(available_now),
        "only_certified": bool(only_certified),
        "only_insured": bool(only_insured),
    }

    if not service_type_id:
        context["service_types"] = service_types
        context["mode"] = "services"
        return render(request, "marketplace/index.html", context)

    selected_service_type = get_object_or_404(
        ServiceType,
        pk=service_type_id,
        is_active=True,
    )
    providers = marketplace_ranked_queryset(
        province=client.province,
        city=city or client.city,
        service_type_id=service_type_id,
    )

    if zone:
        providers = providers.filter(provider__zone__name=zone)

    if available_now:
        providers = providers.filter(provider__is_available_now=True)

    qs = providers
    if language:
        qs = qs.filter(provider__languages_spoken__icontains=language)

    if only_certified:
        qs = [ps for ps in qs if ps.is_compliant]

    if only_insured:
        qs = [
            ps for ps in qs
            if hasattr(ps.provider, "insurance")
            and ps.provider.insurance.has_insurance
            and ps.provider.insurance.is_verified
        ]

    context.update(
        {
            "providers": qs[:20],
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


def request_create_view(request, provider_id):
    provider = get_object_or_404(Provider, pk=provider_id, is_active=True)
    service_types = ServiceType.objects.filter(is_active=True).order_by("name")
    service_type_id = (
        request.POST.get("service_type")
        or request.POST.get("service_type_id")
        or request.GET.get("service_type_id")
        or ""
    ).strip()
    client_id = request.session.get("client_id")
    session_client = Client.objects.filter(pk=client_id).first() if client_id else None
    if client_id and session_client is None:
        request.session.pop("client_id", None)
    client_authenticated = bool(session_client)

    selected_offer = (
        ProviderService.objects.select_related("service_type")
        .filter(
            provider=provider,
            is_active=True,
        )
        .order_by("price_cents", "id")
        .first()
    )
    if service_type_id:
        selected_offer = (
            ProviderService.objects.select_related("service_type")
            .filter(
                provider=provider,
                service_type_id=service_type_id,
                is_active=True,
            )
            .order_by("price_cents", "id")
            .first()
            or selected_offer
        )
    if selected_offer is not None:
        selected_offer.display_price = selected_offer.price_cents / 100
    compliance_blocked = bool(selected_offer and not selected_offer.is_compliant)
    compliance_error = (
        "This service cannot be requested until provider compliance is complete."
        if compliance_blocked
        else None
    )

    default_form_data = {
        "country": getattr(session_client, "country", None) or "CA",
        "province": getattr(session_client, "province", None) or provider.province,
        "city": getattr(session_client, "city", None) or provider.city,
        "postal_code": getattr(session_client, "postal_code", None) or "",
        "address_line1": getattr(session_client, "address_line1", None) or "",
        "job_mode": Job.JobMode.ON_DEMAND,
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

    if request.method == "GET":
        return render(
            request,
            "request/create.html",
            {
                "provider": provider,
                "service_types": service_types,
                "selected_offer": selected_offer,
                "service_type_id": service_type_id,
                "form_data": default_form_data,
                "client": session_client,
                "client_authenticated": client_authenticated,
                "compliance_blocked": compliance_blocked,
                "error": compliance_error,
            },
        )

    if session_client is not None:
        form_data = {
            "first_name": session_client.first_name,
            "last_name": session_client.last_name,
            "phone_number": session_client.phone_number,
            "email": session_client.email,
            "country": session_client.country,
            "province": session_client.province,
            "city": session_client.city,
            "postal_code": session_client.postal_code,
            "address_line1": session_client.address_line1,
            "service_type": (request.POST.get("service_type") or "").strip(),
            "job_mode": (request.POST.get("job_mode") or Job.JobMode.ON_DEMAND).strip(),
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
        error = "Complete all required fields."
    elif form_data["job_mode"] not in {Job.JobMode.ON_DEMAND, Job.JobMode.SCHEDULED}:
        error = "Invalid service mode."
    elif form_data["job_mode"] == Job.JobMode.SCHEDULED and (
        not form_data["scheduled_date"] or not form_data["scheduled_start_time"]
    ):
        error = "Scheduled mode requires date and time."

    service_type = None
    if error is None:
        service_type = ServiceType.objects.filter(
            pk=form_data["service_type"],
            is_active=True,
        ).first()
        if service_type is None:
            error = "Invalid service type."

    if error is None:
        selected_offer = (
            ProviderService.objects.select_related("service_type")
            .filter(
                provider=provider,
                service_type=service_type,
                is_active=True,
            )
            .order_by("price_cents", "id")
            .first()
        )
        if selected_offer is None:
            error = "Provider must have an active priced service for this service type."
        elif not selected_offer.is_compliant:
            error = "This service cannot be requested until provider compliance is complete."
        else:
            selected_offer.display_price = selected_offer.price_cents / 100

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
                    "You must complete your profile before creating a job.",
                )
                request.session["client_id"] = client.pk
                return redirect("client_complete_profile")
            with transaction.atomic():
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
                apply_provider_service_snapshot_to_job(
                    job=created_job,
                    provider_service=selected_offer,
                )
        except PermissionError as exc:
            return HttpResponseForbidden(str(exc))
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
                "service_type_id": service_type_id,
                "form_data": form_data,
                "error": error,
                "client": session_client,
                "client_authenticated": client_authenticated,
                "compliance_blocked": compliance_blocked,
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
    return job


def request_status_view(request, job_id):
    if request.method == "POST":
        job = get_object_or_404(Job, pk=job_id)
        action = request.POST.get("action")

        if action != "confirm_close":
            return HttpResponseBadRequest("Accion invalida.")

        if not job.client_id:
            messages.error(request, "This job has no client assigned.")
            return redirect("ui:request_status", job_id=job.job_id)

        try:
            result = job_services.confirm_service_closed_by_client(
                job_id=job.job_id,
                client_id=job.client_id,
            )
        except job_services.MarketplaceDecisionConflict as exc:
            messages.error(request, f"Unable to close service: {exc}")
        except PermissionError as exc:
            messages.error(request, f"Permission denied: {exc}")
        else:
            messages.success(request, f"Closure processed: {result}")

        return redirect("ui:request_status", job_id=job.job_id)

    job = get_object_or_404(
        Job.objects.select_related("selected_provider", "client", "service_type"),
        pk=job_id,
    )
    _attach_job_lifecycle_details(job)

    return render(
        request,
        "request/status.html",
        {
            "job": job,
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
        .select_related("client", "service_type")
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
            return HttpResponseForbidden("Not authorized.")
        except job_services.MarketplaceDecisionConflict as exc:
            return HttpResponseBadRequest(str(exc))
        else:
            return redirect("ui:provider_jobs")

    if job.selected_provider_id != provider_id:
        return HttpResponseForbidden("Not authorized.")

    if action == "accept":
        provider = Provider.objects.get(pk=job.selected_provider_id)
        if not provider.is_operational:
            messages.warning(
                request,
                "Complete your profile and add a service to accept jobs.",
            )
            request.session["provider_id"] = provider.pk
            return redirect("provider_dashboard")
        try:
            accept_job_by_provider(job, provider)
        except ValueError as e:
            return HttpResponseBadRequest(str(e))
    elif action == "reject":
        if job.job_status != Job.JobStatus.WAITING_PROVIDER_RESPONSE:
            return HttpResponseForbidden("Invalid status.")
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
def quality_providers_dashboard_view(request):
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
    ).annotate(
        quality_component=ExpressionWrapper(
            (F("safe_rating") * Value(0.5))
            + (F("volume_score") * Value(0.3))
            - (F("cancel_rate") * Value(0.2))
            - F("dispute_penalty_last_12m"),
            output_field=FloatField(),
        ),
        hybrid_score=ExpressionWrapper(
            F("quality_component") + (F("verified_bonus") * Value(0.1)),
            output_field=FloatField(),
        ),
    )

    providers = list(providers)
    for provider in providers:
        provider.display_name = str(provider)

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
        {"providers": providers},
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
