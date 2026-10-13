"""The crypto LEARNING book: a small, busier book in the crypto paper account, expected to lose a little.

Rules (RESEARCH_NOTES.md, Oct 10 2026, written before any run; not tuned afterwards):
    coin   SOL/USD only, never BTC or ETH (those belong to the trend200 book)
    rule   mom7d: hold while the last complete UTC daily close is above the close 7 days earlier
    size   $45 a buy (at most 98% of the cash, never under $10), one position at a time
    stop   sell when the price is 6% or more under the buy price at an hourly check;
           after a stop, no new buy until the next UTC day
    limit  at most one buy and one sell a day (dated order ids)

Worst case per trade: about $2.70 plus fees at the stop, more only if the price jumps past the stop between
hourly checks, never more than the $45 in the trade (long only, no leverage).

    python3 crypto_learning.py        # the one expectation check on past SOL prices (prints a table)
"""

from __future__ import annotations

from datetime import date, datetime, timezone

COST = 0.003  # same as crypto_research.py: 0.25% fee plus a little slippage, each side
DEFAULTS = {"enabled": True, "symbol": "SOL/USD", "rule": "mom7d", "usd": 45, "stop_pct": 6}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("learning") or {})}


def wants(closes: list[float]) -> bool:
    """mom7d on complete daily closes, oldest first."""
    return len(closes) >= 8 and closes[-1] > closes[-8]


def stopped_today(journal: list[dict], symbol: str, today: date) -> bool:
    return any(e.get("event") == "order" and e.get("book") == "learning" and e.get("symbol") == symbol
               and e.get("why") == "stop" and str(e.get("ts", ""))[:10] == today.isoformat() for e in journal)


def decide(closes: list[float], position: dict | None, cash: float, ls: dict, stopped: bool) -> list[dict]:
    """Orders for the learning book. `position` is Alpaca's SOLUSD position (or None)."""
    sym, want = ls["symbol"], wants(closes)
    if position:
        entry, price = float(position["avg_entry_price"]), float(position["current_price"])
        est = round(float(position["qty"]) * (price - entry), 2)
        if price <= entry * (1 - ls["stop_pct"] / 100):
            return [{"book": "learning", "symbol": sym, "side": "sell", "qty": position["qty"], "why": "stop",
                     "entry": entry, "price": price, "est_pnl": est}]
        if not want:
            return [{"book": "learning", "symbol": sym, "side": "sell", "qty": position["qty"], "why": "mom7d_down",
                     "entry": entry, "price": price, "est_pnl": est}]
        return []
    if want and not stopped:
        amount = round(min(float(ls["usd"]), cash * 0.98), 2)
        if amount >= 10:
            return [{"book": "learning", "symbol": sym, "side": "buy", "notional": amount, "why": "mom7d_up"}]
    return []


def summary(journal: list[dict], position: dict | None, closes: list[float], ls: dict) -> dict:
    """What the Crypto page shows for the learning book."""
    orders = [e for e in journal if e.get("event") == "order" and e.get("book") == "learning"]
    sells = [e for e in orders if e.get("side") == "sell" and e.get("est_pnl") is not None]
    open_pl = float(position["unrealized_pl"]) if position else 0.0
    closed = round(sum(float(e["est_pnl"]) for e in sells), 2)
    return {"symbol": ls["symbol"], "rule": ls["rule"], "usd": ls["usd"], "stop_pct": ls["stop_pct"],
            "enabled": bool(ls["enabled"]), "hold": wants(closes),
            "close": closes[-1] if closes else None, "close_7d_ago": closes[-8] if len(closes) >= 8 else None,
            "position": position, "stop_price": round(float(position["avg_entry_price"]) * (1 - ls["stop_pct"] / 100), 4)
            if position else None, "trades": len(sells), "wins": sum(1 for e in sells if float(e["est_pnl"]) > 0),
            "closed_pnl": closed, "open_pnl": round(open_pl, 2), "total_pnl": round(closed + open_pl, 2),
            "orders": list(reversed(orders))[:30]}


def backtest(hourly: list[dict], ls: dict = DEFAULTS) -> dict:
    """Replay the rule on hourly bars ({"t", "o", "c"}): each hourly run acts at the bar's open, fees both sides."""
    days: list[date] = []
    closes: list[float] = []
    entry = qty = None
    stop_day = None
    trades = []
    for b in hourly:
        d = b["t"].date()
        if not days or d != days[-1]:
            days.append(d)
            closes.append(b["c"])
        else:
            closes[-1] = b["c"]
        done = closes[:-1]  # complete days only
        price = b["o"]
        if entry is not None:
            why = "stop" if price <= entry * (1 - ls["stop_pct"] / 100) else None if wants(done) else "mom7d_down"
            if why:
                pnl = qty * price * (1 - COST) - float(ls["usd"])
                trades.append({"day": d, "pnl": pnl, "why": why})
                entry = qty = None
                if why == "stop":
                    stop_day = d
                continue
        if entry is None and wants(done) and stop_day != d:
            entry, qty = price, float(ls["usd"]) * (1 - COST) / price
    years = max(1, (hourly[-1]["t"] - hourly[0]["t"]).days) / 365.25 if hourly else 1
    by_year: dict[int, float] = {}
    for t in trades:
        by_year[t["day"].year] = by_year.get(t["day"].year, 0.0) + t["pnl"]
    return {"trades": len(trades), "trades_per_year": round(len(trades) / years, 1),
            "wins": sum(1 for t in trades if t["pnl"] > 0), "total": round(sum(t["pnl"] for t in trades), 2),
            "worst": round(min((t["pnl"] for t in trades), default=0.0), 2),
            "best": round(max((t["pnl"] for t in trades), default=0.0), 2),
            "stops": sum(1 for t in trades if t["why"] == "stop"),
            "by_year": {y: round(v, 2) for y, v in sorted(by_year.items())}}


def fetch(symbol: str = "SOL/USD") -> list[dict]:
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame

    bars = CryptoHistoricalDataClient().get_crypto_bars(CryptoBarsRequest(
        symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, start=datetime(2021, 1, 1, tzinfo=timezone.utc))).data[symbol]
    return [{"t": b.timestamp.astimezone(timezone.utc), "o": float(b.open), "c": float(b.close)} for b in bars]


if __name__ == "__main__":
    h = fetch()
    r = backtest(h)
    print(f"SOL/USD {h[0]['t']:%Y-%m-%d} to {h[-1]['t']:%Y-%m-%d}, $45 a trade, {COST:.2%} each side")
    for k, v in r.items():
        print(f"  {k}: {v}")
