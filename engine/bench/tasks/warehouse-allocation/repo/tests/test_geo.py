import unittest

from fulfil.geo import distance_km


class GeoTest(unittest.TestCase):
    def test_london_paris(self):
        self.assertAlmostEqual(distance_km(51.5072, -0.1276, 48.8566, 2.3522), 343.9, delta=1)

    def test_same_place(self):
        self.assertEqual(distance_km(10, 10, 10, 10), 0)


if __name__ == "__main__":
    unittest.main()
