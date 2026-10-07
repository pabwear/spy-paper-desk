"""Chart data for the dashboard: candles on every timeframe (1m → 1D), session VWAP, the desk's
Mxwll read on each timeframe, today's zones and the desk's trades.

Written to charts.json on every heartbeat that read prices, for each focus stock. The dashboard
draws it. Pure except for `write`.
"""

from __future__ import annotations

from datetime import datetime

import instruments
import journal
from common import ET, aoi_file, focus_symbols, hhmm, load_json, save_json, to_et
from signals import bars_today

FILE = "charts.json"
BUCKET_MIN = 5

# Shortest to longest: (name, minutes per candle or None for daily, candles sent to the page).
# The study reads every candle available; the page gets the most recent ones.
TIMEFRAMES = [("1m", 1, 390), ("3m", 3, 260), ("5m", 5, 156), ("15m", 15, 130), ("30m", 30, 130),
              ("1h", 60, 140), ("4h", 240, 120), ("1D", None, 250)]


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


def daily_candles(daily_bars: list[dict] | None, minute_bars: list[dict] | None, now: datetime,
                  open_hhmm: str = "09:30", close_hhmm: str = "16:00") -> list[dict]:
    """Finished days from the daily history, plus today's candle built from today's regular-hours minutes."""
    today = now.astimezone(ET).date()
    out = []
    for b in daily_bars or []:
        d = b["t"].astimezone(ET).date()
        if d < today and (not out or out[-1]["t"].date() != d):
            out.append({"t": datetime(d.year, d.month, d.day, tzinfo=ET), "o": float(b["o"]), "h": float(b["h"]),
                        "l": float(b["l"]), "c": float(b["c"]), "v": float(b["v"])})
    o_t, c_t = hhmm(open_hhmm), hhmm(close_hhmm)
    session = [b for b in minute_bars or [] if b["t"].astimezone(ET).date() == today
               and o_t <= b["t"].astimezone(ET).time() < c_t]
    if session:
        out.append({"t": datetime(today.year, today.month, today.day, tzinfo=ET), "o": float(session[0]["o"]),
                    "h": max(float(b["h"]) for b in session), "l": min(float(b["l"]) for b in session),
                    "c": float(session[-1]["c"]), "v": sum(float(b["v"]) for b in session)})
    return out


def session_vwaps(candles: list[dict]) -> list[float | None]:
    """VWAP that starts over each trading day."""
    out, day, pv, vol = [], None, 0.0, 0.0
    for c in candles:
        d = c["t"].astimezone(ET).date()
        if d != day:
            day, pv, vol = d, 0.0, 0.0
        pv += (c["h"] + c["l"] + c["c"]) / 3.0 * c["v"]
        vol += c["v"]
        out.append(round(pv / vol, 4) if vol else None)
    return out


def _row(c: dict, daily: bool) -> list:
    t = c["t"].astimezone(ET)
    return [t.date().isoformat() if daily else t.strftime("%Y-%m-%dT%H:%M"),
            round(float(c["o"]), 4), round(float(c["h"]), 4), round(float(c["l"]), 4), round(float(c["c"]), 4),
            round(float(c["v"]))]


def _study(candles: list[dict], cfg: dict, daily: bool) -> dict | None:
    from studies import mxwll

    out = mxwll.analyze(candles, cfg)
    if not out:
        return None
    lookback = int(cfg["aoi_lookback"])
    return {"aoi": out["aoi"], "internal": out["internal"], "external": out["external"],
            "from": _row(candles[-lookback], daily)[0] if len(candles) > lookback else None,
            "order_blocks": out["order_blocks"], "internal_events": out["internal_events"],
            "external_events": out["external_events"], "swing_points": out["swing_points"],
            "fibs": out["fibs"], "fvgs": out["fvgs"]}


