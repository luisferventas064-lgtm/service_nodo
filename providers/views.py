from datetime import timedelta
import random

from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from core.auth_session import require_role
from core.services.sms_service import send_sms
from ui.models import PasswordResetCode

from .forms import (
    ProviderBillingForm,
    ProviderCompanyProfileForm,
    ProviderIndividualProfileForm,
    ProviderRegisterForm,
    _split_contact_name,
)
from .models import Provider, ProviderServiceArea


PASSWORD_CODE_WINDOW = timedelta(minutes=10)
PASSWORD_CODE_PHONE_LIMIT = 3
PASSWORD_CODE_IP_LIMIT = 10


def _to_model_provider_type(provider_type: str) -> str:
    if provider_type == "company":
        return Provider.TYPE_COMPANY
    return Provider.TYPE_SELF_EMPLOYED


def _get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def provider_register(request):
    if request.method == "POST":
        form = ProviderRegisterForm(request.POST)

        if form.is_valid():
            business_name = form.cleaned_data["business_name"].strip()
            provider_type = _to_model_provider_type(form.cleaned_data["provider_type"])
            contact_first_name, contact_last_name = _split_contact_name(business_name)
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
                return render(request, "providers/register.html", {"form": form})

            elif not form.errors:
                try:
                    with transaction.atomic():
                        provider = Provider.objects.create(
                            provider_type=provider_type,
                            company_name=business_name if provider_type == Provider.TYPE_COMPANY else None,
                            contact_first_name=contact_first_name,
                            contact_last_name=contact_last_name,
                            phone_number=form.cleaned_data["phone_number"],
                            email=form.cleaned_data["email"],
                            languages_spoken=form.cleaned_data.get("languages_spoken", ""),
                            password=make_password(form.cleaned_data["password"]),
                            is_phone_verified=False,
                            profile_completed=False,
                            accepts_terms=False,
                            billing_profile_completed=False,
                            country=form.cleaned_data["country_name"],
                            province="QC",
                            city="Pending",
                            postal_code="PENDING",
                            address_line1="Pending profile completion",
                        )

                        code = str(random.randint(100000, 999999))
                        PasswordResetCode.objects.filter(
                            phone_number=provider.phone_number,
                            purpose="verify",
                            used=False,
                        ).update(used=True)
                        PasswordResetCode.objects.create(
                            phone_number=provider.phone_number,
                            code=code,
                            purpose="verify",
                            ip_address=ip,
                        )
                        send_sms(
                            provider.phone_number,
                            f"Your NODO verification code is: {code}",
                        )
                except IntegrityError:
                    form.add_error("email", "A provider with this email already exists.")
                else:
                    request.session["verify_phone"] = provider.phone_number
                    request.session["verify_role"] = "provider"
                    request.session["verify_actor_type"] = "provider"
                    request.session["verify_actor_id"] = provider.pk
                    return redirect("verify_phone")

    else:
        form = ProviderRegisterForm()

    return render(request, "providers/register.html", {"form": form})


def provider_dashboard(request):
    provider_id = request.session.get("provider_id")
    if not provider_id:
        return redirect("provider_register")

    provider = Provider.objects.filter(pk=provider_id).first()
    if not provider:
        request.session.pop("provider_id", None)
        return redirect("provider_register")

    if not provider.profile_completed:
        return redirect("provider_complete_profile")

    active_services_count = provider.services.filter(is_active=True).count()

    return render(
        request,
        "providers/dashboard.html",
        {
            "provider": provider,
            "phone_ok": provider.is_phone_verified,
            "profile_ok": provider.profile_completed,
            "billing_ok": provider.billing_profile_completed,
            "active_services_count": active_services_count,
            "is_operational": provider.is_operational,
        },
    )


def provider_profile(request):
    provider_id = request.session.get("provider_id")
    if not provider_id:
        return redirect("provider_register")

    provider = Provider.objects.filter(pk=provider_id).first()
    if not provider:
        request.session.pop("provider_id", None)
        return redirect("provider_register")

    return render(
        request,
        "providers/profile.html",
        {
            "provider": provider,
        },
    )


@require_role("provider")
def provider_jobs(request):
    return render(request, "providers/jobs.html")


@require_role("provider")
def provider_activity(request):
    return render(request, "providers/activity.html")


@require_role("provider")
def provider_billing(request):
    return render(request, "providers/billing.html")


@require_role("provider")
def provider_compliance(request):
    return render(request, "providers/compliance.html")


@require_role("provider")
def provider_edit(request):
    return render(request, "providers/account.html")


