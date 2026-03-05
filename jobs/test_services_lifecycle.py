from django.test import TestCase

from assignments.models import JobAssignment
from jobs.models import Job
from jobs.services_lifecycle import accept_job_by_provider
from providers.models import Provider
from service_type.models import ServiceType


class ServicesLifecycleTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Lifecycle Accept Test",
            description="Lifecycle accept test service type",
        )

    def _create_job(self):
        return Job.objects.create(
            job_mode=Job.JobMode.ON_DEMAND,
            scheduled_date=None,
            is_asap=True,
            job_status=Job.JobStatus.WAITING_PROVIDER_RESPONSE,
            service_type=self.service_type,
            quoted_base_price="100.00",
            quoted_base_price_cents=10_000,
            quoted_currency_code="CAD",
            quoted_currency="CAD",
            quoted_pricing_source="TestSnapshot",
            quoted_total_price_cents=10_000,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
        )

    def _create_provider(self):
        return Provider.objects.create(
            provider_type="self_employed",
            contact_first_name="Accept",
            contact_last_name="Provider",
            phone_number="555-333-0000",
            email="provider.lifecycle.accept@test.local",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="100 Provider St",
        )

    def test_accept_job_by_provider_creates_active_assignment_and_assigns_job(self):
        job = self._create_job()
        provider = self._create_provider()

        assignment = accept_job_by_provider(job, provider)

        job.refresh_from_db()
        self.assertEqual(job.job_status, Job.JobStatus.ASSIGNED)
        self.assertEqual(assignment.job_id, job.job_id)
        self.assertEqual(assignment.provider_id, provider.provider_id)
        self.assertTrue(assignment.is_active)
        self.assertEqual(assignment.assignment_status, "assigned")
        self.assertEqual(JobAssignment.objects.filter(job=job, is_active=True).count(), 1)