def frames(minute_bars: list[dict] | None, half_hours: list[dict] | None, dailies: list[dict] | None,
           now: datetime, cfg: dict, rth: bool | None = None, daily_frame: dict | None = None,
           models: dict | None = None) -> dict:
    """Candles, VWAP, the Mxwll read and the pattern projection for each timeframe there is enough data for.

    1m–15m come from the minute bars; 30m–4h from ~60 days of 30-minute bars with the minute bars
    on top; 1D from daily bars plus today's session.
    """
    from studies import mxwll

    rth = bool(cfg.get("regular_hours_only", True)) if rth is None else rth
    minute = sorted(minute_bars or [], key=lambda b: b["t"])
    first_day = minute[0]["t"].astimezone(ET).date() if minute else None
    older = [b for b in half_hours or [] if first_day is None or b["t"].astimezone(ET).date() < first_day]
    out = {}
    for name, minutes, show in TIMEFRAMES:
        if minutes is None and daily_frame is not None:  # the daily chart is the same either way
            out[name] = daily_frame
            continue
        if minutes is None:
            candles = daily_candles(dailies, minute, now)
        else:
            candles = mxwll.resample(minute if minutes < 30 else older + minute, minutes, rth)
        candles = [c for c in candles if c["t"] <= now]
        if not candles:
            continue
        daily = minutes is None
        try:
            study = _study(candles, cfg, daily)
        except Exception as e:  # noqa: BLE001 - a study problem never stops the chart
            study = {"error": f"{type(e).__name__}: {e}"[:200]}
        vw = session_vwaps(candles)[-show:] if minutes is not None and minutes <= 60 else None
        rows = [_row(c, daily) for c in candles]
        try:
            from studies import forecast

            proj = forecast.project([r[4] for r in rows], [r[0] for r in rows], name, minutes, regular_hours_only=rth,
                                    model=(models or {}).get(name))
        except Exception as e:  # noqa: BLE001 - a projection problem never stops the chart
            proj = {"error": f"{type(e).__name__}: {e}"[:200]}
        out[name] = {"c": rows[-show:], "vwap": vw, "study": study, "projection": proj}
    return out


