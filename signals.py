"""Pure signal math: RSI, session VWAP, volume, zone proximity, confluence, sizing.

No I/O. Bars are dicts with keys t (datetime), o, h, l, c, v.
"""

from __future__ import annotations

import math
from datetime import datetime

from common import ET, hhmm

SIDE_FOR_COLOR = {"red": "buy", "green": "sell"}
COLOR_FOR_SIDE = {"buy": "red", "sell": "green"}

# Confluence tags Ops may send with a box (read off the Mxwll chart) and the
# weight key each one scores under in learning_weights.json.
OPS_TAG_KEYS = {
    "BOS": "structure",
    "CHoCH": "structure",
    "HH": "structure",
    "LH": "structure",
    "LL": "structure",
    "HL": "structure",
    "structure": "structure",
    "order_block": "order_blocks",
    "order_blocks": "order_blocks",
    "OB": "order_blocks",
    "session": "session",
}


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI on the closes. None until there are period + 1 closes."""
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for prev, cur in zip(closes[:period], closes[1 : period + 1]):
        change = cur - prev
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    for prev, cur in zip(closes[period:], closes[period + 1 :]):
        change = cur - prev
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def session_vwap(bars: list[dict], open_hhmm: str = "09:30") -> float | None:
    """VWAP of today's regular session, from typical price × volume."""
    if not bars:
        return None
    day = bars[-1]["t"].astimezone(ET).date()
    start = hhmm(open_hhmm)
    pv = vol = 0.0
    for b in bars:
        t = b["t"].astimezone(ET)
        if t.date() != day or t.time() < start:
            continue
        typical = (b["h"] + b["l"] + b["c"]) / 3.0
        pv += typical * b["v"]
        vol += b["v"]
    return pv / vol if vol > 0 else None


def volume_above_average(volumes: list[float], n: int = 20) -> bool | None:
    """Is the last bar's volume above the average of the n bars before it?"""
    if len(volumes) < n + 1:
        return None
    window = volumes[-(n + 1) : -1]
    return volumes[-1] > sum(window) / n


def zone_mid(zone: dict) -> float:
    return (float(zone["low"]) + float(zone["high"])) / 2.0


def price_near_zone(price: float, zone: dict, tolerance_pct: float = 0.15) -> bool:
    """Inside the box, or within tolerance_pct % of its midpoint. No chase outside."""
    low, high = float(zone["low"]), float(zone["high"])
    if low <= price <= high:
        return True
    mid = zone_mid(zone)
    return abs(price - mid) / mid <= tolerance_pct / 100.0


def confluence_tags(zone: dict, price: float, rsi_value: float | None, vwap: float | None,
                    volume_up: bool | None) -> list[str]:
    """Weight keys for the extra signals that agree with this zone."""
    tags: list[str] = []
    for raw in zone.get("confluence", []) or []:
        key = OPS_TAG_KEYS.get(str(raw))
        if key and key not in tags:
            tags.append(key)
    color = zone["color"]
    if rsi_value is not None:
        if (color == "red" and rsi_value < 50) or (color == "green" and rsi_value > 50):
            tags.append("rsi")
    if vwap is not None:
        if (color == "red" and price <= vwap) or (color == "green" and price >= vwap):
            tags.append("vwap")
    if volume_up:
        tags.append("volume")
    return tags


def score(tags: list[str], weights: dict) -> float:
    return round(sum(float(weights.get(t, 0.0)) for t in tags), 4)


def rank_zones(zones: list[dict], price: float, rsi_value: float | None, vwap: float | None,
               volume_up: bool | None, weights: dict, tolerance_pct: float = 0.15) -> list[dict]:
    """Every zone with its proximity, tags and score, best candidate first.

    Candidates (price near the zone) sort ahead of non-candidates; ties break on
    distance to the midpoint.
    """
    ranked = []
    for z in zones:
        tags = confluence_tags(z, price, rsi_value, vwap, volume_up)
        mid = zone_mid(z)
        ranked.append({
            "zone": z,
            "side": SIDE_FOR_COLOR.get(z.get("color")),
            "near": price_near_zone(price, z, tolerance_pct),
            "distance_pct": round((price - mid) / mid * 100.0, 4),
            "tags": tags,
            "score": score(tags, weights),
        })
    ranked.sort(key=lambda r: (not r["near"], -r["score"], abs(r["distance_pct"])))
    return ranked


def size_qty(price: float, risk: dict, factor: float = 1.0) -> float:
    """round((book × max notional %) / price, 4), optionally reduced by factor."""
    book = float(risk["size_as_if_equity_usd"])
    pct = float(risk["max_notional_pct_of_sizing_equity"]) / 100.0
    return round(book * pct * factor / price, 4)


def whole_shares(price: float, risk: dict, factor: float = 1.0) -> int:
    """Whole-share quantity inside the notional cap (fractional shorts are not allowed)."""
    book = float(risk["size_as_if_equity_usd"])
    pct = float(risk["max_notional_pct_of_sizing_equity"]) / 100.0
    return int(math.floor(book * pct * factor / price))


def pulse_contradicts(side: str, bias: str | None) -> bool:
    return (side == "buy" and bias == "bearish") or (side == "sell" and bias == "bullish")


def bars_today(bars: list[dict], day: datetime) -> list[dict]:
    d = day.astimezone(ET).date()
    return [b for b in bars if b["t"].astimezone(ET).date() == d]
