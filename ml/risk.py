"""The risk engine: independent of every model. A model may suggest a trade; only this decides if and how big.

Pure: the caller passes the state (account, positions, today's results, data age, the clock). Fails closed: any
missing or bad input means NO new order. It never raises a size because a model is confident.

Defaults (ml/default_config.json "risk"), conservative for a $1,000 paper account:
    risk_per_trade_pct 0.5      loss at the stop is at most 0.5% of equity ($5 on $1,000)
    max_position_pct   80       notional at most 80% of equity, and never more than the buying power
    max_exposure_pct   80       all open positions together
    max_daily_loss_pct 1.5      no new entries once today's loss reaches 1.5%
    max_consecutive_losses 3    no new entries for the rest of the day after 3 losses in a row
    max_positions      1        one position at a time
    max_orders_per_day 8        entries and exits together
    max_spread_bps     5        when a quote is known; unknown spread is allowed only for SPY-like liquidity
    max_data_age_minutes 2      the last bar must be at most 2 minutes old
    cooldown_after_loss_minutes 30
    model_timeout_ms   2000     a slower prediction counts as a failed model
    allow_short false, live false
Kill switch: ml_kill_switch.json {"on": true, "why": ...} in the desk folder stops new orders at once. Open
positions are NOT abandoned: exits stay allowed (they only reduce risk) and the desk's 15:40 flatten still runs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

DEFAULTS = {
    "risk_per_trade_pct": 0.5, "max_position_pct": 80.0, "max_exposure_pct": 80.0, "max_daily_loss_pct": 1.5,
    "max_consecutive_losses": 3, "max_positions": 1, "max_orders_per_day": 8, "max_spread_bps": 5.0,
    "max_data_age_minutes": 2, "cooldown_after_loss_minutes": 30, "model_timeout_ms": 2000,
    "min_stop_bps": 8.0, "allow_fractional": True, "allow_short": False, "live": False,
}


@dataclass
class Verdict:
    ok: bool
    qty: float = 0.0
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "qty": self.qty, "passed": self.passed, "failed": self.failed}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("risk") or {})}


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def check_entry(st: dict, rs: dict) -> Verdict:
    """May a NEW long position be opened, and how many shares? `st` keys:
    equity, buying_power, exposure_usd, positions, day_pnl, consecutive_losses, orders_today,
    minutes_since_loss, data_age_minutes, in_session, model_ok, model_ms, kill_switch, price, stop_bps,
    spread_bps (None if unknown), side ("long"), mode ("paper"/"backtest")."""
    v = Verdict(ok=False)

    def rule(name: str, cond: bool) -> None:
        (v.passed if cond else v.failed).append(name)

    equity, bp, price, stop = (_num(st.get(k)) for k in ("equity", "buying_power", "price", "stop_bps"))
    rule("kill_switch_off", not st.get("kill_switch"))
    rule("not_live", not rs.get("live") and st.get("mode", "paper") in ("paper", "backtest", "shadow"))
    rule("long_only", st.get("side", "long") == "long" or bool(rs.get("allow_short")))
    rule("inputs_known", None not in (equity, bp, price, stop) and equity > 0 and price > 0 and stop > 0)
    rule("in_session", bool(st.get("in_session")))
    age = _num(st.get("data_age_minutes"))
    rule("data_fresh", age is not None and age <= rs["max_data_age_minutes"])
    ms = _num(st.get("model_ms"))
    rule("model_healthy", bool(st.get("model_ok")) and ms is not None and ms <= rs["model_timeout_ms"])
    rule("positions_below_max", int(st.get("positions") or 0) < int(rs["max_positions"]))
    rule("orders_below_max", int(st.get("orders_today") or 0) < int(rs["max_orders_per_day"]))
    day = _num(st.get("day_pnl"))
    rule("daily_loss_ok", day is not None and equity is not None and day > -equity * rs["max_daily_loss_pct"] / 100)
    rule("loss_streak_ok", int(st.get("consecutive_losses") or 0) < int(rs["max_consecutive_losses"]))
    since = _num(st.get("minutes_since_loss"))
    rule("cooldown_over", since is None or since >= rs["cooldown_after_loss_minutes"])
    spread = _num(st.get("spread_bps"))
    rule("spread_ok", spread is None or spread <= rs["max_spread_bps"])
    rule("stop_wide_enough", stop is not None and stop >= rs["min_stop_bps"])
    if v.failed:
        return v
    # size: the loss at the stop is at most risk_per_trade_pct of equity, then every cap
    risk_usd = equity * rs["risk_per_trade_pct"] / 100
    qty = risk_usd / (price * stop / 1e4)
    exposure = _num(st.get("exposure_usd")) or 0.0
    cap_usd = min(equity * rs["max_position_pct"] / 100, equity * rs["max_exposure_pct"] / 100 - exposure, bp)
    qty = min(qty, max(0.0, cap_usd) / price)
    qty = math.floor(qty * 1000) / 1000 if rs["allow_fractional"] else float(math.floor(qty))
    rule("size_positive", qty > 0)
    v.qty = qty if not v.failed else 0.0
    v.ok = not v.failed
    return v


def check_exit(st: dict, rs: dict) -> Verdict:
    """Closing an existing position only reduces risk: allowed even with the kill switch on or a stale feed.
    Refused only without a position or in live mode."""
    v = Verdict(ok=False)
    has = _num(st.get("position_qty")) or 0.0
    (v.passed if has > 0 else v.failed).append("has_position")
    (v.passed if not rs.get("live") else v.failed).append("not_live")
    v.ok = not v.failed
    v.qty = has if v.ok else 0.0
    return v
