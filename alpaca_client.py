"""Alpaca PAPER access. Snapshot the Paper 1000 account; never a live client.

    python3 alpaca_client.py          # print equity, cash, buying power (no keys)
    python3 alpaca_client.py --save   # also write account.json and log the snapshot

Keys come from the environment or from .env.alpaca next to this file
(gitignored). They are never printed or logged.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta

from common import ET, PAPER_HOST, load_json, now_et, path, save_json
from gate import host_of


class LiveTradingRefused(RuntimeError):
    pass


def load_env_file() -> None:
    """Read `export KEY='value'` lines from .env.alpaca into os.environ (no override)."""
    p = path(".env.alpaca")
    if not p.exists():
        return
    pattern = re.compile(r"""^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*['"]?(.*?)['"]?\s*$""")
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#"):
            continue
        m = pattern.match(line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2)


def _keys() -> tuple[str, str]:
    load_env_file()
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set (see .env.alpaca in README).")
    return key, secret


def _assert_paper_config() -> dict:
    config = load_json("alpaca_config.json")
    if config.get("mode") != "paper" or config.get("live_unlocked") is not False:
        raise LiveTradingRefused("alpaca_config.json is not paper-only (mode/live_unlocked). Stop.")
    if host_of(config.get("base_url")) != PAPER_HOST:
        raise LiveTradingRefused("alpaca_config.json base_url is not the paper host. Stop.")
    env_url = os.environ.get("APCA_API_BASE_URL", "")
    if env_url and host_of(env_url) != PAPER_HOST:
        raise LiveTradingRefused(f"APCA_API_BASE_URL points at {env_url!r}, not paper. Stop.")
    return config


