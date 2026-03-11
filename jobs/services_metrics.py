from __future__ import annotations

from datetime import timedelta
from math import ceil
from statistics import median

from django.db.models import Prefetch
from django.utils import timezone

from assignments.models import JobAssignment
from providers.models import Provider

from .models import BroadcastAttemptStatus, Job, JobBroadcastAttempt, JobEvent


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _percentile_nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    rank = max(1, ceil(len(values) * percentile))
    return values[rank - 1]


def _summarize(
    values: list[float],
    *,
    avg_key: str,
    p50_key: str,
    p95_key: str,
) -> dict[str, float | int | None]:
    if not values:
        return {
            "n": 0,
            avg_key: None,
            p50_key: None,
            p95_key: None,
        }

    ordered = sorted(values)
    return {
        "n": len(ordered),
        avg_key: sum(ordered) / len(ordered),
        p50_key: median(ordered),
        p95_key: _percentile_nearest_rank(ordered, 0.95),
    }


def _job_dispatch_anchor(job: Job):
    accept_events = getattr(job, "dispatch_accept_events", None)
    if accept_events is None:
        accept_events = list(
            job.events.filter(event_type=JobEvent.EventType.PROVIDER_ACCEPTED).order_by(
                "created_at",
                "id",
            )[:1]
        )
    if accept_events:
        return accept_events[0].created_at

    assignments = getattr(job, "dispatch_assignments", None)
    if assignments is None:
        assignments = list(
            job.assignments.order_by("created_at", "assignment_id")[:1]
        )
    if not assignments:
        return None

    assignment = assignments[0]
    return assignment.accepted_at or assignment.assigned_at or assignment.created_at


def compute_dispatch_time_seconds(job: Job) -> float | None:
    dispatch_anchor = _job_dispatch_anchor(job)
    if dispatch_anchor is None or job.created_at is None:
        return None
    return max((dispatch_anchor - job.created_at).total_seconds(), 0.0)


def matching_health(*, since_hours: int = 168) -> dict:
    since = timezone.now() - timedelta(hours=since_hours)

    jobs = list(
        Job.objects.exclude(job_status=Job.JobStatus.DRAFT)
        .filter(created_at__gte=since)
        .prefetch_related(
            Prefetch(
                "events",
                queryset=JobEvent.objects.filter(
                    event_type=JobEvent.EventType.PROVIDER_ACCEPTED
                ).order_by("created_at", "id"),
                to_attr="dispatch_accept_events",
            ),
            Prefetch(
                "assignments",
                queryset=JobAssignment.objects.order_by("created_at", "assignment_id"),
                to_attr="dispatch_assignments",
            ),
        )
    )

    job_ids = [job.job_id for job in jobs]
    dispatch_times = [
        dispatch_time
        for dispatch_time in (compute_dispatch_time_seconds(job) for job in jobs)
        if dispatch_time is not None
    ]
    dispatch_attempts = [
        float(int(job.marketplace_attempts or 0) + int(job.alert_attempts or 0))
        for job in jobs
    ]

    broadcast_attempts = JobBroadcastAttempt.objects.filter(created_at__gte=since)
    offers_sent = broadcast_attempts.filter(
        status__in=[
            BroadcastAttemptStatus.SENT,
            BroadcastAttemptStatus.ACCEPTED,
        ]
    ).count()
    offers_accepted = broadcast_attempts.filter(
        status=BroadcastAttemptStatus.ACCEPTED
    ).count()

    assigned_jobs = (
        JobAssignment.objects.filter(job_id__in=job_ids)
        .values("job_id")
        .distinct()
        .count()
        if job_ids
        else 0
    )

    active_providers = Provider.objects.filter(is_active=True).count()
    providers_with_jobs = (
        JobAssignment.objects.filter(created_at__gte=since, provider__is_active=True)
        .exclude(provider_id__isnull=True)
        .values("provider_id")
        .distinct()
        .count()
    )

    return {
        "since_hours": since_hours,
        "dispatch_time_seconds": _summarize(
            dispatch_times,
            avg_key="avg_seconds",
            p50_key="p50_seconds",
            p95_key="p95_seconds",
        ),
        "acceptance_rate": {
            "offers_sent": offers_sent,
            "offers_accepted": offers_accepted,
            "value": _rate(offers_accepted, offers_sent),
        },
        "broadcast_attempts_per_job": {
            "jobs_created": len(jobs),
            **_summarize(
                dispatch_attempts,
                avg_key="avg_waves",
                p50_key="p50_waves",
                p95_key="p95_waves",
            ),
        },
        "coverage_rate": {
            "jobs_created": len(jobs),
            "jobs_assigned": assigned_jobs,
            "value": _rate(assigned_jobs, len(jobs)),
        },
        "provider_utilization": {
            "active_providers": active_providers,
            "providers_with_jobs": providers_with_jobs,
            "value": _rate(providers_with_jobs, active_providers),
        },
    }
