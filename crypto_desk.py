"""The crypto desk: its own Alpaca PAPER account (crypto_config.json), separate from the SPY desk.

Rule (crypto_research.py, the only one that passed train, check and exam on both coins): once a day, after the
UTC daily close, hold each coin while its close is above its 200-day average and hold cash when it is below.
Long or cash only (Alpaca does not short crypto), no leverage, fixed dollars per coin.

A small LEARNING book (crypto_learning.py) trades SOL/USD in the same account, $45 at a time with a 6% stop.
It never touches BTC or ETH, so the two books can't sell each other's coins. It is expected to lose a little.

Safety: paper URL only, the account number must match crypto_config.json, live_unlocked must be false, only the
configured coins, never more than per_symbol_usd per buy or more than the cash there is. Keys come from
ALPACA_CRYPTO_API_KEY / ALPACA_CRYPTO_SECRET_KEY (GitHub secrets); they are never the SPY desk's keys.

    python crypto_desk.py run       # check the rule and trade (crypto.yml runs this hourly)
    python crypto_desk.py status    # show the account and today's signals, no orders
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import crypto_learning as learning
import crypto_research as research
import pnl_periods
from common import ET, PAPER_HOST, load_json, path, save_json
from gate import host_of

CONFIG = "crypto_config.json"
JOURNAL = "crypto_journal.jsonl"
STATE = "crypto_state.json"
ACCOUNT = "crypto_account.json"


class Refused(RuntimeError):
    pass


def config() -> dict:
    c = load_json(CONFIG, {}) or {}
    if c.get("live_unlocked") is not False:
        raise Refused("crypto_config.json must say live_unlocked: false. Stop.")
    if not c.get("account_number"):
        raise Refused("crypto_config.json has no account_number. Stop.")
    return c


def log(event: str, now: datetime, **data) -> dict:
    e = {"ts": now.isoformat(timespec="seconds"), "event": event, **data}
    with open(path(JOURNAL), "a", encoding="utf-8") as f:
        f.write(json.dumps(e, default=str) + "\n")
    return e


def read_journal() -> list[dict]:
    p = path(JOURNAL)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def pos_key(sym: str) -> str:
    return sym.replace("/", "")  # Alpaca lists BTC/USD positions as BTCUSD


def signal(closes: list[float], rule: str) -> dict:
    """Today's call from complete daily closes (oldest first), with the numbers behind it."""
    n = 200 if rule == "trend200" else 50
    want = research.signals(closes, rule)[-1] if closes else False
    avg = sum(closes[-n:]) / n if len(closes) >= n else None
    return {"hold": bool(want), "close": closes[-1] if closes else None, "average": round(avg, 2) if avg else None,
            "days": len(closes)}


def decide(signals: dict[str, dict], positions: dict[str, dict], cash: float, cfg: dict) -> list[dict]:
    """Orders to send. Buys only when the rule says hold and nothing is held; sells everything when it says cash."""
    out, budget = [], float(cfg["per_symbol_usd"])
    left = cash
    for sym in cfg["symbols"]:
        s, held = signals.get(sym), positions.get(pos_key(sym))
        if not s or s.get("close") is None:
            continue
        if s["hold"] and not held:
            amount = round(min(budget, left * 0.98), 2)  # leave room for the fee
            if amount >= 10:
                out.append({"symbol": sym, "side": "buy", "notional": amount, "why": "above_200d"})
                left -= amount
        elif not s["hold"] and held:
            out.append({"symbol": sym, "side": "sell", "qty": held["qty"], "why": "below_200d"})
    return out


class CryptoBroker:
    """alpaca-py TradingClient on the crypto PAPER account only."""

    def __init__(self, cfg: dict) -> None:
        from alpaca.trading.client import TradingClient

        key, secret = os.environ.get("ALPACA_CRYPTO_API_KEY"), os.environ.get("ALPACA_CRYPTO_SECRET_KEY")
        if not key or not secret:
            raise Refused("ALPACA_CRYPTO_API_KEY / ALPACA_CRYPTO_SECRET_KEY are not set.")
        self._c = TradingClient(key, secret, paper=True)
        raw = getattr(self._c, "_base_url", None)
        if host_of(str(getattr(raw, "value", raw) or "")) != PAPER_HOST:
            raise Refused("Crypto client is not on the paper host. Stop.")
        a = self._c.get_account()
        if a.account_number != cfg["account_number"]:
            raise Refused(f"Keys open account {a.account_number}, not the crypto account {cfg['account_number']}. Stop.")
        self.cfg = cfg

    def snapshot(self) -> dict:
        a = self._c.get_account()
        pos = {p.symbol: {"qty": float(p.qty), "avg_entry_price": float(p.avg_entry_price),
                          "market_value": float(p.market_value), "unrealized_pl": float(p.unrealized_pl),
                          "current_price": float(p.current_price)}
               for p in self._c.get_all_positions() if str(getattr(p.asset_class, "value", p.asset_class)) == "crypto"}
        try:
            import pnl_periods
            from alpaca.trading.requests import GetPortfolioHistoryRequest

            hist = pnl_periods.history_from_alpaca(
                self._c.get_portfolio_history(GetPortfolioHistoryRequest(period="3M", timeframe="1D")))[-70:]
        except Exception:  # noqa: BLE001 - a nicety only
            hist = []
        return {"account_number": a.account_number, "equity": float(a.equity), "cash": float(a.cash),
                "last_equity": float(a.last_equity) if a.last_equity else None, "positions": pos, "history": hist}

    def submit(self, order: dict, coid: str) -> dict:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        ls = learning.settings(self.cfg)
        if order.get("book") == "learning":
            if order["symbol"] != ls["symbol"] or order["symbol"] in self.cfg["symbols"]:
                raise Refused(f"{order['symbol']} is not the learning book's coin.")
            cap = float(ls["usd"])
        else:
            if order["symbol"] not in self.cfg["symbols"]:
                raise Refused(f"{order['symbol']} is not a configured coin.")
            cap = float(self.cfg["per_symbol_usd"])
        if order["side"] == "buy":
            if order["notional"] > cap:
                raise Refused("Buy larger than this book's limit.")
            req = MarketOrderRequest(symbol=order["symbol"], notional=order["notional"], side=OrderSide.BUY,
                                     time_in_force=TimeInForce.GTC, client_order_id=coid)
        else:
            req = MarketOrderRequest(symbol=order["symbol"], qty=order["qty"], side=OrderSide.SELL,
                                     time_in_force=TimeInForce.GTC, client_order_id=coid)
        o = self._c.submit_order(order_data=req)
        return {"order_id": str(o.id), "status": str(getattr(o.status, "value", o.status))}


