from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from providers.utils_ranking import (
    dispatch_score_from_base,
    fairness_score,
    provider_base_dispatch_score,
    provider_ranking_score,
)


class ProviderRankingScoreTests(SimpleTestCase):
    def test_returns_top_score_for_best_possible_inputs(self):
        self.assertAlmostEqual(
            provider_ranking_score(
                distance_km=0,
                rating=5,
                response_minutes=0,
                acceptance_rate=1,
                completion_rate=1,
            ),
            1.0,
        )

    def test_uses_neutral_response_when_response_time_is_missing(self):
        self.assertEqual(
            provider_ranking_score(distance_km=50, rating=0, response_minutes=None),
            0.175,
        )

    def test_farther_higher_rated_provider_can_score_better(self):
        close_low_rated = provider_ranking_score(distance_km=1, rating=1, response_minutes=None)
        farther_high_rated = provider_ranking_score(distance_km=13, rating=5, response_minutes=None)

        self.assertGreater(farther_high_rated, close_low_rated)

    def test_fairness_score_caps_at_one_after_four_hours(self):
        now = timezone.now()

        self.assertEqual(fairness_score(None, now=now), 1.0)
        self.assertEqual(
            fairness_score(now - timedelta(hours=4), now=now),
            1.0,
        )
        self.assertEqual(
            fairness_score(now - timedelta(hours=2), now=now),
            0.5,
        )

    def test_provider_waiting_longer_gets_fairness_boost(self):
        now = timezone.now()
        recently_assigned = provider_ranking_score(
            distance_km=0,
            rating=5,
            response_minutes=0,
            acceptance_rate=1,
            completion_rate=1,
            last_job_assigned_at=now,
            now=now,
        )
        stale_provider = provider_ranking_score(
            distance_km=0,
            rating=5,
            response_minutes=0,
            acceptance_rate=1,
            completion_rate=1,
            last_job_assigned_at=now - timedelta(hours=4),
            now=now,
        )

        self.assertGreater(stale_provider, recently_assigned)

    def test_learning_signals_improve_score(self):
        base_score = provider_ranking_score(
            distance_km=5,
            rating=4.5,
            response_minutes=20,
            acceptance_rate=0.1,
            completion_rate=0.1,
        )
        learned_score = provider_ranking_score(
            distance_km=5,
            rating=4.5,
            response_minutes=20,
            acceptance_rate=0.9,
            completion_rate=0.95,
        )

        self.assertGreater(learned_score, base_score)

    def test_provider_base_dispatch_score_uses_only_static_components(self):
        score = provider_base_dispatch_score(
            rating=5,
            response_minutes=0,
            acceptance_rate=1,
            completion_rate=1,
        )

        self.assertAlmostEqual(score, 0.55)

    def test_dispatch_score_from_base_reconstructs_final_score_without_random(self):
        final_score = provider_ranking_score(
            distance_km=10,
            rating=4.5,
            response_minutes=5,
            acceptance_rate=0.8,
            completion_rate=0.9,
            last_job_assigned_at=None,
        )
        reconstructed = dispatch_score_from_base(
            base_dispatch_score=provider_base_dispatch_score(
                rating=4.5,
                response_minutes=5,
                acceptance_rate=0.8,
                completion_rate=0.9,
            ),
            distance_km=10,
            last_job_assigned_at=None,
            random_bonus=0.0,
        )

        self.assertAlmostEqual(final_score, reconstructed)
