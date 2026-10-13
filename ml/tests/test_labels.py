"""Labels: priced like the desk trades, only from bars AFTER the decision, never across sessions."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ml.tests.helpers import CFG, bars
from ml import config as mlconfig
from ml import labels


class LabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b, _ = bars(days=5, seed=21)

    def test_entry_and_exit_timing_and_costs(self):
        b = self.b
        lab = labels.build(b, 15, CFG)
        i = b.index[200]
        delay = CFG["entry_delay_bars"]
        entry, exit_ = b.loc[i + 1 + delay, "o"], b.loc[i + 1 + delay + 14, "c"]
        self.assertEqual(lab.loc[i, "entry"], entry)
        self.assertEqual(lab.loc[i, "exit"], exit_)
        gross = np.log(exit_ / entry) * 1e4
        cost = 0.02 / entry * 1e4 + 2 * 0.5
        self.assertAlmostEqual(lab.loc[i, "net"], gross - cost)
        self.assertEqual(lab.loc[i, "up_net"], float(gross - cost > 0))
        # the trade starts strictly after the decision is known and ends after it starts
        self.assertGreater(lab.loc[i, "entry_t"], b.loc[i, "t"])
        self.assertGreaterEqual(lab.loc[i, "entry_t"], lab.loc[i, "decision_t"])
        self.assertGreater(lab.loc[i, "label_end"], lab.loc[i, "entry_t"])

    def test_no_label_crosses_the_close(self):
        lab = labels.build(self.b, 30, CFG)
        last = self.b["minute"] >= 390 - (1 + CFG["entry_delay_bars"] + 30 - 1)
        self.assertTrue(lab.loc[last, labels.TARGETS + ["net"]].isna().all().all())
        known = lab["label_end"].notna()
        same_day = lab.loc[known, "label_end"].dt.date == self.b.loc[known, "t"].dt.date
        self.assertTrue(same_day.all())

    def test_missing_entry_or_exit_is_unknown_not_guessed(self):
        b, _ = bars(days=3, seed=22, drop_frac=0.1)
        lab = labels.build(b, 15, CFG)
        nxt = b.groupby("session")["missing"].shift(-(1 + CFG["entry_delay_bars"])).fillna(True).astype(bool)
        self.assertTrue(lab.loc[nxt, "up_net"].isna().all())

    def test_thresholds(self):
        lab = labels.build(self.b, 15, mlconfig.merge(CFG, {"move_threshold_bps": 0.0}))
        k = lab["net"].notna()
        np.testing.assert_array_equal(lab.loc[k, "big_up"], (lab.loc[k, "net"] > 0).astype(float))
        self.assertTrue(((lab.loc[k, "big_up"] + lab.loc[k, "big_down"]) <= 1).all())

    def test_labels_never_change_features_rows_before_entry(self):
        """Labels look forward by design; check they look ONLY forward: changing bars <= i leaves label i alone
        except through its own entry/exit bars, which are > i."""
        lab = labels.build(self.b, 15, CFG)
        i = self.b.index[150]
        b2 = self.b.copy()
        b2.loc[: i, ["o", "h", "l", "c"]] *= 1.03
        lab2 = labels.build(b2, 15, CFG)
        self.assertEqual(lab.loc[i, "net"], lab2.loc[i, "net"])

    def test_decision_rows_follow_the_schedule(self):
        d = labels.decision_rows(self.b, CFG)
        day = self.b[d & (self.b["session"] == self.b["session"].iloc[0])]
        clock = [(t + pd.Timedelta(minutes=1)).strftime("%H:%M") for t in day["t"]]
        self.assertEqual(clock[0], "10:00")
        self.assertEqual(clock[1], "10:10")
        self.assertEqual(clock[-1], "15:30")
