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

    def test_extended_hours_run_four_to_eight(self):
        self.assertEqual(forecast.future_times("2026-10-01T19:55", 2, 5, regular_hours_only=False),
                         ["2026-10-02T04:00", "2026-10-02T04:05"])
        self.assertEqual(forecast.future_times("2026-10-01T15:55", 1, 5, regular_hours_only=False), ["2026-10-01T16:00"])

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
        self.assertTrue(0.8 <= p["bands"]["s80"] <= 3.0)  # widened or narrowed to match past misses, within limits
        self.assertAlmostEqual(sum(p["weights"].values()), 1.0, places=2)
        self.assertEqual(p["leader"], max(p["weights"], key=p["weights"].get))
        self.assertEqual(p["weights_source"], "history")
        self.assertEqual(set(p["experts"]), set(forecast.EXPERTS))

    def test_live_model_takes_over(self):
        live = {"n": forecast.MIN_LIVE_BANDS, "weights": {e: (0.9 if e == "flat" else 0.02) for e in forecast.EXPERTS},
                "s50": 1.5, "s80": 2.0}
        p = forecast.project(self.closes, self.times, "5m", 5, model=live)
        self.assertEqual(p["weights_source"], "live")
        self.assertEqual(p["leader"], "flat")
        self.assertEqual((p["bands"]["s80"], p["bands"]["source"]), (2.0, "live"))
        few = forecast.project(self.closes, self.times, "5m", 5, model={**live, "n": 3})
        self.assertEqual(few["weights_source"], "history")  # three scored projections aren't enough to trust


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


class HedgeTests(unittest.TestCase):
    def test_weight_moves_to_the_better_expert(self):
        even = {e: 1 / len(forecast.EXPERTS) for e in forecast.EXPERTS}
        w = forecast.hedge(even, {e: (0.1 if e == "momentum" else 1.5) for e in forecast.EXPERTS})
        self.assertEqual(max(w, key=w.get), "momentum")
        self.assertAlmostEqual(sum(w.values()), 1.0, places=3)
        for _ in range(200):
            w = forecast.hedge(w, {e: (0.0 if e == "momentum" else 2.0) for e in forecast.EXPERTS})
        self.assertGreaterEqual(min(w.values()), forecast.FLOOR * 0.9)  # nobody is written off for good


class PlanTests(unittest.TestCase):
    """Odds for the desk's zone trades along given paths (log returns from now)."""

    def paths(self, *finals, n=10):
        return np.array([np.linspace(0, np.log(1 + f / 100), n) for f in finals])

    def test_inside_the_zone_and_rising(self):
        o = forecast.plan_trade(self.paths(1.0, 1.0), 100.0, 99.5, 100.5, "buy", 0.35)
        self.assertEqual((o["reach_pct"], o["win_if_entered_pct"], o["profit_pct"]), (100.0, 100.0, 100.0))
        self.assertTrue(o["inside_now"])
        self.assertGreater(o["option_est"]["expected"], 0)

    def test_the_stop(self):
        o = forecast.plan_trade(self.paths(-1.0, -1.0, 1.0, 1.0), 100.0, 99.5, 100.5, "buy", 0.35)
        self.assertEqual((o["stopped_if_entered_pct"], o["win_if_entered_pct"]), (50.0, 50.0))
        self.assertEqual(o["option_est"]["at_stop"], -round(0.0035 * 100 * 50))  # 0.35% of $100 × 50 option-dollars per $1

    def test_waiting_for_price_to_reach_the_zone(self):
        # price 101 above a 99.5-100.5 buy zone: only paths that fall to 100.5 get a trade
        o = forecast.plan_trade(self.paths(-1.0, 1.0, 1.0, 1.0), 101.0, 99.5, 100.5, "buy", 0.35)
        self.assertEqual(o["reach_pct"], 25.0)
        self.assertEqual(o["entry"], 100.5)
        self.assertEqual(o["profit_pct"], 0.0)  # the one path that got there kept falling: stopped

    def test_puts_profit_when_price_falls(self):
        o = forecast.plan_trade(self.paths(-1.0, -1.0), 100.0, 99.5, 100.5, "sell", 0.35)
        self.assertEqual(o["win_if_entered_pct"], 100.0)
        self.assertEqual(o["stop"], 100.35)

    def test_never_reached(self):
        o = forecast.plan_trade(self.paths(0.1, 0.2), 100.0, 90.0, 91.0, "sell", 0.35)
        self.assertEqual((o["reach_pct"], o["profit_pct"]), (0.0, 0.0))


