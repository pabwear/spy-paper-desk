"""Journal (every eval, skip, order, fill, snapshot, review) and the trades ledger.

journal.jsonl is append-only, one JSON event per line.
trades.csv has one row per paper fill, with average-cost position and realized P&L.
Rows are only ever written from fills reported by the Alpaca paper account.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime

import instruments
from common import ET, now_et, path, to_et

JOURNAL = "journal.jsonl"
TRADES = "trades.csv"
TRADE_FIELDS = [
    "filled_at", "order_id", "symbol", "asset", "multiplier", "role", "side", "qty", "price", "notional",
    "position_qty_after", "avg_cost_after", "realized_pnl", "exit_reason", "underlying_price",
    "zone_color", "zone_low", "zone_high", "confluence", "score", "pulse_bias", "notes",
]


# ---------------------------------------------------------------- journal

def log(event: str, now: datetime | None = None, **fields) -> dict:
    entry = {"ts": (now or now_et()).astimezone(ET).isoformat(timespec="seconds"), "event": event, **fields}
    with path(JOURNAL).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    return entry


def read_events() -> list[dict]:
    p = path(JOURNAL)
    if not p.exists():
        return []
    events = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def events_on(events: list[dict], day) -> list[dict]:
    return [e for e in events if (to_et(e.get("ts")) or datetime.min.replace(tzinfo=ET)).date() == day]


def order_context(order_id: str, events: list[dict]) -> dict:
    for e in reversed(events):
        if e.get("event") == "order" and e.get("order_id") == order_id:
            return e
    return {}


# ---------------------------------------------------------------- ledger

def read_trades() -> list[dict]:
    p = path(TRADES)
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def ensure_ledger() -> None:
    p = path(TRADES)
    if not p.exists() or p.stat().st_size == 0:
        with p.open("w", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=TRADE_FIELDS).writeheader()


def position_from(trades: list[dict], symbol: str = "SPY") -> tuple[float, float]:
    """(signed qty, average cost) for this symbol after its last ledger row."""
    rows = [t for t in trades if (t.get("symbol") or "SPY") == symbol]
    if not rows:
        return 0.0, 0.0
    last = rows[-1]
    return float(last["position_qty_after"] or 0), float(last["avg_cost_after"] or 0)


def apply_fill(qty_pos: float, avg: float, side: str, qty: float, price: float,
               mult: int = 1) -> tuple[float, float, float | None]:
    """Average-cost position update. Returns (new qty, new avg cost, realized P&L or None).

    Prices are per share (option premiums are quoted per share); mult is 100 for an option contract.
    """
    signed = qty if side == "buy" else -qty
    realized = None
    if qty_pos == 0 or (qty_pos > 0) == (signed > 0):
        new_qty = qty_pos + signed
        new_avg = (abs(qty_pos) * avg + qty * price) / abs(new_qty)
        return round(new_qty, 6), round(new_avg, 6), realized
    closing = min(abs(signed), abs(qty_pos))
    sign = 1.0 if qty_pos > 0 else -1.0
    realized = round((price - avg) * closing * sign * mult, 4)
    new_qty = qty_pos + signed
    if abs(new_qty) < 1e-9:
        return 0.0, 0.0, realized
    if (new_qty > 0) == (qty_pos > 0):
        return round(new_qty, 6), avg, realized
    return round(new_qty, 6), price, realized  # flipped through flat: remainder opens at fill price


def record_fill(*, filled_at: str, order_id: str, side: str, qty: float, price: float, context: dict,
                symbol: str = "SPY") -> dict:
    ensure_ledger()
    trades = read_trades()
    if any(t["order_id"] == order_id for t in trades):
        return {}
    mult = instruments.multiplier(symbol)
    pos, avg = position_from(trades, symbol)
    new_pos, new_avg, realized = apply_fill(pos, avg, side, qty, price, mult)
    zone = context.get("zone") or {}
    row = {
        "filled_at": filled_at,
        "order_id": order_id,
        "symbol": symbol,
        "asset": "option" if mult == 100 else "shares",
        "multiplier": mult,
        "role": context.get("role", ""),
        "side": side,
        "qty": qty,
        "price": price,
        "notional": round(qty * price * mult, 2),
        "position_qty_after": new_pos,
        "avg_cost_after": new_avg,
        "realized_pnl": "" if realized is None else realized,
        "exit_reason": context.get("reason", ""),
        "underlying_price": context.get("underlying_price", ""),
        "zone_color": zone.get("color", ""),
        "zone_low": zone.get("low", ""),
        "zone_high": zone.get("high", ""),
        "confluence": "|".join(context.get("tags", []) or []),
        "score": context.get("score", ""),
        "pulse_bias": context.get("pulse_bias", ""),
        "notes": context.get("note", ""),
    }
    with path(TRADES).open("a", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=TRADE_FIELDS).writerow(row)
    return row


def entries_on(events: list[dict], day) -> int:
    """Entry orders the paper account accepted on this day."""
    return sum(1 for e in events_on(events, day) if e.get("event") == "order" and e.get("role") == "entry")


def round_trips(trades: list[dict], events: list[dict]) -> list[dict]:
    """Flat → open → flat, per symbol, joined with the entry order's setup and the exit's reason."""
    open_trips: dict[str, dict] = {}
    done: list[dict] = []
    for t in trades:
        sym = t.get("symbol") or "SPY"
        after = float(t.get("position_qty_after") or 0)
        trip = open_trips.get(sym)
        if trip is None:
            ctx = order_context(t["order_id"], events)
            trip = open_trips[sym] = {
                "symbol": sym, "asset": t.get("asset") or "shares", "opened_at": t["filled_at"],
                "entry_side": t["side"], "entry_price": float(t["price"]), "qty": float(t["qty"]),
                "pnl": 0.0, "exposure": instruments.direction(sym, float(t["qty"]) * (1 if t["side"] == "buy" else -1)),
                "features": ctx.get("features") or {}, "tags": ctx.get("tags") or [], "zone": ctx.get("zone"),
                "pulse_bias": ctx.get("pulse_bias"), "underlying_entry": ctx.get("underlying_price"),
                "entry_number": ctx.get("entry_number"), "p_loss": ctx.get("p_loss"),
            }
        if t.get("realized_pnl") not in ("", None):
            trip["pnl"] = round(trip["pnl"] + float(t["realized_pnl"]), 4)
        if abs(after) < 1e-9:
            ctx = order_context(t["order_id"], events)
            trip.update(closed_at=t["filled_at"], exit_price=float(t["price"]),
                        exit_reason=t.get("exit_reason") or ctx.get("reason") or "unknown",
                        result="win" if trip["pnl"] > 0 else "loss" if trip["pnl"] < 0 else "scratch")
            done.append(trip)
            del open_trips[sym]
    return done


# ---------------------------------------------------------------- stats

def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def trade_stats(trades: list[dict]) -> dict:
    """Wins/losses come from closing fills (rows with realized P&L)."""
    closes = [t for t in trades if _f(t.get("realized_pnl")) is not None]
    pnls = [_f(t["realized_pnl"]) for t in closes]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    flats = [p for p in pnls if p == 0]
    gross_win, gross_loss = sum(wins), -sum(losses)

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    streak_kind, streak = None, 0
    for p in reversed(pnls):
        kind = "win" if p > 0 else "loss" if p < 0 else "flat"
        if streak_kind in (None, kind):
            streak_kind, streak = kind, streak + 1
        else:
            break

    n = len(pnls)
    return {
        "fills": len(trades),
        "closed_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "scratches": len(flats),
        "win_rate_pct": round(len(wins) / n * 100, 1) if n else None,
        "realized_pnl": round(sum(pnls), 2),
        "gross_profit": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "avg_win": round(gross_win / len(wins), 2) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
        "largest_win": round(max(wins), 2) if wins else None,
        "largest_loss": round(min(losses), 2) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(sum(pnls) / n, 2) if n else None,
        "max_drawdown": round(max_dd, 2),
        "streak": {"kind": streak_kind, "count": streak} if streak_kind else None,
    }


def daily_realized(trades: list[dict]) -> list[dict]:
    by_day: dict[str, float] = {}
    for t in trades:
        p = _f(t.get("realized_pnl"))
        when = to_et(t.get("filled_at"))
        if p is None or when is None:
            continue
        key = when.date().isoformat()
        by_day[key] = round(by_day.get(key, 0.0) + p, 2)
    return [{"date": d, "pnl": v} for d, v in sorted(by_day.items())]
