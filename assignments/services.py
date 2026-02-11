from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from assignments.models import JobAssignment
from jobs.models import Job, JobStatus

ACTIVE_VALUE = True
INACTIVE_VALUE = False


@dataclass(frozen=True)
class ActivateResult:
    assignment_id: int
    created: bool  # True = created, False = already active for this provider.


class AssignmentConflict(Exception):
    """Raised when concurrent activation collides on DB uniqueness constraints."""


@transaction.atomic
def activate_assignment_for_job(
    *,
    job_id: int,
    provider_id: int,
    actor_user_id: Optional[int] = None,
) -> ActivateResult:
    """
    Activate a JobAssignment safely under concurrency.

    Flow:
    1) Lock current assignments for the job.
    2) Return existing active assignment for the same provider (idempotency).
    3) If another provider is already active, raise conflict.
    4) Create the new active assignment.
    5) Convert DB integrity collisions into AssignmentConflict.
    """
    # Keep signature backward-compatible even if the model has no user audit field yet.
    _ = actor_user_id

    # 1) LOCK: serializes concurrent updates on existing rows for this job.
    JobAssignment.objects.select_for_update().filter(job_id=job_id).exists()

        # ✅ NUEVO: lock del Job + validar que siga disponible (posted)
    job_row = (
        Job.objects.select_for_update()
        .filter(job_id=job_id)
        .values("job_status")
        .first()
    )
    if not job_row:
        raise AssignmentConflict("Job no existe.")
    if job_row["job_status"] != "posted":
        raise AssignmentConflict(f"Job no disponible (status={job_row['job_status']}).")

    # 2) Idempotency: same provider already active.
    existing = JobAssignment.objects.filter(
        job_id=job_id,
        provider_id=provider_id,
        is_active=ACTIVE_VALUE,
    ).first()
    if existing:    
        Job.objects.filter(job_id=job_id).update(job_status=JobStatus.ASSIGNED)
        return ActivateResult(
            assignment_id=existing.assignment_id,
            created=False,
        )

    # 3) Another provider is already active for this job.
    active_other = (
        JobAssignment.objects.filter(
            job_id=job_id,
            is_active=ACTIVE_VALUE,
        )
        .exclude(provider_id=provider_id)
        .first()
    )
    if active_other:
        current_status = JobStatus(job_row["job_status"]).label
        raise AssignmentConflict(
    f"Job no disponible (status={current_status})."
)


    # 4) Create new active assignment.
    try:
        assignment = JobAssignment.objects.create(
            job_id=job_id,
            provider_id=provider_id,
            is_active=ACTIVE_VALUE,
            assignment_status="accepted",
            accepted_at=timezone.now(),
        )

        # ✅ NUEVO: al aceptar, marcamos el job como asignado dentro de la misma transacción
        Job.objects.filter(job_id=job_id).update(job_status="assigned")

        return ActivateResult(
            assignment_id=assignment.assignment_id,
            created=True,
        )
    except IntegrityError as e:
        # 5) Unique index for one active assignment per job blocked this transaction.
        raise AssignmentConflict(
            "Concurrency conflict: job was accepted by another process."
        ) from e
def start_job(job_id: int) -> None:
    """
    Transición segura: assigned -> in_progress
    (lock del Job + validación de estado)
    """
    with transaction.atomic():
        job_row = (
            Job.objects.select_for_update()
            .filter(job_id=job_id)
            .values("job_status")
            .first()
        )
        if not job_row:
            raise AssignmentConflict("Job no existe.")

        if job_row["job_status"] != JobStatus.ASSIGNED:
            current_status = JobStatus(job_row["job_status"]).label
            raise AssignmentConflict(f"No se puede iniciar (status={current_status}).")

        Job.objects.filter(job_id=job_id).update(job_status=JobStatus.IN_PROGRESS)

def complete_job(job_id: int) -> None:
    """
    Transición segura: in_progress -> completed
    (lock del Job + validación de estado)
    """
    with transaction.atomic():
        job_row = (
            Job.objects.select_for_update()
            .filter(job_id=job_id)
            .values("job_status")
            .first()
        )
        if not job_row:
            raise AssignmentConflict("Job no existe.")

        if job_row["job_status"] != JobStatus.IN_PROGRESS:
            current_status = JobStatus(job_row["job_status"]).label
            raise AssignmentConflict(f"No se puede completar (status={current_status}).")

        Job.objects.filter(job_id=job_id).update(job_status=JobStatus.COMPLETED)

