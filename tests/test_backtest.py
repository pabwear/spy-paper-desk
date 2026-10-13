"""The call/put area backtest: break vs bounce entries, the 2-signal check, stops, targets, the 15:40 flat."""

from __future__ import annotations

import random
import unittest
from datetime import timedelta

from helpers import THURSDAY, at

import backtest_areas as bt
from studies import auto
from common import load_json

RED = {"color": "red", "low": 101.0, "high": 101.2}
GREEN = {"color": "green", "low": 98.8, "high": 99.0}
CFG = {"timeframe_minutes": 5, "regular_hours_only": False, "aoi_lookback": 50, "atr_length": 14,
       "internal_sensitivity": 3, "external_sensitivity": 25, "order_blocks_kept": 10}


def minutes(prices, start=(9, 50), day=THURSDAY):
    """1-minute bars through the given closes; each bar's range is its open and close ± 0.02."""
    t, out, prev = at(day, *start), [], prices[0]
    for i, p in enumerate(prices):
        out.append({"t": t + timedelta(minutes=i), "o": prev, "h": max(prev, p) + 0.02, "l": min(prev, p) - 0.02,
                    "c": p, "v": 1000.0})
        prev = p
    return out


def day_with(prices, zones, start=(9, 50)):
    d = bt.Day(THURSDAY.date(), minutes(prices, start), [], [], CFG)
    d.zones = zones
    return d


V = {"rule": "confirm", "areas": "both", "min_tags": 0, "stop": 0.35, "target": 0}


class CandidateTests(unittest.TestCase):
    def test_bounce_off_the_red_area_is_a_put(self):
        # 09:50–09:59 below; 10:00–10:04 pokes into 101.0–101.2, closes 100.8; then falls
        p = [100.5] * 10 + [100.7, 100.9, 101.1, 100.9, 100.8] + [100.6, 100.4, 100.2, 100.1, 100.0] * 3
        tr = day_with(p, [RED]).trades(V)
        self.assertEqual((tr[0]["kind"], tr[0]["contract"], tr[0]["entry"], tr[0]["t"][11:16]), ("bounce", "put", 100.8, "10:04"))
        self.assertGreater(tr[0]["usd"], 0)

    def test_close_through_the_red_area_is_a_call(self):
        p = [100.5] * 10 + [100.8, 101.0, 101.2, 101.3, 101.4] + [101.5, 101.6] * 5
        tr = day_with(p, [RED]).trades(V)
        self.assertEqual((tr[0]["kind"], tr[0]["contract"]), ("break", "call"))

    def test_closing_inside_waits_for_the_next_close(self):
        p = [100.5] * 10 + [100.8, 101.0, 101.1, 101.1, 101.1] + [101.0, 100.9, 100.8, 100.7, 100.7] + [100.6] * 5
        tr = day_with(p, [RED]).trades(V)
        self.assertEqual((tr[0]["kind"], tr[0]["t"][11:16]), ("bounce", "10:09"))

    def test_todays_rule_buys_the_call_at_red(self):
        p = [100.5] * 10 + [100.7, 100.9, 101.05, 101.1, 101.1] + [100.6, 100.4, 100.3, 100.2, 100.1] * 3
        tr = day_with(p, [RED]).trades({**V, "rule": "touch"})
        self.assertEqual((tr[0]["contract"], tr[0]["kind"], tr[0]["why"]), ("call", "touch", "stop"))

    def test_area_filter(self):
        p = [100.5] * 10 + [100.7, 100.9, 101.1, 100.9, 100.8] + [100.6] * 10
        self.assertEqual(day_with(p, [RED]).trades({**V, "areas": "green"}), [])

    def test_the_two_signal_check(self):
        p = [100.5] * 10 + [100.7, 100.9, 101.1, 100.9, 100.8] + [100.6] * 10
        d = day_with(p, [RED])
        d._tags = {}
        d.tags = lambda i, z, long: ["rsi"]  # one signal only
        self.assertEqual(d.trades({**V, "min_tags": 2}), [])
        d.tags = lambda i, z, long: ["rsi", "vwap"]
        self.assertEqual(len(d.trades({**V, "min_tags": 2})), 1)


