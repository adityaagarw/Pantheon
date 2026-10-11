import copy
import json
import os
import random
import subprocess
import sys
import tempfile
import textwrap
import unittest

import _ref_sessions
from analytics import sessions as new

PERF_SCRIPT = textwrap.dedent("""
    import json, random, sys, time
    sys.path.insert(0, sys.argv[1])
    from analytics.sessions import sessionize, bounce_rate, top_pages
    rng = random.Random(42)
    events = []
    pages = ["/", "/pricing", "/docs", "/blog", "/login", "/signup", "/cart", "/checkout"]
    for u in range(8000):
        ts = rng.randint(0, 6 * 86400)
        for _ in range(25):
            ts += rng.choice([5, 30, 120, 600, 1700, 1800, 1801, 4000, 20000])
            events.append({"user": f"u{u}", "ts": ts, "page": rng.choice(pages)})
    rng.shuffle(events)
    start = time.perf_counter()
    result = sessionize(events)
    rate = bounce_rate(result)
    top = top_pages(result, 5)
    print(json.dumps({"seconds": time.perf_counter() - start, "sessions": len(result), "events": len(events)}))
""")


def random_events(seed, n, users, span, pages=6):
    rng = random.Random(seed)
    return [{"user": f"u{rng.randrange(users)}", "ts": rng.randrange(span), "page": f"/p{rng.randrange(pages)}"}
            for _ in range(n)]


class CorrectnessTest(unittest.TestCase):
    def compare(self, events, gap=30):
        expected = _ref_sessions.sessionize(copy.deepcopy(events), gap)
        actual = new.sessionize(copy.deepcopy(events), gap)
        self.assertEqual(actual, expected)
        return expected

    def test_random_small(self):
        for seed in range(20):
            self.compare(random_events(seed, 300, 8, 20000))

    def test_many_ties(self):
        for seed in range(10):
            self.compare(random_events(100 + seed, 400, 5, 50))

    def test_exact_gap_boundary(self):
        events = [{"user": "a", "ts": t, "page": str(t)} for t in (0, 1800, 3600, 5401, 7201)]
        self.compare(events)
        self.assertEqual(len(new.sessionize(events)), 2)

    def test_other_gaps(self):
        for gap in (0, 1, 5, 240):
            self.compare(random_events(200 + gap, 300, 6, 30000), gap)

    def test_same_start_sorted_by_user(self):
        events = [{"user": u, "ts": 100, "page": "/"} for u in ("zed", "amy", "bob")]
        self.assertEqual([s["user"] for s in self.compare(events)], ["amy", "bob", "zed"])

    def test_empty_and_single(self):
        self.assertEqual(new.sessionize([]), [])
        self.compare([{"user": "a", "ts": 5, "page": "/"}])

    def test_input_not_changed(self):
        events = random_events(7, 200, 5, 10000)
        before = copy.deepcopy(events)
        new.sessionize(events)
        self.assertEqual(events, before)

    def test_bounce_rate(self):
        for seed in range(10):
            result = _ref_sessions.sessionize(random_events(300 + seed, 300, 10, 40000))
            self.assertAlmostEqual(new.bounce_rate(copy.deepcopy(result)), _ref_sessions.bounce_rate(result))
        self.assertEqual(new.bounce_rate([]), 0.0)

    def test_top_pages(self):
        for seed in range(10):
            result = _ref_sessions.sessionize(random_events(400 + seed, 300, 10, 40000, pages=4))
            for n in (1, 3, 10):
                self.assertEqual(list(map(tuple, new.top_pages(copy.deepcopy(result), n))), _ref_sessions.top_pages(result, n))


class SpeedTest(unittest.TestCase):
    def test_200k_events_under_3_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "perf.py")
            with open(script, "w", encoding="utf-8") as f:
                f.write(PERF_SCRIPT)
            try:
                proc = subprocess.run([sys.executable, script, os.environ["SWARMBENCH_WORKSPACE"]],
                                      capture_output=True, text=True, timeout=45)
            except subprocess.TimeoutExpired:
                self.fail("did not finish in 45 seconds")
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(data["events"], 200000)
        self.assertLess(data["seconds"], 3.0, data)
