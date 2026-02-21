from django.test import TransactionTestCase

from jobs.models import Job
from jobs.services import process_on_demand_job
from service_type.models import ServiceType


class TickAuditTests(TransactionTestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Tick Audit Test",
            description="Tick audit service type",
        )

    def _create_job(self):
        return Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            scheduled_date=None,
            is_asap=True,
            job_status=Job.JobStatus.POSTED,
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
        )

    def test_tick_audit_increments_and_sets_reason(self):
        j = self._create_job()

        def ok_schedule(job_id, run_at):
            return None

        r = process_on_demand_job(j.job_id, schedule_fn=ok_schedule)
        self.assertTrue(r.scheduled)

        j.refresh_from_db()
        self.assertGreaterEqual(j.tick_attempts, 1)
        self.assertIsNotNone(j.last_tick_attempt_at)
        self.assertEqual(j.last_tick_attempt_reason, r.reason)
