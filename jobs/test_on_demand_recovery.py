from datetime import timedelta

from django.test import TransactionTestCase
from django.utils import timezone

from jobs.models import Job
from jobs.services import process_on_demand_job
from service_type.models import ServiceType


class OnDemandRecoveryTests(TransactionTestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Recovery Test",
            description="On-demand recovery test service type",
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

    def test_scheduler_failure_allows_retry_after_window(self):
        j = self._create_job()

        def failing_schedule(job_id, run_at):
            raise RuntimeError("boom")

        r1 = process_on_demand_job(j.job_id, schedule_fn=failing_schedule)
        self.assertFalse(r1.scheduled)
        self.assertEqual(r1.reason, "schedule_fn_failed")

        j.refresh_from_db()
        self.assertIsNotNone(j.on_demand_tick_scheduled_at)
        self.assertIsNone(j.on_demand_tick_dispatched_at)

        Job.objects.filter(job_id=j.job_id).update(
            on_demand_tick_scheduled_at=timezone.now() - timedelta(minutes=10)
        )

        calls = []

        def ok_schedule(job_id, run_at):
            calls.append(job_id)

        r2 = process_on_demand_job(j.job_id, schedule_fn=ok_schedule)
        self.assertTrue(r2.scheduled)
        self.assertEqual(r2.reason, "dispatched_once")
        self.assertEqual(len(calls), 1)

        j.refresh_from_db()
        self.assertIsNotNone(j.on_demand_tick_dispatched_at)
