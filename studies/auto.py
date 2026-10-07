"""The desk's use of the ported Mxwll study: auto zones at the 09:39 snapshot and study confluence tags.

Pure: bars and config in, dicts out. run_study decides when to write files.
"""

from __future__ import annotations

from datetime import datetime

from common import ET, hhmm
from signals import price_near_zone
from studies import mxwll

SOURCE = "desk_mxwll_auto"


def config(rules: dict) -> dict:
    """rules.json studies.mxwll over the port's defaults."""
    return {**mxwll.DEFAULTS, "snapshot_time": "09:39", "auto_zones": True, "trusted": False,
            **((rules.get("studies") or {}).get("mxwll") or {})}


def read(bars: list[dict] | None, until: datetime, cfg: dict) -> dict | None:
    """The study as the chart looked at `until`, or None without enough candles."""
    if not bars:
        return None
    candles = mxwll.candles_until(bars, until, cfg)
    if len(candles) <= int(cfg["aoi_lookback"]):
        return None
    out = mxwll.analyze(candles, cfg)
    if out:
        out["timeframe_minutes"] = int(cfg["timeframe_minutes"])
    return out


def snapshot_at(now: datetime, cfg: dict) -> datetime:
    t = hhmm(cfg["snapshot_time"])
    return now.astimezone(ET).replace(hour=t.hour, minute=t.minute, second=59, microsecond=0)


def zones_file(symbol: str, now: datetime, bars: list[dict] | None, cfg: dict) -> dict | None:
    """Today's zones from the study at the snapshot (default 09:39 ET), in the aoi_override format.

    Approximate (so never tradable) until Roy confirms they match his TradingView chart and sets
    studies.mxwll.trusted to true.
    """
    snap = snapshot_at(now, cfg)
    if now.astimezone(ET) < snap:
        return None
    today = [b for b in (bars or []) if b["t"].astimezone(ET).date() == snap.date()]
    if not today:
        return None
    study = read(bars, snap, cfg)
    if not study or not study.get("aoi"):
        return None
    zones = [{"color": color, "low": study["aoi"][color]["low"], "high": study["aoi"][color]["high"],
              "confluence": []}
             for color in ("red", "green") if study["aoi"][color]["visible"]]
    trusted = cfg.get("trusted") is True
    return {
        "symbol": symbol,
        "tradable": bool(zones) and trusted,
        # The read is of the chart as of the snapshot; computed_at says when the desk did the math.
        "written_at": snap.isoformat(timespec="seconds"),
        "computed_at": now.astimezone(ET).isoformat(timespec="seconds"),
        "source": SOURCE,
        "approximate": not trusted,
        "note": ("Desk-computed Mxwll AOI (" + str(cfg["timeframe_minutes"]) + "-minute candles"
                 + (", regular hours" if cfg.get("regular_hours_only") else ", extended hours") + ")."
                 + ("" if trusted else " Not tradable until checked against Roy's TradingView chart.")),
        "zones": zones,
        "study": study,
    }


def zone_tags(study: dict | None, zones: list[dict], tolerance_pct: float = 0.15) -> dict[str, list[str]]:
    """Extra confluence per zone color from the study right now.

    structure    — the latest internal break (I-BoS / I-CHoCH) points the trade's way
                   (bullish for a red-zone buy, bearish for a green-zone sell).
    order_blocks — an unmitigated swing order block overlaps or sits near the zone.
    """
    out: dict[str, list[str]] = {}
    if not study:
        return out
    internal = study.get("internal") or {}
    blocks = (study.get("order_blocks") or {}).get("high", []) + (study.get("order_blocks") or {}).get("low", [])
    for z in zones:
        color = z.get("color")
        tags: list[str] = []
        if internal.get("dir") == {"red": "bull", "green": "bear"}.get(color):
            tags.append("structure")
        lo, hi = float(z["low"]), float(z["high"])
        if any((b["low"] <= hi and b["high"] >= lo) or price_near_zone((b["low"] + b["high"]) / 2, z, tolerance_pct)
               for b in blocks):
            tags.append("order_blocks")
        if tags:
            out[color] = list(dict.fromkeys(out.get(color, []) + tags))
    return out