class SuggestTests(unittest.TestCase):
    def paths(self, *finals, n=10):
        return np.array([np.linspace(0, np.log(1 + f / 100), n) for f in finals])

    def test_picks_the_entry_with_the_best_expected_result(self):
        o = forecast.suggest(self.paths(1.0, 1.0, -0.2), 100.0, 99.5, 100.5, "buy", 0.35, 100.0)
        names = [r["name"] for r in o["entries"]]
        self.assertEqual(names[0], "price now")  # inside the zone: buying now is one of the choices
        best = o["entries"][o["suggested"]]
        self.assertEqual(best["expected"], max(r["expected"] for r in o["entries"]))
        self.assertEqual(o["targets"][0]["price"], round(best["price"] * 1.0035, 2))
        self.assertEqual(o["stop"], round(best["price"] * 0.9965, 2))

    def test_targets_hit_before_the_stop(self):
        o = forecast.suggest(self.paths(1.0, 1.0), 100.0, 99.5, 100.5, "buy", 0.35, 100.0)
        best = o["entries"][o["suggested"]]
        self.assertEqual((best["target1_pct"], best["target2_pct"]), (100.0, 100.0))  # +1% clears both 0.35% and 0.70%

    def test_size_against_the_risk_limit(self):
        o = forecast.suggest(self.paths(1.0), 600.0, 599.0, 601.0, "buy", 0.35, 100.0)
        size = o["size"]  # 0.35% of $600 × $50 per $1 ≈ $105 a contract: just over a $100 limit
        self.assertFalse(size["within_limit"])
        self.assertEqual(size["contracts_within_limit"], 0)
        self.assertAlmostEqual(size["stop_pct_that_fits"], 100 / (600 * 50) * 100, places=2)
        self.assertTrue(forecast.suggest(self.paths(1.0), 100.0, 99.5, 100.5, "buy", 0.35, 100.0)["size"]["within_limit"])


if __name__ == "__main__":
    unittest.main()


class AreaOutcomeTests(unittest.TestCase):
    """Once price reaches an area: does it break through, bounce back, or stall?"""

    def path(self, *pcts):
        return np.log(1 + np.array(pcts) / 100)

    def test_break_bounce_and_stall_above(self):
        # area 100.5–101 above a price of 100; bounce = 0.35% back below 100.5
        paths = np.array([self.path(0.3, 0.6, 0.9, 1.2, 1.5),      # through the top: break
                          self.path(0.3, 0.6, 0.4, 0.0, -0.2),     # touched, then back under 100.15: bounce
                          self.path(0.3, 0.6, 0.7, 0.6, 0.5),      # touched, went nowhere: stall
                          self.path(0.1, 0.2, 0.1, 0.2, 0.1)])     # never got there
        o = forecast.area_outcomes(paths, 100.0, 100.5, 101.0, above=True, bounce_pct=0.35)
        self.assertEqual(o["touched_pct"], 75.0)
        self.assertEqual((o["break_pct"], o["bounce_pct"], o["stall_pct"]), (33.3, 33.3, 33.3))

    def test_area_below_mirrors(self):
        paths = np.array([self.path(-0.3, -0.6, -1.2), self.path(-0.6, -0.1, 0.2)])
        o = forecast.area_outcomes(paths, 100.0, 99.0, 99.5, above=False, bounce_pct=0.35)
        self.assertEqual((o["break_pct"], o["bounce_pct"]), (50.0, 50.0))

    def test_never_touched(self):
        o = forecast.area_outcomes(np.array([self.path(0.1, 0.1)]), 100.0, 105.0, 106.0, above=True, bounce_pct=0.35)
        self.assertEqual(o["touched_pct"], 0.0)
        self.assertIsNone(o["break_pct"])

    def test_plans_carry_the_bounce_trade(self):
        import charts
        from datetime import datetime, timedelta
        from common import ET

        start = datetime(2026, 9, 21, 9, 30, tzinfo=ET)
        bars, p = [], 600.0
        for d in range(8):
            day = start + timedelta(days=d)
            if day.weekday() >= 5:
                continue
            for m in range(390):
                p += 0.05 if (m // 30) % 2 == 0 else -0.05
                bars.append({"t": day + timedelta(minutes=m), "o": p, "h": p + 0.1, "l": p - 0.1, "c": p, "v": 1000.0})
        now = bars[-1]["t"]
        last = bars[-1]["c"]
        zones = [{"color": "red", "low": last + 0.2, "high": last + 0.5}, {"color": "green", "low": last - 0.5, "high": last - 0.2}]
        out = charts.trade_plans(bars, now, {"timeframe_minutes": 5}, zones, "today's zones", 0.35)
        red = out["plans"][0]
        self.assertEqual(red["contract"], "call")
        self.assertEqual(red["bounce_trade"]["contract"], "put")  # the opposite bet at the same area
        self.assertIn("break_pct", red["outcomes"])
