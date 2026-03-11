from django.test import SimpleTestCase

from providers.utils_geo_grid import compute_geo_grid, grid_window_for_radius


class GeoGridUtilsTests(SimpleTestCase):
    def test_compute_geo_grid_returns_integer_cell_coordinates(self):
        grid_lat, grid_lng = compute_geo_grid(45.5001, -73.6102)

        self.assertEqual(grid_lat, 910)
        self.assertEqual(grid_lng, -1473)

    def test_grid_window_for_radius_expands_conservatively_for_30km(self):
        window = grid_window_for_radius(45.5601, -73.7124, radius_km=30)

        self.assertEqual(window["grid_lat"], 911)
        self.assertEqual(window["grid_lng"], -1475)
        self.assertLessEqual(window["min_grid_lat"], 905)
        self.assertGreaterEqual(window["max_grid_lat"], 917)
        self.assertLessEqual(window["min_grid_lng"], -1483)
        self.assertGreaterEqual(window["max_grid_lng"], -1467)
