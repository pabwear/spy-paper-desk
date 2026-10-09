"""Intraday share strategies: the noise band, the VWAP/band exit, the last-half-hour rule, costs and the splits."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

import intraday
from common import ET


def day(d, closes):
    t0 = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append({"t": t0 + timedelta(minutes=i), "o": prev, "h": max(prev, c), "l": min(prev, c), "c": c, "v": 100.0})
        prev = c
    return out


D = date(2026, 10, 2)
FLAT_SIGMA = {m: 0.001 for m in range(1, 391)}  # a 0.1% band all day


class NoiseTests(unittest.TestCase):
    def test_breaks_out_above_the_band_and_holds_to_the_close(self):
        closes = [600.0] * 25 + [600.0 + 0.1 * i for i in range(1, 366)]  # climbs steadily from 09:55
        t = intraday.noise_day(D, day(D, closes), 600.0, FLAT_SIGMA, True)
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]["side"], t[0]["why"]), ("long", "close"))
        self.assertEqual(t[0]["entry"], closes[59])  # 10:00 is still inside the band (600.5 < 600.6); in at 10:30
        shares = int(4000 // closes[59])
        self.assertAlmostEqual(t[0]["net"], round((closes[-1] - closes[59]) * shares - 0.02 * shares, 2), places=2)

    def test_exits_when_it_falls_back_under_the_band_or_vwap(self):
        closes = [600.0] * 29 + [602.0] * 30 + [599.0] * 331  # up at 10:00, back down by 10:30
        t = intraday.noise_day(D, day(D, closes), 600.0, FLAT_SIGMA, False)
        self.assertEqual([(x["side"], x["why"], x["entry"], x["exit"]) for x in t], [("long", "band", 602.0, 599.0)])

    def test_shorts_only_when_allowed(self):
        closes = [600.0] * 25 + [600.0 - 0.1 * i for i in range(1, 366)]
        self.assertEqual(intraday.noise_day(D, day(D, closes), 600.0, FLAT_SIGMA, False), [])
        t = intraday.noise_day(D, day(D, closes), 600.0, FLAT_SIGMA, True)
        self.assertEqual(t[0]["side"], "short")
        self.assertGreater(t[0]["net"], 0)

    def test_sigma_is_the_average_move_from_the_open(self):
        a = day(D, [100.0, 101.0, 102.0])
        b = day(D, [100.0, 99.0, 100.0])
        s = intraday.noise_sigma([a, b])
        self.assertAlmostEqual(s[2], (0.01 + 0.01) / 2)
        self.assertAlmostEqual(s[3], (0.02 + 0.0) / 2)


class LastHalfTests(unittest.TestCase):
    def test_direction_from_10am_and_trade_the_last_half_hour(self):
        closes = [600.0] * 29 + [601.0] * 331 + [601.0 + 0.1 * i for i in range(1, 31)]
        t = intraday.last_half_day(D, day(D, closes), 600.0, True)
        self.assertEqual((t[0]["side"], t[0]["entry"], t[0]["exit"]), ("long", 601.0, closes[-1]))
        down = [600.0] * 29 + [599.0] * 361
        self.assertEqual(intraday.last_half_day(D, day(D, down), 600.0, False), [])
        self.assertEqual(intraday.last_half_day(D, day(D, down), 600.0, True)[0]["side"], "short")


class RunTests(unittest.TestCase):
    def test_runs_every_strategy_and_marks_passes(self):
        days = [D + timedelta(days=i) for i in range(30)]
        session = [(d, day(d, [600.0 + i] * 390)) for i, d in enumerate(days)]
        res = intraday.run(session)
        self.assertEqual(set(res["strategies"]), set(intraday.STRATEGIES))
        self.assertFalse(res["strategies"]["buy_hold"]["passes"])  # the yardstick never "passes"
        self.assertEqual(res["strategies"]["buy_hold"]["all"]["days_traded"], 30 - intraday.LOOKBACK)
