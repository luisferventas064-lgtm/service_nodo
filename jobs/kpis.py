from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from .models import JobEvent


@dataclass(frozen=True)
class JobKPI:
    job_id: int
    posted_at: timezone.datetime | None
    first_accept_at: timezone.datetime | None
    client_confirmed_at: timezone.datetime | None
    assigned_at: timezone.datetime | None
    time_to_first_accept: timedelta | None
    time_to_client_confirm: timedelta | None
    time_posted_to_assigned: timedelta | None


def funnel_counts(*, since_hours: int = 72) -> dict[str, int]:
    since = timezone.now() - timedelta(hours=since_hours)

    def count(event_type: str) -> int:
        return (
            JobEvent.objects.filter(event_type=event_type, created_at__gte=since)
            .values("job_id")
            .distinct()
            .count()
        )

    return {
        "posted": count(JobEvent.EventType.POSTED),
        "provider_accepted": count(JobEvent.EventType.PROVIDER_ACCEPTED),
        "client_confirmed": count(JobEvent.EventType.CLIENT_CONFIRMED),
        "assigned": count(JobEvent.EventType.ASSIGNED),
        "timeout": count(JobEvent.EventType.TIMEOUT),
        "cancelled": count(JobEvent.EventType.CANCELLED),
    }


def rates(*, since_hours: int = 72) -> dict[str, float]:
    since = timezone.now() - timedelta(hours=since_hours)

    posted_jobs = (
        JobEvent.objects.filter(event_type=JobEvent.EventType.POSTED, created_at__gte=since)
        .values("job_id")
        .distinct()
    )
    posted_count = posted_jobs.count()

    if posted_count == 0:
        return {"timeout_rate": 0.0, "cancel_rate": 0.0}

    timeout_count = (
        JobEvent.objects.filter(event_type=JobEvent.EventType.TIMEOUT, created_at__gte=since)
        .values("job_id")
        .distinct()
        .count()
    )
    cancel_count = (
        JobEvent.objects.filter(event_type=JobEvent.EventType.CANCELLED, created_at__gte=since)
        .values("job_id")
        .distinct()
        .count()
    )

    return {
        "timeout_rate": timeout_count / posted_count,
        "cancel_rate": cancel_count / posted_count,
    }


def kpi_for_job(job_id: int) -> JobKPI:
    events = list(
        JobEvent.objects.filter(job_id=job_id)
        .order_by("created_at", "id")
        .values_list("event_type", "created_at")
    )

    posted_at = None
    first_accept_at = None
    client_confirmed_at = None
    assigned_at = None

    for event_type, created_at in events:
        if event_type == JobEvent.EventType.POSTED and posted_at is None:
            posted_at = created_at
        elif event_type == JobEvent.EventType.PROVIDER_ACCEPTED and first_accept_at is None:
            first_accept_at = created_at
        elif event_type == JobEvent.EventType.CLIENT_CONFIRMED and client_confirmed_at is None:
            client_confirmed_at = created_at
        elif event_type == JobEvent.EventType.ASSIGNED and assigned_at is None:
            assigned_at = created_at

    time_to_first_accept = None
    if posted_at and first_accept_at:
        time_to_first_accept = first_accept_at - posted_at

    time_to_client_confirm = None
    if first_accept_at and client_confirmed_at:
        time_to_client_confirm = client_confirmed_at - first_accept_at

    time_posted_to_assigned = None
    if posted_at and assigned_at:
        time_posted_to_assigned = assigned_at - posted_at

    return JobKPI(
        job_id=job_id,
        posted_at=posted_at,
        first_accept_at=first_accept_at,
        client_confirmed_at=client_confirmed_at,
        assigned_at=assigned_at,
        time_to_first_accept=time_to_first_accept,
        time_to_client_confirm=time_to_client_confirm,
        time_posted_to_assigned=time_posted_to_assigned,
    )
