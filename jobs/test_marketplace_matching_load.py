from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from assignments.models import JobAssignment
from jobs.models import Job
from jobs.services import MAX_ACTIVE_JOBS, get_broadcast_candidates_for_job
from providers.models import Provider, ProviderServiceArea, ProviderServiceType
from service_type.models import ServiceType


class MarketplaceMatchingLoadTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Marketplace Matching Load Test",
            description="Marketplace matching load test service type",
        )

    def _make_provider(self, n: int) -> Provider:
        provider = Provider.objects.create(
            provider_type="self_employed",
            contact_first_name=f"Provider{n}",
            contact_last_name="Load",
            phone_number=f"555-666-000{n}",
            email=f"provider{n}.load.match@test.local",
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1=f"{n} Provider St",
        )
        ProviderServiceType.objects.create(
            provider=provider,
            service_type=self.service_type,
            price_type="fixed",
            base_price="50.00",
            is_active=True,
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city="Laval",
            province="QC",
            is_active=True,
        )
        return provider

    def _make_job(self, *, status=Job.JobStatus.POSTED) -> Job:
        return Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=status,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=3),
            service_type=self.service_type,
            province="QC",
            city="Laval",
            postal_code="H7N1A1",
            address_line1="123 Main St",
        )

    def _add_active_assignment(self, *, provider: Provider):
        assigned_job = self._make_job(status=Job.JobStatus.ASSIGNED)
        JobAssignment.objects.create(
            job=assigned_job,
            provider=provider,
            is_active=True,
            assignment_status="assigned",
        )

    def test_provider_excluded_if_at_capacity(self):
        saturated = self._make_provider(1)
        available = self._make_provider(2)
        target_job = self._make_job()

        for _ in range(MAX_ACTIVE_JOBS):
            self._add_active_assignment(provider=saturated)

        candidates = get_broadcast_candidates_for_job(target_job, limit=10)
        self.assertNotIn(saturated.provider_id, candidates)
        self.assertIn(available.provider_id, candidates)

    def test_provider_penalized_but_not_excluded_if_below_capacity(self):
        loaded = self._make_provider(1)
        idle = self._make_provider(2)
        target_job = self._make_job()

        self._add_active_assignment(provider=loaded)

        candidates = get_broadcast_candidates_for_job(target_job, limit=10)
        self.assertIn(loaded.provider_id, candidates)
        self.assertIn(idle.provider_id, candidates)
        self.assertLess(candidates.index(idle.provider_id), candidates.index(loaded.provider_id))
