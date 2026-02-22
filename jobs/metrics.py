from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import JobEvent


@transaction.atomic
def log_job_event(
    *,
    job_id: int,
    event_type: str,
    provider_id: int | None = None,
    assignment_id: int | None = None,
    note: str = "",
    dedupe_seconds: int = 5,
) -> JobEvent:
    """
    Log idempotente por ventana corta.
    Evita spam de eventos si el mismo endpoint se llama 2 veces (idempotencia / retry).
    """
    now = timezone.now()
    since = now - timezone.timedelta(seconds=dedupe_seconds)

    qs = JobEvent.objects.select_for_update().filter(
        job_id=job_id,
        event_type=event_type,
        provider_id=provider_id,
        assignment_id=assignment_id,
        created_at__gte=since,
    )
    if note:
        qs = qs.filter(note=note)

    existing = qs.order_by("-created_at").first()
    if existing:
        return existing

    return JobEvent.objects.create(
        job_id=job_id,
        event_type=event_type,
        provider_id=provider_id,
        assignment_id=assignment_id,
        note=note[:255],
        created_at=now,
    )
