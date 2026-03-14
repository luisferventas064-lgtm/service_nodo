from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from assignments.models import AssignmentFee, JobAssignment
from jobs.events import create_job_event
from jobs.models import Job, JobDispute, JobEvent, JobStatus

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
        raise AssignmentConflict("Job does not exist.")
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
        create_job_event(
            job=job_id,
            event_type=JobEvent.EventType.JOB_ACCEPTED,
            actor_role=JobEvent.ActorRole.PROVIDER,
            provider_id=provider_id,
            assignment_id=existing.assignment_id,
            payload={"source": "activate_assignment_for_job"},
            unique_per_job=True,
            job_status=JobStatus.ASSIGNED,
        )
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
        assigned_at = timezone.now()
        assignment = JobAssignment.objects.create(
            job_id=job_id,
            provider_id=provider_id,
            is_active=ACTIVE_VALUE,
            assignment_status="accepted",
            accepted_at=assigned_at,
        )

        Job.objects.filter(job_id=job_id).update(job_status=JobStatus.ASSIGNED)
        from providers.models import Provider

        Provider.objects.filter(provider_id=provider_id).update(last_job_assigned_at=assigned_at)

        JobEvent.objects.create(
            job_id=job_id,
            event_type=JobEvent.EventType.ASSIGNED,
            provider_id=provider_id,
            assignment_id=assignment.assignment_id,
            note="provider accepted job",
        )
        create_job_event(
            job=job_id,
            event_type=JobEvent.EventType.JOB_ACCEPTED,
            actor_role=JobEvent.ActorRole.PROVIDER,
            provider_id=provider_id,
            assignment_id=assignment.assignment_id,
            payload={"source": "activate_assignment_for_job"},
            unique_per_job=True,
            job_status=JobStatus.ASSIGNED,
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
            raise AssignmentConflict("Job does not exist.")

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
            raise AssignmentConflict("Worker not authorized to start this job.")

        # 5) Update assignment -> in_progress (+ timestamps)
        assignment.assignment_status = "in_progress"
        if assignment.accepted_at is None:
            assignment.accepted_at = timezone.now()
        assignment.save(update_fields=["worker_id", "assignment_status", "accepted_at", "updated_at"])

        # 6) Update job -> in_progress
        Job.objects.filter(job_id=job_id).update(job_status=JobStatus.IN_PROGRESS)

        # 7) Event
        create_job_event(
            job=job_id,
            event_type=JobEvent.EventType.JOB_IN_PROGRESS,
            actor_role=JobEvent.ActorRole.WORKER,
            payload={"assignment_id": assignment.assignment_id, "worker_id": worker_id},
            assignment_id=assignment.assignment_id,
            unique_per_job=True,
            job_status=JobStatus.IN_PROGRESS,
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
            raise AssignmentConflict("Job does not exist.")

        if JobDispute.objects.filter(
            job_id=job_id,
            status__in=(
                JobDispute.DisputeStatus.OPEN,
                JobDispute.DisputeStatus.UNDER_REVIEW,
            ),
        ).exists():
            raise AssignmentConflict("DISPUTE_OPEN")

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
            raise AssignmentConflict("Worker not authorized to complete this job.")

        # 4) Update assignment -> completed (+ timestamps)
        assignment.assignment_status = "completed"
        assignment.completed_at = timezone.now()
        assignment.save(update_fields=["assignment_status", "completed_at", "updated_at"])

        # 5) Update job -> completed
        Job.objects.filter(job_id=job_id).update(job_status=JobStatus.COMPLETED)

        # 6) Event
        create_job_event(
            job=job_id,
            event_type=JobEvent.EventType.JOB_COMPLETED,
            actor_role=JobEvent.ActorRole.WORKER,
            payload={"assignment_id": assignment.assignment_id, "worker_id": worker_id},
            assignment_id=assignment.assignment_id,
            unique_per_job=True,
            job_status=JobStatus.COMPLETED,
        )


def compute_assignment_fee_off():
    return {
        "payer": AssignmentFee.PAYER_NONE,
        "model": AssignmentFee.MODEL_OFF,
        "status": AssignmentFee.STATUS_OFF,
        "amount_cents": 0,
        "currency": "CAD",
    }