def daily_closes(symbols: list[str], now: datetime) -> dict[str, list[float]]:
    """Complete UTC days only, oldest first (enough for the 200-day average)."""
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = CryptoHistoricalDataClient()
    today = now.astimezone(timezone.utc).date()
    out = {}
    for sym in symbols:
        bars = client.get_crypto_bars(CryptoBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Day,
                                                        start=now - timedelta(days=420))).data.get(sym, [])
        out[sym] = [float(b.close) for b in bars if b.timestamp.astimezone(timezone.utc).date() < today]
    return out


def write_state(now: datetime, cfg: dict, snap: dict, sigs: dict, orders: list[dict], learn: dict | None = None) -> dict:
    prev = load_json(STATE, {}) or {}
    hist = [h for h in prev.get("history", []) if h.get("date") != now.date().isoformat()]
    hist.append({"date": now.date().isoformat(), "equity": snap.get("equity")})
    trades = [e for e in read_journal() if e["event"] == "order"][-50:]
    state = {"updated_at": now.isoformat(timespec="seconds"), "account_number": cfg["account_number"],
             "rule": cfg["rule"], "per_symbol_usd": cfg["per_symbol_usd"], "start_usd": cfg.get("start_usd", 1000),
             "equity": snap.get("equity"), "cash": snap.get("cash"), "last_equity": snap.get("last_equity"),
             "positions": snap.get("positions"), "signals": sigs, "last_orders": orders,
             "orders": list(reversed(trades)), "history": hist[-400:], "equity_history": snap.get("history") or [],
             "pnl_periods": pnl_periods.periods(snap.get("history") or [], snap.get("equity"), snap.get("last_equity"),
                                                float(cfg.get("start_usd", 1000)), now.astimezone(ET).date()),
             "learning": learn}
    save_json(STATE, state)
    save_json(ACCOUNT, snap)
    return state


def run(now: datetime | None = None, broker=None, closes=None, trade: bool = True) -> dict:
    now = now or datetime.now(timezone.utc)
    cfg = config()
    if not cfg.get("enabled", True):
        trade = False
    broker = broker or CryptoBroker(cfg)
    ls = learning.settings(cfg)
    lsym = ls["symbol"] if ls["enabled"] and ls["symbol"] not in cfg["symbols"] else None
    closes = closes or daily_closes(cfg["symbols"] + ([lsym] if lsym else []), now)
    sigs = {s: signal(c, cfg["rule"]) for s, c in closes.items() if s in cfg["symbols"]}
    snap = broker.snapshot()
    orders = decide(sigs, snap["positions"], snap["cash"], cfg) if trade else []
    log("check", now, signals=sigs, equity=snap["equity"], cash=snap["cash"], positions=list(snap["positions"]))
    sent = []
    for o in orders:
        coid = f"crypto-{now:%Y%m%d}-{pos_key(o['symbol']).lower()}-{o['side']}"
        _send(broker, o, coid, now, sent)
    if sent:
        snap = broker.snapshot()
    learn = None
    if lsym:
        lc, lpos = closes.get(lsym) or [], snap["positions"].get(pos_key(lsym))
        stopped = learning.stopped_today(read_journal(), lsym, now.astimezone(timezone.utc).date())
        lorders = learning.decide(lc, lpos, snap["cash"], ls, stopped) if trade and lc else []
        for o in lorders:
            _send(broker, o, f"crypto-learn-{now:%Y%m%d}-{o['side']}", now, sent)
        if lorders:
            snap = broker.snapshot()
        learn = learning.summary(read_journal(), snap["positions"].get(pos_key(lsym)), lc, ls)
    for sym, s in sigs.items():
        print(f"{sym}: close {s['close']:,.2f} vs 200-day {s['average'] or 0:,.2f} → {'HOLD' if s['hold'] else 'CASH'}")
    if learn:
        print(f"Learning {lsym}: {'HOLD' if learn['hold'] else 'CASH'} (mom7d), about ${learn['total_pnl']:+,.2f} so far")
    return write_state(now, cfg, snap, sigs, sent, learn)


def _send(broker, o: dict, coid: str, now: datetime, sent: list) -> None:
    try:
        r = broker.submit(o, coid)
        sent.append({**o, **r})
        log("order", now, **o, **r, client_order_id=coid)
        print(f"Roy: crypto {o['side']} {o['symbol']} ({o['why']}) — {r['status']}.")
    except Exception as e:  # noqa: BLE001 - a failed order must be loud, and the others still go
        log("order_failed", now, **o, error=f"{type(e).__name__}: {e}"[:300])
        print(f"Roy: crypto {o['side']} {o['symbol']} FAILED ({type(e).__name__}).")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    st = run(trade=(cmd == "run"))
    print(f"Equity ${st['equity']:,.2f}, cash ${st['cash']:,.2f}, positions {list(st['positions'] or {})}")
