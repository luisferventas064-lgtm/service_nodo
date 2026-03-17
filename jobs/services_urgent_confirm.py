from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from jobs.models import Job
from jobs.services_state_transitions import transition_job_status


class ConfirmConflict(Exception):
    pass


@dataclass(frozen=True)
class ConfirmResult:
    job_id: int
    provider_id: int
    job_status: str
    urgent_total: Decimal
    urgent_fee: Decimal


def confirm_urgent_job(*, job_id: int, provider_id: int) -> ConfirmResult:
    """
    Concurrency-safe urgent confirm:
    - locks the Job row
    - validates an active hold owned by the provider
    - requires frozen urgent pricing
    - assigns the winning provider
    - transitions to pending_client_confirmation
    - clears the hold
    """

    now = timezone.now()

    with transaction.atomic():
        job = Job.objects.select_for_update().get(job_id=job_id)

        if not job.hold_provider_id or not job.hold_expires_at:
            raise ConfirmConflict("No active HOLD exists for this job.")

        if job.hold_expires_at <= now:
            raise ConfirmConflict(f"HOLD expired at {job.hold_expires_at}.")

        if job.hold_provider_id != provider_id:
            raise ConfirmConflict(
                f"Job is on HOLD for provider_id={job.hold_provider_id}, not provider_id={provider_id}."
            )

        if job.quoted_urgent_total_price is None or job.quoted_urgent_fee_amount is None:
            raise ConfirmConflict("Urgent price is not frozen on the job.")

        allowed_statuses = {
            "posted",
            "hold",
            "pending_provider_confirmation",
            "pending_client_confirmation",
        }
        if job.job_status not in allowed_statuses:
            raise ConfirmConflict(f"Invalid status for urgent confirmation: {job.job_status}")

        if job.job_status == "pending_client_confirmation":
            if job.selected_provider_id and job.selected_provider_id != provider_id:
                raise ConfirmConflict(
                    f"selected_provider_id={job.selected_provider_id} does not match provider_id={provider_id}."
                )
            return ConfirmResult(
                job_id=job.job_id,
                provider_id=provider_id,
                job_status=job.job_status,
                urgent_total=job.quoted_urgent_total_price,
                urgent_fee=job.quoted_urgent_fee_amount,
            )

        job.selected_provider_id = provider_id
        transition_job_status(
            job,
            Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            actor="provider",
            reason="confirm_urgent_job",
            allow_legacy=True,
        )
        job.hold_provider = None
        job.hold_expires_at = None
        job.save(
            update_fields=[
                "selected_provider",
                "hold_provider",
                "hold_expires_at",
                "updated_at",
            ]
        )

        return ConfirmResult(
            job_id=job.job_id,
            provider_id=provider_id,
            job_status=job.job_status,
            urgent_total=job.quoted_urgent_total_price,
            urgent_fee=job.quoted_urgent_fee_amount,
        )
