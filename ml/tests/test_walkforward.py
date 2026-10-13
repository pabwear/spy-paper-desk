"""End-to-end walk-forward on synthetic data. Two controls:
  null      a pure random walk: NOTHING may pass (no skill, costs lose money)
  positive  a planted, learnable trend: the models MUST find it (AUC well above 0.5, money after costs)
If the null passed, the pipeline would be leaking or overfitting; if the positive failed, it would be blind."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from ml.tests.helpers import CFG
from ml import config as mlconfig, data, splits, walkforward

SMALL = mlconfig.merge(CFG, {"splits": {"train_months": 3, "validation_months": 1, "test_months": 1,
                                        "step_months": 1, "holdout_months": 1}})


def run(**provider):
    raw = data.SyntheticProvider(**provider).fetch("SPY", date(2024, 1, 1), date(2024, 9, 1))
    b, _ = data.clean(raw, SMALL["session"])
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "h.json"
        splits.set_lock(date(2024, 7, 31), 1, p)
        rep = walkforward.run(b, SMALL, [15], ["base_rate", "logistic", "gbt"], holdout_path=p, log=lambda *a: None)
    return rep["results"]["15"], rep


class WalkForwardTests(unittest.TestCase):
    def test_null_random_walk_nothing_passes(self):
        res, rep = run(seed=3)
        self.assertEqual(len(rep["folds"]), 2)
        for name in ("logistic", "gbt"):
            self.assertFalse(res[name]["acceptance"]["passed"], name)
            self.assertLess(res[name]["prob"]["auc"], 0.53)
        self.assertLess(res["every_decision"]["trade"]["net_pnl"], 0)  # trading blindly loses the costs
        self.assertEqual(res["base_rate"]["trade"]["trades"], 0)

    def test_positive_control_planted_trend_is_found(self):
        res, _ = run(seed=4, trend_bps=1.5, trend_block=60)
        for name in ("logistic", "gbt"):
            self.assertGreater(res[name]["prob"]["auc"], 0.56, name)
            self.assertGreater(res[name]["prob"]["brier_skill"], 0, name)
            self.assertGreater(res[name]["trade"]["net_pnl"], 0, name)

    def test_holdout_is_never_touched(self):
        raw = data.SyntheticProvider(seed=5).fetch("SPY", date(2024, 1, 1), date(2024, 9, 1))
        b, _ = data.clean(raw, SMALL["session"])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.json"
            splits.set_lock(date(2024, 7, 31), 1, p)
            rep = walkforward.run(b, SMALL, [15], ["logistic"], holdout_path=p, log=lambda *a: None)
            for f in rep["folds"]:
                self.assertLessEqual(f["test"][1], rep["holdout_start"])
            self.assertEqual(rep["holdout_start"], "2024-07-01")
