from django.db.models import Q
from django.utils import timezone


def effective_provider_availability_q(*, now=None):
    now = now or timezone.now()
    return (
        Q(is_available_now=True)
        & ~Q(availability_mode__iexact="paused")
        & (
            Q(temporary_unavailable_until__isnull=True)
            | Q(temporary_unavailable_until__lte=now)
        )
    )


def is_provider_effectively_available(provider, *, now=None) -> bool:
    now = now or timezone.now()
    if not getattr(provider, "is_available_now", False):
        return False
    if str(getattr(provider, "availability_mode", "")).strip().lower() == "paused":
        return False
    until = getattr(provider, "temporary_unavailable_until", None)
    return until is None or until <= now
