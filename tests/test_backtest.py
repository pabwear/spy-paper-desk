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
        self.assertEqual(len(res["table"]), len(bt.variants()))
        self.assertIn("desk_now", res["named"])
        self.assertIn("| Desk today |", bt.markdown(res))


class SummaryTests(unittest.TestCase):
    def test_summary(self):
        s = bt.summarize([{"t": "2026-10-01T10:00", "usd": 25.0, "net": 20.0, "why": "close"},
                          {"t": "2026-10-01T11:00", "usd": -12.5, "net": -17.5, "why": "stop"},
                          {"t": "2026-10-02T10:00", "usd": -5.0, "net": -10.0, "why": "close"}])
        self.assertEqual((s["trades"], s["win_pct"], s["total"], s["max_drawdown"], s["gross"]), (3, 33.3, -7.5, -27.5, 7.5))
