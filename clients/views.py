from django.db import transaction
from django.shortcuts import redirect, render

from verifications.services import create_phone_verification

from .forms import ClientProfileForm, ClientRegisterForm
from .models import Client


def _split_full_name(full_name: str) -> tuple[str, str]:
    normalized = " ".join((full_name or "").strip().split())
    if not normalized:
        return "Client", "User"

    parts = normalized.split(" ", 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else "User"
    return first_name, last_name


def client_register(request):
    if request.method == "POST":
        form = ClientRegisterForm(request.POST)

        if form.is_valid():
            first_name, last_name = _split_full_name(form.cleaned_data["full_name"])

            with transaction.atomic():
                client = Client.objects.create(
                    first_name=first_name,
                    last_name=last_name,
                    email=form.cleaned_data["email"],
                    phone_number=form.cleaned_data["phone_number"].strip(),
                    is_phone_verified=False,
                    profile_completed=False,
                    country="Canada",
                    province="QC",
                    city="Pending",
                    postal_code="PENDING",
                    address_line1="Pending profile completion",
                )

                create_phone_verification(
                    actor_type="client",
                    actor_id=client.pk,
                    phone_number=client.phone_number,
                )

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

    return render(request, "clients/dashboard.html", {"client": client})


def client_profile(request):
    client_id = request.session.get("client_id")
    if not client_id:
        return redirect("client_register")

    client = Client.objects.filter(pk=client_id).first()
    if not client:
        request.session.pop("client_id", None)
        return redirect("client_register")

    return render(request, "clients/profile.html", {"client": client})


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

    if client.profile_completed:
        request.session["client_id"] = client.pk
        request.session.pop("verify_actor_type", None)
        request.session.pop("verify_actor_id", None)
        return redirect("client_dashboard")

    if request.method == "POST":
        form = ClientProfileForm(request.POST, instance=client)
        if form.is_valid():
            form.save()
            client.profile_completed = True
            client.save(update_fields=["profile_completed", "updated_at"])
            request.session["client_id"] = client.pk
            request.session.pop("verify_actor_type", None)
            request.session.pop("verify_actor_id", None)
            return redirect("client_dashboard")
    else:
        form = ClientProfileForm(instance=client)

    return render(request, "clients/complete_profile.html", {"form": form, "client": client})
