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

DESK_FILES = ["alpaca_config.json", "rules.json", "risk.json", "study.json", "account.json", "aoi_override.json",
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


class FakeBroker:
    base_url = "https://paper-api.alpaca.markets"
    is_paper = True

    def __init__(self, account_number="PA3R32D8LP4Q", position=None, market_open=True, fills=None,
                 open_orders=None):
        self.account_number = account_number
        self._position = position
        self._market_open = market_open
        self._fills = fills or []
        self._open = open_orders or []
        self.submitted: list[dict] = []

    def account_snapshot(self):
        return {"source": "fake_paper", "account_name": "Paper 1000", "account_number": self.account_number,
                "status": "ACTIVE", "snapshot_at": "2026-10-01T10:30:00-04:00", "equity": 1000.0, "cash": 1000.0,
                "buying_power": 2000.0, "last_equity": 1000.0, "position": self._position}

    def market_open(self):
        return self._market_open

    def open_orders(self):
        return self._open

    def filled_orders_since(self, since):
        return list(self._fills)

    def submit_market(self, side, qty, client_order_id):
        self.submitted.append({"side": side, "qty": qty, "client_order_id": client_order_id})
        return {"order_id": f"fake-{len(self.submitted)}", "status": "accepted", "submitted_at": "now"}
