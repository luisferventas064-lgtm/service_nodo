import io
from datetime import time, timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from assignments.models import AssignmentFee, JobAssignment
from clients.models import ClientTicket
from jobs.models import Job
from jobs.services import (
    MarketplaceDecisionConflict,
    confirm_marketplace_provider,
    process_marketplace_client_confirmation_timeout,
)
from providers.models import Provider, ProviderTicket
from service_type.models import ServiceType


class MarketplaceClientConfirmationTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Marketplace Client Confirmation Test",
            description="Marketplace client confirmation test service type",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Confirm",
            phone_number="555-999-0001",
            email="provider.confirm.market@test.local",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="11 Provider St",
        )
        self.client = self._mk_client()

    def _mk_client(self):
        from clients.models import Client

        return Client.objects.create(
            first_name="Client",
            last_name="Confirm",
            phone_number="555-888-0001",
            email="client.confirm.market@test.local",
            country="Canada",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="99 Client St",
        )

    def _mk_job(self, *, started_delta_minutes=10):
        return Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=3),
            scheduled_start_time=time(hour=12, minute=0),
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
            client=self.client,
            selected_provider=self.provider,
            marketplace_search_started_at=timezone.now() - timedelta(hours=2),
            client_confirmation_started_at=timezone.now() - timedelta(minutes=started_delta_minutes),
            next_marketplace_alert_at=None,
        )

    def test_confirm_marketplace_provider_assigns_and_cleans_fields(self):
        job = self._mk_job(started_delta_minutes=10)

        result = confirm_marketplace_provider(job_id=job.job_id)
        self.assertEqual(result, "confirmed")

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.ASSIGNED)
        self.assertIsNone(job.next_marketplace_alert_at)
        self.assertIsNone(job.marketplace_search_started_at)
        self.assertIsNone(job.client_confirmation_started_at)
        self.assertIsNone(job.selected_provider_id)
        active = JobAssignment.objects.filter(job=job, is_active=True).first()
        self.assertIsNotNone(active)
        self.assertEqual(active.provider_id, self.provider.provider_id)
        fee = AssignmentFee.objects.get(assignment=active)
        self.assertEqual(fee.amount_cents, 0)
        self.assertEqual(fee.status, AssignmentFee.STATUS_OFF)
        ticket = ProviderTicket.objects.get(
            provider=self.provider,
            ref_type="job",
            ref_id=job.job_id,
        )
        self.assertTrue(ticket.ticket_no.startswith(f"PROV-{self.provider.provider_id}-"))
        self.assertEqual(ticket.stage, ProviderTicket.Stage.ESTIMATE)
        self.assertEqual(ticket.status, ProviderTicket.Status.OPEN)
        client_ticket = ClientTicket.objects.get(
            client=job.client,
            ref_type="job",
            ref_id=job.job_id,
        )
        self.assertEqual(client_ticket.stage, ClientTicket.Stage.ESTIMATE)
        self.assertEqual(client_ticket.status, ClientTicket.Status.OPEN)

    def test_confirm_marketplace_provider_rejects_timeout(self):
        job = self._mk_job(started_delta_minutes=61)

        with self.assertRaises(MarketplaceDecisionConflict):
            confirm_marketplace_provider(job_id=job.job_id)

    def test_process_client_confirmation_timeout_reactivates_marketplace(self):
        job = self._mk_job(started_delta_minutes=61)

        result, updated = process_marketplace_client_confirmation_timeout(job.job_id)
        self.assertEqual(result, "timeout_reopened_marketplace")
        self.assertEqual(updated, 1)

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.WAITING_PROVIDER_RESPONSE)
        self.assertIsNone(job.client_confirmation_started_at)
        self.assertIsNone(job.selected_provider_id)
        self.assertIsNotNone(job.next_marketplace_alert_at)

    def test_process_client_confirmation_timeout_to_pending_client_decision(self):
        job = self._mk_job(started_delta_minutes=61)
        job.marketplace_search_started_at = timezone.now() - timedelta(hours=25)
        job.save(update_fields=["marketplace_search_started_at"])

        result, updated = process_marketplace_client_confirmation_timeout(job.job_id)
        self.assertEqual(result, "timeout_to_pending_client_decision")
        self.assertEqual(updated, 1)

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.PENDING_CLIENT_DECISION)
        self.assertIsNone(job.selected_provider_id)
        self.assertIsNone(job.client_confirmation_started_at)
        self.assertIsNone(job.next_marketplace_alert_at)

    def test_tick_marketplace_processes_client_confirmation_timeouts(self):
        self._mk_job(started_delta_minutes=61)
        out = io.StringIO()

        call_command("tick_marketplace", stdout=out)
        output = out.getvalue()
        self.assertIn("DUE CLIENT CONFIRMATION TIMEOUTS: 1", output)
        self.assertIn("timeout_reopened_marketplace", output)

    def test_confirm_idempotent_when_already_assigned(self):
        job = self._mk_job(started_delta_minutes=10)

        first = confirm_marketplace_provider(job_id=job.job_id)
        self.assertEqual(first, "confirmed")
        active_count_after_first = JobAssignment.objects.filter(job=job, is_active=True).count()
        self.assertEqual(active_count_after_first, 1)

        second = confirm_marketplace_provider(job_id=job.job_id)
        self.assertEqual(second, "already_assigned")
        active_count_after_second = JobAssignment.objects.filter(job=job, is_active=True).count()
        self.assertEqual(active_count_after_second, 1)
        ticket_count = ProviderTicket.objects.filter(
            provider=self.provider,
            ref_type="job",
            ref_id=job.job_id,
        ).count()
        self.assertEqual(ticket_count, 1)
        client_ticket_count = ClientTicket.objects.filter(
            client=job.client,
            ref_type="job",
            ref_id=job.job_id,
        ).count()
        self.assertEqual(client_ticket_count, 1)

    def test_confirm_conflict_if_assignment_already_active_other_provider(self):
        job = self._mk_job(started_delta_minutes=10)
        other_provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Other",
            phone_number="555-999-0002",
            email="provider.other.market@test.local",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="12 Provider St",
        )
        JobAssignment.objects.create(
            job=job,
            provider=other_provider,
            is_active=True,
            assignment_status="assigned",
        )

        with self.assertRaises(MarketplaceDecisionConflict):
            confirm_marketplace_provider(job_id=job.job_id)
