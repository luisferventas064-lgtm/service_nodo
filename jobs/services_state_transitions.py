from __future__ import annotations

from dataclasses import dataclass

from jobs.models import Job
from jobs.observability import log_job_transition


class InvalidStateTransition(ValueError):
    pass


_CANONICAL_JOB_TRANSITIONS = {
    Job.JobStatus.SCHEDULED_PENDING_ACTIVATION: {Job.JobStatus.WAITING_PROVIDER_RESPONSE},
    Job.JobStatus.WAITING_PROVIDER_RESPONSE: {
        Job.JobStatus.ASSIGNED,
        Job.JobStatus.EXPIRED,
        Job.JobStatus.CANCELLED,
    },
    Job.JobStatus.ASSIGNED: {
        Job.JobStatus.IN_PROGRESS,
        Job.JobStatus.CANCELLED,
    },
    Job.JobStatus.IN_PROGRESS: {
        Job.JobStatus.COMPLETED,
        Job.JobStatus.CONFIRMED,
        Job.JobStatus.CANCELLED,
    },
    Job.JobStatus.COMPLETED: {
        Job.JobStatus.CONFIRMED,
        Job.JobStatus.CANCELLED,
    },
    Job.JobStatus.CONFIRMED: set(),
    Job.JobStatus.EXPIRED: set(),
    Job.JobStatus.CANCELLED: set(),
}

# Allowed temporarily while legacy flows are being deprecated.
_LEGACY_JOB_TRANSITIONS = {
    Job.JobStatus.SCHEDULED_PENDING_ACTIVATION: {
        Job.JobStatus.CANCELLED,
    },
    Job.JobStatus.WAITING_PROVIDER_RESPONSE: {
        Job.JobStatus.POSTED,  # provider decline recycles job back to marketplace
        Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
        Job.JobStatus.PENDING_CLIENT_DECISION,
    },
    Job.JobStatus.PENDING_CLIENT_CONFIRMATION: {
        Job.JobStatus.ASSIGNED,
        Job.JobStatus.WAITING_PROVIDER_RESPONSE,
        Job.JobStatus.PENDING_CLIENT_DECISION,
        Job.JobStatus.CANCELLED,
    },
    Job.JobStatus.PENDING_CLIENT_DECISION: {
        Job.JobStatus.WAITING_PROVIDER_RESPONSE,
        Job.JobStatus.POSTED,
        Job.JobStatus.CANCELLED,
        Job.JobStatus.EXPIRED,
    },
    Job.JobStatus.PENDING_PROVIDER_CONFIRMATION: {Job.JobStatus.PENDING_CLIENT_CONFIRMATION},
    Job.JobStatus.POSTED: {Job.JobStatus.ASSIGNED, Job.JobStatus.EXPIRED},
}

