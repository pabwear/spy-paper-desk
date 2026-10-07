"""The research engine: one position at a time, stops held at the broker vs checked every 10 minutes, holds
until a time, the close, the next open or several days, options priced with decay, and the locked final year."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

import research
from common import ET

D1, D2, D3 = date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 5)


def session(d, closes, start=(9, 30)):
    t0 = datetime(d.year, d.month, d.day, *start, tzinfo=ET)
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append({"t": t0 + timedelta(minutes=i), "o": prev, "h": max(prev, c) + 0.01, "l": min(prev, c) - 0.01,
                    "c": c, "v": 1000.0})
        prev = c
    return out


def ctx_of(*days_closes, entries=None):
    ctx = research.Ctx()
    for d, closes in days_closes:
        bars = session(d, closes)
        info = {"vol": 0.16, "vix": 16.0, "trend_up": True, "range_pct": 1.0, "touch": [], "bounce": [], "ten": [],
                "close": [(bars[-1]["t"], bars[-1]["c"])]}
        info.update((entries or {}).get(d, {}))
        ctx.add_day(d, bars, info)
    return ctx


def bar_t(d, hh, mm):
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)


FLAT = [500.0] * 390


class EngineTests(unittest.TestCase):
    def test_shares_held_30_minutes_in_whole_shares(self):
        closes = [500.0] * 30 + [500.0 + 0.05 * i for i in range(360)]
        ctx = ctx_of((D1, closes), entries={D1: {"touch": [(bar_t(D1, 9, 59), 500.0)]}})
        tr = research.simulate({"entry": "touch", "asset": "shares", "budget": 4000, "stop": {"pct": 0.25},
                                "hold": {"minutes": 30}}, ctx)
        self.assertEqual(len(tr), 1)
        t = tr[0]
        self.assertEqual((t["why"], t["exit_t"][11:16]), ("time", "10:30"))
        self.assertAlmostEqual(t["net"], round((t["exit"] - 500.0) * 8 - 0.02 * 8, 2), places=1)  # 8 shares of $500

    def test_broker_stop_fills_at_its_level_desk_check_at_the_close(self):
        closes = [500.0] * 30 + [499.5, 499.0, 498.5, 498.0, 497.5, 497.0, 496.5, 496.0, 495.5, 495.0] + [495.0] * 350
        ent = {D1: {"touch": [(bar_t(D1, 9, 59), 500.0)]}}
        base = {"entry": "touch", "asset": "shares", "budget": 4000, "stop": {"pct": 0.25}, "hold": {"until": "close"}}
        held = research.simulate({**base, "checks": 0}, ctx_of((D1, closes), entries=ent))[0]
        self.assertEqual((held["why"], held["exit"]), ("stop", 498.75))
        looked = research.simulate({**base, "checks": 10}, ctx_of((D1, closes), entries=ent))[0]
        self.assertEqual((looked["why"], looked["exit"], looked["exit_t"][11:16]), ("stop", 495.0, "10:10"))

    def test_close_to_next_open(self):
        d2 = [503.0] + [503.0] * 389
        ctx = ctx_of((D1, FLAT), (D2, d2))
        ctx.flat[ctx.first[1]]["o"] = 503.0
        tr = research.simulate({"entry": "close", "asset": "shares", "budget": 4000, "hold": {"until": "next_open"}}, ctx)
        self.assertEqual((tr[0]["why"], tr[0]["exit"], tr[0]["days_held"]), ("open", 503.0, 1))
        self.assertAlmostEqual(tr[0]["net"], 3.0 * 8 - 0.16, places=2)

    def test_an_option_held_overnight_pays_the_nights_decay(self):
        ctx = ctx_of((D1, FLAT), (D2, FLAT))
        tr = research.simulate({"entry": "close", "asset": "option", "budget": 3000, "expiry_days": 30,
                                "hold": {"until": "next_open"}}, ctx)
        self.assertLess(tr[0]["net"], -5.0)  # the price didn't move: a night's decay plus the $5 cost

    def test_one_position_at_a_time_and_trend_filter(self):
        ent = {D1: {"touch": [(bar_t(D1, 9, 59), 500.0), (bar_t(D1, 10, 9), 500.0)]}}
        spec = {"entry": "touch", "asset": "shares", "budget": 4000, "stop": {"pct": 0.25}, "hold": {"minutes": 30}}
        self.assertEqual(len(research.simulate(spec, ctx_of((D1, FLAT), entries=ent))), 1)  # second came while held
        ctx = ctx_of((D1, FLAT), entries=ent)
        ctx.info[D1]["trend_up"] = False
        self.assertEqual(research.simulate({**spec, "filter": {"trend": True}}, ctx), [])

    def test_swing_trails_across_days(self):
        d1 = [500.0] * 30 + [500.0 + 0.02 * i for i in range(360)]  # ends ~507.2
        d2 = [507.0] * 10 + [507.0 - 0.1 * i for i in range(380)]
        ctx = ctx_of((D1, d1), (D2, d2), (D3, FLAT), entries={D1: {"touch": [(bar_t(D1, 9, 59), 500.0)]}})
        tr = research.simulate({"entry": "touch", "asset": "shares", "budget": 4000, "stop": {"pct": 0.5},
                                "trail": True, "hold": {"days": 10}}, ctx)[0]
        self.assertEqual((tr["why"], tr["days_held"]), ("stop", 1))
        self.assertGreater(tr["net"], 0)

    def test_yardstick_and_the_locked_year(self):
        ctx = ctx_of((D1, FLAT), (D2, [505.0] * 390))
        y = research.yardstick(ctx, {D2}, 4000)
        self.assertEqual(y[0]["net"], 40.0)
        days = {date(2025, 10, 6): [1], research.HOLDOUT_START: [2], date(2026, 1, 2): [3]}
        self.assertEqual(list(research.lock_holdout(days)), [date(2025, 10, 6)])

    def test_rounds_are_well_formed(self):
        for name, specs in research.ROUNDS.items():
            names = [s["name"] for s in specs]
            self.assertEqual(len(names), len(set(names)), name)
            for s in specs:
                self.assertIn(s["entry"], ("touch", "bounce", "ten", "close"))
                self.assertIn(s["asset"], ("shares", "option"))
