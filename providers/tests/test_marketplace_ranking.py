from decimal import Decimal

from django.test import TestCase

from providers.models import Provider, ProviderService, ProviderServiceArea
from providers.services_metrics import (
    increment_accepted,
    increment_cancelled,
    increment_offers_received,
    record_offer_accepted,
)
from providers.services_marketplace import search_provider_services
from service_type.models import ServiceType


class MarketplaceRankingTests(TestCase):
    def setUp(self):
        self.service_type = ServiceType.objects.create(
            name="Ranking Test Service Type",
            description="Ranking Test Service Type",
        )
        self.other_service_type = ServiceType.objects.create(
            name="Other Ranking Service Type",
            description="Other Ranking Service Type",
        )
        self._email_seq = 0

    def _create_provider(
        self,
        *,
        rating,
        price,
        city="Laval",
        province="QC",
        service_type=None,
        completed=10,
        cancelled=0,
    ):
        self._email_seq += 1
        provider = Provider.objects.create(
            provider_type="self_employed",
            company_name=None,
            legal_name=f"Provider {self._email_seq}",
            contact_first_name="Test",
            contact_last_name=f"Provider{self._email_seq}",
            phone_number=f"555000{self._email_seq:04d}",
            email=f"provider{self._email_seq}@example.com",
            is_phone_verified=True,
            profile_completed=True,
            billing_profile_completed=True,
            accepts_terms=True,
            service_area=city,
            province=province,
            city=city,
            postal_code="H7A0A1",
            address_line1="123 Test St",
            is_active=True,
            avg_rating=Decimal(str(rating)),
            completed_jobs_count=completed,
            cancelled_jobs_count=cancelled,
        )
        ProviderServiceArea.objects.create(
            provider=provider,
            city=city,
            province=province,
            is_active=True,
        )
        ProviderService.objects.create(
            provider=provider,
            service_type=service_type or self.service_type,
            custom_name="Test Service",
            description="",
            billing_unit="hour",
            price_cents=price,
            is_active=True,
        )
        return provider

    def test_filters_by_service_type_and_city(self):
        city_provider = self._create_provider(rating=4.5, price=10000)
        self._create_provider(
            rating=4.7,
            price=9000,
            city="Montreal",
            service_type=self.service_type,
        )
        self._create_provider(
            rating=4.9,
            price=8000,
            service_type=self.other_service_type,
        )

        rows = list(
            search_provider_services(
                service_type_id=self.service_type.pk,
                province="QC",
                city="Laval",
            )
        )

        self.assertEqual([row["provider_id"] for row in rows], [city_provider.provider_id])

    def test_orders_by_rating_then_price(self):
        top_provider = self._create_provider(rating=4.9, price=20000)
        cheaper_tie = self._create_provider(rating=4.0, price=8000)
        pricier_tie = self._create_provider(rating=4.0, price=12000)

        rows = list(
            search_provider_services(
                service_type_id=self.service_type.pk,
                province="QC",
                city="Laval",
            )
        )

        self.assertEqual(
            [row["provider_id"] for row in rows],
            [
                top_provider.provider_id,
                cheaper_tie.provider_id,
                pricier_tie.provider_id,
            ],
        )

    def test_cancellation_rate_clamped(self):
        self._create_provider(
            rating=4.0,
            price=10000,
            completed=1,
            cancelled=999,
        )

        row = search_provider_services(
            service_type_id=self.service_type.pk,
            province="QC",
            city="Laval",
        ).first()

        self.assertEqual(row["cancellation_rate"], 1.0)

    def test_pagination_limit_and_offset(self):
        for index in range(10):
            self._create_provider(rating=4.0 + (index / 100), price=10000 + index)

        page1 = list(
            search_provider_services(
                service_type_id=self.service_type.pk,
                province="QC",
                city="Laval",
                limit=5,
                offset=0,
            )
        )
        page2 = list(
            search_provider_services(
                service_type_id=self.service_type.pk,
                province="QC",
                city="Laval",
                limit=5,
                offset=5,
            )
        )

        ids1 = [row["provider_id"] for row in page1]
        ids2 = [row["provider_id"] for row in page2]

        self.assertEqual(len(ids1), 5)
        self.assertEqual(len(ids2), 5)
        self.assertTrue(set(ids1).isdisjoint(set(ids2)))

    def test_provider_save_persists_hybrid_score_fields(self):
        provider = self._create_provider(
            rating=4.5,
            price=10000,
            completed=20,
            cancelled=0,
        )

        provider.refresh_from_db()
        metrics = provider.metrics

        self.assertEqual(metrics.jobs_completed, provider.completed_jobs_count)
        self.assertEqual(metrics.jobs_cancelled, provider.cancelled_jobs_count)
        self.assertGreater(metrics.operational_score, 0.0)
        self.assertGreater(metrics.experience_score, 0.0)
        self.assertGreater(provider.hybrid_score, 0.0)
        self.assertGreater(provider.base_dispatch_score, 0.0)

    def test_provider_save_recalculates_hybrid_score_when_rating_changes(self):
        provider = self._create_provider(
            rating=3.0,
            price=10000,
            completed=20,
            cancelled=0,
        )
        original_hybrid_score = provider.hybrid_score

        provider.avg_rating = Decimal("5.00")
        provider.save(update_fields=["avg_rating"])
        provider.refresh_from_db()

        self.assertGreater(provider.hybrid_score, original_hybrid_score)
        self.assertGreater(provider.base_dispatch_score, 0.0)

    def test_increment_cancelled_reduces_operational_and_hybrid_scores(self):
        provider = self._create_provider(
            rating=4.8,
            price=10000,
            completed=10,
            cancelled=0,
        )
        original_operational_score = provider.metrics.operational_score
        original_hybrid_score = provider.hybrid_score

        increment_cancelled(provider.provider_id)
        provider.refresh_from_db()
        provider.metrics.refresh_from_db()

        self.assertLess(provider.metrics.operational_score, original_operational_score)
        self.assertLess(provider.hybrid_score, original_hybrid_score)

    def test_increment_accepted_updates_provider_metrics(self):
        provider = self._create_provider(
            rating=4.2,
            price=10000,
            completed=0,
            cancelled=0,
        )

        increment_accepted(provider.provider_id)
        provider.refresh_from_db()
        provider.metrics.refresh_from_db()

        self.assertEqual(provider.metrics.jobs_accepted, 1)

    def test_offer_learning_updates_acceptance_rate_and_response_time(self):
        provider = self._create_provider(
            rating=4.2,
            price=10000,
            completed=0,
            cancelled=0,
        )

        increment_offers_received(provider.provider_id)
        increment_offers_received(provider.provider_id)
        record_offer_accepted(provider.provider_id, response_seconds=120)

        provider.refresh_from_db()
        provider.metrics.refresh_from_db()

        self.assertEqual(provider.metrics.offers_received_count, 2)
        self.assertEqual(provider.metrics.offers_accepted_count, 1)
        self.assertEqual(provider.metrics.jobs_accepted, 1)
        self.assertAlmostEqual(provider.metrics.avg_response_time, 2.0, places=2)
        self.assertAlmostEqual(provider.metrics.acceptance_rate, 0.5, places=4)
        self.assertGreater(provider.base_dispatch_score, 0.0)
