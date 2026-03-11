from django.test import TestCase

from assignments.models import JobAssignment
from assignments.services import activate_assignment_for_job
from jobs.models import Job, JobStatus
from providers.models import Provider
from service_type.models import ServiceType


class ActivateAssignmentForJobTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Assignment Activation Test",
            description="Assignment Activation Test",
        )
        self.provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Assignment",
            contact_last_name="Provider",
            phone_number="555-120-0001",
            email="provider.assignment@test.local",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="100 Provider St",
        )
        self.job = Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            scheduled_date=None,
            is_asap=True,
            job_status=JobStatus.POSTED,
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
        )

    def test_activate_assignment_marks_provider_last_job_assigned_at(self):
        result = activate_assignment_for_job(
            job_id=self.job.job_id,
            provider_id=self.provider.provider_id,
        )

        self.job.refresh_from_db()
        self.provider.refresh_from_db()

        self.assertTrue(result.created)
        self.assertEqual(self.job.job_status, JobStatus.ASSIGNED)
        self.assertTrue(
            JobAssignment.objects.filter(
                job=self.job,
                provider=self.provider,
                is_active=True,
            ).exists()
        )
        self.assertIsNotNone(self.provider.last_job_assigned_at)
