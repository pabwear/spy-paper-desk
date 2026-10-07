"""The projection learns from each day's results: logged once per candle, scored when it closes, folded into the model."""

from __future__ import annotations

from datetime import timedelta

from helpers import THURSDAY, DeskTestCase, at

import projection_log
from common import load_json
from studies import forecast


def minute_bars(day, start=(9, 30), end=(16, 0), price=600.0, step=0.01):
    t, stop, out, p = at(day, *start), at(day, *end), [], price
    while t < stop:
        out.append({"t": t, "o": p, "h": p + 0.05, "l": p - 0.05, "c": p, "v": 1000.0})
        p += step
        t += timedelta(minutes=1)
    return out


class Source:
    def __init__(self, bars):
        self.bars = bars

    def get(self, symbol):
        return self.bars, None

    def history(self, symbol, unit):
        return None


def chart_with(last_row_time: str, last: float, target: str, experts: dict) -> dict:
    proj = {"version": 2, "horizon": 2, "t": ["x", target], "last": last,
            **{f"q{q}": [0, v] for q, v in zip(forecast.QUANTILES, (last - 1, last - 0.5, last + 0.2, last + 0.9, last + 1.4))},
            "experts": experts, "weights": {e: 1 / 6 for e in forecast.EXPERTS}, "bands": {"half80": 1.2, "s50": 1.0, "s80": 1.0}}
    return {"SPY": {"frames": {"5m": {"c": [[last_row_time, last, last, last, last, 1]], "projection": proj}}, "frames_eth": {}}}


class LogTests(DeskTestCase):
    experts = {e: 600.0 for e in forecast.EXPERTS} | {"momentum": 600.6}

    def test_logged_once_per_candle(self):
        c = chart_with("2026-10-01T10:00", 600.0, "2026-10-01T10:10", self.experts)
        self.assertEqual(projection_log.record(at(THURSDAY, 10, 2), c, False), 1)
        self.assertEqual(projection_log.record(at(THURSDAY, 10, 4), c, False), 0)
        self.assertEqual(len(projection_log.read()), 1)

    def test_scored_only_after_its_last_candle_closes(self):
        projection_log.record(at(THURSDAY, 10, 2), chart_with("2026-10-01T10:00", 600.0, "2026-10-01T10:10", self.experts), False)
        bars = minute_bars(THURSDAY)  # +0.01 a minute from 600.00 at 09:30
        self.assertEqual(projection_log.score(at(THURSDAY, 10, 12), Source(bars)), 0)  # 10:10 candle still open
        self.assertEqual(projection_log.score(at(THURSDAY, 10, 16), Source(bars)), 1)
        r = projection_log.read()[0]
        self.assertAlmostEqual(r["actual"], 600.0 + 0.01 * 44, places=4)  # the 10:14 minute closes the 10:10 candle
        self.assertTrue(r["direction_hit"])  # it called up, price went up
        self.assertTrue(r["inside_80"])
        self.assertLess(r["expert_loss"]["momentum"], r["expert_loss"]["flat"])

    def test_learning_rewards_the_expert_that_was_closest(self):
        bars = minute_bars(THURSDAY)
        for k in range(12):  # twelve 5-minute projections through the morning
            t0 = at(THURSDAY, 10, 0) + timedelta(minutes=5 * k)
            last = 600.0 + 0.01 * (t0 - at(THURSDAY, 9, 30)).seconds / 60
            tgt = (t0 + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M")
            exp = {e: last for e in forecast.EXPERTS} | {"momentum": last + 0.15}
            projection_log.record(t0, chart_with(t0.strftime("%Y-%m-%dT%H:%M"), last, tgt, exp), False)
        now = at(THURSDAY, 12, 0)
        projection_log.score(now, Source(bars))
        model = projection_log.learn(now)["5m"]
        self.assertEqual(model["n"], 12)
        self.assertEqual(model["leader"], "momentum")
        self.assertEqual(projection_log.learn(now)["5m"]["n"], 12)  # learning twice doesn't double count
        self.assertEqual(projection_log.daily_summary(now)["5m"]["scored"], 12)
        self.assertIn("5m", load_json("projection_model.json"))
