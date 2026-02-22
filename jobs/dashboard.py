from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

from django.db.models import Max
from django.utils import timezone

from .attention import needing_attention
from .kpis import funnel_counts, kpi_for_job, rates
from .kpis_extended import funnel_extended, outcome_rates
from .models import Job, JobEvent


@dataclass(frozen=True)
class StuckJob:
    job_id: int
    status: str
    last_event_type: str | None
    last_event_at: timezone.datetime | None
    age: timedelta | None
    reason: str | None = None


def _pctl(sorted_seconds: list[float], p: float) -> float | None:
    if not sorted_seconds:
        return None
    # p in [0,1]
    idx = int(round((len(sorted_seconds) - 1) * p))
    idx = max(0, min(idx, len(sorted_seconds) - 1))
    return sorted_seconds[idx]


def _collect_durations(job_ids: Iterable[int]) -> dict[str, dict[str, float | None]]:
    t_first_accept = []
    t_client_confirm = []
    t_posted_to_assigned = []

    for jid in job_ids:
        k = kpi_for_job(jid)
        if k.time_to_first_accept:
            t_first_accept.append(k.time_to_first_accept.total_seconds())
        if k.time_to_client_confirm:
            t_client_confirm.append(k.time_to_client_confirm.total_seconds())
        if k.time_posted_to_assigned:
            t_posted_to_assigned.append(k.time_posted_to_assigned.total_seconds())

    def pack(arr: list[float]) -> dict[str, float | None]:
        arr.sort()
        return {
            "n": float(len(arr)),
            "p50_sec": _pctl(arr, 0.50),
            "p95_sec": _pctl(arr, 0.95),
        }

    return {
        "time_to_first_accept": pack(t_first_accept),
        "time_to_client_confirm": pack(t_client_confirm),
        "time_posted_to_assigned": pack(t_posted_to_assigned),
    }


def stuck_jobs(*, older_than_minutes: int = 60, limit: int = 30) -> list[StuckJob]:
    """
    Stuck con SLA por estado.
    older_than_minutes queda como fallback, pero aplicamos SLA especifico por estado.

    SLA sugeridos:
    - waiting_provider_response: 30 min (ajustalo)
    - pending_client_confirmation: 60 min (tu regla)
    - pending_client_decision: 24h (tu regla)
    """
    now = timezone.now()

    sla = {
        "waiting_provider_response": timedelta(minutes=30),
        "pending_client_confirmation": timedelta(minutes=60),
        "pending_client_decision": timedelta(hours=24),
    }

    fallback = timedelta(minutes=older_than_minutes)

    candidates = (
        Job.objects.filter(job_status__in=list(sla.keys()))
        .annotate(last_event_at=Max("events__created_at"))
        .order_by("last_event_at")[: limit * 5]
    )

    out: list[StuckJob] = []

    for job in candidates:
        last = (
            JobEvent.objects.filter(job_id=job.job_id)
            .order_by("-created_at")
            .values_list("event_type", "note", "created_at")
            .first()
        )

        last_type = last[0] if last else None
        last_note = last[1] if last else ""
        last_at = last[2] if last else job.last_event_at

        if not last_at:
            continue

        age = now - last_at
        job_sla = sla.get(job.job_status, fallback)

        if age < job_sla:
            continue

        if job.job_status == "pending_client_confirmation":
            reason = "SLA_BREACH_CLIENT_CONFIRM_60M"
        elif job.job_status == "pending_client_decision":
            reason = "SLA_BREACH_CLIENT_DECISION_24H"
        elif job.job_status == "waiting_provider_response":
            if "client_confirm_60m_revert" in (last_note or ""):
                reason = "SLA_BREACH_AFTER_REVERT_WAITING"
            else:
                reason = "SLA_BREACH_WAITING_PROVIDER"
        else:
            reason = "SLA_BREACH"

        out.append(
            StuckJob(
                job_id=job.job_id,
                status=job.job_status,
                last_event_type=last_type,
                last_event_at=last_at,
                age=age,
                reason=reason,
            )
        )

        if len(out) >= limit:
            break

    return out


def dashboard(*, since_hours: int = 168) -> dict:
    """
    Snapshot compacto para monitoreo.
    """
    fc = funnel_counts(since_hours=since_hours)
    rt = rates(since_hours=since_hours)

    since = timezone.now() - timedelta(hours=since_hours)
    recent_job_ids = list(
        JobEvent.objects.filter(event_type="posted", created_at__gte=since)
        .values_list("job_id", flat=True)
        .distinct()
    )

    durations = _collect_durations(recent_job_ids)

    return {
        "since_hours": since_hours,
        "funnel_counts": fc,
        "rates": rt,
        "funnel_extended": funnel_extended(since_hours=since_hours),
        "outcome_rates": outcome_rates(since_hours=since_hours),
        "attention": needing_attention(since_hours=since_hours),
        "durations_seconds": durations,
        "stuck_preview": [
            {
                "job_id": s.job_id,
                "status": s.status,
                "last_event_type": s.last_event_type,
                "last_event_at": s.last_event_at,
                "age_minutes": (s.age.total_seconds() / 60.0) if s.age else None,
                "reason": s.reason,
            }
            for s in stuck_jobs(older_than_minutes=60, limit=15)
        ],
    }
