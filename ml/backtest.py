"""A cost-aware, minute-by-minute backtester for long-only intraday trades. Research and paper only.

Inputs: cleaned bars (ml.data), a boolean `enter` signal per row (True = the decision on that bar is BUY), the
features (for ATR-sized stops and staleness), and the config. It simulates what the desk would actually do:

  * the decision on bar i is known at t_i + 1 min; the order fills at the OPEN of bar i + 1 + entry_delay_bars,
    plus slippage. Never at a price that was only known after the decision.
  * every entry goes through ml.risk.check_entry first (sizing from the stop distance, buying power, daily loss,
    loss streak, cooldown, order count, staleness, session). Refusals are counted with their reason.
  * exits, checked on each minute bar from the fill on:
        flatten      the bar at or after the flatten time (15:40): out at its open
        stop_gap     the bar OPENS through the stop: out at that open (worse than the stop)
        stop         the bar's low reaches the stop: out at the stop
        take_profit  the bar's high reaches the target (if set)
        time         the close of the last bar of the holding period (entry bar + horizon - 1)
    When one bar reaches both the stop and the target, the STOP is assumed first (one price per minute cannot
    tell the order; this is the conservative choice). Missing minutes are skipped; a time exit that lands on a
    missing minute uses the next real close.
  * costs: slippage in bp on every fill and the per-share round-trip cost (half each side).
  * one position at a time, a cooldown after each exit, at most `max_entries_per_day`, no shorting, flat by
    the session's end. Cash-only buying power (no margin).
Outputs trades, rejections, a per-minute equity curve and the settings used. Pure.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

from ml import risk as riskmod

STRATEGY_DEFAULTS = {"stop_atr_mult": 3.0, "take_profit_atr_mult": None, "cooldown_minutes": 10,
                     "max_entries_per_day": 4, "flatten": "15:40"}


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def run(bars: pd.DataFrame, enter: pd.Series, feats: pd.DataFrame, cfg: dict, horizon: int,
        start_equity: float = 1000.0) -> dict:
    st = {**STRATEGY_DEFAULTS, **(cfg.get("strategy") or {})}
    rs = riskmod.settings(cfg)
    ses = cfg["session"]
    open_m = _hhmm(ses["open"])
    flat_min = _hhmm(st["flatten"]) - open_m
    first_dec, last_entry = _hhmm(ses["first_decision"]) - open_m, _hhmm(ses["last_entry"]) - open_m
    delay = int(cfg["entry_delay_bars"])
    slip = float(cfg["costs"]["slippage_bps_each_side"]) / 1e4
    per_share_side = float(cfg["costs"]["per_share_round_trip_usd"]) / 2

    o, h, l, c = (bars[k].to_numpy(float) for k in ("o", "h", "l", "c"))
    miss = bars["missing"].to_numpy(bool)
    minute = bars["minute"].to_numpy(int)
    sess = bars["session"].to_numpy()
    tt = bars["t"].to_numpy()
    sig = enter.reindex(bars.index).fillna(False).to_numpy(bool)
    atr = feats["atr_14"].to_numpy(float)
    stale = feats["stale"].to_numpy(float)
    cf = pd.Series(c).groupby(sess).ffill().to_numpy(float)

    cash = start_equity
    trades, rejects = [], Counter()
    eq = np.empty(len(bars))
    pos = None          # open position
    pending = None      # approved entry waiting for its fill bar
    day, day_start_eq, n_entries, n_orders, streak, last_exit_i, last_loss_i = None, cash, 0, 0, 0, -10**9, None

    def close_pos(i: int, px: float, why: str) -> None:
        nonlocal cash, pos, n_orders, streak, last_exit_i, last_loss_i
        fill = px * (1 - slip)
        proceeds = pos["qty"] * fill - pos["qty"] * per_share_side
        pnl = proceeds - pos["cost_usd"]
        cash += proceeds
        trades.append({"decision_t": pos["dec"], "entry_t": pos["t"], "entry": pos["px"], "exit_t": tt[i], "exit": fill, "qty": pos["qty"],
                       "pnl": pnl, "ret_bps": pnl / pos["cost_usd"] * 1e4, "why": why, "stop": pos["stop"],
                       "minutes": int(minute[i] - pos["m"]) + (0 if why in ("flatten", "stop_gap") else 1),
                       "costs": pos["qty"] * (pos["raw"] * slip + px * slip + 2 * per_share_side)})
        n_orders += 1
        streak = streak + 1 if pnl < 0 else 0
        last_exit_i = i
        if pnl < 0:
            last_loss_i = i
        pos = None

    for i in range(len(bars)):
        if sess[i] != day:  # a new session: anything left open is closed at the last real close
            if pos is not None:
                close_pos(i - 1, cf[i - 1], "session_end")
            pending = None
            day, day_start_eq, n_entries, n_orders, streak, last_loss_i = sess[i], cash, 0, 0, 0, None
        # 1. a pending entry fills at this bar's open
        if pending is not None and pending["i"] == i:
            if miss[i]:
                rejects["entry_bar_missing"] += 1
            else:
                px = o[i] * (1 + slip)
                qty = min(pending["qty"], cash / (px + per_share_side))
                if qty <= 0:
                    rejects["no_buying_power_at_fill"] += 1
                else:
                    cost_usd = qty * px + qty * per_share_side
                    cash -= cost_usd
                    stop_px = o[i] * (1 - pending["stop_bps"] / 1e4)
                    tp = pending["tp_bps"]
                    pos = {"t": tt[i], "px": px, "raw": o[i], "qty": qty, "cost_usd": cost_usd, "stop": stop_px, "m": minute[i],
                           "tp": o[i] * (1 + tp / 1e4) if tp else None, "due": i + horizon - 1, "i": i,
                           "dec": pending["dec"]}
                    n_entries += 1
                    n_orders += 1
            pending = None
        # 2. exits on this bar (the fill bar included)
        if pos is not None and not miss[i]:
            if minute[i] >= flat_min:
                close_pos(i, o[i], "flatten")
            elif o[i] <= pos["stop"]:
                close_pos(i, o[i], "stop_gap")
            elif l[i] <= pos["stop"]:
                close_pos(i, pos["stop"], "stop")
            elif pos["tp"] is not None and h[i] >= pos["tp"]:
                close_pos(i, pos["tp"], "take_profit")
            elif i >= pos["due"]:
                close_pos(i, c[i], "time" if i == pos["due"] else "time_late")
        # 3. a new decision on this bar
        if sig[i]:
            if pos is not None or pending is not None:
                rejects["already_in_position"] += 1
            elif not (first_dec <= minute[i] + 1 <= last_entry):
                rejects["outside_entry_hours"] += 1
            elif minute[i] + 1 + delay + horizon - 1 >= flat_min:
                rejects["too_late_for_horizon"] += 1
            elif i - last_exit_i < int(st["cooldown_minutes"]):
                rejects["cooldown"] += 1
            elif n_entries >= int(st["max_entries_per_day"]):
                rejects["max_entries_per_day"] += 1
            else:
                stop_bps = max(rs["min_stop_bps"], float(st["stop_atr_mult"]) * atr[i]) if np.isfinite(atr[i]) else None
                tp_bps = float(st["take_profit_atr_mult"]) * atr[i] if st.get("take_profit_atr_mult") and np.isfinite(atr[i]) else None
                verdict = riskmod.check_entry({
                    "equity": cash, "buying_power": cash, "exposure_usd": 0.0, "positions": 0,
                    "day_pnl": cash - day_start_eq, "consecutive_losses": streak, "orders_today": n_orders,
                    "minutes_since_loss": None if last_loss_i is None else i - last_loss_i,
                    "data_age_minutes": stale[i] if np.isfinite(stale[i]) else None, "in_session": True,
                    "model_ok": True, "model_ms": 0, "kill_switch": False, "price": cf[i], "stop_bps": stop_bps,
                    "spread_bps": None, "side": "long", "mode": "backtest"}, rs)
                if verdict.ok:
                    j = i + 1 + delay
                    if j < len(bars) and sess[j] == sess[i]:
                        pending = {"i": j, "qty": verdict.qty, "stop_bps": stop_bps, "tp_bps": tp_bps,
                                   "dec": tt[i] + np.timedelta64(1, "m")}
                    else:
                        rejects["no_bar_to_fill"] += 1
                else:
                    rejects["risk:" + verdict.failed[0]] += 1
        eq[i] = cash + (pos["qty"] * cf[i] if pos is not None else 0.0)
    if pos is not None:
        close_pos(len(bars) - 1, cf[-1], "session_end")
        eq[-1] = cash
    tdf = pd.DataFrame(trades)
    return {"trades": tdf, "equity": pd.Series(eq, index=pd.DatetimeIndex(bars["t"]), name="equity"),
            "rejections": dict(rejects), "start_equity": start_equity, "end_equity": float(cash),
            "settings": {"strategy": st, "risk": rs, "horizon": horizon, "entry_delay_bars": delay,
                         "costs": cfg["costs"]}}
