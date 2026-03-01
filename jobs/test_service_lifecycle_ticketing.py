from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from assignments.models import JobAssignment
from assignments.services import AssignmentConflict, complete_job as complete_job_by_worker
from clients.models import Client, ClientTicket
from jobs.models import Job, JobDispute, JobEvent
from jobs.services import (
    MarketplaceDecisionConflict,
    complete_service_by_provider,
    confirm_service_closed_by_client,
    resolve_job_dispute_client_wins,
    start_service_by_provider,
)
from jobs.services_normal_client_confirm import confirm_normal_job_by_client
from providers.models import Provider, ProviderTicket
from service_type.models import ServiceType
from workers.models import Worker

User = get_user_model()


class ServiceLifecycleTicketingTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Lifecycle Ticketing Test",
            description="Lifecycle Ticketing Test",
        )
        self.client = Client.objects.create(
            first_name="Client",
            last_name="Lifecycle",
            phone_number="555-123-0001",
            email="client.lifecycle@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Provider",
            contact_last_name="Lifecycle",
            phone_number="555-123-0002",
            email="provider.lifecycle@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="2 Provider St",
        )
        self.job = Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.PENDING_CLIENT_CONFIRMATION,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=3),
            service_type=self.service_type,
            client=self.client,
            selected_provider=self.provider,
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="3 Job St",
        )

    def test_start_and_close_finalize_provider_and_client_tickets(self):
        ok, *_ = confirm_normal_job_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertTrue(ok)

        started = start_service_by_provider(job_id=self.job.job_id, provider_id=self.provider.provider_id)
        self.assertEqual(started, "started")

        closed = confirm_service_closed_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertEqual(closed, "closed_and_confirmed")

        self.job.refresh_from_db()
        self.assertEqual(self.job.job_status, Job.JobStatus.CONFIRMED)

        pt = ProviderTicket.objects.get(provider=self.provider, ref_type="job", ref_id=self.job.job_id)
        ct = ClientTicket.objects.get(client=self.client, ref_type="job", ref_id=self.job.job_id)
        self.assertEqual(pt.stage, ProviderTicket.Stage.FINAL)
        self.assertEqual(pt.status, ProviderTicket.Status.FINALIZED)
        self.assertEqual(ct.stage, ClientTicket.Stage.FINAL)
        self.assertEqual(ct.status, ClientTicket.Status.FINALIZED)

    def test_close_is_idempotent(self):
        ok, *_ = confirm_normal_job_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        self.assertTrue(ok)
        start_service_by_provider(job_id=self.job.job_id, provider_id=self.provider.provider_id)
        first = confirm_service_closed_by_client(job_id=self.job.job_id, client_id=self.client.client_id)
        second = confirm_service_closed_by_client(job_id=self.job.job_id, client_id=self.client.client_id)

        self.assertEqual(first, "closed_and_confirmed")
        self.assertEqual(second, "already_confirmed")
        self.assertEqual(
            ProviderTicket.objects.filter(provider=self.provider, ref_type="job", ref_id=self.job.job_id).count(),
            1,
        )
        self.assertEqual(
            ClientTicket.objects.filter(client=self.client, ref_type="job", ref_id=self.job.job_id).count(),
            1,
        )

    def test_provider_can_complete_before_client_confirmation(self):
        ok, *_ = confirm_normal_job_by_client(
            job_id=self.job.job_id,
            client_id=self.client.client_id,
        )
        self.assertTrue(ok)

        started = start_service_by_provider(
            job_id=self.job.job_id,
            provider_id=self.provider.provider_id,
        )
        self.assertEqual(started, "started")

        completed = complete_service_by_provider(
            job_id=self.job.job_id,
            provider_id=self.provider.provider_id,
        )
        self.assertEqual(completed, "completed")

        self.job.refresh_from_db()
        self.assertEqual(self.job.job_status, Job.JobStatus.COMPLETED)

        closed = confirm_service_closed_by_client(
            job_id=self.job.job_id,
            client_id=self.client.client_id,
        )
        self.assertEqual(closed, "closed_and_confirmed")

    def test_open_dispute_blocks_client_confirmation(self):
        ok, *_ = confirm_normal_job_by_client(
            job_id=self.job.job_id,
            client_id=self.client.client_id,
        )
        self.assertTrue(ok)

        start_service_by_provider(
            job_id=self.job.job_id,
            provider_id=self.provider.provider_id,
        )
        complete_service_by_provider(
            job_id=self.job.job_id,
            provider_id=self.provider.provider_id,
        )

        JobDispute.objects.create(
            job=self.job,
            client_id=self.client.client_id,
            provider_id=self.provider.provider_id,
            reason="Client disputes completion",
        )

        with self.assertRaises(MarketplaceDecisionConflict) as exc:
            confirm_service_closed_by_client(
                job_id=self.job.job_id,
                client_id=self.client.client_id,
            )

        self.assertEqual(str(exc.exception), "DISPUTE_OPEN")
        self.job.refresh_from_db()
        self.assertEqual(self.job.job_status, Job.JobStatus.COMPLETED)

    def test_under_review_dispute_blocks_provider_completion(self):
        ok, *_ = confirm_normal_job_by_client(
            job_id=self.job.job_id,
            client_id=self.client.client_id,
        )
        self.assertTrue(ok)

        start_service_by_provider(
            job_id=self.job.job_id,
            provider_id=self.provider.provider_id,
        )

        JobDispute.objects.create(
            job=self.job,
            client_id=self.client.client_id,
            provider_id=self.provider.provider_id,
            reason="Dispute under review",
            status=JobDispute.DisputeStatus.UNDER_REVIEW,
        )

        with self.assertRaises(MarketplaceDecisionConflict) as exc:
            complete_service_by_provider(
                job_id=self.job.job_id,
                provider_id=self.provider.provider_id,
            )

        self.assertEqual(str(exc.exception), "DISPUTE_OPEN")
        self.job.refresh_from_db()
        self.assertEqual(self.job.job_status, Job.JobStatus.IN_PROGRESS)

    def test_under_review_dispute_blocks_worker_completion(self):
        worker = Worker.objects.create(
            first_name="Worker",
            last_name="Lifecycle",
            email="worker.lifecycle@test.local",
            province="QC",
            city="Montreal",
        )
        self.job.job_status = Job.JobStatus.IN_PROGRESS
        self.job.save(update_fields=["job_status", "updated_at"])

        JobAssignment.objects.create(
            job=self.job,
            provider=self.provider,
            worker=worker,
            assignment_status="in_progress",
            is_active=True,
            accepted_at=timezone.now(),
        )
        JobDispute.objects.create(
            job=self.job,
            client_id=self.client.client_id,
            provider_id=self.provider.provider_id,
            reason="Worker completion frozen",
            status=JobDispute.DisputeStatus.UNDER_REVIEW,
        )

        with self.assertRaises(AssignmentConflict) as exc:
            complete_job_by_worker(
                job_id=self.job.job_id,
                worker_id=worker.worker_id,
            )

        self.assertEqual(str(exc.exception), "DISPUTE_OPEN")
        self.job.refresh_from_db()
        self.assertEqual(self.job.job_status, Job.JobStatus.IN_PROGRESS)

    def test_resolve_job_dispute_client_wins_tracks_admin_and_cancels_job(self):
        admin_user = User.objects.create_user(
            username="admin_dispute_resolution",
            password="test-pass-123",
        )
        worker = Worker.objects.create(
            first_name="Worker",
            last_name="Dispute",
            email="worker.dispute.resolution@test.local",
            province="QC",
            city="Montreal",
        )
        ok, *_ = confirm_normal_job_by_client(
            job_id=self.job.job_id,
            client_id=self.client.client_id,
        )
        self.assertTrue(ok)

        start_service_by_provider(
            job_id=self.job.job_id,
            provider_id=self.provider.provider_id,
        )
        complete_service_by_provider(
            job_id=self.job.job_id,
            provider_id=self.provider.provider_id,
        )
        assignment = JobAssignment.objects.get(job=self.job, is_active=True)
        assignment.worker = worker
        assignment.save(update_fields=["worker", "updated_at"])

        JobDispute.objects.create(
            job=self.job,
            client_id=self.client.client_id,
            provider_id=self.provider.provider_id,
            reason="Client requests refund",
            status=JobDispute.DisputeStatus.OPEN,
        )
        for days_ago in (30, 60):
            historical_job = Job.objects.create(
                job_mode=Job.JobMode.SCHEDULED,
                job_status=Job.JobStatus.CANCELLED,
                cancel_reason=Job.CancelReason.DISPUTE_APPROVED,
                is_asap=False,
                scheduled_date=timezone.localdate() + timedelta(days=3),
                service_type=self.service_type,
                client=self.client,
                selected_provider=self.provider,
                province="QC",
                city="Montreal",
                postal_code="H1H1H1",
                address_line1="4 Historical St",
            )
            historical_dispute = JobDispute.objects.create(
                job=historical_job,
                client_id=self.client.client_id,
                provider_id=self.provider.provider_id,
                reason="Historical dispute",
                status=JobDispute.DisputeStatus.OPEN,
            )
            historical_dispute.status = JobDispute.DisputeStatus.RESOLVED
            historical_dispute.resolved_at = timezone.now() - timedelta(days=days_ago)
            historical_dispute.save(update_fields=["status", "resolved_at"])

        with patch("jobs.services.send_dispute_resolution_email") as send_email:
            with patch("jobs.services.send_quality_warning_email") as send_warning:
                with self.captureOnCommitCallbacks(execute=True) as callbacks:
                    result = resolve_job_dispute_client_wins(
                        job_id=self.job.job_id,
                        admin_user=admin_user,
                        resolution_note="Full refund approved due to service quality issue.",
                        public_resolution_note=(
                            "After review, your dispute has been approved. "
                            "A full refund has been granted."
                        ),
                    )

        self.assertEqual(result, "dispute_resolved_client_wins")
        self.assertEqual(len(callbacks), 2)
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(send_email.call_args.args[0].job_id, self.job.job_id)
        self.assertEqual(send_warning.call_count, 1)
        self.job.refresh_from_db()
        self.provider.refresh_from_db()
        worker.refresh_from_db()
        self.assertEqual(self.job.job_status, Job.JobStatus.CANCELLED)
        self.assertEqual(self.job.cancel_reason, Job.CancelReason.DISPUTE_APPROVED)
        self.assertEqual(self.provider.disputes_lost_count, 1)
        self.assertTrue(self.provider.quality_warning_active)
        self.assertIsNone(self.provider.restricted_until)
        self.assertEqual(worker.disputes_lost_count, 0)

        dispute = self.job.dispute
        self.assertEqual(dispute.status, JobDispute.DisputeStatus.RESOLVED)
        self.assertIsNotNone(dispute.resolved_at)
        self.assertEqual(dispute.resolved_by_id, admin_user.id)
        self.assertEqual(
            dispute.resolution_note,
            "Full refund approved due to service quality issue.",
        )
        self.assertEqual(
            dispute.public_resolution_note,
            "After review, your dispute has been approved. "
            "A full refund has been granted.",
        )

        event = self.job.events.latest("created_at")
        self.assertEqual(event.event_type, JobEvent.EventType.CANCELLED)
        self.assertEqual(event.note, "dispute_resolved_client_wins")
        assignment.refresh_from_db()
        self.assertEqual(assignment.assignment_status, "cancelled")
        self.assertFalse(assignment.is_active)
