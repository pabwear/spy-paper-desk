"""Features: correct values, and NO information from the future (the leakage tests)."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ml.tests.helpers import bars
from ml import features


class FeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b, _ = bars(days=20, seed=11, drop_frac=0.02)
        cls.f = features.build(cls.b)

    def test_same_rows_and_schema(self):
        self.assertEqual(len(self.f), len(self.b))
        self.assertEqual(list(self.f.columns), features.NAMES)
        self.assertEqual(set(features.NAMES), set(features.DEFINITIONS))

    def test_values_against_hand_calculation(self):
        b, f = self.b, self.f
        day = b["session"].unique()[5]
        g = b[b["session"] == day]
        i = g.index[100]
        cf = g["c"].ffill()
        self.assertAlmostEqual(f.loc[i, "ret_5"], np.log(cf.loc[i] / cf.loc[i - 5]) * 1e4, places=6)
        real = g.loc[:i][~g.loc[:i, "missing"]]
        tp = (real["h"] + real["l"] + real["c"]) / 3
        vwap = (tp * real["v"]).sum() / real["v"].sum()
        self.assertAlmostEqual(f.loc[i, "vwap_dist"], np.log(cf.loc[i] / vwap) * 1e4, places=6)
        self.assertAlmostEqual(f.loc[i, "tod"], 100 / 390)

    def test_early_minutes_are_unknown_not_zero(self):
        first = self.f[self.b["minute"] == 0]
        self.assertTrue(first["ret_1"].isna().all())
        self.assertTrue(first["rv_60"].isna().all())
        # the first sessions have no volume history yet
        self.assertTrue(self.f.loc[self.b["session"] == self.b["session"].iloc[0], "rel_vol_15"].isna().all())

    def test_stale_counts_minutes_since_last_real_bar(self):
        miss = self.b.index[self.b["missing"] & (self.b["minute"] > 0)]
        self.assertGreater(len(miss), 0)
        i = miss[0]
        self.assertGreaterEqual(self.f.loc[i, "stale"], 1)
        self.assertEqual(self.f.loc[~self.b["missing"], "stale"].max(), 0)

    def test_no_feature_depends_on_the_future(self):
        """Cut the data at row i (and scramble everything after it): rows <= i must not change at all."""
        rng = np.random.default_rng(0)
        for i in rng.choice(np.arange(500, len(self.b) - 1), size=6, replace=False):
            cut = features.build(self.b.iloc[: i + 1])
            pd.testing.assert_frame_equal(cut, self.f.iloc[: i + 1], check_exact=False, rtol=1e-9, atol=1e-9)
            scrambled = self.b.copy()
            after = scrambled.index > i
            for col in ("o", "h", "l", "c"):
                scrambled.loc[after, col] = scrambled.loc[after, col] * 1.05
            scrambled.loc[after, "v"] = scrambled.loc[after, "v"] * 3
            f2 = features.build(scrambled)
            pd.testing.assert_frame_equal(f2.iloc[: i + 1], self.f.iloc[: i + 1], check_exact=False, rtol=1e-9, atol=1e-9)

    def test_no_future_leak_when_a_session_opens_with_missing_minutes(self):
        """The day's first open is unknown until the first real bar; before it, ret_open/gap must be NaN."""
        b = self.b.copy()
        day = b["session"].unique()[3]
        head = b.index[(b["session"] == day) & (b["minute"] < 4)]
        b.loc[head, ["o", "h", "l", "c", "v"]] = np.nan
        b.loc[head, "missing"] = True
        f = features.build(b)
        self.assertTrue(f.loc[head, ["ret_open", "gap"]].isna().all().all())
        i = head[-1]
        pd.testing.assert_frame_equal(features.build(b.iloc[: i + 1]), f.iloc[: i + 1], check_exact=False,
                                      rtol=1e-9, atol=1e-9)

    def test_usable_rows(self):
        u = features.usable(self.f)
        self.assertTrue(self.f[u].notna().all().all())
        self.assertTrue((self.f.loc[u, "stale"] <= 2).all())
        self.assertGreater(u.mean(), 0.5)
