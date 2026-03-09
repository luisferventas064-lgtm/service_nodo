from datetime import timedelta
import random

from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.auth_session import require_role
from core.services.sms_service import send_sms
from ui.models import PasswordResetCode

from .forms import (
    ProviderBillingForm,
    ProviderCertificateForm,
    ProviderInsuranceForm,
    ProviderRegisterForm,
    _split_contact_name,
)
from .models import Provider, ProviderCertificate, ProviderServiceArea


PASSWORD_CODE_WINDOW = timedelta(minutes=10)
PASSWORD_CODE_PHONE_LIMIT = 3
PASSWORD_CODE_IP_LIMIT = 10


def _get_provider_session_id(request):
    return request.session.get("nodo_profile_id") or request.session.get("provider_id")


def _set_provider_session(request, provider):
    request.session["provider_id"] = provider.pk
    request.session["nodo_role"] = "provider"
    request.session["nodo_profile_id"] = provider.pk


def _to_model_provider_type(provider_type: str) -> str:
    if provider_type == "company":
        return Provider.TYPE_COMPANY
    return Provider.TYPE_SELF_EMPLOYED


def _get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _clean_placeholder_value(value: str) -> str:
    value = (value or "").strip()
    blocked = {
        "pending",
        "pending profile completion",
    }
    return "" if value.lower() in blocked else value


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
    provider_id = _get_provider_session_id(request)
    if not provider_id:
        return redirect("provider_register")

    provider = Provider.objects.filter(pk=provider_id).first()
    if not provider:
        request.session.pop("provider_id", None)
        request.session.pop("nodo_profile_id", None)
        return redirect("provider_register")

    if not provider.is_phone_verified:
        return redirect("verify_phone")

    if not provider.profile_completed:
        return redirect("provider_complete_profile")

    _set_provider_session(request, provider)
    return redirect("portal:provider_dashboard")


def provider_profile(request):
    provider_id = _get_provider_session_id(request)
    if not provider_id:
        return redirect("provider_register")

    provider = Provider.objects.filter(pk=provider_id).first()
    if not provider:
        request.session.pop("provider_id", None)
        request.session.pop("nodo_profile_id", None)
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
    return redirect("ui:provider_jobs")


@require_role("provider")
def provider_activity(request):
    return render(request, "providers/activity.html")


@require_role("provider")
def provider_billing(request):
    return redirect("provider_complete_billing")


@require_role("provider")
def provider_compliance(request):
    provider_id = _get_provider_session_id(request)
    if not provider_id:
        return redirect("ui:root_login")

    provider = get_object_or_404(Provider, pk=provider_id)

    insurance = getattr(provider, "insurance", None)

    active_service_areas_qs = ProviderServiceArea.objects.filter(
        provider=provider,
        is_active=True,
    )
    active_service_areas = active_service_areas_qs.order_by("province", "city")

    certificates_qs = ProviderCertificate.objects.filter(provider=provider).order_by(
        "-created_at",
        "-provider_certificate_id",
    )
    today = timezone.localdate()

    total_certificates = certificates_qs.count()
    verified_certificates = certificates_qs.filter(status__iexact="verified").count()
    expired_certificates = certificates_qs.filter(expires_date__lt=today).count()
    pending_certificates = certificates_qs.exclude(status__iexact="verified").count()

    profile_completed = bool(getattr(provider, "profile_completed", False))
    billing_completed = bool(
        getattr(provider, "billing_profile_completed", getattr(provider, "billing_completed", False))
    )
    phone_verified = bool(
        getattr(provider, "is_phone_verified", getattr(provider, "phone_verified", False))
    )
    accepts_terms = bool(getattr(provider, "accepts_terms", False))
    operational = bool(
        getattr(provider, "is_operational", getattr(provider, "operational", False))
    )

    operational_reasons = []

    if not profile_completed:
        operational_reasons.append("Base profile is not completed yet.")

    if not billing_completed:
        operational_reasons.append("Payment setup is not completed yet.")

    if not phone_verified:
        operational_reasons.append("Phone verification is still pending.")

    if not accepts_terms:
        operational_reasons.append("Terms and Conditions are not accepted yet.")

    if not provider.has_active_service():
        operational_reasons.append("No active services are configured yet.")

    # Inference from current model rules: operational status also depends on service-level compliance.
    if provider.has_active_service() and not provider.has_required_certifications:
        operational_reasons.append(
            "Some required certificates or insurance items are still missing for your active services."
        )

    context = {
        "provider": provider,
        "insurance": insurance,
        "active_service_areas": active_service_areas,
        "active_service_areas_count": active_service_areas_qs.count(),
        "certificates": certificates_qs[:5],
        "total_certificates": total_certificates,
        "verified_certificates": verified_certificates,
        "expired_certificates": expired_certificates,
        "pending_certificates": pending_certificates,
        "profile_completed": profile_completed,
        "billing_completed": billing_completed,
        "phone_verified": phone_verified,
        "accepts_terms": accepts_terms,
        "operational": operational,
        "operational_reasons": operational_reasons,
    }
    return render(request, "providers/compliance.html", context)