class PaperBroker:
    """Thin wrapper over alpaca-py's TradingClient, constructed with paper=True only."""

    def __init__(self) -> None:
        self.config = _assert_paper_config()
        from alpaca.trading.client import TradingClient  # imported late so tests need no alpaca-py

        key, secret = _keys()
        self._client = TradingClient(key, secret, paper=True)
        raw = getattr(self._client, "_base_url", None)
        self.base_url = str(getattr(raw, "value", raw) or "")
        self.is_paper = True
        if host_of(self.base_url) != PAPER_HOST:
            raise LiveTradingRefused(f"Trading client resolved to {host_of(self.base_url)!r}, not paper. Stop.")

    # -- reads
    def account_snapshot(self) -> dict:
        a = self._client.get_account()
        pos = self.position()
        return {
            "source": "alpaca_paper",
            "account_name": self.config.get("account_name"),
            "account_number": a.account_number,
            "status": str(getattr(a.status, "value", a.status)),
            "snapshot_at": now_et().isoformat(timespec="seconds"),
            "equity": float(a.equity),
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "last_equity": float(a.last_equity) if getattr(a, "last_equity", None) else None,
            "position": pos,
        }

    def market_open(self) -> bool:
        return bool(self._client.get_clock().is_open)

    def position(self) -> dict | None:
        for p in self._client.get_all_positions():
            if p.symbol == "SPY":
                return {
                    "symbol": "SPY",
                    "qty": float(p.qty),
                    "avg_entry_price": float(p.avg_entry_price),
                    "current_price": float(p.current_price) if p.current_price else None,
                    "market_value": float(p.market_value) if p.market_value else None,
                    "unrealized_pl": float(p.unrealized_pl) if p.unrealized_pl else None,
                    "unrealized_plpc": float(p.unrealized_plpc) if p.unrealized_plpc else None,
                }
        return None

    def open_orders(self) -> list[dict]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self._client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=["SPY"]))
        return [{"id": str(o.id), "side": str(o.side.value), "qty": o.qty} for o in orders]

    def filled_orders_since(self, since: datetime) -> list[dict]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, symbols=["SPY"], after=since, limit=500)
        out = []
        for o in self._client.get_orders(filter=req):
            if o.filled_at is None or not o.filled_qty or float(o.filled_qty) == 0:
                continue
            out.append({
                "order_id": str(o.id),
                "client_order_id": o.client_order_id,
                "side": str(o.side.value),
                "qty": float(o.filled_qty),
                "price": float(o.filled_avg_price),
                "filled_at": o.filled_at.astimezone(ET).isoformat(timespec="seconds"),
                "status": str(o.status.value),
            })
        out.sort(key=lambda r: r["filled_at"])
        return out

    # -- the only write
    def submit_market(self, side: str, qty: float, client_order_id: str) -> dict:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        if not self.is_paper or host_of(self.base_url) != PAPER_HOST:
            raise LiveTradingRefused("Refusing to submit: client is not paper.")
        req = MarketOrderRequest(
            symbol="SPY",
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        o = self._client.submit_order(order_data=req)
        return {"order_id": str(o.id), "status": str(o.status.value), "submitted_at": str(o.submitted_at)}


def fetch_bars(now: datetime, minutes: int = 1) -> list[dict]:
    """Today's SPY bars (plus a little of yesterday for RSI warm-up).

    Alpaca market data (IEX feed) first; yfinance as a fallback. Raises on no data.
    """
    errors = []
    start = (now - timedelta(days=4)).astimezone(ET)
    try:
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        key, secret = _keys()
        client = StockHistoricalDataClient(key, secret)
        req = StockBarsRequest(symbol_or_symbols="SPY", timeframe=TimeFrame(minutes, TimeFrameUnit.Minute),
                               start=start, end=now, feed=DataFeed.IEX)
        data = client.get_stock_bars(req).data.get("SPY", [])
        bars = [{"t": b.timestamp, "o": float(b.open), "h": float(b.high), "l": float(b.low),
                 "c": float(b.close), "v": float(b.volume)} for b in data]
        if bars:
            return bars
        errors.append("alpaca: no bars")
    except SystemExit as e:
        errors.append(f"alpaca: {e}")
    except Exception as e:  # noqa: BLE001 - fall through to the fallback source
        errors.append(f"alpaca: {type(e).__name__}")
    try:
        import yfinance as yf

        df = yf.download("SPY", period="5d", interval=f"{minutes}m", prepost=False, progress=False,
                         auto_adjust=False, multi_level_index=False)
        bars = [{"t": idx.to_pydatetime(), "o": float(r["Open"]), "h": float(r["High"]),
                 "l": float(r["Low"]), "c": float(r["Close"]), "v": float(r["Volume"])}
                for idx, r in df.iterrows()]
        bars = [b for b in bars if b["t"] <= now]
        if bars:
            return bars
        errors.append("yfinance: no bars")
    except Exception as e:  # noqa: BLE001
        errors.append(f"yfinance: {type(e).__name__}")
    raise RuntimeError("; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper 1000 account snapshot (no keys printed).")
    parser.add_argument("--save", action="store_true", help="write account.json and log the snapshot")
    args = parser.parse_args()
    broker = PaperBroker()
    snap = broker.account_snapshot()
    expected = broker.config.get("account_number")
    print(f"Account   {snap['account_name']} ({snap['account_number']}) — PAPER")
    if expected and snap["account_number"] != expected:
        print(f"WARNING   expected {expected}; these keys belong to a different paper account.")
    print(f"Equity    ${snap['equity']:,.2f}")
    print(f"Cash      ${snap['cash']:,.2f}")
    print(f"Buy power ${snap['buying_power']:,.2f}")
    pos = snap["position"]
    print(f"SPY       {pos['qty']} @ {pos['avg_entry_price']:.2f}" if pos else "SPY       flat")
    if args.save:
        import journal
        import rebuild_dashboard

        save_json("account.json", snap)
        journal.log("account", equity=snap["equity"], cash=snap["cash"], buying_power=snap["buying_power"],
                    position=pos)
        rebuild_dashboard.write_state()
        print("Saved account.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
