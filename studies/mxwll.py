# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Python port of "Mxwll Suite" by Mxwll Capital (Pine Script v5, MPL 2.0), as published on
# TradingView ("Mxwll Price Action Suite [Mxwll]"). Ported for the paper desk; logic follows the
# original line for line. The session table and background colors are drawn by the dashboard.
"""Mxwll Suite, ported: Areas of Interest, market structure (BoS / CHoCH), swing labels, order blocks,
auto Fibonacci levels, fair value gaps and the rolling 4-hour / 1-day highs and lows.

Pure functions over a list of candles {t, o, h, l, c, v}, oldest first, on the chart's timeframe.
Index `i` plays the role of Pine's `bar_index`; "[1]" means the previous candle.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from common import ET, hhmm

DEFAULTS = {
    "timeframe_minutes": 5,
    "regular_hours_only": True,
    "aoi_lookback": 50,          # closeArr.slice(0, 50)
    "atr_length": 14,            # ta.atr(14)
    "internal_sensitivity": 3,   # intSens
    "external_sensitivity": 25,  # extSens
    "order_blocks_kept": 10,     # showLast
    "fvg_kept": 10,              # open gaps sent to the chart per side (Pine keeps every open one)
}

FIB_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)  # fib1..fib5


# ---------------------------------------------------------------- candles

def resample(bars: list[dict], minutes: int, regular_hours_only: bool = True,
             open_hhmm: str = "09:30", close_hhmm: str = "16:00",
             ext_open: str = "04:00", ext_close: str = "20:00") -> list[dict]:
    """1-minute bars → `minutes` candles.

    Regular hours: candles start at 09:30, like TradingView's RTH chart. Extended hours: 04:00–20:00 ET,
    candles on the clock (TradingView's session starts at 04:00, which lines up the same way).
    """
    out: list[dict] = []
    o_t, c_t = hhmm(open_hhmm), hhmm(close_hhmm)
    eo_t, ec_t = hhmm(ext_open), hhmm(ext_close)
    for b in bars:
        t = b["t"].astimezone(ET)
        if regular_hours_only and not (o_t <= t.time() < c_t):
            continue
        if not regular_hours_only and not (eo_t <= t.time() < ec_t):
            continue
        if regular_hours_only:
            start = t.replace(hour=o_t.hour, minute=o_t.minute, second=0, microsecond=0)
            k = int((t - start).total_seconds() // 60 // minutes)
            bucket = start + timedelta(minutes=k * minutes)
        else:
            m = t.hour * 60 + t.minute
            bucket = t.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=m - m % minutes)
        if out and out[-1]["t"] == bucket:
            c = out[-1]
            c["h"] = max(c["h"], b["h"])
            c["l"] = min(c["l"], b["l"])
            c["c"] = b["c"]
            c["v"] += b["v"]
        else:
            out.append({"t": bucket, "o": float(b["o"]), "h": float(b["h"]), "l": float(b["l"]),
                        "c": float(b["c"]), "v": float(b["v"])})
    return out


# ---------------------------------------------------------------- ta.atr

def atr(candles: list[dict], length: int = 14) -> list[float | None]:
    """ta.atr(length) = ta.rma(ta.tr(true), length): Wilder smoothing seeded with a simple average."""
    out: list[float | None] = []
    trs: list[float] = []
    prev = None
    for i, k in enumerate(candles):
        if i == 0:
            tr = k["h"] - k["l"]
        else:
            pc = candles[i - 1]["c"]
            tr = max(k["h"] - k["l"], abs(k["h"] - pc), abs(k["l"] - pc))
        trs.append(tr)
        if len(trs) < length:
            out.append(None)
        elif prev is None:
            prev = sum(trs[-length:]) / length
            out.append(prev)
        else:
            prev = (prev * (length - 1) + tr) / length
            out.append(prev)
    return out


# ---------------------------------------------------------------- Areas of Interest (drawAOE)

def areas_of_interest(candles: list[dict], lookback: int = 50, atr_length: int = 14) -> list[dict | None]:
    """Per candle: the red (above) and green (below) Area of Interest boxes, with Pine's visibility rule.

    red   = [max body of the last `lookback` candles, + ATR]     (bgcolor #F24968)
    green = [min body of the last `lookback` candles − ATR, min]  (bgcolor #14D990)
    """
    atrs = atr(candles, atr_length)
    out: list[dict | None] = []
    high_top = low_bottom = None  # the boxes' previous top / bottom, as Pine keeps them
    for i, k in enumerate(candles):
        if i + 1 <= lookback or atrs[i] is None:  # closeArr.size() > 50
            out.append(None)
            continue
        window = candles[i - lookback + 1: i + 1]
        max_h = max(max(w["c"] for w in window), max(w["o"] for w in window))
        min_l = min(min(w["c"] for w in window), min(w["o"] for w in window))
        a = atrs[i]
        if high_top is None:  # box.new on the first eligible candle
            high_top, low_bottom = max_h * 1.01, min_l * 0.99
        red_visible = k["c"] <= high_top * 1.01
        high_top = max_h + a
        green_visible = k["c"] >= low_bottom * 0.99
        low_bottom = (min_l - a) if green_visible else -a
        out.append({
            "red": {"low": round(max_h, 4), "high": round(max_h + a, 4), "visible": red_visible},
            "green": {"low": round(min_l - a, 4), "high": round(min_l, 4), "visible": green_visible},
            "atr": round(a, 4),
        })
    return out


# ---------------------------------------------------------------- pivots (calculatePivots)

def pivots(candles: list[dict], length: int) -> tuple[list[float], list[float]]:
    """topSwing / botSwing per candle (0 when none). A swing is confirmed `length` candles after it."""
    tops, bots = [], []
    intra = 0
    for i in range(len(candles)):
        top = bot = 0.0
        if i > length + 1:
            up = max(c["h"] for c in candles[i - length + 1: i + 1])
            dn = min(c["l"] for c in candles[i - length + 1: i + 1])
            c_hi, c_lo = candles[i - length]["h"], candles[i - length]["l"]
            prev = intra
            if c_hi > up:
                intra = 0
            elif c_lo < dn:
                intra = 1
            top = c_hi if intra == 0 and prev != 0 else 0.0
            bot = c_lo if intra == 1 and prev != 1 else 0.0
        tops.append(top)
        bots.append(bot)
    return tops, bots


# ---------------------------------------------------------------- structure (drawStructureExt / Internals)

def structure(candles: list[dict], length: int, internal: bool, keep_blocks: int = 10) -> dict:
    """Market-structure breaks (BoS / CHoCH), swing labels (HH/LH/HL/LL) and, for externals, order blocks."""
    tops, bots = pivots(candles, length)
    events, swings = [], []
    up_level = 0.0 if not internal else None    # external levels start at 0.0, internal ones at na
    dn_level = 0.0 if not internal else None
    up_x = dn_x = None
    upside = 1 if not internal else None  # bigData starts with upside 1 and no downside
    downside = None
    moving = 0
    prev_up = prev_dn = None
    high_blocks: list[dict] = []
    low_blocks: list[dict] = []
    for i, k in enumerate(candles):
        if tops[i]:
            x1 = i - length
            if not internal:
                swings.append({"i": x1, "t": candles[x1]["t"], "price": tops[i], "side": "high",
                               "label": "HH" if tops[i] > up_level else "LH"})
                high_blocks.append({"from_i": x1, "top": tops[i], "bottom": tops[i] * 0.998})
            upside, up_level, up_x = 1, tops[i], x1
        if bots[i]:
            x1 = i - length
            if not internal:
                swings.append({"i": x1, "t": candles[x1]["t"], "price": bots[i], "side": "low",
                               "label": "LL" if bots[i] < dn_level else "HL"})
                low_blocks.append({"from_i": x1, "top": bots[i], "bottom": bots[i] * 1.002})
            downside, dn_level, dn_x = 1, bots[i], x1
        c, pc = k["c"], (candles[i - 1]["c"] if i else None)
        crossed_up = (up_level is not None and prev_up is not None and pc is not None
                      and c > up_level and pc <= prev_up)
        crossed_dn = (dn_level is not None and prev_dn is not None and pc is not None
                      and c < dn_level and pc >= prev_dn)
        if crossed_up and upside not in (None, 0):
            events.append({"i": i, "t": k["t"], "dir": "bull", "kind": "CHoCH" if moving < 0 else "BoS",
                           "level": up_level, "from_i": up_x})
            upside, moving = 0, 1
        if crossed_dn and downside not in (None, 0):
            events.append({"i": i, "t": k["t"], "dir": "bear", "kind": "CHoCH" if moving > 0 else "BoS",
                           "level": dn_level, "from_i": dn_x})
            downside, moving = 0, -1
        prev_up, prev_dn = up_level, dn_level
        if not internal:  # cleanseLevel
            high_blocks = [b for b in high_blocks if not c >= b["top"]]
            low_blocks = [b for b in low_blocks if not c <= b["bottom"]]
            for blocks in (high_blocks, low_blocks):
                if keep_blocks and len(blocks) > keep_blocks:
                    # Pine loops `for i = size - showLast to 0`, which removes one more than the excess.
                    del blocks[: len(blocks) - keep_blocks + 1]
    return {"events": events, "swings": swings, "high_blocks": high_blocks, "low_blocks": low_blocks,
            "moving": moving}


# ---------------------------------------------------------------- auto Fibs (updateMain / drawFibs)

def fibs(candles: list[dict], length: int = 25) -> dict | None:
    """The dashed swing line and its Fibonacci levels on the latest candle.

    The line runs from the latest swing (high or low) to the most extreme price on the other side since the
    swing before it; levels are measured from the line's later end: y2 + (y1 - y2) × ratio.
    """
    tops, bots = pivots(candles, length)
    up, up_x, dn, dn_x = 0.0, 0, 0.0, 0   # bigData upaxis / upaxis2 / dnaxis / dnaxis2 start at 0
    counter = 0
    for i in range(len(candles)):
        if tops[i]:
            up, up_x, counter = tops[i], i - length, 1
        if bots[i]:
            dn, dn_x, counter = bots[i], i - length, -1
    if counter == 0:
        return None
    last = len(candles) - 1
    if counter == 1:  # from the swing high to the lowest low since the swing low before it
        x1, y1 = up_x, up
        lo, x2 = float("inf"), last
        for i in range(last, dn_x - 1, -1):  # newest first; a tie moves to the older candle, as in Pine
            if candles[i]["l"] <= lo:
                lo, x2 = candles[i]["l"], i
        y2 = lo
    else:  # from the swing low to the highest high since the swing high before it
        x1, y1 = dn_x, dn
        hi, x2 = 0.0, last
        for i in range(last, up_x - 1, -1):
            if candles[i]["h"] >= hi:
                hi, x2 = candles[i]["h"], i
        y2 = hi
    if x2 < x1:
        x1, y1, x2, y2 = x2, y2, x1, y1
    sub = y1 - y2
    return {"from": {"t": candles[x1]["t"].isoformat(timespec="minutes"), "price": round(y1, 4)},
            "to": {"t": candles[x2]["t"].isoformat(timespec="minutes"), "price": round(y2, 4)},
            "dir": "down" if y2 < y1 else "up",
            "levels": [{"ratio": r, "price": round(y2 + sub * r, 4)} for r in FIB_RATIOS]}


# ---------------------------------------------------------------- fair value gaps (fvg)

def fair_value_gaps(candles: list[dict], keep: int = 10) -> dict:
    """Open fair value gaps: three candles in one direction leave a gap between the first and the third.

    A down gap (first candle's low above the third's high) closes once a high reaches the first candle's low;
    an up gap closes once a low reaches the first candle's high. Each box starts at the middle candle.
    """
    def sign(k):
        d = k["c"] - k["o"]
        return (d > 0) - (d < 0)

    down: list[dict] = []
    up: list[dict] = []
    for i, k in enumerate(candles):
        if i >= 2:
            a, m = candles[i - 2], candles[i - 1]
            s3 = sign(a) + sign(m) + sign(k)
            if s3 == -3:  # box top = first low, bottom = third high
                down.append({"top": a["l"], "bottom": k["h"], "i": i - 1})
            elif s3 == 3:  # box top = third low, bottom = first high
                up.append({"top": k["l"], "bottom": a["h"], "i": i - 1})
        down = [g for g in down if not k["h"] >= g["top"]]
        up = [g for g in up if not k["l"] <= g["bottom"]]

    def out(g, d):
        return {"dir": d, "low": round(min(g["top"], g["bottom"]), 4), "high": round(max(g["top"], g["bottom"]), 4),
                "t": candles[g["i"]]["t"].isoformat(timespec="minutes")}

    return {"down": [out(g, "down") for g in down[-keep:]], "up": [out(g, "up") for g in up[-keep:]]}


# ---------------------------------------------------------------- rolling 4-hour / 1-day levels (tfDrawLower)

def _nearest_rank(values: list[float], pct: float) -> float:
    s = sorted(values)
    k = max(1, -(-len(s) * pct // 100))  # ceil(n × p / 100)
    return s[int(k) - 1]


def activity(history: list[float], now: float) -> str:
    """getActivity: where the latest rolling volume sits among its own history."""
    p10, p33, p50, p66 = (_nearest_rank(history, p) for p in (10, 33, 50, 66))
    if now <= p10:
        return "Very Low"
    if now <= p33:
        return "Low"
    if now <= p50:
        return "Average"
    if now <= p66:
        return "High"
    return "Very High"


def rolling_levels(minute_bars: list[dict], regular_hours_only: bool = True) -> dict | None:
    """High and low of the last 240 and 1,440 one-minute bars in the chart's session ("240H/L", "1DH/L"),
    and how busy the latest 4-hour / 24-hour volume is next to the rolling volume before it."""
    eo, ec = (hhmm("09:30"), hhmm("16:00")) if regular_hours_only else (hhmm("04:00"), hhmm("20:00"))
    bars = sorted((b for b in minute_bars or [] if eo <= b["t"].astimezone(ET).time() < ec), key=lambda b: b["t"])
    if not bars:
        return None
    out = {}
    for name, n in (("4h", 240), ("1d", 1440)):
        window = bars[-n:]
        hi = max(window, key=lambda b: (b["h"], b["t"]))  # the latest candle that touched the high
        lo = min(window, key=lambda b: (b["l"], -b["t"].timestamp()))
        vols = [float(b["v"]) for b in bars]
        sums, run = [], 0.0
        for i, v in enumerate(vols):
            run += v - (vols[i - n] if i >= n else 0.0)
            if i >= n - 1:
                sums.append(run)
        out[name] = {"high": round(float(hi["h"]), 4), "high_t": hi["t"].astimezone(ET).isoformat(timespec="minutes"),
                     "low": round(float(lo["l"]), 4), "low_t": lo["t"].astimezone(ET).isoformat(timespec="minutes"),
                     "bars": len(window), "full": len(window) == n,
                     "volume": round(sum(vols[-n:])),
                     "activity": activity(sums, sums[-1]) if len(sums) >= 10 else None}
    return out


# ---------------------------------------------------------------- one call for the desk

def analyze(candles: list[dict], cfg: dict | None = None) -> dict | None:
    """The study's state on the latest candle."""
    cfg = {**DEFAULTS, **(cfg or {})}
    if not candles:
        return None
    aois = areas_of_interest(candles, cfg["aoi_lookback"], cfg["atr_length"])
    internal = structure(candles, cfg["internal_sensitivity"], internal=True)
    external = structure(candles, cfg["external_sensitivity"], internal=False,
                         keep_blocks=int(cfg["order_blocks_kept"]))
    last_i = len(candles) - 1

    def last_event(evs):
        if not evs:
            return None
        e = evs[-1]
        return {"dir": e["dir"], "kind": e["kind"], "level": round(e["level"], 4),
                "t": e["t"].isoformat(timespec="minutes"), "bars_ago": last_i - e["i"]}

    def event(e):
        return {"dir": e["dir"], "kind": e["kind"], "level": round(e["level"], 4),
                "t": e["t"].isoformat(timespec="minutes"),
                "from_t": candles[e["from_i"]]["t"].isoformat(timespec="minutes") if e.get("from_i") is not None else None}

    def block(b):  # Pine's low blocks have top < bottom; report them as a low–high band
        return {"low": round(min(b["top"], b["bottom"]), 4), "high": round(max(b["top"], b["bottom"]), 4),
                "since": candles[b["from_i"]]["t"].isoformat(timespec="minutes")}

    return {
        "as_of": candles[-1]["t"].isoformat(timespec="minutes"),
        "candles": len(candles),
        "aoi": aois[-1],
        "internal": last_event(internal["events"]),
        "external": last_event(external["events"]),
        "swings": [{"label": s["label"], "price": round(s["price"], 4), "t": s["t"].isoformat(timespec="minutes")}
                   for s in external["swings"][-4:]],
        "swing_points": [{"label": s["label"], "price": round(s["price"], 4), "t": s["t"].isoformat(timespec="minutes")}
                         for s in external["swings"][-16:]],
        "internal_events": [event(e) for e in internal["events"][-12:]],
        "external_events": [event(e) for e in external["events"][-8:]],
        "order_blocks": {"high": [block(b) for b in external["high_blocks"]],
                         "low": [block(b) for b in external["low_blocks"]]},
        "fibs": fibs(candles, cfg["external_sensitivity"]),
        "fvgs": fair_value_gaps(candles, int(cfg["fvg_kept"])),
    }


def candles_until(bars: list[dict], until: datetime, cfg: dict | None = None) -> list[dict]:
    """The chart as it looked at `until` (the last candle may still be forming, as on TradingView)."""
    cfg = {**DEFAULTS, **(cfg or {})}
    upto = [b for b in bars if b["t"].astimezone(ET) <= until.astimezone(ET)]
    return resample(upto, int(cfg["timeframe_minutes"]), bool(cfg["regular_hours_only"]))