@require_role("provider")
def provider_edit(request):
    provider_id = _get_provider_session_id(request)
    if not provider_id:
        return redirect("ui:root_login")

    provider = Provider.objects.filter(pk=provider_id).first()
    if not provider:
        return redirect("ui:root_login")

    if request.method == "POST":
        provider.provider_type = request.POST.get("provider_type", provider.provider_type).strip() or provider.provider_type
        provider.company_name = request.POST.get("company_name", "").strip()
        provider.legal_name = request.POST.get("legal_name", "").strip()
        provider.business_registration_number = request.POST.get("business_registration_number", "").strip()
        provider.employee_count = request.POST.get("employee_count", "").strip()
        provider.contact_first_name = request.POST.get("contact_first_name", "").strip()
        provider.contact_last_name = request.POST.get("contact_last_name", "").strip()
        provider.phone_number = request.POST.get("phone_number", "").strip()
        provider.email = request.POST.get("email", "").strip()
        provider.languages_spoken = request.POST.get("languages_spoken", "").strip()
        provider.country = _clean_placeholder_value(request.POST.get("country", "").strip()) or "Canada"
        provider.province = _clean_placeholder_value(request.POST.get("province", "").strip())
        provider.city = _clean_placeholder_value(request.POST.get("city", "").strip())
        provider.postal_code = _clean_placeholder_value(request.POST.get("postal_code", "").strip())
        provider.address_line1 = _clean_placeholder_value(request.POST.get("address_line1", "").strip())

        service_radius_raw = request.POST.get("service_radius_km", "").strip()
        if service_radius_raw.isdigit():
            provider.service_radius_km = int(service_radius_raw)

        provider.availability_mode = request.POST.get("availability_mode", provider.availability_mode).strip() or provider.availability_mode
        provider.is_available_now = bool(request.POST.get("is_available_now"))

        provider.save()
        provider.evaluate_profile_completion()
        return redirect("provider_profile")

    context = {
        "provider": provider,
        "provider_type_choices": Provider.PROVIDER_TYPE_CHOICES,
        "employee_choices": Provider.EMPLOYEE_CHOICES,
    }
    return render(request, "providers/account.html", context)


@require_role("provider")
def provider_insurance(request):
    provider_id = _get_provider_session_id(request)
    if not provider_id:
        return redirect("ui:root_login")

    provider = get_object_or_404(Provider, pk=provider_id)
    insurance = getattr(provider, "insurance", None)

    if request.method == "POST":
        form = ProviderInsuranceForm(request.POST, instance=insurance)
        if form.is_valid():
            insurance_obj = form.save(commit=False)
            insurance_obj.provider = provider
            insurance_obj.save()

            messages.success(request, "Insurance information saved successfully.")
            return redirect("provider_insurance")
    else:
        form = ProviderInsuranceForm(instance=insurance)

    context = {
        "provider": provider,
        "insurance": insurance,
        "form": form,
    }
    return render(request, "providers/insurance.html", context)


