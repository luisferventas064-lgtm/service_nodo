from django.db import transaction
from django.shortcuts import redirect, render

from verifications.services import create_phone_verification

from .forms import (
    ProviderBillingForm,
    ProviderCompanyProfileForm,
    ProviderIndividualProfileForm,
    ProviderRegisterForm,
    _split_contact_name,
)
from .models import Provider


def _to_model_provider_type(provider_type: str) -> str:
    if provider_type == "company":
        return Provider.TYPE_COMPANY
    return Provider.TYPE_SELF_EMPLOYED


def provider_register(request):
    if request.method == "POST":
        form = ProviderRegisterForm(request.POST)

        if form.is_valid():
            business_name = form.cleaned_data["business_name"].strip()
            provider_type = _to_model_provider_type(form.cleaned_data["provider_type"])
            contact_first_name, contact_last_name = _split_contact_name(business_name)

            with transaction.atomic():
                provider = Provider.objects.create(
                    provider_type=provider_type,
                    company_name=business_name if provider_type == Provider.TYPE_COMPANY else None,
                    contact_first_name=contact_first_name,
                    contact_last_name=contact_last_name,
                    phone_number=form.cleaned_data["phone_number"].strip(),
                    email=form.cleaned_data["email"],
                    is_phone_verified=False,
                    profile_completed=False,
                    accepts_terms=False,
                    billing_profile_completed=False,
                    country="Canada",
                    province="QC",
                    city="Pending",
                    postal_code="PENDING",
                    address_line1="Pending profile completion",
                )

                create_phone_verification(
                    actor_type="provider",
                    actor_id=provider.pk,
                    phone_number=provider.phone_number,
                )

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

    if request.method == "POST":
        form = profile_form_class(request.POST, instance=provider)

        if form.is_valid():
            with transaction.atomic():
                form.save()
                provider.accepts_terms = form.cleaned_data["accepts_terms"]
                provider.profile_completed = True
                provider.save(
                    update_fields=[
                        "accepts_terms",
                        "profile_completed",
                        "updated_at",
                    ]
                )

            request.session["provider_id"] = provider.pk
            return redirect("provider_complete_billing")
    else:
        form = profile_form_class(instance=provider)

    return render(
        request,
        "providers/complete_profile.html",
        {
            "provider": provider,
            "form": form,
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
