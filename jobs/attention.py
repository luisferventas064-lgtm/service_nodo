from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from assignments.models import JobAssignment

from .models import Job, JobEvent


@dataclass(frozen=True)
class AttentionItem:
    job_id: int
    kind: str
    detail: str


def jobs_with_repeated_reverts(
    *,
    since_hours: int = 168,
    min_reverts: int = 2,
    limit: int = 50,
) -> list[AttentionItem]:
    """
    Jobs con >= min_reverts eventos TIMEOUT de tipo revert 60m.
    """
    since = timezone.now() - timedelta(hours=since_hours)

    rows = (
        JobEvent.objects.filter(
            event_type="timeout",
            created_at__gte=since,
            note__icontains="client_confirm_60m_revert",
        )
        .values("job_id")
        .annotate(n=Count("id"))
        .filter(n__gte=min_reverts)
        .order_by("-n")[:limit]
    )

    out = []
    for r in rows:
        out.append(
            AttentionItem(
                job_id=r["job_id"],
                kind="REPEATED_REVERTS_60M",
                detail=f"reverts={r['n']}",
            )
        )
    return out


def accept_without_assign(*, since_hours: int = 168, limit: int = 50) -> list[AttentionItem]:
    """
    Jobs con PROVIDER_ACCEPTED pero sin ASSIGNED (en la ventana).
    Esto puede ser normal si estan esperando confirmacion, pero te sirve para monitoreo.
    """
    since = timezone.now() - timedelta(hours=since_hours)

    accepted_jobs = (
        JobEvent.objects.filter(event_type="provider_accepted", created_at__gte=since)
        .values_list("job_id", flat=True)
        .distinct()
    )

    assigned_jobs = set(
        JobEvent.objects.filter(event_type="assigned", created_at__gte=since)
        .values_list("job_id", flat=True)
        .distinct()
    )

    out = []
    count = 0
    for jid in accepted_jobs:
        if jid not in assigned_jobs:
            status = Job.objects.filter(job_id=jid).values_list("job_status", flat=True).first()
            out.append(
                AttentionItem(
                    job_id=jid,
                    kind="ACCEPTED_NOT_ASSIGNED_YET",
                    detail=f"status={status}",
                )
            )
            count += 1
            if count >= limit:
                break
    return out


def assignment_inconsistencies(*, since_hours: int = 168, limit: int = 50) -> list[AttentionItem]:
    """
    Casos estructurales:
    - evento ASSIGNED pero no existe JobAssignment activo para ese job.
    """
    since = timezone.now() - timedelta(hours=since_hours)

    assigned_job_ids = list(
        JobEvent.objects.filter(event_type="assigned", created_at__gte=since)
        .values_list("job_id", flat=True)
        .distinct()
    )

    out = []
    for jid in assigned_job_ids:
        has_active = JobAssignment.objects.filter(job_id=jid, is_active=True).exists()
        if not has_active:
            out.append(
                AttentionItem(
                    job_id=jid,
                    kind="ASSIGNED_WITHOUT_ACTIVE_ASSIGNMENT",
                    detail="assigned event exists but no active JobAssignment found",
                )
            )
            if len(out) >= limit:
                break
    return out


def needing_attention(*, since_hours: int = 168) -> dict[str, list[dict]]:
    a = jobs_with_repeated_reverts(since_hours=since_hours)
    b = accept_without_assign(since_hours=since_hours)
    c = assignment_inconsistencies(since_hours=since_hours)

    def pack(items: list[AttentionItem]) -> list[dict]:
        return [{"job_id": i.job_id, "kind": i.kind, "detail": i.detail} for i in items]

    return {
        "repeated_reverts": pack(a),
        "accepted_not_assigned": pack(b),
        "assignment_inconsistencies": pack(c),
    }