class ExitTests(unittest.TestCase):
    def test_stop_target_and_cutoff(self):
        from common import hhmm

        up = minutes([100.0, 100.2, 100.4, 100.6])
        self.assertEqual(bt.exit_walk(up, 1, 100.0, True, 0.35, 1, hhmm("15:40"))[1], "target")
        self.assertEqual(bt.exit_walk(up, 1, 100.0, True, 0.35, 0, hhmm("15:40"))[1], "close")
        down = minutes([100.0, 99.8, 99.6])
        px, why, _ = bt.exit_walk(down, 1, 100.0, True, 0.35, 0, hhmm("15:40"))
        self.assertEqual((why, round(px, 2)), ("stop", 99.65))
        late = minutes([100.0, 100.1, 100.2], start=(15, 39))
        self.assertEqual(bt.exit_walk(late, 1, 100.0, True, 0.35, 0, hhmm("15:40"))[:2], (100.0, "close"))


class RunTests(unittest.TestCase):
    def test_train_and_test_split(self):
        random.seed(3)
        bars, p = [], 600.0
        for n in range(14):
            d = THURSDAY - timedelta(days=20 - n)
            if d.weekday() >= 5:
                continue
            for m in range(240, 1200):
                o = p
                p += random.gauss(0, 0.08)
                bars.append({"t": at(d, 0, 0) + timedelta(minutes=m), "o": o, "h": max(o, p) + 0.03,
                             "l": min(o, p) - 0.03, "c": p, "v": 1000.0 + random.random() * 500})
        cfg = auto.config(load_json("rules.json"))
        res = bt.run(bt.group_days(bars, False), cfg)
        self.assertGreater(res["days"], 3)
        self.assertEqual(res["train_days"] + res["test_days"], res["days"])
        self.assertEqual(len(res["table"]), len(bt.variants()) + 3)  # plus the live desk and its two companions
        self.assertIn("desk_now", res["named"])
        self.assertIn("| Original rules (before Oct 7) |", bt.markdown(res))
        self.assertIn("current", res["monthly"])


class SummaryTests(unittest.TestCase):
    def test_summary(self):
        s = bt.summarize([{"t": "2026-10-01T10:00", "usd": 25.0, "net": 20.0, "opt": 20.0, "why": "close"},
                          {"t": "2026-10-01T11:00", "usd": -12.5, "net": -17.5, "opt": -17.5, "why": "stop"},
                          {"t": "2026-10-02T10:00", "usd": -5.0, "net": -10.0, "opt": -10.0, "why": "close"}])
        self.assertEqual((s["trades"], s["win_pct"], s["total"], s["max_drawdown"], s["gross"]), (3, 33.3, -7.5, -27.5, 7.5))


class OptionPriceTests(unittest.TestCase):
    def test_black_scholes(self):
        c = bt.bs_price(100, 100, 30 / 365, 0.2, True)
        self.assertAlmostEqual(c, 2.28, delta=0.02)  # at the money: about 0.4 × S × σ × √T
        p = bt.bs_price(100, 100, 30 / 365, 0.2, False)
        self.assertAlmostEqual(c - p, 0.0, places=6)  # put-call parity with no rates
        self.assertEqual(bt.bs_price(105, 100, 0, 0.2, True), 5)

    def test_expiries(self):
        from datetime import date

        self.assertEqual(bt.expiry_for(date(2026, 10, 6)), date(2026, 10, 6))   # a Tuesday: same day
        self.assertEqual(bt.expiry_for(date(2021, 6, 1)), date(2021, 6, 2))     # a Tuesday in 2021: Wednesday

    def test_time_decay_costs_a_flat_day(self):
        t_in, t_out = at(THURSDAY, 10, 0), at(THURSDAY, 15, 40)
        self.assertAlmostEqual(bt.bs_price(600, 600, bt.trading_years(t_in, THURSDAY.date()), 0.16, True), 2.3, delta=0.15)
        self.assertLess(bt.option_pnl(600.0, 600.0, t_in, t_out, 0.16, True), -150)  # same-day option, price unchanged
        self.assertGreater(bt.option_pnl(600.0, 606.0, t_in, t_out, 0.16, True), 300)  # a 1 % move pays more than delta 0.5

    def test_benchmark_buys_at_ten(self):
        p = [100.5] * 40
        tr = day_with(p, [RED]).trades({"rule": "always_call", "areas": "both", "min_tags": 0, "stop": 0.35, "target": 0})
        self.assertEqual((tr[0]["kind"], tr[0]["t"][11:16], tr[0]["contract"]), ("benchmark", "09:59", "call"))


