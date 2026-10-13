"""Scoring: probability metrics, trade metrics, day-block bootstrap, the acceptance check and the report."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ml import evaluate, report


def trades(pnls, days):
    return pd.DataFrame({"pnl": pnls, "entry_t": pd.to_datetime(days), "qty": 1.0, "entry": 100.0, "costs": 0.02,
                         "minutes": 15, "why": "time"})


class EvaluateTests(unittest.TestCase):
    def test_probability_metrics(self):
        y = np.array([0, 1, 1, 0, 1, 0, 1, 1])
        perfect = evaluate.prob_metrics(y, y * 0.98 + 0.01, 0.5)
        self.assertGreater(perfect["brier_skill"], 0.9)
        self.assertEqual(perfect["auc"], 1.0)
        base = evaluate.prob_metrics(y, np.full(8, 0.5), 0.5)
        self.assertEqual(base["brier_skill"], 0.0)
        worse = evaluate.prob_metrics(y, 1 - y * 0.98 - 0.01, 0.5)
        self.assertLess(worse["brier_skill"], 0)

    def test_bootstrap_keeps_days_together_and_brackets_the_mean(self):
        t = trades([1, 1, -0.5, 2, -1, 0.5] * 20, ["2026-01-02", "2026-01-02", "2026-01-05", "2026-01-05", "2026-01-06", "2026-01-07"] * 20)
        ci = evaluate.bootstrap_expectancy(t)
        self.assertEqual(ci["days"], 4)
        self.assertLessEqual(ci["lo"], ci["mean"])
        self.assertGreaterEqual(ci["hi"], ci["mean"])
        self.assertAlmostEqual(ci["mean"], t["pnl"].mean(), places=4)

    def test_trade_metrics(self):
        t = trades([2.0, -1.0, 3.0, -1.0], ["2026-01-02", "2026-01-02", "2026-01-05", "2026-01-05"])
        idx = pd.date_range("2026-01-02 09:30", periods=4, freq="1D", tz="America/New_York")
        r = {"trades": t, "equity": pd.Series([1000.0, 1001.0, 1004.0, 1003.0], index=idx), "start_equity": 1000.0,
             "end_equity": 1003.0}
        m = evaluate.trade_metrics(r, minutes_total=600)
        self.assertEqual((m["trades"], m["net_pnl"], m["win_rate"], m["profit_factor"]), (4, 3.0, 0.5, 2.5))
        self.assertAlmostEqual(m["expectancy"], 0.75)
        self.assertAlmostEqual(m["max_drawdown_pct"], -0.0996, places=3)
        self.assertEqual(m["exposure"], 0.1)

    def test_acceptance_needs_every_rule(self):
        good = {"model": "gbt", "trade": {"trades": 300, "net_pnl": 50.0, "profit_factor": 1.3, "ci": {"lo": 0.05}},
                "prob": {"brier_skill": 0.001, "brier": 0.24}}
        logi = {"model": "logistic", "trade": {"net_pnl": 10.0}, "prob": {"brier": 0.245}}
        every = {"trade": {"net_pnl": -40.0}}
        folds = [{"net_pnl": x} for x in (5, 10, -2, 20, 17)]
        self.assertTrue(evaluate.check(good, logi, every, folds)["passed"])
        for change in ({"trades": 150}, {"net_pnl": -1.0}, {"profit_factor": 1.05}, {"ci": {"lo": -0.01}}):
            bad = {**good, "trade": {**good["trade"], **change}}
            self.assertFalse(evaluate.check(bad, logi, every, folds)["passed"], change)
        self.assertFalse(evaluate.check(good, logi, every, [{"net_pnl": x} for x in (5, -1, -2, 20, -3)])["passed"])
        self.assertFalse(evaluate.check({**good, "prob": {"brier_skill": -0.001, "brier": 0.24}}, logi, every, folds)["passed"])
        self.assertFalse(evaluate.check(good, {**logi, "trade": {"net_pnl": 60.0}}, every, folds)["passed"])

    def test_report_renders(self):
        rep = {"target": "up_net", "sessions": 10, "holdout_start": "2026-05-01", "folds": [], "seconds": 1.0,
               "results": {"15": {"every_decision": {"model": "every_decision", "trade": {"trades": 3, "net_pnl": -1.0,
                                                    "profitable_folds": 0, "folds": 1, "ci": {}}, "folds": []}}}}
        md = report.markdown(rep, {"symbol": "SPY", "provider": "synthetic", "first": "2026-01-02", "last": "2026-03-01"}, "abc")
        self.assertIn("15-minute horizon", md)
        self.assertIn("not used", md)
