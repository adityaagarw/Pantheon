import unittest

from analytics.sessions import bounce_rate, sessionize, top_pages


def ev(user, ts, page):
    return {"user": user, "ts": ts, "page": page}


class SessionTest(unittest.TestCase):
    def test_gap_splits_sessions(self):
        events = [ev("a", 0, "/"), ev("a", 1800, "/x"), ev("a", 3601, "/y")]
        sessions = sessionize(events)
        self.assertEqual([s["pages"] for s in sessions], [["/", "/x"], ["/y"]])

    def test_unsorted_input(self):
        events = [ev("b", 100, "/2"), ev("a", 50, "/1"), ev("b", 10, "/0")]
        sessions = sessionize(events)
        self.assertEqual([(s["user"], s["start"]) for s in sessions], [("b", 10), ("a", 50)])
        self.assertEqual(sessions[0]["pages"], ["/0", "/2"])

    def test_bounce_rate_and_top_pages(self):
        sessions = sessionize([ev("a", 0, "/"), ev("b", 0, "/"), ev("b", 5, "/x"), ev("c", 0, "/p")])
        self.assertAlmostEqual(bounce_rate(sessions), 2 / 3)
        self.assertEqual(top_pages(sessions, 2), [("/", 2), ("/p", 1)])


if __name__ == "__main__":
    unittest.main()
