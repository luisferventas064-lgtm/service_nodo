from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from jobs.models import Job
from jobs.services import process_on_demand_job, schedule_next_alert, should_broadcast
from service_type.models import ServiceType


class JobServicesTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Plumbing",
            description="General plumbing service",
        )

    def _make_job(self, *, mode=Job.JobMode.SCHEDULED, status=Job.JobStatus.DRAFT):
        return Job.objects.create(
            job_mode=mode,
            job_status=status,
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
        )

    def test_should_broadcast_true_for_on_demand_posted(self):
        job = self._make_job(mode=Job.JobMode.ON_DEMAND, status=Job.JobStatus.POSTED)

        self.assertTrue(should_broadcast(job))

    def test_should_broadcast_false_for_non_broadcastable_job(self):
        scheduled_job = self._make_job(
            mode=Job.JobMode.SCHEDULED,
            status=Job.JobStatus.POSTED,
        )
        non_posted_job = self._make_job(
            mode=Job.JobMode.ON_DEMAND,
            status=Job.JobStatus.DRAFT,
        )

        self.assertFalse(should_broadcast(scheduled_job))
        self.assertFalse(should_broadcast(non_posted_job))

    def test_schedule_next_alert_sets_next_alert_and_increments_attempts(self):
        job = self._make_job(mode=Job.JobMode.ON_DEMAND, status=Job.JobStatus.POSTED)
        before = timezone.now()

        changed = schedule_next_alert(job)

        job.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(job.alert_attempts, 1)
        self.assertIsNotNone(job.next_alert_at)
        self.assertGreater(job.next_alert_at, before)

    def test_schedule_next_alert_does_not_override_future_alert(self):
        job = self._make_job(mode=Job.JobMode.ON_DEMAND, status=Job.JobStatus.POSTED)
        future_alert = timezone.now() + timedelta(minutes=5)
        job.next_alert_at = future_alert
        job.alert_attempts = 2
        job.save(update_fields=["next_alert_at", "alert_attempts"])

        changed = schedule_next_alert(job)

        job.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(job.alert_attempts, 2)
        self.assertEqual(job.next_alert_at, future_alert)

    def test_process_on_demand_job_schedules_only_when_eligible(self):
        eligible = self._make_job(mode=Job.JobMode.ON_DEMAND, status=Job.JobStatus.POSTED)
        not_eligible = self._make_job(mode=Job.JobMode.SCHEDULED, status=Job.JobStatus.POSTED)

        process_on_demand_job(eligible)
        process_on_demand_job(not_eligible)

        eligible.refresh_from_db()
        not_eligible.refresh_from_db()

        self.assertEqual(eligible.alert_attempts, 1)
        self.assertIsNotNone(eligible.next_alert_at)

        self.assertEqual(not_eligible.alert_attempts, 0)
        self.assertIsNone(not_eligible.next_alert_at)