@require_role("provider")
def provider_certificates(request):
    provider_id = _get_provider_session_id(request)
    if not provider_id:
        return redirect("ui:root_login")

    provider = get_object_or_404(Provider, pk=provider_id)
    certificates = provider.certificates.all().order_by("-created_at", "-provider_certificate_id")

    edit_id = request.GET.get("edit")
    edit_certificate = None

    if edit_id:
        edit_certificate = get_object_or_404(provider.certificates.all(), pk=edit_id)

    if request.method == "POST":
        certificate_id = request.POST.get("certificate_id")
        instance = None

        if certificate_id:
            instance = get_object_or_404(provider.certificates.all(), pk=certificate_id)

        form = ProviderCertificateForm(request.POST, instance=instance)

        if form.is_valid():
            certificate = form.save(commit=False)
            certificate.provider = provider

            if not getattr(certificate, "status", None):
                try:
                    field = ProviderCertificate._meta.get_field("status")
                    default_status = field.get_default()
                    if default_status not in (None, ""):
                        certificate.status = default_status
                except Exception:
                    pass

            certificate.save()

            if instance:
                messages.success(request, "Certificate updated successfully.")
            else:
                messages.success(request, "Certificate added successfully.")

            return redirect("provider_certificates")
        edit_certificate = instance
    else:
        form = ProviderCertificateForm(instance=edit_certificate)

    context = {
        "provider": provider,
        "certificates": certificates,
        "form": form,
        "edit_certificate": edit_certificate,
        "total_certificates": certificates.count(),
    }
    return render(request, "providers/certificates.html", context)


@require_role("provider")
def provider_service_areas(request):
    provider_id = _get_provider_session_id(request)
    if not provider_id:
        return redirect("ui:root_login")

    provider = Provider.objects.filter(pk=provider_id).first()
    if not provider:
        return redirect("ui:root_login")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "add":
            city = (request.POST.get("city") or "").strip()
            province = (request.POST.get("province") or "").strip()

            if not city or not province:
                messages.error(request, "City and province are required.")
                return redirect("provider_service_areas")

            existing = ProviderServiceArea.objects.filter(
                provider=provider,
                city__iexact=city,
                province__iexact=province,
            ).first()

            if existing:
                if not existing.is_active:
                    existing.is_active = True
                    existing.save(update_fields=["is_active"])
                    messages.success(request, "Service area reactivated.")
                else:
                    messages.info(request, "That service area is already active.")
            else:
                ProviderServiceArea.objects.create(
                    provider=provider,
                    city=city,
                    province=province,
                    is_active=True,
                )
                messages.success(request, "Service area added.")

            provider.evaluate_profile_completion()

            return redirect("provider_service_areas")

        if action == "toggle":
            area_id = request.POST.get("area_id")
            area = ProviderServiceArea.objects.filter(
                pk=area_id,
                provider=provider,
            ).first()

            if area:
                area.is_active = not area.is_active
                area.save(update_fields=["is_active"])
                provider.evaluate_profile_completion()
                if area.is_active:
                    messages.success(request, "Service area activated.")
                else:
                    messages.success(request, "Service area deactivated.")
            else:
                messages.error(request, "Service area not found.")

            return redirect("provider_service_areas")

        messages.error(request, "Unsupported service area action.")
        return redirect("provider_service_areas")

    active_areas = ProviderServiceArea.objects.filter(
        provider=provider,
        is_active=True,
    ).order_by("province", "city")

    inactive_areas = ProviderServiceArea.objects.filter(
        provider=provider,
        is_active=False,
    ).order_by("province", "city")

    context = {
        "provider": provider,
        "active_areas": active_areas,
        "inactive_areas": inactive_areas,
    }
    return render(request, "providers/service_areas.html", context)


