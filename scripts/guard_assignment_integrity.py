from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from assignments.models import JobAssignment  # noqa: E402
from jobs.models import Job  # noqa: E402


CANONICAL_NO_ACTIVE_ASSIGNMENT_JOB_STATUSES = {
    Job.JobStatus.DRAFT,
    Job.JobStatus.POSTED,
    Job.JobStatus.SCHEDULED_PENDING_ACTIVATION,
    Job.JobStatus.WAITING_PROVIDER_RESPONSE,
    Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
    Job.JobStatus.PENDING_CLIENT_DECISION,
    Job.JobStatus.HOLD,
    Job.JobStatus.PENDING_PROVIDER_CONFIRMATION,
    Job.JobStatus.EXPIRED,
    Job.JobStatus.CANCELLED,
}

CANONICAL_ACTIVE_ASSIGNMENT_JOB_STATUSES = {
    Job.JobStatus.ASSIGNED,
    Job.JobStatus.IN_PROGRESS,
    Job.JobStatus.COMPLETED,
    Job.JobStatus.CONFIRMED,
}


def _line(code: str, message: str) -> str:
    return f"[{code}] {message}"


def main() -> int:
    problems: list[str] = []

    jobs = Job.objects.all().only(
        "job_id",
        "job_status",
        "selected_provider_id",
    )

    for job in jobs.iterator():
        active_assignments = list(
            JobAssignment.objects.filter(job_id=job.job_id, is_active=True).only(
                "assignment_id",
                "provider_id",
                "assignment_status",
                "is_active",
            )
        )

        if len(active_assignments) > 1:
            problems.append(
                _line(
                    "MULTIPLE_ACTIVE_ASSIGNMENTS",
                    f"job={job.job_id} has {len(active_assignments)} active assignments",
                )
            )

        active_assignment = active_assignments[0] if active_assignments else None

        if job.job_status in CANONICAL_NO_ACTIVE_ASSIGNMENT_JOB_STATUSES and active_assignment:
            problems.append(
                _line(
                    "UNEXPECTED_ACTIVE_ASSIGNMENT_FOR_JOB_STATUS",
                    (
                        f"job={job.job_id} status={job.job_status} has active assignment="
                        f"{active_assignment.assignment_id}"
                    ),
                )
            )

        if job.job_status in CANONICAL_ACTIVE_ASSIGNMENT_JOB_STATUSES and not active_assignment:
            problems.append(
                _line(
                    "MISSING_ACTIVE_ASSIGNMENT_FOR_JOB_STATUS",
                    f"job={job.job_id} status={job.job_status} has no active assignment",
                )
            )

        if active_assignment and job.selected_provider_id is None:
            problems.append(
                _line(
                    "ACTIVE_ASSIGNMENT_WITHOUT_SELECTED_PROVIDER",
                    (
                        f"job={job.job_id} active_assignment={active_assignment.assignment_id} "
                        "exists but selected_provider_id is null"
                    ),
                )
            )

        if (
            active_assignment
            and job.selected_provider_id is not None
            and active_assignment.provider_id != job.selected_provider_id
        ):
            problems.append(
                _line(
                    "ACTIVE_ASSIGNMENT_PROVIDER_MISMATCH",
                    (
                        f"job={job.job_id} selected_provider_id={job.selected_provider_id} "
                        f"active_assignment.provider_id={active_assignment.provider_id}"
                    ),
                )
            )

        if active_assignment and job.job_status == Job.JobStatus.PENDING_CLIENT_CONFIRMATION:
            problems.append(
                _line(
                    "ACTIVE_ASSIGNMENT_IN_PENDING_CLIENT_CONFIRMATION",
                    (
                        f"job={job.job_id} has active assignment={active_assignment.assignment_id} "
                        "while waiting for client confirmation"
                    ),
                )
            )

        if active_assignment and active_assignment.assignment_status == "cancelled":
            problems.append(
                _line(
                    "CANCELLED_ASSIGNMENT_MARKED_ACTIVE",
                    (
                        f"job={job.job_id} assignment={active_assignment.assignment_id} "
                        "is cancelled but still active"
                    ),
                )
            )

        if (
            active_assignment
            and job.job_status == Job.JobStatus.ASSIGNED
            and active_assignment.assignment_status != "assigned"
        ):
            problems.append(
                _line(
                    "ASSIGNMENT_STATUS_MISMATCH_ASSIGNED",
                    (
                        f"job={job.job_id} status=assigned but "
                        f"assignment={active_assignment.assignment_id} status={active_assignment.assignment_status}"
                    ),
                )
            )

        if (
            active_assignment
            and job.job_status == Job.JobStatus.IN_PROGRESS
            and active_assignment.assignment_status != "in_progress"
        ):
            problems.append(
                _line(
                    "ASSIGNMENT_STATUS_MISMATCH_IN_PROGRESS",
                    (
                        f"job={job.job_id} status=in_progress but "
                        f"assignment={active_assignment.assignment_id} status={active_assignment.assignment_status}"
                    ),
                )
            )

        if (
            active_assignment
            and job.job_status == Job.JobStatus.COMPLETED
            and active_assignment.assignment_status != "completed"
        ):
            problems.append(
                _line(
                    "ASSIGNMENT_STATUS_MISMATCH_COMPLETED",
                    (
                        f"job={job.job_id} status=completed but "
                        f"assignment={active_assignment.assignment_id} status={active_assignment.assignment_status}"
                    ),
                )
            )

        if (
            active_assignment
            and job.job_status == Job.JobStatus.CONFIRMED
            and active_assignment.assignment_status != "completed"
        ):
            problems.append(
                _line(
                    "ASSIGNMENT_STATUS_MISMATCH_CONFIRMED",
                    (
                        f"job={job.job_id} status=confirmed but "
                        f"assignment={active_assignment.assignment_id} status={active_assignment.assignment_status}"
                    ),
                )
            )

    if problems:
        print("ASSIGNMENT INTEGRITY GUARD FAILED")
        for item in problems:
            print(item)
        return 1

    print("ASSIGNMENT INTEGRITY GUARD PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())