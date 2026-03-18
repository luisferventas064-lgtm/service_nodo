import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from jobs.events import get_visible_job_status_label
from clients.models import Client
from jobs.models import Job, JobBroadcastAttempt, JobEvent, JobProviderExclusion
from notifications.models import PushDevice, PushDispatchAttempt
from providers.models import Provider, ProviderService, ProviderServiceArea, ProviderUser
from service_type.models import ServiceType


class EnglishLocaleTestMixin:
    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_ACCEPT_LANGUAGE"] = "en"


@override_settings(PUSH_PROVIDER="stub")
class TickOnDemandCommandTest(EnglishLocaleTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.service_type = ServiceType.objects.create(
            name="Tick Command Test",
            description="Tick command test service type",
        )

    def _make_provider(self, n: int) -> Provider:
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name=f"Provider{n}",
            contact_last_name="Test",
            phone_number=f"555-000-000{n}",
            email=f"provider{n}.tick@test.local",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1=f"{n} Provider St",
            availability_mode="manual",
            is_available_now=True,
            accepts_urgent=True,
            accepts_scheduled=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=self.service_type,
            custom_name="On Demand Tick Service",
            description="",
            billing_unit="fixed",
            price_cents=5000,
            is_active=True,
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            is_active=True,
        )
        return provider

    def _make_due_job(self) -> Job:
        return Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            scheduled_date=None,
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
            next_alert_at=timezone.now() - timedelta(minutes=1),
            on_demand_tick_scheduled_at=None,
            on_demand_tick_dispatched_at=None,
        )

    def _make_client(self, suffix: str) -> Client:
        client_number = Client.objects.count() + 1
        return Client.objects.create(
            first_name="Tick",
            last_name="Client",
            phone_number=f"555300{client_number:04d}",
            email=f"client.{suffix}@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="321 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )

    def _make_waiting_job(
        self,
        *,
        provider: Provider,
        client: Client,
        created_at,
        job_mode=Job.JobMode.ON_DEMAND,
    ) -> Job:
        job = Job.objects.create(
            selected_provider=provider,
            client=client,
            job_mode=job_mode,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            is_asap=job_mode == Job.JobMode.ON_DEMAND,
            scheduled_date=(
                None if job_mode == Job.JobMode.ON_DEMAND else timezone.localdate() + timedelta(days=2)
            ),
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Waiting St",
        )
        Job.objects.filter(pk=job.pk).update(created_at=created_at)
        job.refresh_from_db()
        return job

    @patch("jobs.services.default_schedule_fn", autospec=True)
    def test_tick_on_demand_command_is_idempotent_for_broadcast_attempts(self, _schedule_mock):
        p1 = self._make_provider(1)
        p2 = self._make_provider(2)
        job = self._make_due_job()

        self.assertEqual(JobBroadcastAttempt.objects.count(), 0)

        out1 = io.StringIO()
        call_command("tick_on_demand", stdout=out1)

        attempts_after_first = JobBroadcastAttempt.objects.filter(job=job).count()
        self.assertEqual(attempts_after_first, 2)

        statuses_first = list(
            JobBroadcastAttempt.objects.filter(job=job)
            .order_by("provider_id")
            .values_list("provider_id", "status")
        )
        self.assertEqual(statuses_first, [(p1.provider_id, "sent"), (p2.provider_id, "sent")])

        out2 = io.StringIO()
        call_command("tick_on_demand", stdout=out2)

        attempts_after_second = JobBroadcastAttempt.objects.filter(job=job).count()
        self.assertEqual(attempts_after_second, 2)

        job.refresh_from_db()
        self.assertIsNotNone(job.on_demand_tick_dispatched_at)

    @patch("jobs.services.default_schedule_fn", autospec=True)
    def test_second_run_does_not_create_more_attempts_even_after_retry_window(self, _schedule_mock):
        self._make_provider(1)
        self._make_provider(2)
        job = self._make_due_job()

        call_command("tick_on_demand")
        self.assertEqual(JobBroadcastAttempt.objects.filter(job=job).count(), 2)

        job.refresh_from_db()
        job.on_demand_tick_scheduled_at = timezone.now() - timedelta(minutes=10)
        job.on_demand_tick_dispatched_at = None
        job.next_alert_at = timezone.now() - timedelta(minutes=1)
        job.save(
            update_fields=[
                "on_demand_tick_scheduled_at",
                "on_demand_tick_dispatched_at",
                "next_alert_at",
            ]
        )

        out2 = io.StringIO()
        call_command("tick_on_demand", stdout=out2)
        output = out2.getvalue().lower()
        self.assertIn("sent=0", output)
        self.assertIn("skipped=0", output)
        self.assertIn("candidates=0", output)
        self.assertEqual(JobBroadcastAttempt.objects.filter(job=job).count(), 2)

    @patch("jobs.services.default_schedule_fn", autospec=True)
    def test_tick_on_demand_skips_provider_who_already_declined_same_job(self, _schedule_mock):
        declined_provider = self._make_provider(1)
        eligible_provider = self._make_provider(2)
        job = self._make_due_job()
        JobProviderExclusion.objects.create(
            job=job,
            provider=declined_provider,
            reason=JobProviderExclusion.Reason.DECLINED,
        )

        out = io.StringIO()
        call_command("tick_on_demand", stdout=out)

        self.assertEqual(
            list(
                JobBroadcastAttempt.objects.filter(job=job).values_list(
                    "provider_id",
                    flat=True,
                )
            ),
            [eligible_provider.provider_id],
        )
        self.assertIn("sent=1", out.getvalue())

    def test_tick_on_demand_expires_stale_waiting_jobs_and_dispatches_stub_push(self):
        provider = self._make_provider(1)
        client = self._make_client("waiting-expired")
        job = self._make_waiting_job(
            provider=provider,
            client=client,
            created_at=timezone.now() - timedelta(minutes=6),
        )

        user_model = get_user_model()
        client_user = user_model.objects.create_user(
            username="tick_waiting_client_user",
            email=client.email,
            password="test-pass-123",
        )
        provider_user = user_model.objects.create_user(
            username="tick_waiting_provider_user",
            email=provider.email,
            password="test-pass-123",
        )
        ProviderUser.objects.create(
            provider=provider,
            user=provider_user,
            role="owner",
            is_active=True,
        )
        client_device = PushDevice.objects.create(
            user=client_user,
            role=PushDevice.Role.CLIENT,
            platform=PushDevice.Platform.ANDROID,
            token="tick-waiting-client-token",
        )
        provider_device = PushDevice.objects.create(
            user=provider_user,
            role=PushDevice.Role.PROVIDER,
            platform=PushDevice.Platform.IOS,
            token="tick-waiting-provider-token",
        )

        out = io.StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command("tick_on_demand", stdout=out)

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.EXPIRED)
        self.assertEqual(job.cancelled_by, Job.CancellationActor.SYSTEM)
        self.assertEqual(job.cancel_reason, Job.CancelReason.AUTO_TIMEOUT)

        event = job.events.get(event_type=JobEvent.EventType.JOB_EXPIRED)
        self.assertEqual(event.actor_role, JobEvent.ActorRole.SYSTEM)
        self.assertEqual(event.visible_status, get_visible_job_status_label(Job.JobStatus.EXPIRED))
        self.assertEqual(event.payload_json, {"reason": "timeout"})

        attempts = list(
            PushDispatchAttempt.objects.filter(job_event=event)
            .select_related("device")
            .order_by("device__role", "device__token")
        )
        self.assertEqual(len(attempts), 2)
        self.assertEqual(
            {(attempt.device.role, attempt.device.token) for attempt in attempts},
            {
                (PushDevice.Role.CLIENT, client_device.token),
                (PushDevice.Role.PROVIDER, provider_device.token),
            },
        )
        self.assertTrue(
            all(attempt.status == PushDispatchAttempt.Status.STUB_SENT for attempt in attempts)
        )
        self.assertIn("EXPIRED WAITING JOBS: 1", out.getvalue())

    def test_tick_on_demand_does_not_expire_scheduled_waiting_jobs(self):
        provider = self._make_provider(1)
        client = self._make_client("waiting-scheduled")
        job = self._make_waiting_job(
            provider=provider,
            client=client,
            created_at=timezone.now() - timedelta(minutes=6),
            job_mode=Job.JobMode.SCHEDULED,
        )

        call_command("tick_on_demand", stdout=io.StringIO())

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        self.assertFalse(job.events.filter(event_type=JobEvent.EventType.JOB_EXPIRED).exists())
