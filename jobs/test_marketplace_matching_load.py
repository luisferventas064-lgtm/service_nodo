from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from assignments.models import JobAssignment
from jobs.models import Job, JobLocation
from jobs.services import (
    MAX_ACTIVE_JOBS,
    dispatch_soft_random_bonus,
    get_broadcast_candidates_for_job,
)
from providers.models import Provider, ProviderLocation, ProviderService, ProviderServiceArea
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
            availability_mode="manual",
            is_available_now=True,
            accepts_urgent=True,
            accepts_scheduled=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=self.service_type,
            custom_name="Load Match Service",
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

    def _make_job(self, *, status=Job.JobStatus.POSTED, mode=Job.JobMode.SCHEDULED) -> Job:
        return Job.objects.create(
            job_mode=mode,
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

    def test_provider_excluded_when_not_available_now(self):
        unavailable = self._make_provider(8)
        unavailable.is_available_now = False
        unavailable.save(update_fields=["is_available_now", "updated_at"])

        available = self._make_provider(9)
        target_job = self._make_job()

        candidates = get_broadcast_candidates_for_job(target_job, limit=10)

        self.assertNotIn(unavailable.provider_id, candidates)
        self.assertIn(available.provider_id, candidates)

    def test_provider_excluded_when_temporarily_unavailable(self):
        paused = self._make_provider(10)
        paused.temporary_unavailable_until = timezone.now() + timedelta(hours=2)
        paused.save(update_fields=["temporary_unavailable_until", "updated_at"])

        available = self._make_provider(11)
        target_job = self._make_job()

        candidates = get_broadcast_candidates_for_job(target_job, limit=10)

        self.assertNotIn(paused.provider_id, candidates)
        self.assertIn(available.provider_id, candidates)

    def test_provider_included_when_temporary_pause_expired(self):
        resumed = self._make_provider(12)
        resumed.temporary_unavailable_until = timezone.now() - timedelta(minutes=1)
        resumed.save(update_fields=["temporary_unavailable_until", "updated_at"])

        target_job = self._make_job()

        candidates = get_broadcast_candidates_for_job(target_job, limit=10)

        self.assertIn(resumed.provider_id, candidates)

    def test_provider_excluded_from_on_demand_job_when_accepts_urgent_false(self):
        rejects_urgent = self._make_provider(13)
        rejects_urgent.accepts_urgent = False
        rejects_urgent.save(update_fields=["accepts_urgent", "updated_at"])

        accepts_all = self._make_provider(14)
        on_demand_job = self._make_job(mode=Job.JobMode.ON_DEMAND)

        candidates = get_broadcast_candidates_for_job(on_demand_job, limit=10)

        self.assertNotIn(rejects_urgent.provider_id, candidates)
        self.assertIn(accepts_all.provider_id, candidates)

    def test_provider_excluded_from_scheduled_job_when_accepts_scheduled_false(self):
        rejects_scheduled = self._make_provider(15)
        rejects_scheduled.accepts_scheduled = False
        rejects_scheduled.save(update_fields=["accepts_scheduled", "updated_at"])

        accepts_all = self._make_provider(16)
        scheduled_job = self._make_job(mode=Job.JobMode.SCHEDULED)

        candidates = get_broadcast_candidates_for_job(scheduled_job, limit=10)

        self.assertNotIn(rejects_scheduled.provider_id, candidates)
        self.assertIn(accepts_all.provider_id, candidates)

    def test_provider_penalized_but_not_excluded_if_below_capacity(self):
        loaded = self._make_provider(1)
        idle = self._make_provider(2)
        target_job = self._make_job()

        self._add_active_assignment(provider=loaded)

        candidates = get_broadcast_candidates_for_job(target_job, limit=10)
        self.assertIn(loaded.provider_id, candidates)
        self.assertIn(idle.provider_id, candidates)
        self.assertLess(candidates.index(idle.provider_id), candidates.index(loaded.provider_id))

    def test_location_models_compute_grid_on_create(self):
        provider = self._make_provider(99)
        job = self._make_job()

        job_location = JobLocation.objects.create(
            job=job,
            latitude=Decimal("45.560100"),
            longitude=Decimal("-73.712400"),
            postal_code="H7N1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )
        provider_location = ProviderLocation.objects.create(
            provider=provider,
            latitude=Decimal("45.560100"),
            longitude=Decimal("-73.712400"),
            postal_code="H7N1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )

        self.assertEqual(job_location.grid_lat, 911)
        self.assertEqual(job_location.grid_lng, -1475)
        self.assertEqual(provider_location.grid_lat, 911)
        self.assertEqual(provider_location.grid_lng, -1475)

    def test_fairness_reorders_broadcast_candidates_when_job_has_location(self):
        recent = self._make_provider(3)
        rested = self._make_provider(4)
        recent.avg_rating = 5
        recent.last_job_assigned_at = timezone.now()
        recent.save(update_fields=["avg_rating", "last_job_assigned_at"])
        rested.avg_rating = 5
        rested.last_job_assigned_at = timezone.now() - timedelta(hours=4)
        rested.save(update_fields=["avg_rating", "last_job_assigned_at"])

        target_job = self._make_job()
        JobLocation.objects.create(
            job=target_job,
            latitude=Decimal("45.560100"),
            longitude=Decimal("-73.712400"),
            postal_code="H7N1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )
        ProviderLocation.objects.create(
            provider=recent,
            latitude=Decimal("45.560100"),
            longitude=Decimal("-73.712400"),
            postal_code="H7N1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )
        ProviderLocation.objects.create(
            provider=rested,
            latitude=Decimal("45.515000"),
            longitude=Decimal("-73.620000"),
            postal_code="H1A1A1",
            city="Montreal",
            province="QC",
            country="Canada",
        )

        candidates = get_broadcast_candidates_for_job(target_job, limit=10)

        self.assertLess(candidates.index(rested.provider_id), candidates.index(recent.provider_id))

    def test_dispatch_soft_random_bonus_is_stable_per_attempt(self):
        first = dispatch_soft_random_bonus(job_id=99, provider_id=42, attempt_number=1)
        second = dispatch_soft_random_bonus(job_id=99, provider_id=42, attempt_number=1)
        third = dispatch_soft_random_bonus(job_id=99, provider_id=42, attempt_number=2)

        self.assertEqual(first, second)
        self.assertGreaterEqual(first, 0.0)
        self.assertLessEqual(first, 0.02)
        self.assertNotEqual(first, third)

    def test_geographic_prefilter_excludes_providers_outside_radius_when_nearby_exist(self):
        nearby = self._make_provider(5)
        remote = self._make_provider(6)
        target_job = self._make_job()

        JobLocation.objects.create(
            job=target_job,
            latitude=Decimal("45.560100"),
            longitude=Decimal("-73.712400"),
            postal_code="H7N1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )
        ProviderLocation.objects.create(
            provider=nearby,
            latitude=Decimal("45.561000"),
            longitude=Decimal("-73.713000"),
            postal_code="H7N1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )
        ProviderLocation.objects.create(
            provider=remote,
            latitude=Decimal("46.813900"),
            longitude=Decimal("-71.208000"),
            postal_code="G1A1A1",
            city="Quebec City",
            province="QC",
            country="Canada",
        )

        candidates = get_broadcast_candidates_for_job(target_job, limit=10)

        self.assertIn(nearby.provider_id, candidates)
        self.assertNotIn(remote.provider_id, candidates)

    def test_geographic_prefilter_falls_back_when_no_provider_is_within_radius(self):
        remote = self._make_provider(7)
        target_job = self._make_job()

        JobLocation.objects.create(
            job=target_job,
            latitude=Decimal("45.560100"),
            longitude=Decimal("-73.712400"),
            postal_code="H7N1A1",
            city="Laval",
            province="QC",
            country="Canada",
        )
        ProviderLocation.objects.create(
            provider=remote,
            latitude=Decimal("46.813900"),
            longitude=Decimal("-71.208000"),
            postal_code="G1A1A1",
            city="Quebec City",
            province="QC",
            country="Canada",
        )

        candidates = get_broadcast_candidates_for_job(target_job, limit=10)

        self.assertEqual(candidates, [remote.provider_id])

    def test_provider_temporary_pause_excludes_from_broadcast_candidates_and_restores_after_expiry(self):
        """
        Ciclo completo de pausa temporal sobre matching real.
        Validado manualmente contra DB el 2026-03-17 (provider 1591, job seed #919).
        Protege: effective_provider_availability_q(), rank_broadcast_candidates_for_job().
        """
        provider = self._make_provider(20)
        target_job = self._make_job()

        # ── ANTES: sin pausa, el provider es candidato ──────────────────────────
        candidates_before = get_broadcast_candidates_for_job(target_job, limit=50)
        self.assertIn(provider.provider_id, candidates_before, "Provider debe aparecer antes de la pausa")

        # ── DURANTE: pausa activa (30 min futuro) ───────────────────────────────
        provider.temporary_unavailable_until = timezone.now() + timedelta(minutes=30)
        provider.save(update_fields=["temporary_unavailable_until", "updated_at"])
        provider.refresh_from_db()

        candidates_during = get_broadcast_candidates_for_job(target_job, limit=50)
        self.assertNotIn(provider.provider_id, candidates_during, "Provider no debe aparecer con pausa activa")

        # ── DESPUÉS: pausa expirada (1 min pasado) ───────────────────────────────
        provider.temporary_unavailable_until = timezone.now() - timedelta(minutes=1)
        provider.save(update_fields=["temporary_unavailable_until", "updated_at"])
        provider.refresh_from_db()

        candidates_after = get_broadcast_candidates_for_job(target_job, limit=50)
        self.assertIn(provider.provider_id, candidates_after, "Provider debe volver a aparecer tras expirar la pausa")
