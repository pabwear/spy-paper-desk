"""Profit by period: today from the previous close, week from before Monday, month from before the 1st."""

from __future__ import annotations

import unittest
from datetime import date

import pnl_periods as pp

H = [{"date": "2026-09-30", "equity": 990.0}, {"date": "2026-10-02", "equity": 1000.0},
     {"date": "2026-10-07", "equity": 1016.83}, {"date": "2026-10-08", "equity": 1025.62},
     {"date": "2026-10-09", "equity": 1056.42}]


class PeriodTests(unittest.TestCase):
    def test_periods(self):
        p = pp.periods(H, 1060.0, 1056.42, 1000.0, date(2026, 10, 9))  # a Friday
        self.assertEqual(p["today"], 3.58)
        self.assertEqual(p["week"], 60.0)    # from Oct 2's close (the last before Monday Oct 5)
        self.assertEqual(p["month"], 70.0)   # from Sep 30's close
        self.assertEqual(p["all"], 60.0)

    def test_new_account_counts_from_its_start(self):
        p = pp.periods([], 997.34, 1000.0, 1000.0, date(2026, 10, 10))
        self.assertEqual((p["today"], p["week"], p["month"], p["all"]), (-2.66, -2.66, -2.66, -2.66))
        self.assertEqual(pp.periods([], None, None, 1000.0, date(2026, 10, 10)), {})

    def test_days_before_the_account_had_money_are_skipped(self):
        # Alpaca's 3-month history reports $0 for days before a new account was funded.
        zeros = [{"date": "2026-08-14", "equity": 0.0}, {"date": "2026-09-30", "equity": 0.0},
                 {"date": "2026-10-09", "equity": 1000.0}]
        p = pp.periods(zeros, 996.57, 1000.0, 1000.0, date(2026, 10, 10))
        self.assertEqual((p["today"], p["week"], p["month"], p["all"]), (-3.43, -3.43, -3.43, -3.43))

        class Hist:
            timestamp = [1791345600, 1791432000]  # Oct 7, 8 2026 00:00 ET
            equity = [0.0, 1000.0]
        self.assertEqual(pp.history_from_alpaca(Hist()), [{"date": "2026-10-08", "equity": 1000.0}])

    def test_reads_alpaca_history(self):
        class Hist:
            timestamp = [1791345600, 1791432000, 1791518400]  # Oct 7, 8, 9 2026 00:00 ET
            equity = [1016.83, None, 1056.42]
        self.assertEqual(pp.history_from_alpaca(Hist()), [{"date": "2026-10-07", "equity": 1016.83},
                                                          {"date": "2026-10-09", "equity": 1056.42}])
