from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from .models import JobEvent


def event_job_count(*, event_type: str, since, note_contains: str | None = None) -> int:
    qs = JobEvent.objects.filter(event_type=event_type, created_at__gte=since)
    if note_contains:
        qs = qs.filter(note__icontains=note_contains)
    return qs.values("job_id").distinct().count()


def outcome_rates(*, since_hours: int = 168) -> dict[str, float]:
    """
    Tasas basadas en jobs posted en la ventana.
    - revert_rate: % posted que tuvieron timeout de 60m revert
    - expire_rate: % posted que tuvieron timeout 24h pending_client_decision
    - cancel_rate: % posted cancelados
    """
    since = timezone.now() - timedelta(hours=since_hours)

    posted_count = (
        JobEvent.objects.filter(event_type="posted", created_at__gte=since)
        .values("job_id")
        .distinct()
        .count()
    )
    if posted_count == 0:
        return {
            "posted": 0.0,
            "revert_rate": 0.0,
            "expire_rate": 0.0,
            "cancel_rate": 0.0,
        }

    revert_jobs = event_job_count(
        event_type="timeout",
        since=since,
        note_contains="client_confirm_60m_revert",
    )
    expire_jobs = event_job_count(
        event_type="timeout",
        since=since,
        note_contains="pending_client_decision_24h",
    )
    cancel_jobs = event_job_count(
        event_type="cancelled",
        since=since,
        note_contains=None,
    )

    return {
        "posted": float(posted_count),
        "revert_rate": revert_jobs / posted_count,
        "expire_rate": expire_jobs / posted_count,
        "cancel_rate": cancel_jobs / posted_count,
    }


def funnel_extended(*, since_hours: int = 168) -> dict[str, int]:
    since = timezone.now() - timedelta(hours=since_hours)

    return {
        "posted": event_job_count(event_type="posted", since=since),
        "provider_accepted": event_job_count(event_type="provider_accepted", since=since),
        "client_confirmed": event_job_count(event_type="client_confirmed", since=since),
        "assigned": event_job_count(event_type="assigned", since=since),
        "timeout_revert_60m": event_job_count(
            event_type="timeout",
            since=since,
            note_contains="client_confirm_60m_revert",
        ),
        "timeout_expire_24h": event_job_count(
            event_type="timeout",
            since=since,
            note_contains="pending_client_decision_24h",
        ),
        "cancelled": event_job_count(event_type="cancelled", since=since),
    }
