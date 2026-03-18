from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from assignments.models import JobAssignment
from jobs.events import get_visible_job_status_label
from clients.models import Client
from jobs.models import Job, JobEvent
from jobs.services import complete_service_by_provider
from providers.models import Provider
from service_type.models import ServiceType


class CompleteServiceAuthorizationTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Complete Authorization Test",
            description="Complete authorization test service type",
        )
        self.client = Client.objects.create(
            first_name="Client",
            last_name="CompleteAuth",
            phone_number="555-555-0001",
            email="client.complete.auth@test.local",
            country="Canada",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="1 Client St",
        )
        self.provider_assigned = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Assigned",
            contact_last_name="Provider",
            phone_number="555-555-0002",
            email="provider.assigned.complete.auth@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="2 Provider St",
        )
        self.provider_other = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Other",
            contact_last_name="Provider",
            phone_number="555-555-0003",
            email="provider.other.complete.auth@test.local",
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="3 Provider St",
        )
        self.job = Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.IN_PROGRESS,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=2),
            service_type=self.service_type,
            client=self.client,
            selected_provider=self.provider_assigned,
            province="QC",
            city="Montreal",
            postal_code="H1H1H1",
            address_line1="4 Job St",
        )

    def test_complete_requires_active_assignment(self):
        with self.assertRaises(ValueError) as exc:
            complete_service_by_provider(
                job_id=self.job.job_id,
                provider_id=self.provider_assigned.provider_id,
            )

        self.assertEqual(str(exc.exception), "No active assignment for this job.")

    def test_only_assigned_provider_can_complete(self):
        assignment = JobAssignment.objects.create(
            job=self.job,
            provider=self.provider_assigned,
            is_active=True,
            assignment_status="in_progress",
            accepted_at=timezone.now(),
        )

        with self.assertRaises(ValueError) as exc:
            complete_service_by_provider(
                job_id=self.job.job_id,
                provider_id=self.provider_other.provider_id,
            )

        self.assertEqual(str(exc.exception), "Provider not authorized to complete this job.")

        result = complete_service_by_provider(
            job_id=self.job.job_id,
            provider_id=self.provider_assigned.provider_id,
        )

        self.assertEqual(result, "completed")
        self.job.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(self.job.job_status, Job.JobStatus.COMPLETED)
        self.assertEqual(assignment.assignment_status, "completed")
        self.assertIsNotNone(assignment.completed_at)
        event = self.job.events.get(event_type=JobEvent.EventType.JOB_COMPLETED)
        self.assertEqual(event.actor_role, JobEvent.ActorRole.PROVIDER)
        self.assertEqual(
            event.visible_status,
            get_visible_job_status_label(Job.JobStatus.COMPLETED),
        )
        self.assertEqual(event.assignment_id, assignment.assignment_id)
