"""Test fixtures: a temporary desk folder, synthetic bars and a fake paper broker."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

DESK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESK))

from common import ET  # noqa: E402

DESK_FILES = ["watchlist.json", "alpaca_config.json", "rules.json", "risk.json", "study.json", "account.json", "aoi_override.json",
              "learning_weights.json", "market_pulse.json", "trades.csv", "console_pin.json"]

# A weekday (Thursday) and a Saturday, in New York time.
THURSDAY = datetime(2026, 10, 1, tzinfo=ET)
SATURDAY = datetime(2026, 10, 3, tzinfo=ET)


def at(day: datetime, hh: int, mm: int, ss: int = 0) -> datetime:
    return day.replace(hour=hh, minute=mm, second=ss)


class DeskTestCase(unittest.TestCase):
    """Copies the desk's starting files into a temp folder and points DESK_DIR at it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="desk-"))
        for name in DESK_FILES:
            shutil.copy(DESK / name, self.tmp / name)
        self._old = os.environ.get("DESK_DIR")
        os.environ["DESK_DIR"] = str(self.tmp)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DESK_DIR", None)
        else:
            os.environ["DESK_DIR"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


def falling_bars(day: datetime, until: datetime, start: float = 600.0, end: float = 590.0,
                 last_volume: float = 5000.0) -> list[dict]:
    """1-minute bars from 09:30 to `until`, drifting down from start to end. RSI < 50, price < VWAP."""
    bars = []
    t = at(day, 9, 30)
    n = max(int((until - t).total_seconds() // 60), 1)
    for i in range(n + 1):
        price = start + (end - start) * i / n + (0.05 if i % 2 else -0.05)
        bars.append({"t": t + timedelta(minutes=i), "o": price + 0.02, "h": price + 0.1, "l": price - 0.1,
                     "c": price, "v": 1000.0})
    bars[-1]["v"] = last_volume
    return bars


def rising_bars(day: datetime, until: datetime, start: float = 600.0, end: float = 610.0,
                last_volume: float = 5000.0) -> list[dict]:
    """Mirror of falling_bars: RSI > 50, price > VWAP."""
    return falling_bars(day, until, start=start, end=end, last_volume=last_volume)


def set_rules(**kw) -> None:
    """Change rules.json in the test desk (for tests that check a rule other than the desk's current setup)."""
    from common import load_json, save_json

    r = load_json("rules.json")
    for k, v in kw.items():
        if isinstance(v, dict) and isinstance(r.get(k), dict):
            r[k] = {**r[k], **v}
        else:
            r[k] = v
    save_json("rules.json", r)


BOTH_AREAS = ["red", "green"]
ORIGINAL_EXITS = {"stop_underlying_pct": 0.35, "max_hold_minutes": 0}


def use_shares(enabled: bool = True) -> None:
    from common import load_json, save_json

    r = load_json("rules.json")
    r.update(active="spy_shares", shares_enabled=enabled, spy_options_enabled=False)
    save_json("rules.json", r)


class FakeBroker:
    """Stands in for alpaca_client.PaperBroker. Records what would have been sent."""

    base_url = "https://paper-api.alpaca.markets"
    is_paper = True

    def __init__(self, account_number="PA36VOEO5PHB", positions=None, market_open=True, fills=None,
                 open_orders=None, reject=False, expiries=(0, 1, 2, 7, 9, 14, 21, 28, 35, 42), ask_per_day=0.45, cash=1000.0):
        self.account_number = account_number
        self._positions = positions or []
        self._market_open = market_open
        self._fills = fills or []
        self._open = open_orders or []
        self._reject = reject
        self._expiries = expiries
        self._ask_per_day = ask_per_day  # a contract's ask: $2 plus this much per day to expiry
        self._cash = cash
        self.submitted: list[dict] = []

    def positions(self):
        return list(self._positions)

    def account_snapshot(self):
        return {"source": "fake_paper", "account_name": "Paper 1000", "account_number": self.account_number,
                "status": "ACTIVE", "snapshot_at": "2026-10-01T10:30:00-04:00", "equity": self._cash, "cash": self._cash,
                "buying_power": 2 * self._cash, "options_buying_power": self._cash, "last_equity": self._cash,
                "position": self._positions[0] if self._positions else None, "positions": self.positions()}

    def market_open(self):
        return self._market_open

    def open_orders(self):
        return self._open

    def filled_orders_since(self, since):
        return list(self._fills)

    def option_contracts(self, right, around, today, underlying="SPY"):
        from instruments import occ_symbol

        out = []
        for d in self._expiries:
            exp = today + timedelta(days=d)
            for k in range(int(around) - 3, int(around) + 4):
                out.append({"symbol": occ_symbol(underlying, exp, right, k), "expiry": exp, "right": right,
                            "strike": float(k), "tradable": True})
        return out

    def option_asks(self, symbols):
        from instruments import parse_occ

        out = {}
        for sym in symbols:
            o = parse_occ(sym)
            if o:
                out[sym] = round(2.0 + self._ask_per_day * (o["expiry"] - THURSDAY.date()).days, 2)
        return out

    def submit_market(self, side, qty, client_order_id, symbol="SPY", intent=None):
        if self._reject:
            raise RuntimeError("insufficient options buying power")
        self.submitted.append({"side": side, "qty": qty, "client_order_id": client_order_id, "symbol": symbol,
                               "intent": intent})
        return {"order_id": f"fake-{len(self.submitted)}", "status": "accepted", "submitted_at": "now"}
