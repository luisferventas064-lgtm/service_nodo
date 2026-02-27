from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from providers.models import Provider
from settlements.services import (
    get_provider_monthly_dashboard,
    get_provider_year_summary,
)


def can_view_provider_financials(user, provider) -> bool:
    if user.is_superuser or user.is_staff:
        return True

    user_email = (getattr(user, "email", None) or "").strip().lower()
    provider_email = (getattr(provider, "email", None) or "").strip().lower()
    if user_email and provider_email and user_email == provider_email:
        return True

    return False


@login_required
@require_GET
def provider_financial_dashboard(request, provider_id):
    """
    Returns provider financial dashboard data:
    - Monthly closed snapshots
    - Year summary
    """

    try:
        provider = Provider.objects.get(provider_id=provider_id)
    except Provider.DoesNotExist:
        return JsonResponse({"detail": "Provider not found"}, status=404)

    if not can_view_provider_financials(request.user, provider):
        return JsonResponse({"detail": "Forbidden"}, status=403)

    monthly = get_provider_monthly_dashboard(provider_id)
    yearly = get_provider_year_summary(provider_id)

    return JsonResponse(
        {
            "provider_id": provider_id,
            "year_summary": yearly,
            "monthly_closes": monthly,
        }
    )
