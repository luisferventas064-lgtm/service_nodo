from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from .services import verify_phone_code


def verify_phone_page(request):
    actor_type = request.session.get("verify_actor_type")
    actor_id = request.session.get("verify_actor_id")

    if not actor_type or not actor_id:
        return redirect("client_register")

    if request.method == "POST":
        code = (request.POST.get("code") or "").strip()

        try:
            verify_phone_code(actor_type, actor_id, code)
        except ValidationError:
            return render(
                request,
                "verifications/verify.html",
                {"error": "Invalid or expired code."},
            )

        if actor_type == "provider":
            request.session["provider_id"] = actor_id
            return redirect("provider_complete_profile")

        request.session["client_id"] = actor_id
        return redirect("client_complete_profile")

    return render(request, "verifications/verify.html")