def provider_complete_profile(request):
    provider_id = _get_provider_session_id(request)
    if not provider_id and request.session.get("verify_actor_type") == "provider":
        provider_id = request.session.get("verify_actor_id")
    if not provider_id:
        return redirect("provider_register")

    provider = Provider.objects.filter(pk=provider_id).first()
    if not provider:
        request.session.pop("provider_id", None)
        request.session.pop("nodo_profile_id", None)
        request.session.pop("verify_actor_type", None)
        request.session.pop("verify_actor_id", None)
        return redirect("provider_register")

    if not provider.is_phone_verified:
        return redirect("verify_phone")

    provider.evaluate_profile_completion()
    if provider.profile_completed:
        _set_provider_session(request, provider)
        return redirect("portal:provider_dashboard")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if request.POST.get("area_action"):
            messages.info(
                request,
                "Service areas are now managed from the dedicated Manage Service Areas page.",
            )
            return redirect("provider_service_areas")

        if action == "accept_terms" or request.POST.get("accepts_terms"):
            if not provider.accepts_terms:
                provider.accepts_terms = True
                provider.save(update_fields=["accepts_terms", "updated_at"])

            provider.evaluate_profile_completion()
            if provider.profile_completed:
                _set_provider_session(request, provider)
                return redirect("portal:provider_dashboard")

            messages.success(
                request,
                "Terms accepted. Complete the remaining requirements below to finish your profile.",
            )
            return redirect("provider_complete_profile")

        messages.info(
            request,
            "This page is now a checklist. Use the dedicated pages below to update your profile.",
        )
        return redirect("provider_complete_profile")

    active_areas = ProviderServiceArea.objects.filter(
        provider=provider,
        is_active=True,
    ).order_by("province", "city")

    if provider.normalized_provider_type == "company":
        profile_step_title = "Business profile details"
        profile_step_description = (
            "Complete your company name, business registration number, and contact person in Edit Profile."
        )
        profile_details_complete = bool(
            provider.company_name
            and provider.business_registration_number
            and provider.contact_first_name
            and provider.contact_last_name
        )
    else:
        profile_step_title = "Personal profile details"
        profile_step_description = "Complete your legal name in Edit Profile."
        profile_details_complete = bool(provider.legal_name)

    has_active_area = active_areas.exists()
    required_steps_total = 3
    completed_required_steps = sum(
        [
            profile_details_complete,
            has_active_area,
            provider.accepts_terms,
        ]
    )

    missing_required_steps = []
    if not profile_details_complete:
        missing_required_steps.append("Complete your profile details from Edit Profile.")
    if not has_active_area:
        missing_required_steps.append("Add at least one active service area.")
    if not provider.accepts_terms:
        missing_required_steps.append("Accept the provider terms.")

    insurance = getattr(provider, "insurance", None)
    if insurance and insurance.has_insurance:
        insurance_status = "Verified" if insurance.is_verified else "Pending verification"
    else:
        insurance_status = "Not provided"

    certificate_count = provider.certificates.count()
    verified_certificate_count = provider.certificates.filter(status="verified").count()
    if certificate_count:
        certificates_status = f"{verified_certificate_count}/{certificate_count} verified"
    else:
        certificates_status = "No certificates uploaded"

    return render(
        request,
        "providers/complete_profile.html",
        {
            "provider": provider,
            "active_areas": active_areas,
            "profile_step_title": profile_step_title,
            "profile_step_description": profile_step_description,
            "profile_details_complete": profile_details_complete,
            "has_active_area": has_active_area,
            "required_steps_total": required_steps_total,
            "completed_required_steps": completed_required_steps,
            "missing_required_steps": missing_required_steps,
            "insurance": insurance,
            "insurance_status": insurance_status,
            "certificate_count": certificate_count,
            "certificates_status": certificates_status,
        },
    )


def provider_complete_billing(request):
    provider_id = _get_provider_session_id(request)
    if not provider_id and request.session.get("verify_actor_type") == "provider":
        provider_id = request.session.get("verify_actor_id")
    if not provider_id:
        return redirect("provider_register")

    provider = Provider.objects.filter(pk=provider_id).first()
    if not provider:
        request.session.pop("provider_id", None)
        request.session.pop("nodo_profile_id", None)
        request.session.pop("verify_actor_type", None)
        request.session.pop("verify_actor_id", None)
        return redirect("provider_register")

    if not provider.is_phone_verified:
        return redirect("verify_phone")

    if not provider.profile_completed:
        return redirect("provider_complete_profile")

    if request.method == "POST":
        form = ProviderBillingForm(request.POST, instance=provider, provider=provider)
        if form.is_valid():
            with transaction.atomic():
                provider = form.save()
                provider.billing_profile_completed = form.is_billing_complete()
                provider.save(
                    update_fields=[
                        "billing_profile_completed",
                        "updated_at",
                    ]
                )

            _set_provider_session(request, provider)
            request.session.pop("verify_actor_type", None)
            request.session.pop("verify_actor_id", None)
            messages.success(request, "Billing information saved successfully.")
            return redirect("portal:provider_dashboard")
    else:
        form = ProviderBillingForm(instance=provider, provider=provider)

    return render(
        request,
        "providers/complete_billing.html",
        {
            "provider": provider,
            "form": form,
        },
    )
