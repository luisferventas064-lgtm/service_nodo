from datetime import time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from assignments.models import JobAssignment
from clients.models import Client
from jobs.models import Job
from providers.models import Provider
from service_type.models import ServiceType


class MarketplaceClientConfirmationRuntimeTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Marketplace Runtime Confirmation",
            description="Marketplace Runtime Confirmation",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Runtime",
            phone_number="5559988101",
            email="provider.runtime.confirmation@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="10 Provider St",
        )
        self.client_obj = Client.objects.create(
            first_name="Client",
            last_name="Runtime",
            phone_number="5559988102",
            email="client.runtime.confirmation@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="11 Client St",
            is_phone_verified=True,
            profile_completed=True,
        )

    def _mk_job_pending_client_confirmation(self):
        return Job.objects.create(
            selected_provider=self.provider,
            client=self.client_obj,
            service_type=self.service_type,
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            scheduled_date=timezone.localdate() + timedelta(days=3),
            scheduled_start_time=time(hour=12, minute=0),
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Runtime St",
            quoted_base_price="140.00",
            quoted_base_price_cents=14000,
            quoted_currency_code="CAD",
            quoted_currency="CAD",
            quoted_pricing_source="RuntimeTest",
            quoted_total_price_cents=14000,
            marketplace_search_started_at=timezone.now() - timedelta(hours=1),
            client_confirmation_started_at=timezone.now() - timedelta(minutes=10),
            next_marketplace_alert_at=None,
        )

    def test_request_status_confirm_provider_moves_to_assigned(self):
        job = self._mk_job_pending_client_confirmation()

        response = self.client.post(
            reverse("ui:request_status", args=[job.job_id]),
            data={"action": "confirm_provider"},
            follow=True,
        )

        job.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(job.job_status, Job.JobStatus.ASSIGNED)
        self.assertEqual(job.selected_provider_id, self.provider.provider_id)
        self.assertEqual(JobAssignment.objects.filter(job=job, is_active=True).count(), 1)

    def test_request_status_reject_provider_reopens_marketplace_waiting(self):
        job = self._mk_job_pending_client_confirmation()
        assignment = JobAssignment.objects.create(
            job=job,
            provider=self.provider,
            is_active=True,
            assignment_status="assigned",
        )

        response = self.client.post(
            reverse("ui:request_status", args=[job.job_id]),
            data={"action": "reject_provider"},
            follow=True,
        )

        job.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(job.job_status, Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        self.assertIsNone(job.selected_provider_id)
        self.assertIsNone(job.client_confirmation_started_at)
        self.assertIsNotNone(job.next_marketplace_alert_at)
        self.assertFalse(assignment.is_active)
        self.assertEqual(assignment.assignment_status, "cancelled")

    def test_request_status_cancel_request_from_pending_client_confirmation(self):
        job = self._mk_job_pending_client_confirmation()

        response = self.client.post(
            reverse("ui:request_status", args=[job.job_id]),
            data={"action": "cancel_request"},
            follow=True,
        )

        job.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(job.job_status, Job.JobStatus.CANCELLED)
        self.assertEqual(job.cancelled_by, Job.CancellationActor.CLIENT)