def trade_plans(bars: list[dict] | None, now: datetime, cfg: dict, zones: list[dict], zones_from: str,
                stop_pct: float, risk_usd: float = 100.0, contracts: int = 1) -> dict | None:
    """Projected trades: for each zone, the chance price gets there and the chance the desk's trade would pay,
    along the look-alike paths on the desk's own timeframe and session. Display only."""
    from studies import forecast, mxwll

    minutes = int(cfg.get("timeframe_minutes", 5))
    # the desk only trades in regular hours, so the paths are regular-hours candles whatever the chart shows
    candles = mxwll.resample(sorted(bars or [], key=lambda b: b["t"]), minutes, True)
    candles = [c for c in candles if c["t"] <= now]
    if len(candles) < 80 or not zones:
        return None
    t = now.astimezone(ET)
    mins = t.hour * 60 + t.minute
    open_now = t.weekday() < 5 and 570 <= mins < 960
    horizon = max(6, min(78, (955 - mins) // minutes)) if open_now else min(78, 360 // minutes)  # to the close, or a full next session
    paths = forecast.analog_paths([c["c"] for c in candles], horizon)
    if paths is None:
        return None
    last = float(candles[-1]["c"])
    plans = []
    for z in zones:
        side = "buy" if z.get("color") == "red" else "sell"
        odds = forecast.plan_trade(paths, last, float(z["low"]), float(z["high"]), side, stop_pct)
        sug = forecast.suggest(paths, last, float(z["low"]), float(z["high"]), side, stop_pct, risk_usd, contracts)
        plans.append({"color": z.get("color"), "low": float(z["low"]), "high": float(z["high"]), "side": side,
                      "contract": "call" if side == "buy" else "put", **odds, "suggest": sug})
    ends = forecast.future_times(_row(candles[-1], False)[0], horizon, minutes, True)[-1]
    return {"tf": f"{minutes}m", "horizon": horizon, "until": ends, "by_close": open_now, "last": round(last, 2),
            "stop_pct": stop_pct, "zones_from": zones_from, "plans": plans,
            "note": "Odds from what followed the look-alike moments; option dollars use an at-the-money delta of 0.5 and leave out the option's price and time decay."}


def build(symbol: str, bars: list[dict] | None, now: datetime, risk: dict, events: list[dict],
          half_hours: list[dict] | None = None, dailies: list[dict] | None = None) -> dict | None:
    if not bars:
        return None
    today = [b for b in bars_today(bars, now) if b["t"].astimezone(ET).time() >= hhmm(risk.get("rth_open", "09:30"))]
    if not today:
        return None
    candles = resample(today)
    override = load_json(aoi_file(symbol), None) or {}
    try:  # the desk's own Mxwll read right now: rolling AOI boxes, last breaks, order blocks
        from studies import auto as auto_study

        cfg = auto_study.desk_config()
        study = auto_study.read(bars, now, cfg)
    except Exception as e:  # noqa: BLE001 - a study problem never stops the chart
        cfg, study = None, {"error": f"{type(e).__name__}: {e}"[:200]}
    try:
        import projection_log

        models = projection_log.models()
        tf = frames(bars, half_hours, dailies, now, cfg, rth=True, models=models) if cfg else {}
        tf_eth = frames(bars, half_hours, dailies, now, cfg, rth=False, daily_frame=tf.get("1D"), models=models) if cfg else {}
    except Exception as e:  # noqa: BLE001
        journal.log("chart_failed", now=now, symbol=symbol, error=f"timeframes: {type(e).__name__}: {e}"[:300])
        tf, tf_eth = {}, {}
    try:  # the Mxwll Suite's rolling 4-hour and 1-day highs and lows, from the 1-minute bars
        from studies import mxwll

        rolling = {"rth": mxwll.rolling_levels(bars, True), "eth": mxwll.rolling_levels(bars, False)}
    except Exception as e:  # noqa: BLE001
        rolling = {"error": f"{type(e).__name__}: {e}"[:200]}
    plans = None
    try:
        written = to_et(override.get("written_at"))
        today_zones = override.get("zones") if written and written.date() == now.astimezone(ET).date() else []
        zones_from = "today's zones"
        if not today_zones and isinstance(study, dict) and study.get("aoi"):
            today_zones = [{"color": k, "low": study["aoi"][k]["low"], "high": study["aoi"][k]["high"]}
                           for k in ("red", "green") if study["aoi"][k]["visible"]]
            zones_from = "the desk's live Mxwll boxes (no zones for today yet)"
        rules = load_json("rules.json", {}) or {}
        stop = float(rules.get("stop_underlying_pct", 0.35))
        book = float(rules.get("book_usd") or risk.get("target_book_usd") or 1000)
        risk_usd = book * float(risk.get("max_risk_pct_per_idea", 10)) / 100
        contracts = int((rules.get("option") or {}).get("contracts", 1))
        plans = trade_plans(bars, now, cfg, today_zones, zones_from, stop, risk_usd, contracts) if cfg else None
    except Exception as e:  # noqa: BLE001 - a plan problem never stops the chart
        plans = {"error": f"{type(e).__name__}: {e}"[:200]}
    return {
        "symbol": symbol,
        "date": now.astimezone(ET).date().isoformat(),
        "plans": plans,
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
        "study_timeframe": (cfg or {}).get("timeframe_minutes"),
        "frames": tf,
        "frames_eth": tf_eth,
        "rolling": rolling,
        "extended_hours": not bool((cfg or {}).get("regular_hours_only", True)),
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
        history = getattr(source, "history", None)
        c = build(sym, bars, now, risk, events,
                  history(sym, "30Min") if bars and history else None,
                  history(sym, "1Day") if bars and history else None)
        if c:
            charts[sym] = c
    charts = {k: v for k, v in charts.items() if k in focus_symbols()}
    save_json(FILE, charts)
    return charts
