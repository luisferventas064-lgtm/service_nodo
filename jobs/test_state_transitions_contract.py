from django.test import SimpleTestCase

from jobs.models import Job
from jobs.services_state_transitions import (
    InvalidStateTransition,
    normalize_job_status,
    reactivate_assignment_legacy,
    transition_assignment_status,
    transition_job_status,
)


class _DummyPersisted:
    def __init__(self):
        self.saved_update_fields = None

    def save(self, update_fields):
        self.saved_update_fields = list(update_fields)


class _DummyJob(_DummyPersisted):
    def __init__(self, status):
        super().__init__()
        self.job_status = status


class _DummyAssignment(_DummyPersisted):
    def __init__(self, status, is_active=False):
        super().__init__()
        self.assignment_status = status
        self.is_active = is_active


class StateTransitionsContractTests(SimpleTestCase):
    def test_job_transition_invalid_raises(self):
        job = _DummyJob(Job.JobStatus.WAITING_PROVIDER_RESPONSE)

        with self.assertRaises(InvalidStateTransition):
            transition_job_status(job, Job.JobStatus.IN_PROGRESS)

    def test_assignment_transition_invalid_raises(self):
        assignment = _DummyAssignment("completed", is_active=False)

        with self.assertRaises(InvalidStateTransition):
            transition_assignment_status(assignment, "in_progress")

    def test_posted_normalizes_to_waiting_provider_response(self):
        self.assertEqual(
            normalize_job_status(Job.JobStatus.POSTED),
            Job.JobStatus.WAITING_PROVIDER_RESPONSE,
        )

    def test_legacy_posted_to_waiting_is_normalization_classification(self):
        job = _DummyJob(Job.JobStatus.POSTED)

        meta = transition_job_status(job, Job.JobStatus.WAITING_PROVIDER_RESPONSE)

        self.assertEqual(meta.classification, "legacy-normalization")
        self.assertEqual(job.job_status, Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        self.assertEqual(job.saved_update_fields, ["job_status", "updated_at"])

    def test_reactivate_assignment_legacy_from_cancelled(self):
        assignment = _DummyAssignment("cancelled", is_active=False)

        meta = reactivate_assignment_legacy(assignment)

        self.assertEqual(meta.classification, "legacy-reactivation")
        self.assertEqual(assignment.assignment_status, "assigned")
        self.assertTrue(assignment.is_active)
        self.assertIn("assignment_status", assignment.saved_update_fields)
        self.assertIn("is_active", assignment.saved_update_fields)

    def test_reactivate_assignment_legacy_rejects_completed(self):
        assignment = _DummyAssignment("completed", is_active=False)

        with self.assertRaises(InvalidStateTransition):
            reactivate_assignment_legacy(assignment)
