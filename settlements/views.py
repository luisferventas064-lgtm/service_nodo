from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from providers.models import Provider, ProviderUser
from settlements.services import (
    get_provider_monthly_dashboard,
    get_provider_year_summary,
)


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

    user = request.user

    # Acceso total para staff o superuser
    if user.is_staff or user.is_superuser:
        pass
    else:
        has_access = ProviderUser.objects.filter(
            provider=provider,
            user=user,
            role__in=["owner", "finance"],
            is_active=True
        ).exists()

        if not has_access:
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
