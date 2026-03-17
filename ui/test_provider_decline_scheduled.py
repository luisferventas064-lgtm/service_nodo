"""
Contract test: provider declines a scheduled_pending_activation job.

Flow:
  scheduled_pending_activation
    -> provider POSTs /provider/jobs/<id>/decline-scheduled/
  -> waiting_provider_response
  + selected_provider cleared
  + active assignment cancelled
  + JobProviderExclusion created
  + PROVIDER_DECLINED event recorded
"""
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from assignments.models import JobAssignment
from jobs.models import Job, JobEvent, JobProviderExclusion
from providers.models import Provider, ProviderService, ProviderServiceArea
from service_type.models import ServiceType


def _make_provider(tag: str) -> Provider:
    return Provider.objects.create(
        provider_type="self_employed",
        contact_first_name="Decline",
        contact_last_name=tag,
        phone_number=f"555{tag[:7].zfill(7)}",
        email=f"decline.{tag}@test.local",
        is_phone_verified=True,
        profile_completed=True,
        billing_profile_completed=True,
        accepts_terms=True,
        country="Canada",
        province="QC",
        city="Laval",
        postal_code="H7N1A1",
        address_line1="1 Decline St",
    )


def _make_job(provider: Provider, service_type: ServiceType, **kwargs) -> Job:
    defaults = dict(
        selected_provider=provider,
        service_type=service_type,
        job_mode=Job.JobMode.SCHEDULED,
        job_status=Job.JobStatus.SCHEDULED_PENDING_ACTIVATION,
        scheduled_date=date.today() + timedelta(days=5),
        country="Canada",
        province="QC",
        city="Laval",
        postal_code="H7N1A1",
        address_line1="100 Main St",
        quoted_base_price="100.00",
        quoted_base_price_cents=10000,
        quoted_currency_code="CAD",
        quoted_currency="CAD",
        quoted_pricing_source="Test",
        quoted_total_price_cents=10000,
    )
    defaults.update(kwargs)
    return Job.objects.create(**defaults)


class ProviderDeclineScheduledJobTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Scheduled Decline Service",
            description="Test",
        )
        self.provider = _make_provider("scheduled1")
        ProviderService.objects.create(
            provider=self.provider,
            service_type=self.service_type,
            custom_name="Scheduled Decline",
            description="",
            billing_unit="fixed",
            price_cents=10000,
            is_active=True,
        )
        ProviderServiceArea.objects.create(
            provider=self.provider,
            city="Laval",
            province="QC",
            is_active=True,
        )

    def _login(self, provider=None):
        p = provider or self.provider
        session = self.client.session
        session["provider_id"] = p.pk
        session.save()

    def _url(self, job):
        return reverse("ui:provider_decline_scheduled_job", args=[job.job_id])

    # ------------------------------------------------------------------ #
    # Happy path                                                            #
    # ------------------------------------------------------------------ #

    def test_decline_scheduled_transitions_job_to_waiting_and_clears_provider(self):
        job = _make_job(self.provider, self.service_type)
        self._login()

        response = self.client.post(self._url(job))

        job.refresh_from_db()
        self.assertRedirects(response, reverse("ui:provider_jobs"), fetch_redirect_response=False)
        self.assertEqual(job.job_status, Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        self.assertIsNone(job.selected_provider_id)

    def test_decline_scheduled_cancels_active_assignment(self):
        job = _make_job(self.provider, self.service_type)
        assignment = JobAssignment.objects.create(
            job=job,
            provider=self.provider,
            assignment_status="assigned",
            is_active=True,
        )
        self._login()

        self.client.post(self._url(job))

        assignment.refresh_from_db()
        self.assertEqual(assignment.assignment_status, "cancelled")
        self.assertFalse(assignment.is_active)

    def test_decline_scheduled_creates_provider_exclusion(self):
        job = _make_job(self.provider, self.service_type)
        self._login()

        self.client.post(self._url(job))

        self.assertTrue(
            JobProviderExclusion.objects.filter(
                job=job, provider=self.provider
            ).exists()
        )

    def test_decline_scheduled_records_provider_declined_event(self):
        job = _make_job(self.provider, self.service_type)
        self._login()

        self.client.post(self._url(job))

        event = job.events.filter(
            event_type=JobEvent.EventType.PROVIDER_DECLINED
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor_role, JobEvent.ActorRole.PROVIDER)
        self.assertEqual(event.payload_json.get("source"), "provider_scheduled_decline")

    def test_decline_scheduled_does_not_cancel_the_job(self):
        """The job is returned to the market — not cancelled."""
        job = _make_job(self.provider, self.service_type)
        self._login()

        self.client.post(self._url(job))

        job.refresh_from_db()
        self.assertNotEqual(job.job_status, Job.JobStatus.CANCELLED)
        self.assertIsNone(job.cancelled_by)
        self.assertIsNone(job.cancel_reason)

    # ------------------------------------------------------------------ #
    # Guard rails                                                           #
    # ------------------------------------------------------------------ #

    def test_decline_scheduled_rejects_wrong_status(self):
        """Returns 403 when job is not in scheduled_pending_activation."""
        job = _make_job(
            self.provider,
            self.service_type,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
        )
        self._login()

        response = self.client.post(self._url(job))

        self.assertEqual(response.status_code, 403)
        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.WAITING_PROVIDER_RESPONSE)

    def test_decline_scheduled_rejects_mismatched_provider(self):
        """Returns 403 when session provider is not the selected_provider."""
        other_provider = _make_provider("other999")
        job = _make_job(self.provider, self.service_type)  # selected = self.provider
        self._login(other_provider)  # logged in as different provider

        response = self.client.post(self._url(job))

        self.assertEqual(response.status_code, 403)
        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.SCHEDULED_PENDING_ACTIVATION)

    def test_decline_scheduled_redirects_without_session(self):
        """Redirects to registration when no provider session exists."""
        job = _make_job(self.provider, self.service_type)
        # no session set

        response = self.client.post(self._url(job))

        self.assertRedirects(response, reverse("provider_register"), fetch_redirect_response=False)

    def test_decline_scheduled_rejects_get_method(self):
        """GET is not allowed — redirects to provider jobs board."""
        job = _make_job(self.provider, self.service_type)
        self._login()

        response = self.client.get(self._url(job))

        self.assertRedirects(response, reverse("ui:provider_jobs"), fetch_redirect_response=False)
