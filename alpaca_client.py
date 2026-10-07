"""Alpaca PAPER access for SPY shares and SPY options. Never a live client.

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
from datetime import date, datetime, timedelta

from common import ET, PAPER_HOST, load_json, now_et, path, save_json, watchlist
from gate import host_of
from instruments import is_desk_symbol


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
        positions = self.positions()
        desk = [p for p in positions if is_desk_symbol(p["symbol"], _watched())]
        return {
            "source": "alpaca_paper",
            "account_name": self.config.get("account_name"),
            "account_number": a.account_number,
            "status": str(getattr(a.status, "value", a.status)),
            "snapshot_at": now_et().isoformat(timespec="seconds"),
            "equity": float(a.equity),
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "options_buying_power": _f(getattr(a, "options_buying_power", None)),
            "last_equity": float(a.last_equity) if getattr(a, "last_equity", None) else None,
            "position": desk[0] if desk else None,
            "positions": positions,
        }

    def market_open(self) -> bool:
        return bool(self._client.get_clock().is_open)

    def positions(self) -> list[dict]:
        out = []
        for p in self._client.get_all_positions():
            out.append({
                "symbol": p.symbol,
                "asset_class": str(getattr(p.asset_class, "value", p.asset_class)),
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": _f(p.current_price),
                "market_value": _f(p.market_value),
                "unrealized_pl": _f(p.unrealized_pl),
                "unrealized_plpc": _f(p.unrealized_plpc),
            })
        return out

    def open_orders(self) -> list[dict]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self._client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        return [{"id": str(o.id), "symbol": o.symbol, "side": str(o.side.value), "qty": o.qty} for o in orders]

    def filled_orders_since(self, since: datetime) -> list[dict]:
        """Fills on watchlist symbols (shares and options) only."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=since, limit=500)
        out = []
        for o in self._client.get_orders(filter=req):
            if o.filled_at is None or not o.filled_qty or float(o.filled_qty) == 0:
                continue
            if not is_desk_symbol(o.symbol, _watched()):
                continue
            out.append({
                "order_id": str(o.id),
                "client_order_id": o.client_order_id,
                "symbol": o.symbol,
                "side": str(o.side.value),
                "qty": float(o.filled_qty),
                "price": float(o.filled_avg_price),
                "filled_at": o.filled_at.astimezone(ET).isoformat(timespec="seconds"),
                "status": str(o.status.value),
            })
        out.sort(key=lambda r: r["filled_at"])
        return out

    def option_contracts(self, right: str, around: float, today: date, underlying: str = "SPY") -> list[dict]:
        """Listed, active contracts of one right, strikes within ±5 (or ±3%), expiring in the window rules.json
        asks for: within 7 days by default, or from expiry_min_days to expiry_target_days + 14."""
        from alpaca.trading.enums import AssetStatus, ContractType
        from alpaca.trading.requests import GetOptionContractsRequest

        opt = (load_json("rules.json", {}) or {}).get("option", {})
        target, least = int(opt.get("expiry_target_days", 0) or 0), int(opt.get("expiry_min_days", 0) or 0)
        first, last = (today + timedelta(days=least), today + timedelta(days=target + 14)) if target else (today, today + timedelta(days=7))
        width = max(5.0, around * 0.03)
        req = GetOptionContractsRequest(
            underlying_symbols=[underlying], status=AssetStatus.ACTIVE,
            type=ContractType.CALL if right == "call" else ContractType.PUT,
            expiration_date_gte=first.isoformat(), expiration_date_lte=last.isoformat(),
            strike_price_gte=f"{max(around - width, 0.5):.2f}", strike_price_lte=f"{around + width:.2f}", limit=500,
        )
        res = self._client.get_option_contracts(req)
        return [{"symbol": c.symbol, "expiry": c.expiration_date, "right": right, "strike": float(c.strike_price),
                 "tradable": bool(c.tradable)} for c in (res.option_contracts or [])]

    def option_asks(self, symbols: list[str]) -> dict[str, float]:
        """Latest asks for option contracts (Alpaca's free indicative feed). Missing quotes are left out."""
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionLatestQuoteRequest

        if not symbols:
            return {}
        key, secret = _keys()
        client = OptionHistoricalDataClient(key, secret)
        quotes = client.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=list(symbols),
                                                                         feed=OptionsFeed.INDICATIVE))
        return {s: float(q.ask_price) for s, q in quotes.items() if q is not None and q.ask_price}

    # -- writes (paper only)
    def submit_market(self, side: str, qty: float, client_order_id: str, symbol: str = "SPY",
                      intent: str | None = None) -> dict:
        from alpaca.trading.enums import OrderSide, PositionIntent, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        if not self.is_paper or host_of(self.base_url) != PAPER_HOST:
            raise LiveTradingRefused("Refusing to submit: client is not paper.")
        if not is_desk_symbol(symbol, _watched()):
            raise LiveTradingRefused(f"Refusing to submit {symbol}: not a watchlist symbol or its option.")
        kwargs = {}
        if intent in ("buy_to_open", "sell_to_close", "buy_to_close", "sell_to_open"):
            kwargs["position_intent"] = PositionIntent(intent)
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
            **kwargs,
        )
        o = self._client.submit_order(order_data=req)
        return {"order_id": str(o.id), "status": str(o.status.value), "submitted_at": str(o.submitted_at)}