class HoldAndExpiryTests(unittest.TestCase):
    def test_time_limit(self):
        from common import hhmm

        flat = minutes([100.0] * 60)
        px, why, j = bt.exit_walk(flat, 1, 100.0, True, 0.35, 0, hhmm("15:40"), 30)
        self.assertEqual((why, j), ("time", 31))  # in at the close of minute 0, out 30 minutes later

    def test_a_month_out_decays_slower(self):
        from datetime import date

        t_in, t_out = at(THURSDAY, 10, 0), at(THURSDAY, 15, 40)
        same_day = bt.option_pnl(600.0, 600.0, t_in, t_out, 0.16, True, "0d")
        month = bt.option_pnl(600.0, 600.0, t_in, t_out, 0.16, True, "30d")
        self.assertLess(same_day, month)
        self.assertLess(month, 0)
        self.assertGreaterEqual(bt.expiry_for(date(2026, 10, 6), "7d"), date(2026, 10, 13))


class LiveDeskRealismTests(unittest.TestCase):
    def test_affordable_expiry(self):
        from datetime import date

        t_in = at(THURSDAY, 10, 0)
        calm = bt.affordable_expiry(600.0, t_in, 0.10, True)       # a calm market: about a month fits under $1,000
        stormy = bt.affordable_expiry(600.0, t_in, 0.40, True)     # a wild one: only a shorter expiry fits
        self.assertIsNotNone(calm)
        self.assertGreaterEqual((calm - THURSDAY.date()).days, 21)
        self.assertTrue(stormy is None or stormy < calm)
        self.assertIsNone(bt.affordable_expiry(600.0, t_in, 0.40, True, max_cost=50))  # nothing fits: no trade
        self.assertGreaterEqual((bt.affordable_expiry(600.0, t_in, 0.10, True) - date(2026, 10, 1)).days, 7)

    def test_checks_every_ten_minutes(self):
        from common import hhmm

        # in at the close of 09:50; falls through the stop at 09:53, but nobody looks until the 09:59 bar closes
        bars = minutes([100.0, 100.0, 100.0, 99.7, 99.6, 99.6, 99.6, 99.6, 99.6, 99.5, 99.5])
        px, why, j = bt.exit_walk(bars, 1, 100.0, True, 0.25, 0, hhmm("15:40"), 30, poll=10)
        self.assertEqual((why, j, px), ("stop", 9, 99.5))
        px2, why2, j2 = bt.exit_walk(bars, 1, 100.0, True, 0.25, 0, hhmm("15:40"), 30)
        self.assertEqual((why2, j2), ("stop", 3))

    def test_time_limit_on_heartbeats(self):
        from common import hhmm

        flat = minutes([100.0] * 50)  # in at 09:51 (off the grid); 10:20 is only 29 minutes in, so out at the 10:30 look
        px, why, j = bt.exit_walk(flat, 1, 100.0, True, 0.25, 0, hhmm("15:40"), 30, poll=10)
        self.assertEqual((why, flat[j]["t"].strftime("%H:%M")), ("time", "10:29"))  # the bar that closes at 10:30


class RehearsalTests(unittest.TestCase):
    def test_round_trips_pair_buys_and_sells(self):
        import rehearsal

        fills = [{"symbol": "A", "side": "buy", "qty": 1.0, "price": 9.8, "filled_at": "2026-10-02T10:10:00-04:00"},
                 {"symbol": "A", "side": "sell", "qty": 1.0, "price": 8.37, "filled_at": "2026-10-02T11:10:00-04:00"}]
        exits = [{"ts": "2026-10-02T11:10:00-04:00", "reason": "stop"}]
        self.assertEqual(rehearsal.round_trips(fills, exits),
                         [{"symbol": "A", "opened_at": "2026-10-02T10:10:00-04:00", "closed_at": "2026-10-02T11:10:00-04:00",
                           "entry_price": 9.8, "exit_price": 8.37, "pnl": -143.0, "exit_reason": "stop"}])
