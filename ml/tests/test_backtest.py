"""The backtester: execution timing, costs, stops, sizing, rejections. Hand-built minutes, exact numbers."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from ml.tests.helpers import CFG, bars
from ml import backtest, config as mlconfig, data, features
from common import ET

NOCOST = mlconfig.merge(CFG, {"costs": {"per_share_round_trip_usd": 0.0, "slippage_bps_each_side": 0.0}})


def flat_day(price=100.0, n=390, day=date(2026, 3, 2)):
    rows = []
    t0 = datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET)
    for k in range(n):
        rows.append({"t": t0 + timedelta(minutes=k), "o": price, "h": price + 0.01, "l": price - 0.01, "c": price, "v": 1000.0})
    return pd.DataFrame(rows)


def prep(raw, cfg=CFG):
    b, _ = data.clean(raw, cfg["session"])
    return b, features.build(b)


def signal_at(b, clock: str):
    """True on the bar whose decision is made at `clock` (the bar that ENDS then)."""
    hh, mm = map(int, clock.split(":"))
    m = hh * 60 + mm - 570 - 1
    return pd.Series(b["minute"] == m, index=b.index)


class BacktestTests(unittest.TestCase):
    def test_fills_after_the_decision_never_at_a_known_price(self):
        raw = flat_day()
        # the price jumps on the decision bar's close; the fill must be at a LATER bar's open
        t_dec = raw.index[raw["t"].dt.strftime("%H:%M") == "10:29"][0]
        raw.loc[t_dec:, ["o", "h", "l", "c"]] = raw.loc[t_dec:, ["o", "h", "l", "c"]] + 1.0
        raw.loc[t_dec, "o"] = 100.0
        b, f = prep(raw, NOCOST)
        r = backtest.run(b, signal_at(b, "10:30"), f, NOCOST, horizon=15)
        tr = r["trades"].iloc[0]
        delay = CFG["entry_delay_bars"]
        self.assertEqual(pd.Timestamp(tr["entry_t"]).strftime("%H:%M"), f"10:{30 + delay:02d}")
        self.assertAlmostEqual(tr["entry"], 101.0)
        self.assertEqual(tr["why"], "time")
        self.assertAlmostEqual(tr["pnl"], 0.0, places=9)

    def test_costs_make_a_flat_trade_lose(self):
        b, f = prep(flat_day())
        r = backtest.run(b, signal_at(b, "10:30"), f, CFG, horizon=15)
        tr = r["trades"].iloc[0]
        per_share = 0.02 + 100 * 2 * 0.5 / 1e4
        self.assertAlmostEqual(tr["pnl"], -tr["qty"] * per_share, places=6)
        self.assertLess(r["end_equity"], 1000.0)

    def test_stop_and_take_profit_same_bar_assumes_the_stop(self):
        raw = flat_day()
        i = raw.index[raw["t"].dt.strftime("%H:%M") == "10:40"][0]
        raw.loc[i, ["h", "l"]] = [105.0, 95.0]  # both the stop and the target inside one minute
        cfg = mlconfig.merge(NOCOST, {"strategy": {"take_profit_atr_mult": 3.0}})
        b, f = prep(raw, cfg)
        r = backtest.run(b, signal_at(b, "10:30"), f, cfg, horizon=30)
        self.assertEqual(r["trades"].iloc[0]["why"], "stop")
        self.assertLess(r["trades"].iloc[0]["pnl"], 0)

    def test_gap_through_the_stop_fills_at_the_worse_open(self):
        raw = flat_day()
        i = raw.index[raw["t"].dt.strftime("%H:%M") == "10:40"][0]
        raw.loc[i:, ["o", "h", "l", "c"]] = raw.loc[i:, ["o", "h", "l", "c"]] - 3.0
        b, f = prep(raw, NOCOST)
        r = backtest.run(b, signal_at(b, "10:30"), f, NOCOST, horizon=30)
        tr = r["trades"].iloc[0]
        self.assertEqual(tr["why"], "stop_gap")
        self.assertAlmostEqual(tr["exit"], 97.0)

    def test_one_position_cooldown_session_limits_and_reasons(self):
        b, f = prep(flat_day())
        sig = signal_at(b, "10:30") | signal_at(b, "10:40") | signal_at(b, "09:50") | signal_at(b, "15:30")
        r = backtest.run(b, sig, f, CFG, horizon=30)
        self.assertEqual(len(r["trades"]), 1)
        rej = r["rejections"]
        self.assertEqual(rej.get("already_in_position"), 1)   # 10:40 while the 10:30 trade is open
        self.assertEqual(rej.get("outside_entry_hours"), 1)   # 09:50
        self.assertEqual(rej.get("too_late_for_horizon"), 1)  # 15:30 + 30 min runs past the 15:40 flatten

    def test_flat_every_night_and_equity_curve(self):
        b, _ = bars(days=5, seed=31)
        f = features.build(b)
        sig = pd.Series(b["minute"] % 37 == 0, index=b.index)
        r = backtest.run(b, sig, f, CFG, horizon=60)
        self.assertGreater(len(r["trades"]), 3)
        same_day = pd.to_datetime(r["trades"]["entry_t"]).dt.date == pd.to_datetime(r["trades"]["exit_t"]).dt.date
        self.assertTrue(same_day.all())
        self.assertAlmostEqual(r["equity"].iloc[-1], r["end_equity"], places=6)
        self.assertAlmostEqual(r["end_equity"] - 1000.0, r["trades"]["pnl"].sum(), places=6)
        # never more shares than the cash could buy
        self.assertTrue((r["trades"]["qty"] * r["trades"]["entry"] <= 1000.0 + 1e-6).all())

    def test_daily_loss_limit_and_loss_streak_stop_new_entries(self):
        raw = pd.concat([flat_day(day=date(2026, 3, 2))])
        b, f = prep(raw)
        every = pd.Series((b["minute"] - 30) % 20 == 0, index=b.index) & (b["minute"] >= 30)
        r = backtest.run(b, every, f, mlconfig.merge(CFG, {"strategy": {"cooldown_minutes": 1, "max_entries_per_day": 99},
                                                           "risk": {"max_consecutive_losses": 2}}), horizon=5)
        self.assertEqual(len(r["trades"]), 2)  # each loses its costs; after 2 losses in a row: no more
        self.assertGreater(r["rejections"].get("risk:loss_streak_ok", 0), 0)
