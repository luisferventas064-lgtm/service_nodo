from datetime import time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from jobs.models import BroadcastAttemptStatus, Job, JobBroadcastAttempt, JobEvent
from providers.models import Provider, ProviderService, ProviderServiceArea
from service_type.models import ServiceType


class MarketplaceProviderAcceptCanonicalFlowTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Marketplace Canonical Accept",
            description="Marketplace Canonical Accept",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Canonical",
            contact_last_name="Provider",
            phone_number="5551234100",
            email="provider.canonical.accept@test.local",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="100 Provider St",
        )
        ProviderService.objects.create(
            provider=self.provider,
            service_type=self.service_type,
            custom_name="Canonical Service",
            description="",
            billing_unit="fixed",
            price_cents=12000,
            is_active=True,
        )
        ProviderServiceArea.objects.create(
            provider=self.provider,
            city="Laval",
            province="QC",
            is_active=True,
        )

    def _login_provider(self):
        session = self.client.session
        session["provider_id"] = self.provider.pk
        session.save()

    def test_provider_accept_scheduled_job_goes_to_pending_client_confirmation(self):
        job = Job.objects.create(
            selected_provider=self.provider,
            service_type=self.service_type,
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            scheduled_date=timezone.localdate() + timedelta(days=3),
            scheduled_start_time=time(hour=12, minute=0),
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
            quoted_base_price="120.00",
            quoted_base_price_cents=12000,
            quoted_currency_code="CAD",
            quoted_currency="CAD",
            quoted_pricing_source="CanonicalTest",
            quoted_total_price_cents=12000,
            marketplace_search_started_at=timezone.now() - timedelta(hours=1),
            next_marketplace_alert_at=timezone.now() + timedelta(hours=2),
        )
        attempt = JobBroadcastAttempt.objects.create(
            job=job,
            provider_id=self.provider.provider_id,
            status=BroadcastAttemptStatus.SENT,
            detail="canonical-test",
        )

        self._login_provider()

        response = self.client.post(
            reverse("ui:provider_accept_job", args=[job.job_id]),
            follow=True,
        )

        job.refresh_from_db()
        attempt.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Request accepted.")
        self.assertEqual(job.job_status, Job.JobStatus.PENDING_CLIENT_CONFIRMATION)
        self.assertEqual(job.selected_provider_id, self.provider.provider_id)
        self.assertIsNotNone(job.client_confirmation_started_at)
        self.assertFalse(job.assignments.filter(is_active=True).exists())
        self.assertEqual(attempt.status, BroadcastAttemptStatus.ACCEPTED)

        event = job.events.get(event_type=JobEvent.EventType.JOB_ACCEPTED)
        self.assertEqual(event.actor_role, JobEvent.ActorRole.PROVIDER)
        self.assertEqual(event.payload_json.get("source"), "accept_marketplace_offer")

    def test_legacy_assign_provider_endpoint_is_disabled(self):
        job = Job.objects.create(
            service_type=self.service_type,
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            scheduled_date=timezone.localdate() + timedelta(days=2),
            scheduled_start_time=time(hour=10, minute=0),
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="501 Legacy St",
        )

        response = self.client.get(reverse("assign_provider", args=[job.job_id, self.provider.provider_id]))

        job.refresh_from_db()
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body.get("ok", True))
        self.assertEqual(body.get("error"), "legacy_assign_provider_endpoint_disabled")
        self.assertEqual(job.job_status, Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        self.assertIsNone(job.selected_provider_id)
