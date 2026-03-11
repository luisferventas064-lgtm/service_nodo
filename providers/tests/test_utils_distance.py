from types import SimpleNamespace

from django.test import SimpleTestCase

from providers.utils_distance import haversine_distance_km, providers_within_radius


class HaversineDistanceTests(SimpleTestCase):
    def test_returns_zero_for_same_coordinates(self):
        self.assertEqual(
            haversine_distance_km(45.5601, -73.7124, 45.5601, -73.7124),
            0.0,
        )

    def test_returns_expected_distance_for_laval_to_montreal(self):
        distance_km = haversine_distance_km(
            45.5601,
            -73.7124,
            45.5017,
            -73.5673,
        )

        self.assertAlmostEqual(distance_km, 13.04, places=2)

    def test_providers_within_radius_returns_only_nearby_providers(self):
        job_location = SimpleNamespace(latitude=45.5601, longitude=-73.7124)
        near_provider = SimpleNamespace(
            provider_id=1,
            location=SimpleNamespace(latitude=45.5610, longitude=-73.7130),
        )
        far_provider = SimpleNamespace(
            provider_id=2,
            location=SimpleNamespace(latitude=46.8139, longitude=-71.2080),
        )
        no_location_provider = SimpleNamespace(provider_id=3)

        results = providers_within_radius(
            job_location,
            [near_provider, far_provider, no_location_provider],
            radius_km=30,
        )

        self.assertEqual(results, [(near_provider, results[0][1])])
        self.assertLess(results[0][1], 30)
