"""The call/put area backtest: break vs bounce entries, stops, the 15:40 flat, the touch rule."""

from __future__ import annotations

import unittest
from datetime import timedelta

from helpers import THURSDAY, at

import backtest_areas as bt

RED = {"color": "red", "low": 101.0, "high": 101.2}
GREEN = {"color": "green", "low": 98.8, "high": 99.0}


def day(*ohlc, start=(9, 50)):
    t = at(THURSDAY, *start)
    return [{"t": t + timedelta(minutes=5 * i), "o": o, "h": h, "l": lo, "c": c, "v": 1.0} for i, (o, h, lo, c) in enumerate(ohlc)]


class ConfirmTests(unittest.TestCase):
    def test_bounce_off_the_red_area_buys_a_put(self):
        k = day((100.5, 100.6, 100.4, 100.5), (100.5, 100.7, 100.4, 100.6),  # 09:50, 09:55: below the area
                (100.6, 101.1, 100.6, 100.8),                                 # 10:00: pokes in, closes back below
                (100.8, 100.8, 100.0, 100.1), (100.1, 100.2, 100.0, 100.1))
        tr = bt.simulate_day(k, [RED], "confirm")
        self.assertEqual((tr[0]["kind"], tr[0]["contract"], tr[0]["entry"]), ("bounce", "put", 100.8))
        self.assertGreater(tr[0]["usd"], 0)

    def test_close_through_the_red_area_buys_a_call(self):
        k = day((100.5, 100.6, 100.4, 100.5), (100.5, 100.7, 100.4, 100.6),
                (100.6, 101.4, 100.6, 101.3),                                 # closes above 101.2: breakout
                (101.3, 101.9, 101.3, 101.8))
        tr = bt.simulate_day(k, [RED], "confirm")
        self.assertEqual((tr[0]["kind"], tr[0]["contract"]), ("break", "call"))

    def test_closing_inside_waits(self):
        k = day((100.5, 100.6, 100.4, 100.5), (100.5, 100.7, 100.4, 100.6),
                (100.6, 101.1, 100.6, 101.1),                                 # closes inside: no trade yet
                (101.1, 101.15, 100.7, 100.75))                               # back out below: bounce
        tr = bt.simulate_day(k, [RED], "confirm")
        self.assertEqual(len(tr), 1)
        self.assertEqual((tr[0]["kind"], tr[0]["t"][11:16]), ("bounce", "10:05"))

    def test_the_stop_and_target_one(self):
        k = day((99.5, 99.6, 99.4, 99.5), (99.5, 99.6, 99.4, 99.5),
                (99.5, 99.5, 98.9, 99.2),                                     # bounce up off green → call at 99.2
                (99.2, 99.6, 99.15, 99.55))                                   # +0.35 % = 99.547: target 1
        self.assertEqual(bt.simulate_day(k, [GREEN], "confirm_t1")[0]["why"], "target")
        k2 = k[:3] + day((99.2, 99.2, 98.7, 98.8), start=(10, 5))             # −0.35 % = 98.853: stopped
        t2 = bt.simulate_day(k2, [GREEN], "confirm")[0]
        self.assertEqual((t2["why"], t2["exit"]), ("stop", 98.85))
        self.assertAlmostEqual(t2["usd"], -0.35 * 50, delta=1)

    def test_flat_by_the_cutoff(self):
        k = day((100.5, 100.6, 100.4, 100.5), (100.5, 101.4, 100.4, 101.3), (101.3, 101.4, 101.25, 101.35),
                start=(15, 30))
        tr = bt.simulate_day(k, [RED], "confirm", cutoff="15:40")
        self.assertEqual((tr[0]["why"], tr[0]["exit"]), ("close", 101.3))


class TouchTests(unittest.TestCase):
    def test_todays_rule_buys_the_call_at_red(self):
        k = day((100.5, 100.6, 100.4, 100.5), (100.5, 100.6, 100.4, 100.5), (100.6, 101.05, 100.6, 101.0),
                (101.0, 101.0, 100.4, 100.5))
        tr = bt.simulate_day(k, [RED], "touch")
        self.assertEqual((tr[0]["contract"], tr[0]["kind"]), ("call", "touch"))
        self.assertLess(tr[0]["usd"], 0)  # it bounced: the call lost

    def test_two_entries_a_day_at_most(self):
        k = day(*[(100.95, 101.1, 100.0, 100.0)] * 20)
        self.assertLessEqual(len(bt.simulate_day(k, [RED], "touch")), 2)


class SummaryTests(unittest.TestCase):
    def test_summary(self):
        s = bt.summarize([{"t": "2026-10-01T10:00", "usd": 20.0, "why": "close"},
                          {"t": "2026-10-01T11:00", "usd": -17.5, "why": "stop"},
                          {"t": "2026-10-02T10:00", "usd": -10.0, "why": "close"}])
        self.assertEqual((s["trades"], s["win_pct"], s["total"], s["max_drawdown"]), (3, 33.3, -7.5, -27.5))
        self.assertEqual(s["worst_day"], -10.0)
