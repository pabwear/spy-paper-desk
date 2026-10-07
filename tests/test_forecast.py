"""The pattern projection: honest ranges, no peeking at the future, real market hours."""

from __future__ import annotations

import random
import unittest

import helpers

import numpy as np

from studies import forecast

DESK = helpers.DESK  # importing helpers puts the desk on sys.path


def walk(n: int, seed: int = 7) -> list[float]:
    rnd, p, out = random.Random(seed), 600.0, []
    for _ in range(n):
        p *= 1 + rnd.gauss(0, 0.002)
        out.append(p)
    return out


def minute_times(n: int) -> list[str]:
    return forecast.future_times("2026-09-01T09:29", n, 5)


class FutureTimeTests(unittest.TestCase):
    def test_intraday_skips_nights_and_weekends(self):
        # Friday 15:55 on 5-minute candles → Monday's open
        self.assertEqual(forecast.future_times("2026-10-02T15:55", 2, 5), ["2026-10-05T09:30", "2026-10-05T09:35"])
        self.assertEqual(forecast.future_times("2026-10-01T15:30", 2, 60), ["2026-10-02T09:30", "2026-10-02T10:30"])
        self.assertEqual(forecast.future_times("2026-10-01T13:30", 2, 240), ["2026-10-02T09:30", "2026-10-02T13:30"])

    def test_daily_skips_weekends(self):
        self.assertEqual(forecast.future_times("2026-10-02", 2, None), ["2026-10-05", "2026-10-06"])


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.closes = walk(600)
        self.times = minute_times(600)

    def test_bands_are_ordered_and_sized(self):
        p = forecast.project(self.closes, self.times, "5m", 5)
        self.assertEqual(len(p["t"]), forecast.HORIZON["5m"])
        for i in range(p["horizon"]):
            vals = [p[f"q{q}"][i] for q in forecast.QUANTILES]
            self.assertEqual(vals, sorted(vals))
        self.assertGreater(p["t"][0], self.times[-1])
        rec = p["record"]
        self.assertGreater(rec["tests"], 20)
        self.assertTrue(0 <= rec["direction_hit_pct"] <= 100)
        self.assertGreaterEqual(rec["widened_80"], 1.0)  # bands only ever get wider

    def test_too_little_history(self):
        self.assertIsNone(forecast.project(self.closes[:60], self.times[:60], "5m", 5))

    def test_no_peeking_at_later_prices(self):
        r = np.diff(np.log(np.asarray(self.closes)))
        end, w, h, k = 400, 20, 12, 25
        full_s, full_v = forecast._shapes(r, w)
        part_s, part_v = forecast._shapes(r[: end + 1], w)
        a = forecast._project(r, full_s, full_v, end, w, h, k)
        b = forecast._project(r[: end + 1], part_s, part_v, end, w, h, k)
        np.testing.assert_allclose(a, b)


if __name__ == "__main__":
    unittest.main()