_CANONICAL_ASSIGNMENT_TRANSITIONS = {
    "assigned": {"in_progress", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed": {"cancelled"},
    "cancelled": set(),
    "accepted": {"in_progress", "cancelled", "completed"},
    "expired": set(),
}


@dataclass(frozen=True)
class TransitionMeta:
    previous: str
    normalized_previous: str
    target: str
    classification: str


def normalize_job_status(status: str | None) -> str:
    value = str(status or "").strip()
    if value == Job.JobStatus.POSTED:
        return Job.JobStatus.WAITING_PROVIDER_RESPONSE
    return value


def _apply_cancel_defaults(*, job: Job, target: str, actor: str | None) -> list[str]:
    if target != Job.JobStatus.CANCELLED:
        return []

    updated_fields = []
    actor_value = str(actor or "").strip().lower()
    actor_to_cancelled_by = {
        "client": Job.CancellationActor.CLIENT,
        "provider": Job.CancellationActor.PROVIDER,
    }
    actor_to_cancel_reason = {
        "client": Job.CancelReason.CLIENT_CANCELLED,
        "provider": Job.CancelReason.PROVIDER_REJECTED,
    }

    if not getattr(job, "cancelled_by", None):
        job.cancelled_by = actor_to_cancelled_by.get(actor_value, Job.CancellationActor.SYSTEM)
        updated_fields.append("cancelled_by")

    if not getattr(job, "cancel_reason", None):
        job.cancel_reason = actor_to_cancel_reason.get(actor_value, Job.CancelReason.SYSTEM)
        updated_fields.append("cancel_reason")

    return updated_fields


def transition_job_status(
    job: Job,
    to_status: str,
    *,
    actor: str | None = None,
    reason: str = "",
    allow_legacy: bool = True,
) -> TransitionMeta:
    _ = actor
    _ = reason

    previous = str(job.job_status or "").strip()
    normalized_previous = normalize_job_status(previous)
    target = str(to_status or "").strip()

    if target == previous:
        return TransitionMeta(previous, normalized_previous, target, "no-op")

    if previous == Job.JobStatus.POSTED and target == Job.JobStatus.WAITING_PROVIDER_RESPONSE:
        job.job_status = target
        job.save(update_fields=["job_status", "updated_at"])
        log_job_transition(
            getattr(job, "job_id", getattr(job, "pk", "unknown")),
            previous,
            target,
            source="transition_job_status",
        )
        return TransitionMeta(previous, normalized_previous, target, "legacy-normalization")

    if target in _CANONICAL_JOB_TRANSITIONS.get(normalized_previous, set()):
        classification = "canonical"
    elif allow_legacy and target in _LEGACY_JOB_TRANSITIONS.get(normalized_previous, set()):
        classification = "legacy"
    else:
        raise InvalidStateTransition(
            f"Invalid Job transition: {normalized_previous} -> {target}"
        )

    job.job_status = target
    update_fields = ["job_status", "updated_at"]
    update_fields.extend(_apply_cancel_defaults(job=job, target=target, actor=actor))
    if target == Job.JobStatus.CANCELLED:
        update_fields.extend(["cancelled_by", "cancel_reason"])
    # Preserve deterministic field ordering for save/update_fields.
    update_fields = list(dict.fromkeys(update_fields))
    job.save(update_fields=update_fields)
    log_job_transition(
        getattr(job, "job_id", getattr(job, "pk", "unknown")),
        previous,
        target,
        source="transition_job_status",
    )
    return TransitionMeta(previous, normalized_previous, target, classification)


def transition_assignment_status(
    assignment,
    to_status: str,
    *,
    actor: str | None = None,
    reason: str = "",
) -> TransitionMeta:
    _ = actor
    _ = reason

    previous = str(getattr(assignment, "assignment_status", "") or "").strip()
    target = str(to_status or "").strip()

    if target == previous:
        return TransitionMeta(previous, previous, target, "no-op")

    allowed_targets = _CANONICAL_ASSIGNMENT_TRANSITIONS.get(previous)
    if allowed_targets is None or target not in allowed_targets:
        raise InvalidStateTransition(
            f"Invalid JobAssignment transition: {previous} -> {target}"
        )

    assignment.assignment_status = target
    update_fields = ["assignment_status", "updated_at"]
    if target == "cancelled" and getattr(assignment, "is_active", False):
        assignment.is_active = False
        update_fields.append("is_active")
    assignment.save(update_fields=update_fields)
    return TransitionMeta(previous, previous, target, "canonical")


def reactivate_assignment_legacy(
    assignment,
    *,
    actor: str | None = None,
    reason: str = "legacy reactivation",
) -> TransitionMeta:
    _ = actor
    _ = reason

    previous = str(getattr(assignment, "assignment_status", "") or "").strip()
    target = "assigned"

    # Legacy reactivation path used while historical data still contains
    # cancelled/inactive assignment rows that must be reused.
    if previous not in {"cancelled", "accepted", "assigned"}:
        raise InvalidStateTransition(
            f"Legacy assignment reactivation not allowed: {previous} -> {target}"
        )

    update_fields = ["updated_at"]
    if previous != target:
        assignment.assignment_status = target
        update_fields.append("assignment_status")
    if not getattr(assignment, "is_active", False):
        assignment.is_active = True
        update_fields.append("is_active")

    assignment.save(update_fields=update_fields)
    return TransitionMeta(previous, previous, target, "legacy-reactivation")
