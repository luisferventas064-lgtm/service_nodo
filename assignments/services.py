```python
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
    _ = actor_user_id

    # 1) LOCK assignments rows (serialize concurrency)
    JobAssignment.objects.select_for_update().filter(job_id=job_id).exists()

    # 2) LOCK job row + validate available
    job_row = (
        Job.objects.select_for_update()
        .filter(job_id=job_id)
        .values("job_status")
        .first()
    )
    if not job_row:
        raise AssignmentConflict("Job no existe.")
    if job_row["job_status"] != JobStatus.POSTED:
        raise AssignmentConflict(f"Job no disponible (status={job_row['job_status']}).")

    # 3) Idempotency: same provider already active
    existing = JobAssignment.objects.filter(
        job_id=job_id,
        provider_id=provider_id,
        is_active=ACTIVE_VALUE,
    ).first()
    if existing:
        Job.objects.filter(job_id=job_id).update(job_status=JobStatus.ASSIGNED)
        return ActivateResult(assignment_id=existing.assignment_id, created=False)

    # 4) Another provider already active
    active_other = (
        JobAssignment.objects.filter(job_id=job_id, is_active=ACTIVE_VALUE)
        .exclude(provider_id=provider_id)
        .first()
    )
    if active_other:
        raise AssignmentConflict("Concurrency conflict: job already active for another provider.")

    # 5) Create assignment + update job + event (same transaction)
    try:
        assignment = JobAssignment.objects.create(
            job_id=job_id,
            provider_id=provider_id,
            is_active=ACTIVE_VALUE,
            assignment_status="accepted",
            accepted_at=timezone.now(),
        )

        Job.objects.filter(job_id=job_id).update(job_status=JobStatus.ASSIGNED)

        from jobs.models import JobEvent
        JobEvent.objects.create(
            job_id=job_id,
            event_type="JOB_ACCEPTED",
            actor_type="provider",
            provider_id=provider_id,
            job_status_snapshot=JobStatus.ASSIGNED,
            payload={"assignment_id": assignment.assignment_id},
        )

        return ActivateResult(assignment_id=assignment.assignment_id, created=True)

    except IntegrityError as e:
        raise AssignmentConflict(
            "Concurrency conflict: job was accepted by another process."
        ) from e


def start_job(*, job_id: int, worker_id: int) -> None:
    """
    Transición segura: assigned -> in_progress
    + actualiza JobAssignment activo
    + crea JobEvent "JOB_STARTED"

    Reglas:
    - Job debe estar ASSIGNED
    - Debe existir JobAssignment activo
    - Si assignment.worker está vacío, se setea al worker_id (first-start-wins)
    - Si assignment.worker existe y no coincide, conflicto
    """
    with transaction.atomic():
        # 1) LOCK assignments rows (serialize)
        JobAssignment.objects.select_for_update().filter(job_id=job_id).exists()

        # 2) LOCK job row + validate
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

        # 3) Get active assignment (locked by step 1)
        assignment = JobAssignment.objects.filter(
            job_id=job_id,
            is_active=True,
        ).first()

        if not assignment:
            raise AssignmentConflict("No hay JobAssignment activo para este job.")

        # 4) Worker authorization / binding
        if assignment.worker_id is None:
            assignment.worker_id = worker_id
        elif assignment.worker_id != worker_id:
            raise AssignmentConflict("Worker no autorizado para iniciar este job.")

        # 5) Update assignment -> in_progress (+ timestamps)
        assignment.assignment_status = "in_progress"
        if assignment.accepted_at is None:
            assignment.accepted_at = timezone.now()
        assignment.save(update_fields=["worker_id", "assignment_status", "accepted_at", "updated_at"])

        # 6) Update job -> in_progress
        Job.objects.filter(job_id=job_id).update(job_status=JobStatus.IN_PROGRESS)

        # 7) Event
        from jobs.models import JobEvent
        JobEvent.objects.create(
            job_id=job_id,
            event_type="JOB_STARTED",
            actor_type="worker",
            worker_id=worker_id,
            job_status_snapshot=JobStatus.IN_PROGRESS,
            payload={"assignment_id": assignment.assignment_id},
        )


def complete_job(*, job_id: int, worker_id: int) -> None:
    """
    Transición segura: in_progress -> completed
    + actualiza JobAssignment activo
    + crea JobEvent "JOB_COMPLETED"

    Reglas:
    - Job debe estar IN_PROGRESS
    - Debe existir JobAssignment activo
    - Worker debe coincidir con assignment.worker
    """
    with transaction.atomic():
        # 1) LOCK assignments rows (serialize)
        JobAssignment.objects.select_for_update().filter(job_id=job_id).exists()

        # 2) LOCK job row + validate
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

        # 3) Get active assignment
        assignment = JobAssignment.objects.filter(
            job_id=job_id,
            is_active=True,
        ).first()

        if not assignment:
            raise AssignmentConflict("No hay JobAssignment activo para este job.")

        if assignment.worker_id != worker_id:
            raise AssignmentConflict("Worker no autorizado para completar este job.")

        # 4) Update assignment -> completed (+ timestamps)
        assignment.assignment_status = "completed"
        assignment.completed_at = timezone.now()
        assignment.save(update_fields=["assignment_status", "completed_at", "updated_at"])

        # 5) Update job -> completed
        Job.objects.filter(job_id=job_id).update(job_status=JobStatus.COMPLETED)

        # 6) Event
        from jobs.models import JobEvent
        JobEvent.objects.create(
            job_id=job_id,
            event_type="JOB_COMPLETED",
            actor_type="worker",
            worker_id=worker_id,
            job_status_snapshot=JobStatus.COMPLETED,
            payload={"assignment_id": assignment.assignment_id},
        )
```
