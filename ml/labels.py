"""Labels: what actually happened AFTER a decision, priced the way the desk would have traded it.

For a decision on bar i (made at t_i + 1 min, when bar i's close is known) and a horizon of h minutes:
    entry  = open of bar i + 1 + entry_delay_bars   (the desk's cloud run starts up to ~1-2 minutes late)
    exit   = close of bar entry + h - 1             (h minutes in the trade)
    gross  = log(exit / entry), in basis points
    cost   = per-share round-trip cost / entry price + 2 x slippage, in basis points
    net    = gross - cost                            (a long trade's result after costs)

Targets (each 1/0, NaN when the label cannot be known):
    up       gross > 0                               (price went up)
    up_net   net > 0                                 (a long trade made money after costs)  <- the main one
    big_up   gross >  cost + move_threshold_bps      (a move worth trading, up)
    big_down gross < -(cost + move_threshold_bps)    (a move worth avoiding, down)
    and `net` itself for an expected-return model.

A label is NaN (never guessed) when the entry or exit minute is missing or the window would run past the
session's close: the desk is flat every night, so no trade spans sessions. `label_end` (exit minute + 1) is
kept for purging: a training row whose label_end is after a test window starts must be dropped.

Limits: one price per minute (no quotes), a fixed cost model, and no partial fills. The backtester (ml/backtest.py)
adds stops, sizing and buying power; these labels only say what an un-stopped h-minute hold would have earned.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TARGETS = ["up", "up_net", "big_up", "big_down"]


def cost_bps(price: pd.Series | float, costs: dict) -> pd.Series | float:
    return float(costs["per_share_round_trip_usd"]) / price * 1e4 + 2 * float(costs["slippage_bps_each_side"])


def build(bars: pd.DataFrame, horizon: int, cfg: dict) -> pd.DataFrame:
    """Labels for every row (decision minute) of cleaned bars, for one horizon in minutes. Pure."""
    delay = int(cfg["entry_delay_bars"])
    g = bars.groupby("session", sort=False)
    e_off, x_off = 1 + delay, 1 + delay + horizon - 1
    entry = g["o"].shift(-e_off)
    exit_ = g["c"].shift(-x_off)
    entry_t = g["t"].shift(-e_off)
    exit_t = g["t"].shift(-x_off)
    gross = np.log(exit_ / entry) * 1e4
    cost = cost_bps(entry, cfg["costs"])
    net = gross - cost
    thr = cost + float(cfg["move_threshold_bps"])
    known = entry.notna() & exit_.notna()
    out = pd.DataFrame(index=bars.index)
    out["entry"] = entry
    out["exit"] = exit_
    out["entry_t"] = entry_t
    out["gross"] = gross.where(known)
    out["cost"] = cost.where(known)
    out["net"] = net.where(known)
    for name, cond in (("up", gross > 0), ("up_net", net > 0), ("big_up", gross > thr), ("big_down", gross < -thr)):
        out[name] = cond.astype(float).where(known)
    out["decision_t"] = bars["t"] + pd.Timedelta(minutes=1)
    out["label_end"] = (exit_t + pd.Timedelta(minutes=1)).where(known)
    out["horizon"] = horizon
    return out


def decision_rows(bars: pd.DataFrame, cfg: dict) -> pd.Series:
    """The minutes the desk would actually decide on: every `decision_every_minutes` from first_decision to
    last_entry (New York time), the way the cloud run is scheduled."""
    s = cfg["session"]
    first = _hhmm(s["first_decision"]) - _hhmm(s["open"])
    last = _hhmm(s["last_entry"]) - _hhmm(s["open"])
    every = int(cfg["decision_every_minutes"])
    # the decision at clock time T uses the bar that ended at T: bar minute index T - open - 1
    m = bars["minute"] + 1
    return (m >= first) & (m <= last) & ((m - first) % every == 0)


def _hhmm(s: str) -> int:
    h, mm = s.split(":")
    return int(h) * 60 + int(mm)