def provider_complete_profile(request):
    provider_id = request.session.get("provider_id")
    if not provider_id and request.session.get("verify_actor_type") == "provider":
        provider_id = request.session.get("verify_actor_id")
    if not provider_id:
        return redirect("provider_register")

    provider = Provider.objects.filter(pk=provider_id).first()
    if not provider:
        request.session.pop("provider_id", None)
        request.session.pop("verify_actor_type", None)
        request.session.pop("verify_actor_id", None)
        return redirect("provider_register")

    if not provider.is_phone_verified:
        return redirect("verify_phone")

    if provider.profile_completed:
        return redirect("provider_dashboard")

    profile_form_class = (
        ProviderCompanyProfileForm
        if provider.normalized_provider_type == "company"
        else ProviderIndividualProfileForm
    )

    if request.method == "POST" and request.POST.get("area_action"):
        action = request.POST.get("area_action")

        if action == "add":
            province = (request.POST.get("area_province") or "").strip()
            city = (request.POST.get("area_city") or "").strip()
            city_other = (request.POST.get("area_city_other") or "").strip()

            if city == "OTHER":
                city = city_other

            if len(province) < 2:
                messages.error(request, "Please select a province.")
                return redirect("provider_complete_profile")

            if len(city) < 2 or len(city) > 100:
                messages.error(request, "Please enter a valid city.")
                return redirect("provider_complete_profile")

            ProviderServiceArea.objects.update_or_create(
                provider=provider,
                city=city,
                province=province,
                defaults={"is_active": True},
            )
            provider.evaluate_profile_completion()
            messages.success(request, "Service area added.")
            return redirect("provider_complete_profile")

        if action == "remove":
            area_id = request.POST.get("area_id")
            if area_id:
                ProviderServiceArea.objects.filter(
                    provider_service_area_id=area_id,
                    provider=provider,
                ).update(is_active=False)
                provider.evaluate_profile_completion()
                messages.success(request, "Service area removed.")
            return redirect("provider_complete_profile")

    if request.method == "POST":
        form = profile_form_class(request.POST, instance=provider)

        if form.is_valid():
            with transaction.atomic():
                provider = form.save()
                provider.accepts_terms = form.cleaned_data["accepts_terms"]
                provider.save(
                    update_fields=[
                        "accepts_terms",
                        "updated_at",
                    ]
                )
                provider.evaluate_profile_completion()

            if provider.profile_completed:
                request.session["provider_id"] = provider.pk
                request.session["nodo_role"] = "provider"
                request.session["nodo_profile_id"] = provider.pk
                return redirect("portal:provider_dashboard")

            form.add_error(None, "Complete all required profile fields.")
    else:
        form = profile_form_class(instance=provider)

    areas = ProviderServiceArea.objects.filter(
        provider=provider,
        is_active=True,
    ).order_by("province", "city")

    return render(
        request,
        "providers/complete_profile.html",
        {
            "provider": provider,
            "form": form,
            "areas": areas,
        },
    )


def provider_complete_billing(request):
    provider_id = request.session.get("provider_id")
    if not provider_id and request.session.get("verify_actor_type") == "provider":
        provider_id = request.session.get("verify_actor_id")
    if not provider_id:
        return redirect("provider_register")

    provider = Provider.objects.filter(pk=provider_id).first()
    if not provider:
        request.session.pop("provider_id", None)
        request.session.pop("verify_actor_type", None)
        request.session.pop("verify_actor_id", None)
        return redirect("provider_register")

    if not provider.is_phone_verified:
        return redirect("verify_phone")

    if not provider.profile_completed:
        return redirect("provider_complete_profile")

    if provider.billing_profile_completed:
        request.session["provider_id"] = provider.pk
        request.session.pop("verify_actor_type", None)
        request.session.pop("verify_actor_id", None)
        return redirect("provider_dashboard")

    if request.method == "POST":
        form = ProviderBillingForm(request.POST, instance=provider)
        if form.is_valid():
            with transaction.atomic():
                form.save()
                provider.billing_profile_completed = True
                provider.save(
                    update_fields=[
                        "billing_profile_completed",
                        "updated_at",
                    ]
                )

            request.session["provider_id"] = provider.pk
            request.session.pop("verify_actor_type", None)
            request.session.pop("verify_actor_id", None)
            return redirect("provider_dashboard")
    else:
        form = ProviderBillingForm(instance=provider)

    return render(
        request,
        "providers/complete_billing.html",
        {
            "provider": provider,
            "form": form,
        },
    )
