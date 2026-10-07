"""Chart data for the dashboard: today's 5-minute candles, session VWAP, zones and the desk's trades.

Written to charts.json on every heartbeat that read prices, for each focus stock. The dashboard
draws it. Pure except for `write`.
"""

from __future__ import annotations

from datetime import datetime

import instruments
import journal
from common import ET, aoi_file, focus_symbols, hhmm, load_json, save_json
from signals import bars_today

FILE = "charts.json"
BUCKET_MIN = 5


def resample(bars: list[dict], minutes: int = BUCKET_MIN) -> list[dict]:
    """1-minute bars → `minutes` candles, keyed by the bucket's start time (New York)."""
    out: list[dict] = []
    for b in bars:
        t = b["t"].astimezone(ET)
        start = t.replace(minute=t.minute - t.minute % minutes, second=0, microsecond=0)
        key = start.isoformat(timespec="minutes")
        if out and out[-1]["t"] == key:
            c = out[-1]
            c["h"] = max(c["h"], b["h"])
            c["l"] = min(c["l"], b["l"])
            c["c"] = b["c"]
            c["v"] += b["v"]
        else:
            out.append({"t": key, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
    for c in out:
        for k in ("o", "h", "l", "c"):
            c[k] = round(float(c[k]), 4)
        c["v"] = round(float(c["v"]))
    return out


def vwap_series(candles: list[dict]) -> list[float | None]:
    pv = vol = 0.0
    out = []
    for c in candles:
        pv += (c["h"] + c["l"] + c["c"]) / 3.0 * c["v"]
        vol += c["v"]
        out.append(round(pv / vol, 4) if vol else None)
    return out


def trade_marks(symbol: str, day, events: list[dict]) -> list[dict]:
    """Entries and exits on this stock (or its options) today, for markers on the chart."""
    marks = []
    for e in journal.events_on(events, day):
        if e.get("event") != "order":
            continue
        underlying = e.get("underlying") or instruments.underlying_of(e.get("symbol") or "")
        if underlying != symbol:
            continue
        marks.append({"t": e["ts"], "role": e.get("role"), "reason": e.get("reason"), "signal": e.get("signal"),
                      "price": e.get("underlying_price"), "symbol": e.get("symbol")})
    return marks


def build(symbol: str, bars: list[dict] | None, now: datetime, risk: dict, events: list[dict]) -> dict | None:
    if not bars:
        return None
    today = [b for b in bars_today(bars, now) if b["t"].astimezone(ET).time() >= hhmm(risk.get("rth_open", "09:30"))]
    if not today:
        return None
    candles = resample(today)
    override = load_json(aoi_file(symbol), None) or {}
    try:  # the desk's own Mxwll read right now: rolling AOI boxes, last breaks, order blocks
        from studies import auto as auto_study

        study = auto_study.read(bars, now, auto_study.config(load_json("rules.json", {}) or {}))
    except Exception as e:  # noqa: BLE001 - a study problem never stops the chart
        study = {"error": f"{type(e).__name__}: {e}"[:200]}
    return {
        "symbol": symbol,
        "date": now.astimezone(ET).date().isoformat(),
        "interval_min": BUCKET_MIN,
        "candles": candles,
        "vwap": vwap_series(candles),
        "last": candles[-1]["c"],
        "zones": override.get("zones") or [],
        "zones_written_at": override.get("written_at"),
        "zones_tradable": bool(override.get("tradable")),
        "zones_source": override.get("source"),
        "zones_approximate": bool(override.get("approximate")),
        "study": study,
        "trades": trade_marks(symbol, now.astimezone(ET).date(), events),
        "updated_at": now.astimezone(ET).isoformat(timespec="seconds"),
    }


def write(now: datetime, source) -> dict:
    """Refresh charts.json for every focus stock whose bars this run already read (or can read)."""
    risk = load_json("risk.json", {}) or {}
    events = journal.read_events()
    charts = load_json(FILE, {}) or {}
    for sym in focus_symbols():
        bars, _err = source.get(sym)
        c = build(sym, bars, now, risk, events)
        if c:
            charts[sym] = c
    charts = {k: v for k, v in charts.items() if k in focus_symbols()}
    save_json(FILE, charts)
    return charts
