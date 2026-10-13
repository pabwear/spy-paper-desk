"""The TCN: causal windows, learning a planted pattern, numpy == PyTorch, reproducible, safe checkpoints."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ml.tests.helpers import CFG
from ml import data, features, labels

try:
    import torch  # noqa: F401

    HAVE_TORCH = True
except ImportError:  # the ML CI job installs it; skip cleanly elsewhere
    HAVE_TORCH = False


def setup_data(trend=1.5, seed=4):
    raw = data.SyntheticProvider(seed=seed, trend_bps=trend, trend_block=60).fetch("SPY", date(2024, 1, 1), date(2024, 4, 1))
    b, _ = data.clean(raw, CFG["session"])
    f = features.build(b)
    lab = labels.build(b, 15, CFG)
    ok = features.usable(f) & lab["up_net"].notna()
    F = f[features.NAMES].to_numpy(float)
    sess = pd.factorize(b["session"])[0]
    y = lab["up_net"].to_numpy(float)
    months = b["t"].dt.month.to_numpy()
    tr = np.flatnonzero(ok.to_numpy() & (months == 1))
    va = np.flatnonzero(ok.to_numpy() & (months == 2) & labels.decision_rows(b, CFG).to_numpy())
    te = np.flatnonzero(ok.to_numpy() & (months == 3) & labels.decision_rows(b, CFG).to_numpy())
    return F, sess, y, tr, va, te


class WindowTests(unittest.TestCase):
    def test_windows_are_causal_and_session_bound(self):
        from ml.tcn import windows

        F = np.arange(20, dtype=float).reshape(10, 2)
        sess = np.array([0] * 5 + [1] * 5)
        W = windows(F, sess, np.array([6, 9]), 3)
        self.assertEqual(W.shape, (2, 2, 3))
        np.testing.assert_array_equal(W[1, 0], [14, 16, 18])          # rows 7, 8, 9: never row 10+
        self.assertTrue(np.isnan(W[0, 0, 0]))                           # row 4 is yesterday: blanked
        np.testing.assert_array_equal(W[0, 0, 1:], [10, 12])
        F2 = F.copy()
        F2[7:] = -1                                                     # change the future of row 6
        np.testing.assert_array_equal(windows(F2, sess, np.array([6]), 3), W[:1])


@unittest.skipUnless(HAVE_TORCH, "PyTorch not installed")
class TCNTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ml.tcn import TCN

        cls.F, cls.sess, cls.y, cls.tr, cls.va, cls.te = setup_data()
        cls.m = TCN(seed=7, train={"max_epochs": 4}).fit_rows(cls.F, cls.sess, cls.tr, cls.y[cls.tr], cls.va, cls.y[cls.va])

    def test_learns_the_planted_pattern(self):
        from ml import evaluate

        p = self.m.predict_rows(self.F, self.sess, self.te)
        self.assertEqual(p.shape, (len(self.te),))
        self.assertTrue(((p > 0) & (p < 1)).all())
        self.assertGreater(evaluate.prob_metrics(self.y[self.te], p, self.y[self.tr].mean())["auc"], 0.56)

    def test_same_seed_same_model(self):
        from ml.tcn import TCN

        m2 = TCN(seed=7, train={"max_epochs": 4}).fit_rows(self.F, self.sess, self.tr, self.y[self.tr], self.va, self.y[self.va])
        np.testing.assert_allclose(m2.predict_rows(self.F, self.sess, self.te),
                                   self.m.predict_rows(self.F, self.sess, self.te), atol=1e-5)

    def test_numpy_inference_matches_pytorch_and_checkpoint_is_safe(self):
        from ml.tcn import ModelLoadError, NumpyTCN

        with tempfile.TemporaryDirectory() as d:
            info = self.m.save(d, {"horizon": 15, "target": "up_net", "trained": ["2024-01-01", "2024-02-01"]})
            self.assertTrue(info["version"].startswith("tcn-"))
            npm = NumpyTCN(d)
            p_np, ood = npm.predict_rows(self.F, self.sess, self.te)
            np.testing.assert_allclose(p_np, self.m.predict_rows(self.F, self.sess, self.te), atol=1e-5)
            self.assertFalse(ood.any())
            F_bad = self.F.copy()
            F_bad[self.te[0], 0] = 1e9  # an absurd input
            _, ood2 = npm.predict_rows(F_bad, self.sess, self.te[:1])
            self.assertTrue(ood2[0])
            mj = Path(d) / "model.json"
            meta = json.loads(mj.read_text())
            meta["feature_version"] = -1
            mj.write_text(json.dumps(meta))
            with self.assertRaises(ModelLoadError):
                NumpyTCN(d)
        with self.assertRaises(ModelLoadError):
            NumpyTCN("/nonexistent/folder")