def _watched() -> set[str]:
    return set(watchlist()["symbols"])


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def fetch_bars(now: datetime, minutes: int = 1, symbol: str = "SPY") -> list[dict]:
    """Today's bars for one symbol (plus a little of yesterday for RSI warm-up).

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
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame(minutes, TimeFrameUnit.Minute),
                               start=start, end=now, feed=DataFeed.IEX)
        data = client.get_stock_bars(req).data.get(symbol, [])
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

        df = yf.download(symbol, period="5d", interval=f"{minutes}m", prepost=True, progress=False,
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


HISTORY = {  # unit → (Alpaca timeframe, days back, yfinance interval, yfinance period)
    "30Min": ((30, "Minute"), 59, "30m", "60d"),
    "1Day": ((1, "Day"), 400, "1d", "2y"),
}


def fetch_history(now: datetime, symbol: str, unit: str) -> list[dict]:
    """Longer history for the chart's bigger timeframes: 30-minute bars (about 60 days) or daily bars.

    Alpaca market data (IEX feed) first; yfinance as a fallback. Raises on no data.
    """
    (amount, tf_unit), days, yf_interval, yf_period = HISTORY[unit]
    errors = []
    try:
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        key, secret = _keys()
        client = StockHistoricalDataClient(key, secret)
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame(amount, getattr(TimeFrameUnit, tf_unit)),
                               start=(now - timedelta(days=days)).astimezone(ET), end=now, feed=DataFeed.IEX)
        data = client.get_stock_bars(req).data.get(symbol, [])
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

        df = yf.download(symbol, period=yf_period, interval=yf_interval, prepost=True, progress=False,
                         auto_adjust=False, multi_level_index=False)
        bars = []
        for idx, r in df.iterrows():
            t = idx.to_pydatetime()
            if t.tzinfo is None:  # daily rows come without a time zone
                t = t.replace(tzinfo=ET)
            bars.append({"t": t, "o": float(r["Open"]), "h": float(r["High"]), "l": float(r["Low"]),
                         "c": float(r["Close"]), "v": float(r["Volume"])})
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
    try:
        snap = broker.account_snapshot()
    except Exception as e:  # noqa: BLE001 - explain the usual setup mistakes instead of a traceback
        text = str(e).lower()
        if "unauthorized" in text or "forbidden" in text:
            print("Alpaca refused these keys (unauthorized). Check that ALPACA_API_KEY holds the Key (a paper key")
            print("starts with PK, not the Endpoint URL), ALPACA_SECRET_KEY holds the Secret, both come from the")
            print("Paper 1000 PAPER account, and neither has spaces. Regenerating keys makes the old ones stop working.")
            return 1
        raise
    expected = broker.config.get("account_number")
    print(f"Account   {snap['account_name']} ({snap['account_number']}) — PAPER")
    if expected and snap["account_number"] != expected:
        print(f"WARNING   expected {expected}; these keys belong to a different paper account.")
    print(f"Equity    ${snap['equity']:,.2f}")
    print(f"Cash      ${snap['cash']:,.2f}")
    print(f"Buy power ${snap['buying_power']:,.2f}")
    pos = snap["position"]
    print(f"Position  {pos['symbol']} {pos['qty']} @ {pos['avg_entry_price']:.2f}" if pos else "Position  flat")
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
