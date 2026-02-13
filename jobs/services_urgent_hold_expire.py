from django.utils import timezone
from jobs.models import Job


def release_expired_holds() -> int:
    """
    Libera todos los HOLD expirados.
    Retorna cantidad liberada.
    """

    now = timezone.now()

    expired_jobs = Job.objects.filter(
        hold_provider__isnull=False,
        hold_expires_at__lte=now,
    )

    count = expired_jobs.count()

    expired_jobs.update(
        hold_provider=None,
        hold_expires_at=None,
        quoted_urgent_total_price=None,
        quoted_urgent_fee_amount=None,
    )

    return count
