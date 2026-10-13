"""Config checks and data cleaning: what the models may see, and what they must never be shown."""

from __future__ import annotations

import unittest
from datetime import date, datetime

import pandas as pd

from ml.tests.helpers import CFG, bars
from ml import config as mlconfig
from ml import data
from common import ET


class ConfigTests(unittest.TestCase):
    def test_default_config_is_valid_and_hash_is_stable(self):
        self.assertEqual(mlconfig.digest(CFG), mlconfig.digest(mlconfig.load()))
        self.assertNotEqual(mlconfig.digest(CFG), mlconfig.digest(mlconfig.merge(CFG, {"seed": 8})))

    def test_refuses_unsafe_settings(self):
        bad = [{"costs": {"per_share_round_trip_usd": -0.01}},
               {"splits": {"embargo_minutes": 10}},          # shorter than the 60-minute horizon: labels would leak
               {"horizons_minutes": []},
               {"session": {"first_decision": "09:00"}},
               {"entry_delay_bars": -1}]
        for b in bad:
            with self.assertRaises(mlconfig.ConfigError, msg=str(b)):
                mlconfig.validate(mlconfig.merge(CFG, b))


class CleaningTests(unittest.TestCase):
    def test_full_minute_grid_regular_hours_only(self):
        b, audit = bars(days=3)
        self.assertEqual(audit["sessions"], 3)
        self.assertEqual(len(b), 3 * 390)
        self.assertEqual(audit["outside_hours"], 3)  # the synthetic pre-market minute each day
        first = b["t"].iloc[0]
        self.assertEqual((first.hour, first.minute, str(first.tz)), (9, 30, "America/New_York"))
        self.assertEqual(b.groupby("session")["minute"].max().tolist(), [389] * 3)

    def test_missing_minutes_stay_missing_never_zero(self):
        b, audit = bars(days=3, drop_frac=0.05, seed=4)
        miss = b[b["missing"]]
        self.assertGreater(len(miss), 0)
        self.assertEqual(audit["missing_minutes"], len(miss))
        self.assertTrue(miss[["o", "h", "l", "c", "v"]].isna().all().all())  # not 0, not filled

    def test_duplicates_and_impossible_bars_are_removed_and_counted(self):
        raw = data.SyntheticProvider(seed=2, duplicate_frac=0.02).fetch("SPY", date(2026, 3, 2), date(2026, 3, 3))
        bad = raw.iloc[[10]].copy()
        bad["h"] = bad["l"] - 1  # high under low
        raw = pd.concat([raw, bad.assign(t=bad["t"] + pd.Timedelta(seconds=30))], ignore_index=True)
        b, audit = data.clean(raw, CFG["session"])
        self.assertGreater(audit["duplicates"], 0)
        self.assertEqual(audit["invalid"], 1)
        self.assertFalse(b["t"].duplicated().any())

    def test_half_day_and_thin_sessions(self):
        raw = data.SyntheticProvider(seed=3, half_days=(date(2026, 11, 27),)).fetch("SPY", date(2026, 11, 26), date(2026, 11, 28))
        b, audit = data.clean(raw, CFG["session"])
        self.assertEqual(audit["half_days"], ["2026-11-27"])
        self.assertEqual(len(b[b["session"] == date(2026, 11, 27)]), 210)
        thin = raw[raw["t"].dt.minute % 3 == 0]  # a third of the minutes
        _, a2 = data.clean(thin, CFG["session"])
        self.assertEqual(a2["sessions"], 0)
        self.assertEqual(len(a2["dropped_sessions"]), 2)

    def test_timezones_are_normalised(self):
        raw = data.SyntheticProvider(seed=5).fetch("SPY", date(2026, 3, 2), date(2026, 3, 3))
        utc = raw.assign(t=raw["t"].dt.tz_convert("UTC"))
        a, _ = data.clean(raw, CFG["session"])
        b, _ = data.clean(utc, CFG["session"])
        pd.testing.assert_frame_equal(a, b)
        self.assertEqual(a["t"].iloc[0], pd.Timestamp(datetime(2026, 3, 2, 9, 30, tzinfo=ET)))

    def test_cache_round_trip(self):
        import tempfile

        src = data.SyntheticProvider(seed=6)
        with tempfile.TemporaryDirectory() as d:
            cp = data.CachedProvider(src, d)
            a = cp.fetch("SPY", date(2024, 1, 1), date(2024, 3, 1))
            b = cp.fetch("SPY", date(2024, 1, 1), date(2024, 3, 1))  # from the cache this time
            pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True), check_dtype=False,
                                          check_exact=False, rtol=1e-12)
