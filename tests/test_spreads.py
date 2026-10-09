"""Daily credit spreads: strikes, credit, settlement, the stop and take-profit, costs and the capped worst case."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

import spreads
from common import ET

FRI = date(2026, 10, 2)   # a day with a same-day SPY expiry
THU_2021 = date(2021, 3, 4)  # before Nov 2022, Thursdays had none


def day(d, closes):
    t0 = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append({"t": t0 + timedelta(minutes=i), "o": prev, "h": max(prev, c), "l": min(prev, c), "c": c, "v": 1.0})
        prev = c
    return out


SPEC = {**spreads.BASE}


class SpreadTests(unittest.TestCase):
    def test_strikes_sit_below_the_price_and_cap_the_loss(self):
        lg = spreads.legs(SPEC, 600.0, 4.0)
        self.assertEqual(lg, [("put", 596, 591)])
        self.assertEqual(spreads.legs({**SPEC, "calls": True}, 600.0, 4.0), [("put", 596, 591), ("call", 604, 609)])

    def test_quiet_day_keeps_the_credit(self):
        t = spreads.day_trade(SPEC, FRI, day(FRI, [600.0] * 390), 0.16)
        self.assertEqual(t["why"], "expiry")
        self.assertAlmostEqual(t["net"], t["credit"] - 3.0, places=2)
        self.assertGreater(t["credit"], 5.0)

    def test_crash_loses_at_most_the_width(self):
        closes = [600.0] * 30 + [600.0 - 0.2 * i for i in range(360)]  # ends 528
        t = spreads.day_trade(SPEC, FRI, day(FRI, closes), 0.16)
        self.assertEqual(t["why"], "expiry")
        self.assertAlmostEqual(t["net"], t["credit"] - 500.0 - 3.0, places=2)
        self.assertLessEqual(-t["net"], t["worst_case"])

    def test_stop_closes_early_and_pays_to_close(self):
        closes = [600.0] * 30 + [600.0 - 0.05 * i for i in range(360)]
        t = spreads.day_trade({**SPEC, "stop_x": 2.0}, FRI, day(FRI, closes), 0.16)
        self.assertEqual(t["why"], "stop")
        self.assertLess(t["net"], -t["credit"] + 0.01)

    def test_take_profit(self):
        t = spreads.day_trade({**SPEC, "take": 0.5}, FRI, day(FRI, [600.0] * 30 + [600.5] * 360), 0.16)
        self.assertEqual(t["why"], "take")
        self.assertAlmostEqual(t["net"], round(t["credit"] / 2 - 6.0, 2), delta=t["credit"] * 0.2)

    def test_no_same_day_expiry_no_trade(self):
        self.assertIsNone(spreads.day_trade(SPEC, THU_2021, day(THU_2021, [400.0] * 390), 0.16))

    def test_summary_counts_days_weeks_months(self):
        trades = [{"t": "2026-10-01T10:00", "net": 20.0, "usd": 20.0, "opt": 20.0, "why": "expiry", "credit": 23.0,
                   "worst_case": 480.0},
                  {"t": "2026-10-02T10:00", "net": -100.0, "usd": -100.0, "opt": -100.0, "why": "expiry",
                   "credit": 23.0, "worst_case": 480.0}]
        s = spreads.summary(trades)
        self.assertEqual((s["days_won_pct"], s["weeks_won_pct"], s["months_won_pct"]), (50.0, 0.0, 0.0))


class DeskCloseTests(unittest.TestCase):
    def test_vol_mult_prices_lower_but_keeps_the_strikes(self):
        bars = day(FRI, [600.0] * 390)
        full = spreads.day_trade({**SPEC, "sd": 0.5}, FRI, bars, 0.16)
        real = spreads.day_trade({**SPEC, "sd": 0.5, "vol_mult": 0.5}, FRI, bars, 0.16)
        self.assertEqual(full["legs"], real["legs"])
        self.assertLess(real["credit"], full["credit"])

    def test_a_crash_still_costs_the_full_width_at_real_prices(self):
        drop = [600.0] * 30 + [600.0 - i * 0.2 for i in range(1, 361)]  # 72 points down by the close
        t = spreads.day_trade({**SPEC, "sd": 0.5, "vol_mult": 0.5, "close_at": "15:40"}, FRI, day(FRI, drop), 0.16)
        self.assertLess(t["net"], -400)

    def test_bought_back_at_1540_pays_to_close(self):
        t = spreads.day_trade({**SPEC, "close_at": "15:40"}, FRI, day(FRI, [600.0] * 390), 0.16)
        self.assertEqual(t["why"], "close")
        self.assertLess(t["net"], t["credit"] - 6.0 + 0.01)  # open and close costs, plus what's left of its value
        self.assertGreater(t["net"], 0)
