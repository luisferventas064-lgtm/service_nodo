from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from jobs.models import Job, JobDispute
from providers.models import Provider, ProviderService, ServiceCategory
from providers.services import enforce_provider_quality_policy
from providers.services_marketplace import search_provider_services
from service_type.models import ServiceType


class MarketplaceRankingTests(TestCase):
    def setUp(self):
        self.cat = ServiceCategory.objects.create(
            name="Plumbing",
            slug="plumbing",
        )
        self.service_type = ServiceType.objects.create(
            name="Ranking Test Service Type",
            description="Ranking Test Service Type",
        )
        self._email_seq = 0

    def _create_provider(
        self,
        rating,
        completed,
        cancelled,
        verified,
        price,
        restricted_until=None,
    ):
        self._email_seq += 1
        p = Provider.objects.create(
            provider_type="self_employed",
            company_name=None,
            contact_first_name="Test",
            contact_last_name=f"Provider{self._email_seq}",
            phone_number="5550000000",
            email=f"provider{self._email_seq}@example.com",
            is_phone_verified=True,
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="123 Test St",
            is_active=True,
            avg_rating=Decimal(str(rating)),
            completed_jobs_count=completed,
            cancelled_jobs_count=cancelled,
            is_verified=verified,
            restricted_until=restricted_until,
        )

        ProviderService.objects.create(
            provider=p,
            category=self.cat,
            custom_name="Test Service",
            description="",
            billing_unit="hour",
            price_cents=price,
            is_active=True,
        )

        return p

    def _create_resolved_dispute(self, provider, *, days_ago):
        job = Job.objects.create(
            job_mode=Job.JobMode.SCHEDULED,
            job_status=Job.JobStatus.CANCELLED,
            cancel_reason=Job.CancelReason.DISPUTE_APPROVED,
            is_asap=False,
            scheduled_date=timezone.localdate() + timedelta(days=3),
            service_type=self.service_type,
            selected_provider=provider,
            province="QC",
            city="Laval",
            postal_code="H7A0A1",
            address_line1="123 Test St",
        )
        dispute = JobDispute.objects.create(
            job=job,
            client_id=1,
            provider_id=provider.provider_id,
            reason="Resolved dispute",
        )
        dispute.status = JobDispute.DisputeStatus.RESOLVED
        dispute.resolved_at = timezone.now() - timedelta(days=days_ago)
        dispute.save(update_fields=["status", "resolved_at"])
        return dispute

    def test_order_by_hybrid_score(self):
        self._create_provider(4.9, 50, 0, True, 20000)

        # Same hybrid_score & safe_rating, different prices (inserted high -> low).
        self._create_provider(4.0, 10, 0, False, 12000)
        self._create_provider(4.0, 10, 0, False, 8000)

        # Same hybrid_score, same safe_rating, same price (tie broken by provider_id).
        self._create_provider(3.0, 0, 0, False, 5000)
        self._create_provider(3.0, 0, 0, False, 5000)

        qs = search_provider_services(
            service_category_id=self.cat.id,
            province="QC",
            city="Laval",
        )

        rows = list(qs)
        ids = [row["provider_id"] for row in rows]
        self.assertEqual(len(ids), 5)

        epsilon = 1e-6
        saw_rating_tie = False
        saw_price_tie = False

        for i in range(len(rows) - 1):
            a = rows[i]
            b = rows[i + 1]

            if a["hybrid_score"] < b["hybrid_score"] - epsilon:
                self.fail("hybrid_score order violated")

            if abs(a["hybrid_score"] - b["hybrid_score"]) <= epsilon:
                if a["safe_rating"] < b["safe_rating"] - epsilon:
                    self.fail("safe_rating tiebreaker violated")

                if abs(a["safe_rating"] - b["safe_rating"]) <= epsilon:
                    saw_rating_tie = True
                    if a["price_cents"] > b["price_cents"]:
                        self.fail("price_cents tiebreaker violated")

                    if a["price_cents"] == b["price_cents"]:
                        saw_price_tie = True
                        if a["provider_id"] > b["provider_id"]:
                            self.fail("provider_id tiebreaker violated")

        self.assertTrue(saw_rating_tie, "No safe_rating ties detected")
        self.assertTrue(saw_price_tie, "No price_cents ties detected")

    def test_cancellation_rate_clamped(self):
        self._create_provider(
            rating=4.0,
            completed=1,
            cancelled=999,
            verified=False,
            price=10000,
        )

        qs = search_provider_services(
            service_category_id=self.cat.id,
            province="QC",
            city="Laval",
        )

        result = qs.first()

        self.assertLessEqual(result["cancellation_rate"], 1.0)
        self.assertGreaterEqual(result["cancellation_rate"], 0.0)

    def test_recent_dispute_reduces_hybrid_score_by_point_fifteen(self):
        provider_without_dispute = self._create_provider(4.0, 10, 0, False, 10000)
        provider_with_dispute = self._create_provider(4.0, 10, 0, False, 10000)
        self._create_resolved_dispute(provider_with_dispute, days_ago=30)

        rows = list(
            search_provider_services(
                service_category_id=self.cat.id,
                province="QC",
                city="Laval",
            )
        )
        scores = {row["provider_id"]: row["hybrid_score"] for row in rows}

        self.assertAlmostEqual(
            scores[provider_without_dispute.provider_id]
            - scores[provider_with_dispute.provider_id],
            0.15,
            places=6,
        )

    def test_restricted_provider_hidden_from_search(self):
        self._create_provider(4.0, 10, 0, False, 10000)
        self._create_provider(
            4.0,
            10,
            0,
            False,
            10000,
            restricted_until=timezone.now() + timedelta(days=30),
        )

        rows = list(
            search_provider_services(
                service_category_id=self.cat.id,
                province="QC",
                city="Laval",
            )
        )

        self.assertEqual(len(rows), 1)

    def test_quality_policy_escalates_warning_and_restriction_levels(self):
        provider = self._create_provider(4.0, 10, 0, False, 10000)
        self._create_resolved_dispute(provider, days_ago=10)
        self._create_resolved_dispute(provider, days_ago=20)
        self._create_resolved_dispute(provider, days_ago=30)

        warning_result = enforce_provider_quality_policy(provider.provider_id)
        provider.refresh_from_db()
        self.assertTrue(warning_result.warning_activated)
        self.assertEqual(warning_result.recent_disputes_last_12m, 3)
        self.assertTrue(provider.quality_warning_active)
        self.assertIsNone(provider.restricted_until)

        self._create_resolved_dispute(provider, days_ago=40)
        self._create_resolved_dispute(provider, days_ago=50)

        restriction_30_result = enforce_provider_quality_policy(provider.provider_id)
        provider.refresh_from_db()
        self.assertFalse(restriction_30_result.warning_activated)
        self.assertEqual(restriction_30_result.recent_disputes_last_12m, 5)
        self.assertTrue(provider.quality_warning_active)
        self.assertIsNotNone(provider.restricted_until)
        self.assertGreater(provider.restricted_until, timezone.now() + timedelta(days=29))
        self.assertLess(provider.restricted_until, timezone.now() + timedelta(days=31))

        self._create_resolved_dispute(provider, days_ago=60)
        restriction_60_result = enforce_provider_quality_policy(provider.provider_id)
        provider.refresh_from_db()
        self.assertEqual(restriction_60_result.recent_disputes_last_12m, 6)
        self.assertGreater(provider.restricted_until, timezone.now() + timedelta(days=59))
        self.assertLess(provider.restricted_until, timezone.now() + timedelta(days=61))

        self._create_resolved_dispute(provider, days_ago=70)
        self._create_resolved_dispute(provider, days_ago=80)
        restriction_90_result = enforce_provider_quality_policy(provider.provider_id)
        provider.refresh_from_db()
        self.assertEqual(restriction_90_result.recent_disputes_last_12m, 8)
        self.assertGreater(provider.restricted_until, timezone.now() + timedelta(days=89))
        self.assertLess(provider.restricted_until, timezone.now() + timedelta(days=91))

    def test_log10_zero_jobs_safe(self):
        self._create_provider(
            rating=4.5,
            completed=0,
            cancelled=0,
            verified=False,
            price=10000,
        )

        qs = search_provider_services(
            service_category_id=self.cat.id,
            province="QC",
            city="Laval",
        )

        result = qs.first()

        self.assertAlmostEqual(result["volume_score"], 0.0, places=6)

    def test_pagination_limit(self):
        for _ in range(30):
            self._create_provider(4.0, 10, 0, False, 10000)

        qs = search_provider_services(
            service_category_id=self.cat.id,
            province="QC",
            city="Laval",
            limit=10,
        )

        self.assertEqual(len(qs), 10)

    def test_pagination_offset(self):
        for i in range(10):
            self._create_provider(4.0, i, 0, False, 10000)

        qs1 = search_provider_services(
            service_category_id=self.cat.id,
            province="QC",
            city="Laval",
            limit=5,
            offset=0,
        )

        qs2 = search_provider_services(
            service_category_id=self.cat.id,
            province="QC",
            city="Laval",
            limit=5,
            offset=5,
        )

        ids1 = [row["provider_id"] for row in qs1]
        ids2 = [row["provider_id"] for row in qs2]

        self.assertTrue(set(ids1).isdisjoint(set(ids2)))

    def test_limit_capped(self):
        for _ in range(200):
            self._create_provider(4.0, 10, 0, False, 10000)

        qs = search_provider_services(
            service_category_id=self.cat.id,
            province="QC",
            city="Laval",
            limit=1000,
        )

        self.assertLessEqual(len(qs), 100)
