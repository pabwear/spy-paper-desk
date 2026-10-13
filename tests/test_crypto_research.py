"""Crypto rules: signals never look ahead, costs are charged on every change, splits are respected."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import crypto_research as cr


class CryptoRuleTests(unittest.TestCase):
    def test_signals_use_only_the_days_so_far(self):
        closes = [100.0] * 60 + [200.0]
        w = cr.signals(closes, "trend50")
        self.assertFalse(w[59])  # flat price: not above its own average
        self.assertTrue(w[60])   # decided on day 60's close, used on day 61
        self.assertEqual(cr.signals(closes[:60], "trend50"), w[:60])

    def test_hold_pays_two_costs_and_follows_the_price(self):
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(4)]
        r = cr.simulate(days, [100.0, 100.0, 110.0, 121.0], [100.0, 110.0, 121.0, 121.0], "hold")
        self.assertAlmostEqual(r["end"], round(1000 * 1.21 * (1 - cr.COST) ** 2, 2))
        self.assertEqual(r["trades"], 1)

    def test_donchian_enters_on_a_breakout_and_leaves_on_a_breakdown(self):
        closes = [100.0] * 25 + [130.0] + [130.0] * 5 + [90.0]
        w = cr.signals(closes, "donchian")
        self.assertFalse(w[24])
        self.assertTrue(w[25])
        self.assertFalse(w[-1])


if __name__ == "__main__":
    unittest.main()
