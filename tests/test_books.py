"""Trading books: SPY shares, SNDK shares and SPY options side by side, each with its own budget, position,
entry count and stop; shares carry a stop held at Alpaca."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from common import ET
from signals import range_stop_pct


def daily(ranges_pct, close=100.0, start=datetime(2026, 9, 1, tzinfo=ET)):
    return [{"t": start + timedelta(days=i), "o": close, "h": close * (1 + r / 200), "l": close * (1 - r / 200),
             "c": close, "v": 1.0} for i, r in enumerate(ranges_pct)]


class RangeStopTests(unittest.TestCase):
    def test_a_quarter_of_the_average_daily_range(self):
        self.assertAlmostEqual(range_stop_pct(daily([4.0] * 20), 0.25, 20), 1.0, places=6)

    def test_uses_only_the_last_n_days(self):
        self.assertAlmostEqual(range_stop_pct(daily([100.0] * 5 + [2.0] * 20), 0.25, 20), 0.5, places=6)

    def test_too_little_history_gives_none(self):
        self.assertIsNone(range_stop_pct(daily([2.0] * 4), 0.25, 20))
