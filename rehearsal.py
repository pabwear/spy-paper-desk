"""Dress rehearsal: the real desk program, heartbeat by heartbeat, over past days, with a simulated broker.

Unlike the backtest (which re-implements the trading rule), this runs run_study.cmd_tick itself every
10 minutes from 09:00 to 16:50 New York time, exactly as the cloud does: auto boxes at 09:39, the gate,
the contract choice from priced quotes, entries, the stop / time / flatten exits, fills into trades.csv,
the 16:10 review and learner, the charts and the dashboard state. Nothing touches Alpaca or real files:
it works in a temporary desk folder, and the broker below fills orders on paper inside this process.

Option prices come from Black-Scholes on SPY's price at that minute, with the day's VIX as volatility
(backtest_areas.bs_price), plus a 2-cent half-spread each way. Sentiment readings aren't replayed (no
history exists), so Market Pulse stays neutral.

    python3 rehearsal.py --days 5            # the last 5 trading days of free 1-minute data (yfinance)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from common import ET

DESK = Path(__file__).resolve().parent
DESK_FILES = ["watchlist.json", "alpaca_config.json", "rules.json", "risk.json", "study.json", "account.json",
              "aoi_override.json", "learning_weights.json", "market_pulse.json", "trades.csv", "console_pin.json"]
HALF_SPREAD = 0.02


class SimBroker:
    """Behaves like alpaca_client.PaperBroker for the desk, filling at modelled option prices."""

    base_url = "https://paper-api.alpaca.markets"
    is_paper = True
    account_number = "PA36VOEO5PHB"

    def __init__(self, minute_bars: list[dict], vix: dict[date, float], cash: float = 1000.0):
        self.bars = sorted(minute_bars, key=lambda b: b["t"])
        self.vix = vix
        self.cash = cash
        self.now: datetime | None = None
        self.pos: dict[str, dict] = {}
        self.fills: list[dict] = []
        self.n = 0

    # -- the simulated market
    def spy(self) -> float:
        last = None
        for b in self.bars:
            if b["t"] <= self.now:
                last = b
            else:
                break
        return float(last["c"]) if last else 0.0

    def _vol(self) -> float:
        d = self.now.astimezone(ET).date()
        prior = [k for k in self.vix if k < d]
        return self.vix[max(prior)] / 100 if prior else 0.16

    def mid(self, symbol: str) -> float:
        from backtest_areas import bs_price, trading_years
        from instruments import parse_occ

        o = parse_occ(symbol)
        return bs_price(self.spy(), o["strike"], trading_years(self.now, o["expiry"]), self._vol(), o["right"] == "call")

    # -- what the desk calls
    def positions(self):
        out = []
        for sym, p in self.pos.items():
            px = self.mid(sym)
            out.append({"symbol": sym, "qty": p["qty"], "avg_entry_price": p["avg"], "current_price": round(px, 2),
                        "market_value": round(px * 100 * p["qty"], 2),
                        "unrealized_pl": round((px - p["avg"]) * 100 * p["qty"], 2), "asset_class": "us_option"})
        return out

    def equity(self) -> float:
        return round(self.cash + sum(x["market_value"] for x in self.positions()), 2)

    def account_snapshot(self):
        ps = self.positions()
        eq = self.equity()
        return {"source": "alpaca_paper", "account_name": "Paper 1000 (rehearsal)", "account_number": self.account_number,
                "status": "ACTIVE", "snapshot_at": self.now.astimezone(ET).isoformat(timespec="seconds"),
                "equity": eq, "cash": round(self.cash, 2), "buying_power": round(self.cash, 2),
                "options_buying_power": round(self.cash, 2), "last_equity": eq,
                "position": ps[0] if ps else None, "positions": ps}

    def market_open(self):
        t = self.now.astimezone(ET)
        return t.weekday() < 5 and 570 <= t.hour * 60 + t.minute < 960

    def open_orders(self):
        return []

    def filled_orders_since(self, since):
        return [f for f in self.fills if f["filled_at_dt"] >= since]

    def option_contracts(self, right, around, today, underlying="SPY"):
        from backtest_areas import expiry_for
        from instruments import occ_symbol

        out, seen = [], set()
        for d in range(0, 50):
            e = expiry_for(today + timedelta(days=d))
            if e in seen:
                continue
            seen.add(e)
            for k in range(int(around) - 5, int(around) + 6):
                out.append({"symbol": occ_symbol(underlying, e, right, k), "expiry": e, "right": right,
                            "strike": float(k), "tradable": True})
        return out

    def option_asks(self, symbols):
        return {s: round(self.mid(s) + HALF_SPREAD, 2) for s in symbols}

    def submit_market(self, side, qty, client_order_id, symbol="SPY", intent=None):
        px = round(self.mid(symbol) + (HALF_SPREAD if side == "buy" else -HALF_SPREAD), 2)
        px = max(px, 0.01)
        self.n += 1
        oid = f"sim-{self.n}"
        qty = float(qty)
        cost = px * 100 * qty
        p = self.pos.get(symbol)
        if side == "buy":
            if cost > self.cash + 1e-9:
                raise RuntimeError("insufficient options buying power")
            self.cash -= cost
            if p:
                p["avg"] = (p["avg"] * p["qty"] + px * qty) / (p["qty"] + qty)
                p["qty"] += qty
            else:
                self.pos[symbol] = {"qty": qty, "avg": px}
        else:
            self.cash += cost
            if p:
                p["qty"] -= qty
                if abs(p["qty"]) < 1e-9:
                    del self.pos[symbol]
        t = self.now.astimezone(ET)
        self.fills.append({"order_id": oid, "symbol": symbol, "side": side, "qty": qty, "price": px,
                           "filled_at": t.isoformat(timespec="seconds"), "filled_at_dt": t, "status": "filled"})
        return {"order_id": oid, "status": "filled", "submitted_at": t.isoformat(timespec="seconds")}


def round_trips(fills: list[dict], exits: list[dict]) -> list[dict]:
    """Pair each buy with the sell that closes it: times, prices, dollars and why it was sold."""
    out, open_ = [], {}
    why = {e["ts"][:16]: e.get("reason") for e in exits}
    for f in fills:
        if f["side"] == "buy":
            open_[f["symbol"]] = f
        elif f["symbol"] in open_:
            b = open_.pop(f["symbol"])
            out.append({"symbol": f["symbol"], "opened_at": b["filled_at"], "closed_at": f["filled_at"],
                        "entry_price": b["price"], "exit_price": f["price"],
                        "pnl": round((f["price"] - b["price"]) * 100 * f["qty"], 2), "exit_reason": why.get(f["filled_at"][:16])})
    return out


def heartbeats(day: date, every: int = 10):
    t = datetime(day.year, day.month, day.day, 9, 0, tzinfo=ET)
    end = datetime(day.year, day.month, day.day, 16, 50, tzinfo=ET)
    while t <= end:
        yield t
        t += timedelta(minutes=every)


def run(minute_bars: list[dict], half_hours: list[dict], dailies: list[dict], vix: dict[date, float],
        days: list[date], keep: str | None = None) -> dict:
    """Replay the desk over `days` in a temporary desk folder. Returns what happened."""
    tmp = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="rehearsal-"))
    tmp.mkdir(parents=True, exist_ok=True)
    for name in DESK_FILES:
        if (DESK / name).exists():
            shutil.copy(DESK / name, tmp / name)
    # a clean start: no earlier trades, account, zones or pulse
    (tmp / "trades.csv").unlink(missing_ok=True)
    for name in ("account.json", "aoi_override.json", "market_pulse.json"):
        (tmp / name).write_text("{}")
    old = os.environ.get("DESK_DIR")
    os.environ["DESK_DIR"] = str(tmp)
    import journal
    import run_study

    broker = SimBroker(minute_bars, vix)
    log: list[dict] = []
    errors: list[str] = []
    try:
        for d in days:
            start_eq = None
            for now in heartbeats(d):
                broker.now = now
                if start_eq is None:
                    start_eq = broker.equity()
                bars = [b for b in minute_bars if b["t"] <= now and b["t"] > now - timedelta(days=4)]
                src = {"SPY": bars, "SPY|30Min": [b for b in half_hours if b["t"] <= now],
                       "SPY|1Day": [b for b in dailies if b["t"].astimezone(ET).date() < d]}
                try:
                    state = run_study.cmd_tick(now, broker_factory=lambda: broker, bars=src)
                except Exception as e:  # noqa: BLE001 - a crash is exactly what a rehearsal is for
                    errors.append(f"{now:%m-%d %H:%M} {type(e).__name__}: {e}")
                    state = "crash"
                log.append({"t": now.isoformat(timespec="minutes"), "state": state, "equity": broker.equity(),
                            "open": [p["symbol"] for p in broker.positions()]})
        events = journal.read_events()
    finally:
        if old is None:
            os.environ.pop("DESK_DIR", None)
        else:
            os.environ["DESK_DIR"] = old
    orders = [e for e in events if e.get("event") == "order"]
    entries = [e for e in orders if e.get("role") == "entry"]
    exits = [e for e in orders if e.get("role") == "exit"]
    failures = [e for e in events if str(e.get("event", "")).endswith("_failed") or e.get("event") in ("order_rejected",)]
    skips: dict[str, int] = {}
    for e in events:
        if e.get("event") == "skip":
            for r in e.get("reasons") or []:
                skips[r] = skips.get(r, 0) + 1
    by_day = {}
    for d in days:
        ds = d.isoformat()
        by_day[ds] = {"entries": sum(1 for e in entries if e["ts"].startswith(ds)),
                      "exits": [e.get("reason") for e in exits if e["ts"].startswith(ds)],
                      "zones": next((e.get("zones") for e in events if e.get("event") == "aoi" and e["ts"].startswith(ds)), None),
                      "equity_end": next((x["equity"] for x in reversed(log) if x["t"].startswith(ds)), None)}
    return {"ran_at": datetime.now(ET).isoformat(timespec="seconds"), "folder": str(tmp),
            "days": [d.isoformat() for d in days], "heartbeats": len(log),
            "equity": [[x["t"], x["equity"]] for x in log], "trades": round_trips(broker.fills, exits),
            "crashes": errors, "failures": [{k: e.get(k) for k in ("ts", "event", "reasons", "error", "reason", "symbol")}
                                            for e in failures],
            "entries": [{k: e.get(k) for k in ("ts", "symbol", "strike", "expiry", "underlying_price", "zone", "tags")}
                        for e in entries],
            "exits": [{k: e.get(k) for k in ("ts", "symbol", "reason", "underlying_price", "stop_level")} for e in exits],
            "fills": [{k: f[k] for k in ("filled_at", "symbol", "side", "qty", "price")} for f in broker.fills],
            "skip_reasons": dict(sorted(skips.items(), key=lambda kv: -kv[1])), "by_day": by_day,
            "start_equity": 1000.0, "end_equity": broker.equity(), "still_open": broker.positions(),
            "dashboard_ok": (tmp / "dashboard_state.json").exists()}


def _yf(symbol: str, period: str, interval: str) -> list[dict]:
    import yfinance as yf

    df = yf.download(symbol, period=period, interval=interval, prepost=interval != "1d", progress=False,
                     auto_adjust=False, multi_level_index=False)
    out = []
    for ts, r in df.iterrows():
        t = ts.to_pydatetime()
        t = t.replace(tzinfo=ET) if t.tzinfo is None else t.astimezone(ET)
        out.append({"t": t, "o": float(r["Open"]), "h": float(r["High"]), "l": float(r["Low"]), "c": float(r["Close"]),
                    "v": float(r["Volume"])})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--keep", help="keep the rehearsal desk folder here")
    ap.add_argument("--json", help="write the result here")
    a = ap.parse_args(argv)
    minute = _yf("SPY", "7d", "1m")
    half = _yf("SPY", "60d", "30m")
    daily = _yf("SPY", "2y", "1d")
    vix = {b["t"].date(): b["c"] for b in _yf("^VIX", "3mo", "1d")}
    days = sorted({b["t"].astimezone(ET).date() for b in minute if b["t"].astimezone(ET).weekday() < 5})
    # the first day only warms up the study (the boxes need earlier candles)
    days = days[1:][-a.days:]
    res = run(minute, half, daily, vix, days, a.keep)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("days", "heartbeats", "crashes", "entries", "exits", "fills", "skip_reasons",
                                          "by_day", "end_equity", "still_open", "dashboard_ok")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
