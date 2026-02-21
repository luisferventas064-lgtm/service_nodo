import threading
from datetime import timedelta

from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone

from jobs.models import Job
from jobs.services import process_on_demand_job
from service_type.models import ServiceType


class OnDemandIdempotencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.calls = []
        self.service_type = ServiceType.objects.create(
            name="Idempotency Test",
            description="On-demand idempotency test service type",
        )

    def fake_schedule(self, job_id, run_at):
        self.calls.append((job_id, run_at))

    def _create_on_demand_posted_job(self) -> Job:
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

    def test_process_is_idempotent_sequential(self):
        job = self._create_on_demand_posted_job()

        r1 = process_on_demand_job(job.job_id, schedule_fn=self.fake_schedule)
        r2 = process_on_demand_job(job.job_id, schedule_fn=self.fake_schedule)

        self.assertTrue(r1.scheduled)
        self.assertFalse(r2.scheduled)
        self.assertEqual(len(self.calls), 1)

        job.refresh_from_db()
        self.assertIsNotNone(job.on_demand_tick_scheduled_at)

    def test_process_is_idempotent_concurrent_two_threads(self):
        job = self._create_on_demand_posted_job()

        start = threading.Event()
        done = threading.Event()
        results = []

        def worker():
            close_old_connections()
            start.wait()
            res = process_on_demand_job(job.job_id, schedule_fn=self.fake_schedule)
            results.append(res)
            if len(results) == 2:
                done.set()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        start.set()

        done.wait(timeout=10)
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(self.calls), 1)
        scheduled_count = sum(1 for r in results if r.scheduled)
        self.assertEqual(scheduled_count, 1)

        job.refresh_from_db()
        self.assertIsNotNone(job.on_demand_tick_scheduled_at)
        self.assertIsNotNone(job.next_alert_at)
        self.assertGreater(job.next_alert_at, timezone.now() - timedelta(minutes=5))
