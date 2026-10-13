"""Walk-forward folds, purging, the holdout lock and the risk engine."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from ml.tests.helpers import CFG
from ml import risk, splits
from common import ET


class SplitTests(unittest.TestCase):
    def test_folds_are_ordered_non_overlapping_and_stop_before_the_holdout(self):
        folds = splits.walk_forward(date(2020, 7, 1), date(2026, 4, 1), CFG["splits"])
        self.assertGreater(len(folds), 5)
        for f in folds:
            self.assertLess(f.train[0], f.train[1])
            self.assertEqual(f.train[1], f.validation[0])
            self.assertEqual(f.validation[1], f.test[0])
            self.assertLessEqual(f.test[1], date(2026, 4, 1))
        tests = [f.test for f in folds]
        for a, b in zip(tests, tests[1:]):
            self.assertLessEqual(a[1], b[0])  # test windows never overlap: each row is scored once
        self.assertEqual(folds[0].train, (date(2020, 7, 1), date(2022, 7, 1)))

    def test_purge_drops_labels_that_reach_into_the_next_window(self):
        t = pd.Series(pd.to_datetime(["2026-03-31 15:20", "2026-03-31 15:50", "2026-04-01 09:45", None]).tz_localize(ET))
        keep = splits.purge(pd.Series([True] * 4), t, date(2026, 4, 1), 60, ET)
        self.assertEqual(keep.tolist(), [True, True, False, False])
        keep2 = splits.purge(pd.Series([True] * 4), t, date(2026, 4, 1), 60 * 9, ET)  # 9h embargo
        self.assertEqual(keep2.tolist(), [False, False, False, False])

    def test_holdout_lock_is_set_once_guards_and_refuses_retuning(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "holdout.json"
            with self.assertRaises(splits.HoldoutError):
                splits.holdout_start(p)
            lock = splits.set_lock(date(2026, 10, 9), 6, p)
            self.assertEqual((lock["start"], lock["end"]), ("2026-05-01", "2026-11-01"))
            again = splits.set_lock(date(2027, 3, 1), 6, p)  # never moves
            self.assertEqual(again["start"], "2026-05-01")
            splits.guard(pd.Series([date(2026, 4, 30)]), p)
            with self.assertRaises(splits.HoldoutError):
                splits.guard(pd.Series([date(2026, 4, 30), date(2026, 5, 1)]), p)
            splits.record_final("cfgA", "m1", {"pnl": 1}, p)
            splits.record_final("cfgA", "m1", {"pnl": 1}, p)  # the same run can be repeated
            with self.assertRaises(splits.HoldoutError):
                splits.record_final("cfgB", "m1", {"pnl": 2}, p)
            self.assertEqual(len(json.loads(p.read_text())["evaluations"]), 2)


def ok_state(**kw):
    s = {"equity": 1000.0, "buying_power": 1000.0, "exposure_usd": 0.0, "positions": 0, "day_pnl": 0.0,
         "consecutive_losses": 0, "orders_today": 0, "minutes_since_loss": None, "data_age_minutes": 0,
         "in_session": True, "model_ok": True, "model_ms": 5, "kill_switch": False, "price": 500.0,
         "stop_bps": 20.0, "spread_bps": 1.0, "side": "long", "mode": "paper"}
    s.update(kw)
    return s


class RiskTests(unittest.TestCase):
    rs = risk.settings(CFG)

    def test_sizes_from_the_stop_and_caps(self):
        # 0.5% of $1,000 = $5 at risk; a 200 bp stop on $500 loses $10 a share -> 0.5 share ($250)
        v = risk.check_entry(ok_state(stop_bps=200.0), self.rs)
        self.assertTrue(v.ok, v.failed)
        self.assertAlmostEqual(v.qty, 0.5)
        # a 20 bp stop would allow 5 shares ($2,500): capped at 80% of equity = $800 -> 1.6 shares
        self.assertAlmostEqual(risk.check_entry(ok_state(stop_bps=20.0), self.rs).qty, 1.6)
        self.assertAlmostEqual(risk.check_entry(ok_state(buying_power=100.0), self.rs).qty, 0.2)  # never over buying power
        self.assertAlmostEqual(risk.check_entry(ok_state(exposure_usd=700.0), self.rs).qty, 0.2)  # total exposure cap
        whole = risk.check_entry(ok_state(stop_bps=200.0), {**self.rs, "allow_fractional": False})
        self.assertFalse(whole.ok)  # 0.5 share rounds DOWN to 0: refused, never rounded up
        self.assertIn("size_positive", whole.failed)

    def test_confidence_never_raises_size(self):
        a = risk.check_entry(ok_state(), self.rs)
        b = risk.check_entry(ok_state(prob=0.99, confidence=1.0), self.rs)
        self.assertEqual(a.qty, b.qty)

    def test_every_limit_refuses_and_fails_closed(self):
        cases = {"kill_switch_off": {"kill_switch": True}, "not_live": {"mode": "live"},
                 "long_only": {"side": "short"}, "inputs_known": {"price": None}, "in_session": {"in_session": False},
                 "data_fresh": {"data_age_minutes": 5}, "model_healthy": {"model_ok": False},
                 "positions_below_max": {"positions": 1}, "orders_below_max": {"orders_today": 8},
                 "daily_loss_ok": {"day_pnl": -15.0}, "loss_streak_ok": {"consecutive_losses": 3},
                 "cooldown_over": {"minutes_since_loss": 10}, "spread_ok": {"spread_bps": 9.0},
                 "stop_wide_enough": {"stop_bps": 3.0}}
        for rule, change in cases.items():
            v = risk.check_entry(ok_state(**change), self.rs)
            self.assertFalse(v.ok, rule)
            self.assertIn(rule, v.failed, rule)
            self.assertEqual(v.qty, 0.0)
        self.assertFalse(risk.check_entry(ok_state(data_age_minutes=None), self.rs).ok)  # unknown = refused
        self.assertFalse(risk.check_entry(ok_state(model_ms=5000), self.rs).ok)  # too slow = failed model
        self.assertFalse(risk.check_entry(ok_state(), {**self.rs, "live": True}).ok)

    def test_exits_allowed_with_kill_switch_but_never_live(self):
        self.assertTrue(risk.check_exit({"position_qty": 0.5, "kill_switch": True}, self.rs).ok)
        self.assertFalse(risk.check_exit({"position_qty": 0}, self.rs).ok)
        self.assertFalse(risk.check_exit({"position_qty": 1}, {**self.rs, "live": True}).ok)
