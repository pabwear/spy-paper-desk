"""Shared paths, JSON file IO and the New York clock for the paper desk.

Everything here is pure or touches only the desk's own files. Nothing in this
module talks to Alpaca.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PAPER_HOST = "paper-api.alpaca.markets"
LIVE_HOST = "api.alpaca.markets"


def desk_dir() -> Path:
    """The folder holding the desk's JSON/CSV files. DESK_DIR overrides it (tests, demos)."""
    override = os.environ.get("DESK_DIR")
    return Path(override) if override else Path(__file__).resolve().parent


def path(name: str) -> Path:
    return desk_dir() / name


def load_json(name: str, default: Any = None) -> Any:
    p = path(name)
    if not p.exists():
        return default
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(name: str, data: Any) -> None:
    p = path(name)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    tmp.replace(p)


def now_et() -> datetime:
    return datetime.now(ET)


def to_et(value: datetime | str | None) -> datetime | None:
    """Parse an ISO timestamp (or datetime) into New York time. Naive values are rejected."""
    if value is None or value == "":
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None:
        return None
    return dt.astimezone(ET)


def hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def session_state(now: datetime, risk: dict) -> str:
    """One of: weekend, pre_open, watch_only, trade_window, flatten_window, after_close."""
    now = now.astimezone(ET)
    if now.weekday() >= 5:
        return "weekend"
    t = now.time()
    if t < hhmm(risk["rth_open"]):
        return "pre_open"
    if t < hhmm(risk["rth_watch_only_until"]):
        return "watch_only"
    if t < hhmm(risk.get("flatten_start", risk["rth_close"])):
        return "trade_window"
    if t < hhmm(risk["rth_close"]):
        return "flatten_window"
    return "after_close"


SYMBOL_RE = r"^[A-Z]{1,5}(\.[A-Z])?$"


def watchlist() -> dict:
    w = load_json("watchlist.json", None) or {}
    w.setdefault("focus", ["SPY"])
    w.setdefault("symbols", {"SPY": {}})
    return w


def focus_symbols() -> list[str]:
    w = watchlist()
    return [s for s in w["focus"] if s in w["symbols"]]


def aoi_file(symbol: str) -> str:
    """SPY keeps the original aoi_override.json; other symbols get their own file."""
    return "aoi_override.json" if symbol == "SPY" else f"aoi_override.{symbol}.json"


SESSION_LABELS = {
    "weekend": "Weekend — no orders",
    "pre_open": "Before the open — no orders",
    "watch_only": "09:30–09:59 watch only — no orders",
    "trade_window": "Entry window (gate still applies)",
    "flatten_window": "15:55–16:00 flatten only — no entries",
    "after_close": "After the close — no orders",
}
