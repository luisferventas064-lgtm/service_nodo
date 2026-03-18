from datetime import timedelta

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone

from clients.models import Client
from jobs.models import Job
from jobs.services import (
    accept_provider_offer,
    get_broadcast_candidates_for_job,
    process_marketplace_job,
)
from providers.models import Provider, ProviderService, ProviderServiceArea
from service_type.models import ServiceType
from ui.views_provider import handle_provider_decline_action


class LifecycleRegressionGuardsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.service_type = ServiceType.objects.create(
            name="Lifecycle Guard Service",
            description="Lifecycle Guard Service",
        )

    def _make_provider(
        self,
        n: int,
        *,
        accepts_urgent: bool = True,
        accepts_scheduled: bool = True,
    ) -> Provider:
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name=f"Provider{n}",
            contact_last_name="Guard",
            phone_number=f"555-990-00{n:02d}",
            email=f"provider{n}.lifecycle.guard@test.local",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1=f"{n} Provider St",
            availability_mode="manual",
            is_available_now=True,
            accepts_urgent=accepts_urgent,
            accepts_scheduled=accepts_scheduled,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=self.service_type,
            custom_name="Lifecycle Guard Offer",
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

    def _make_client(self, n: int) -> Client:
        return Client.objects.create(
            first_name=f"Client{n}",
            last_name="Guard",
            phone_number=f"555-991-00{n:02d}",
            email=f"client{n}.lifecycle.guard@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1=f"{n} Client St",
            is_phone_verified=True,
            profile_completed=True,
        )

    def _make_scheduled_posted_job(self, *, client: Client) -> Job:
        return Job.objects.create(
            client=client,
            service_type=self.service_type,
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.POSTED,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=3),
            scheduled_start_time="12:00",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
        )

    def _make_on_demand_waiting_job(self, *, client: Client, provider: Provider) -> Job:
        return Job.objects.create(
            client=client,
            selected_provider=provider,
            service_type=self.service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="125 Main St",
            quoted_base_price="100.00",
            quoted_base_price_cents=10000,
            quoted_currency_code="CAD",
            quoted_currency="CAD",
            quoted_pricing_source="LifecycleGuard",
            quoted_total_price_cents=10000,
        )

    def _request_with_session_and_messages(self):
        request = self.factory.post("/provider/jobs/decline/")
        session_middleware = SessionMiddleware(lambda req: HttpResponse())
        session_middleware.process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)  # type: ignore[attr-defined]
        return request

    def test_marketplace_tick_is_idempotent_without_external_changes(self):
        provider = self._make_provider(1)
        client = self._make_client(1)
        job = self._make_scheduled_posted_job(client=client)

        first = process_marketplace_job(job.job_id)
        self.assertEqual(first[0], "dispatched_wave")
        self.assertGreaterEqual(first[1], 1)

        job.refresh_from_db()
        attempts_after_first = job.marketplace_attempts
        broadcasts_after_first = job.broadcast_attempts.count()

        second = process_marketplace_job(job.job_id)
        third = process_marketplace_job(job.job_id)

        job.refresh_from_db()

        self.assertEqual(second[0], "not_due")
        self.assertEqual(third[0], "not_due")
        self.assertEqual(job.marketplace_attempts, attempts_after_first)
        self.assertEqual(job.broadcast_attempts.count(), broadcasts_after_first)
        self.assertEqual(job.job_status, Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        self.assertIn(provider.provider_id, get_broadcast_candidates_for_job(job, limit=10))

    def test_posted_assigned_decline_reposts_and_reappears_for_other_candidates(self):
        provider_a = self._make_provider(2)
        provider_b = self._make_provider(3)
        client = self._make_client(2)

        job = self._make_on_demand_waiting_job(client=client, provider=provider_a)

        accept_result = accept_provider_offer(job_id=job.job_id, provider_id=provider_a.provider_id)
        self.assertEqual(accept_result, "accepted_assigned")

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.ASSIGNED)

        # Recreate the incoming-decline contract through the real decline handler.
        job.job_status = Job.JobStatus.WAITING_PROVIDER_RESPONSE
        job.selected_provider = provider_a
        job.save(update_fields=["job_status", "selected_provider", "updated_at"])

        request = self._request_with_session_and_messages()
        response = handle_provider_decline_action(
            request=request,
            job=job,
            provider=provider_a,
            redirect_name="ui:provider_jobs",
        )
        self.assertEqual(response.status_code, 302)

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.POSTED)
        self.assertIsNone(job.selected_provider_id)

        candidates = get_broadcast_candidates_for_job(job, limit=10)
        self.assertNotIn(provider_a.provider_id, candidates)
        self.assertIn(provider_b.provider_id, candidates)

    def test_type_isolation_hard_filter_urgent_vs_scheduled(self):
        urgent_only_provider = self._make_provider(
            4,
            accepts_urgent=True,
            accepts_scheduled=False,
        )
        client = self._make_client(3)

        scheduled_job = self._make_scheduled_posted_job(client=client)
        urgent_job = Job.objects.create(
            client=client,
            service_type=self.service_type,
            job_mode=Job.JobMode.ON_DEMAND,
            job_status=Job.JobStatus.POSTED,
            is_asap=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="126 Main St",
        )

        scheduled_candidates = get_broadcast_candidates_for_job(scheduled_job, limit=10)
        urgent_candidates = get_broadcast_candidates_for_job(urgent_job, limit=10)

        self.assertNotIn(urgent_only_provider.provider_id, scheduled_candidates)
        self.assertIn(urgent_only_provider.provider_id, urgent_candidates)
