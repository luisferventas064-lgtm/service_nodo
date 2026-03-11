from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from assignments.models import JobAssignment
from clients.models import Client
from jobs.dashboard import dashboard
from jobs.models import BroadcastAttemptStatus, Job, JobBroadcastAttempt, JobEvent
from jobs.services_metrics import compute_dispatch_time_seconds, matching_health
from providers.models import Provider
from service_type.models import ServiceType


class MatchingHealthMetricsTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(name="Matching KPI Service")
        self.client = Client.objects.create(
            first_name="Client",
            last_name="Metrics",
            phone_number="+15551000000",
            email="client.metrics@test.local",
            accepts_terms=True,
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )
        self.provider_one = self._create_provider(1)
        self.provider_two = self._create_provider(2)
        self.provider_three = self._create_provider(3)

    def _create_provider(self, index: int) -> Provider:
        return Provider.objects.create(
            provider_type=Provider.TYPE_SELF_EMPLOYED,
            contact_first_name=f"Provider{index}",
            contact_last_name="Metrics",
            phone_number=f"+1555200000{index}",
            email=f"provider{index}.metrics@test.local",
            province="QC",
            city="Montreal",
            postal_code=f"H{index}H{index}H{index}",
            address_line1=f"{index} Provider St",
            accepts_terms=True,
        )

    def _create_job(
        self,
        *,
        index: int,
        created_at,
        marketplace_attempts: int = 0,
        alert_attempts: int = 0,
        job_status: str = Job.JobStatus.POSTED,
    ) -> Job:
        job = Job.objects.create(
            client=self.client,
            service_type=self.service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=job_status,
            province="QC",
            city="Montreal",
            postal_code=f"H2{index}H{index}",
            address_line1=f"{index} Job St",
            marketplace_attempts=marketplace_attempts,
            alert_attempts=alert_attempts,
        )
        Job.objects.filter(pk=job.pk).update(created_at=created_at)
        job.refresh_from_db()
        return job

    def _create_assignment(self, *, job: Job, provider: Provider, assigned_at, accepted_at=None):
        assignment = JobAssignment.objects.create(
            job=job,
            provider=provider,
            assignment_status="assigned",
        )
        JobAssignment.objects.filter(pk=assignment.pk).update(
            created_at=assigned_at,
            assigned_at=assigned_at,
            accepted_at=accepted_at,
        )
        assignment.refresh_from_db()
        return assignment

    def _create_broadcast_attempt(self, *, job: Job, provider: Provider, status: str, created_at):
        attempt = JobBroadcastAttempt.objects.create(
            job=job,
            provider=provider,
            status=status,
        )
        JobBroadcastAttempt.objects.filter(pk=attempt.pk).update(created_at=created_at)
        attempt.refresh_from_db()
        return attempt

    def test_compute_matching_health_metrics(self):
        base_time = timezone.now() - timedelta(hours=2)

        job_one = self._create_job(
            index=1,
            created_at=base_time,
            marketplace_attempts=1,
        )
        job_two = self._create_job(
            index=2,
            created_at=base_time + timedelta(minutes=1),
            marketplace_attempts=2,
        )
        job_three = self._create_job(
            index=3,
            created_at=base_time + timedelta(minutes=2),
            alert_attempts=3,
        )

        self._create_assignment(
            job=job_one,
            provider=self.provider_one,
            assigned_at=job_one.created_at + timedelta(seconds=12),
            accepted_at=job_one.created_at + timedelta(seconds=12),
        )
        JobEvent.objects.create(
            job=job_one,
            event_type=JobEvent.EventType.PROVIDER_ACCEPTED,
            provider_id=self.provider_one.provider_id,
            note="provider accepted fast",
            created_at=job_one.created_at + timedelta(seconds=10),
        )

        self._create_assignment(
            job=job_two,
            provider=self.provider_two,
            assigned_at=job_two.created_at + timedelta(seconds=45),
            accepted_at=job_two.created_at + timedelta(seconds=45),
        )

        self._create_broadcast_attempt(
            job=job_one,
            provider=self.provider_one,
            status=BroadcastAttemptStatus.ACCEPTED,
            created_at=job_one.created_at + timedelta(seconds=5),
        )
        self._create_broadcast_attempt(
            job=job_one,
            provider=self.provider_two,
            status=BroadcastAttemptStatus.SENT,
            created_at=job_one.created_at + timedelta(seconds=6),
        )
        self._create_broadcast_attempt(
            job=job_two,
            provider=self.provider_two,
            status=BroadcastAttemptStatus.ACCEPTED,
            created_at=job_two.created_at + timedelta(seconds=20),
        )
        self._create_broadcast_attempt(
            job=job_two,
            provider=self.provider_three,
            status=BroadcastAttemptStatus.SENT,
            created_at=job_two.created_at + timedelta(seconds=21),
        )
        self._create_broadcast_attempt(
            job=job_three,
            provider=self.provider_one,
            status=BroadcastAttemptStatus.SENT,
            created_at=job_three.created_at + timedelta(seconds=10),
        )
        self._create_broadcast_attempt(
            job=job_three,
            provider=self.provider_two,
            status=BroadcastAttemptStatus.SENT,
            created_at=job_three.created_at + timedelta(seconds=11),
        )

        metrics = matching_health(since_hours=24)

        self.assertEqual(metrics["dispatch_time_seconds"]["n"], 2)
        self.assertAlmostEqual(metrics["dispatch_time_seconds"]["avg_seconds"], 27.5)
        self.assertAlmostEqual(metrics["dispatch_time_seconds"]["p50_seconds"], 27.5)
        self.assertAlmostEqual(metrics["dispatch_time_seconds"]["p95_seconds"], 45.0)

        self.assertEqual(metrics["acceptance_rate"]["offers_sent"], 6)
        self.assertEqual(metrics["acceptance_rate"]["offers_accepted"], 2)
        self.assertAlmostEqual(metrics["acceptance_rate"]["value"], 2 / 6)

        self.assertEqual(metrics["broadcast_attempts_per_job"]["jobs_created"], 3)
        self.assertAlmostEqual(metrics["broadcast_attempts_per_job"]["avg_waves"], 2.0)
        self.assertAlmostEqual(metrics["broadcast_attempts_per_job"]["p50_waves"], 2.0)
        self.assertAlmostEqual(metrics["broadcast_attempts_per_job"]["p95_waves"], 3.0)

        self.assertEqual(metrics["coverage_rate"]["jobs_created"], 3)
        self.assertEqual(metrics["coverage_rate"]["jobs_assigned"], 2)
        self.assertAlmostEqual(metrics["coverage_rate"]["value"], 2 / 3)

        self.assertEqual(metrics["provider_utilization"]["active_providers"], 3)
        self.assertEqual(metrics["provider_utilization"]["providers_with_jobs"], 2)
        self.assertAlmostEqual(metrics["provider_utilization"]["value"], 2 / 3)

    def test_compute_dispatch_time_prefers_provider_accept_event(self):
        created_at = timezone.now() - timedelta(hours=1)
        job = self._create_job(index=9, created_at=created_at)
        self._create_assignment(
            job=job,
            provider=self.provider_one,
            assigned_at=created_at + timedelta(seconds=30),
            accepted_at=created_at + timedelta(seconds=30),
        )
        JobEvent.objects.create(
            job=job,
            event_type=JobEvent.EventType.PROVIDER_ACCEPTED,
            provider_id=self.provider_one.provider_id,
            note="accept before assignment",
            created_at=created_at + timedelta(seconds=12),
        )

        self.assertEqual(compute_dispatch_time_seconds(job), 12.0)

    def test_dashboard_exposes_matching_health(self):
        payload = dashboard(since_hours=24)

        self.assertIn("matching_health", payload)
        self.assertEqual(payload["matching_health"]["since_hours"], 24)
