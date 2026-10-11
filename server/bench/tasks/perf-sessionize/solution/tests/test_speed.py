import random
import time
import unittest

from analytics.sessions import bounce_rate, sessionize, top_pages


class SpeedTest(unittest.TestCase):
    def test_100k_events_fast(self):
        rng = random.Random(1)
        events = [{"user": f"u{rng.randrange(3000)}", "ts": rng.randrange(7 * 86400), "page": "/"} for _ in range(100_000)]
        start = time.perf_counter()
        result = sessionize(events)
        bounce_rate(result)
        top_pages(result)
        self.assertLess(time.perf_counter() - start, 2.0)

    def test_same_time_keeps_input_order(self):
        events = [{"user": "a", "ts": 5, "page": p} for p in ("/1", "/2", "/3")]
        self.assertEqual(sessionize(events)[0]["pages"], ["/1", "/2", "/3"])


if __name__ == "__main__":
    unittest.main()
