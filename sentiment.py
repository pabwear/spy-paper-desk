"""Sentiment, rumors and candidate plays from the hourly reader, kept as history and scored.

The reader is a scheduled Claude routine (market hours, every hour). It reads Stocktwits, Stocklake,
news and Reddit, then hands the desk one readings object through the Market Pulse form (pulse.yml):

    {
      "as_of": "2026-10-07T14:05:00Z",
      "spy_price": 781.2, "spy_change_pct": 0.4, "vix": 15.8, "fear_greed": 52,
      "stocktwits": {"SPY": {"score": 64, "bullish_pct": 58}},
      "reddit": {"score": 0.3, "posts": 40, "top": ["..."]},          # tone -1 (bearish) .. +1 (bullish)
      "news": {"score": -0.1, "headlines": ["..."]},
      "rumors": [{"text": "...", "source": "reddit", "tickers": ["NVDA"], "names": {"NVDA": "NVIDIA"},
                  "direction": "up", "credibility": 0.4, "why": "what it would mean if true"}],
      "plays": [{"ticker": "SPY", "direction": "up", "horizon": "close", "confidence": 0.55,
                 "reason": "...", "catalyst": "..."}],
      "whats_new": "..."
    }

record()  — every readings object becomes one line in sentiment.jsonl, and each play a line in plays.jsonl.
score()   — once a play's time is up (1h, the close, or the next close), look up SPY's real move and mark it
            right or wrong. Plays on other tickers are kept but not scored (the desk prices SPY only).
series(), plays_summary(), latest_brief() — for the dashboard and for each entry's record.

Nothing here places or gates an order: the desk logs the mood with every entry so the learner can test it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from common import ET, path, to_et

LOG = "sentiment.jsonl"
PLAYS = "plays.jsonl"
KEEP_DAYS = 120
HORIZONS = ("1h", "close", "1d")
MAX_TEXT = 220


def _read(name: str) -> list[dict]:
    p = path(name)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _write(name: str, rows: list[dict]) -> None:
    path(name).write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))


def _num(v, lo: float | None = None, hi: float | None = None) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return round(x, 4)


def _text(v) -> str:
    return str(v or "").strip()[:MAX_TEXT]


def _dir(v) -> str | None:
    v = str(v or "").lower()
    return "up" if v in ("up", "bull", "bullish", "long", "call") else "down" if v in ("down", "bear", "bearish", "short", "put") else None


# ---------------------------------------------------------------- record

def record(now: datetime, readings: dict) -> dict:
    """Keep one compact line per readings object, and each candidate play. Returns the line."""
    now = now.astimezone(ET)
    st = ((readings.get("stocktwits") or {}).get("SPY") or {})
    rd, nw = readings.get("reddit") or {}, readings.get("news") or {}
    names = {str(k).upper()[:8]: _text(v)[:60] for k, v in (readings.get("names") or {}).items()} \
        if isinstance(readings.get("names"), dict) else {}
    rumors = []
    for r in (readings.get("rumors") or [])[:20]:
        if not isinstance(r, dict) or not r.get("text"):
            continue
        tickers = [str(t).upper().lstrip("$")[:8] for t in (r.get("tickers") or [])][:5]
        rn = r.get("names") if isinstance(r.get("names"), dict) else {}
        names.update({str(k).upper()[:8]: _text(v)[:60] for k, v in rn.items()})
        rumors.append({"text": _text(r.get("text")), "source": _text(r.get("source"))[:30], "tickers": tickers,
                       "direction": _dir(r.get("direction")), "credibility": _num(r.get("credibility"), 0, 1),
                       "why": _text(r.get("why")) or None})
    line = {
        "ts": now.isoformat(timespec="seconds"), "as_of": readings.get("as_of"),
        "source": str(readings.get("source") or "reader")[:20],
        "spy_price": _num(readings.get("spy_price")), "spy_change_pct": _num(readings.get("spy_change_pct")),
        "vix": _num(readings.get("vix")), "fear_greed": _num(readings.get("fear_greed"), 0, 100),
        "stocktwits": _num(st.get("score"), 0, 100), "stocktwits_bullish_pct": _num(st.get("bullish_pct"), 0, 100),
        "reddit": _num(rd.get("score"), -1, 1), "reddit_posts": _num(rd.get("posts")),
        "news": _num(nw.get("score"), -1, 1),
        "headlines": [_text(h) for h in (nw.get("headlines") or [])[:8]],
        "reddit_top": [_text(h) for h in (rd.get("top") or [])[:8]],
        "rumors": rumors, "names": names, "whats_new": _text(readings.get("whats_new"))[:600] if readings.get("whats_new") else None,
    }
    rows = _read(LOG) + [line]
    cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
    _write(LOG, [r for r in rows if r["ts"] >= cutoff])

    plays = _read(PLAYS)
    n = len(plays)
    for i, p in enumerate((readings.get("plays") or [])[:10]):
        if not isinstance(p, dict):
            continue
        d, h = _dir(p.get("direction")), str(p.get("horizon") or "close").lower()
        if d is None:
            continue
        ticker = str(p.get("ticker") or "SPY").upper()[:8]
        plays.append({"id": f"{now:%Y%m%d-%H%M}-{n + i}", "ts": line["ts"], "ticker": ticker, "direction": d,
                      "horizon": h if h in HORIZONS else "close", "confidence": _num(p.get("confidence"), 0, 1),
                      "reason": _text(p.get("reason")), "catalyst": _text(p.get("catalyst")),
                      "ref_price": line["spy_price"] if ticker == "SPY" else None,
                      "status": "open" if ticker == "SPY" else "unscored"})
    _write(PLAYS, [p for p in plays if p["ts"] >= cutoff or p["status"] == "open"])
    return line


# ---------------------------------------------------------------- score

def _next_weekday(d):
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def due(ts: datetime, horizon: str) -> datetime:
    """When a play's call is checked. Plays made outside 09:30–16:00 start from the next open."""
    t = ts.astimezone(ET)
    day = t.date()
    if t.weekday() >= 5 or t.hour * 60 + t.minute >= 960:
        day = _next_weekday(day)
        t = datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET)
    elif t.hour * 60 + t.minute < 570:
        t = t.replace(hour=9, minute=30, second=0, microsecond=0)
    close = datetime(day.year, day.month, day.day, 16, 0, tzinfo=ET)
    if horizon == "1h":
        return min(t + timedelta(hours=1), close)
    if horizon == "1d":
        nd = _next_weekday(day)
        return datetime(nd.year, nd.month, nd.day, 16, 0, tzinfo=ET)
    return close


def _price_at(bars: list[dict], t: datetime, after: bool) -> float | None:
    """The close of the last bar at or before t (after=False) or the first bar at or after t (after=True)."""
    if after:
        for b in bars:
            if b["t"] >= t:
                return float(b["c"])
        return None
    best = None
    for b in bars:
        if b["t"] <= t:
            best = float(b["c"])
        else:
            break
    return best


def score(now: datetime, spy_bars: list[dict] | None) -> int:
    """Mark every SPY play whose time is up. Returns how many were scored this run."""
    plays = _read(PLAYS)
    bars = sorted(spy_bars or [], key=lambda b: b["t"])
    scored = changed = 0
    for p in plays:
        if p.get("status") != "open":
            continue
        start = to_et(p["ts"])
        end = due(start, p["horizon"])
        if now < end:
            continue
        p0 = _price_at(bars, start, after=True) if bars else None
        p1 = _price_at(bars, end, after=False) if bars else None
        if p0 is None and p.get("ref_price"):
            p0 = float(p["ref_price"])
        if p0 is None or p1 is None or not bars or bars[-1]["t"] < end:
            if now - end > timedelta(days=3):
                p["status"] = "unscored"  # the bars never covered it
                changed += 1
            continue
        move = (p1 - p0) / p0 * 100
        p.update(status="scored", checked_at=now.astimezone(ET).isoformat(timespec="seconds"),
                 price_then=round(p0, 4), price_after=round(p1, 4), move_pct=round(move, 4),
                 right=(move > 0) == (p["direction"] == "up") and move != 0)
        scored += 1
    if scored or changed:
        _write(PLAYS, plays)
    return scored


# ---------------------------------------------------------------- read back

def series(days: int = 30, now: datetime | None = None) -> list[dict]:
    """The numbers over time, for the dashboard's lines."""
    rows = _read(LOG)
    if now is not None:
        cutoff = (now.astimezone(ET) - timedelta(days=days)).isoformat()
        rows = [r for r in rows if r["ts"] >= cutoff]
    keys = ("ts", "spy_price", "vix", "fear_greed", "stocktwits", "reddit", "news")
    return [{k: r.get(k) for k in keys} for r in rows]


def latest_brief() -> dict | None:
    """The newest reading's numbers and its newest rumors, for an entry's record."""
    rows = _read(LOG)
    if not rows:
        return None
    r = rows[-1]
    return {k: r.get(k) for k in ("ts", "stocktwits", "reddit", "news", "fear_greed", "vix", "whats_new")} | \
        {"rumors": [x["text"] for x in r.get("rumors", [])[:3]]}


def plays_summary(limit: int = 40) -> dict:
    """Recent plays and how often each kind turned out right."""
    plays = _read(PLAYS)
    scored = [p for p in plays if p.get("status") == "scored"]

    def rate(ps):
        return {"n": len(ps), "right_pct": round(100 * sum(1 for p in ps if p.get("right")) / len(ps), 1) if ps else None}

    by_conf = {}
    for name, lo, hi in (("low", 0, 0.5), ("mid", 0.5, 0.7), ("high", 0.7, 1.01)):
        by_conf[name] = rate([p for p in scored if p.get("confidence") is not None and lo <= p["confidence"] < hi])
    return {"recent": list(reversed(plays))[:limit], "all": rate(scored),
            "by_horizon": {h: rate([p for p in scored if p["horizon"] == h]) for h in HORIZONS},
            "by_confidence": by_conf,
            "open": sum(1 for p in plays if p.get("status") == "open")}


# ---------------------------------------------------------------- the rumor mill

def lean(bull: float, bear: float) -> str:
    """Bullish or bearish when one side carries at least 60 % of the believability-weighted rumors."""
    total = bull + bear
    if total <= 0:
        return "mixed"
    share = bull / total
    return "bullish" if share >= 0.6 else "bearish" if share <= 0.4 else "mixed"


def rumor_mill(days: int = 3, now: datetime | None = None, limit: int = 60) -> dict:
    """Every rumor of the last `days`, newest first (repeats of the same text kept once, at their latest
    sighting), and a lean per stock: each rumor counts by its believability, up for bullish, down for bearish."""
    rows = _read(LOG)
    if now is not None:
        cutoff = (now.astimezone(ET) - timedelta(days=days)).isoformat()
        rows = [r for r in rows if r["ts"] >= cutoff]
    names: dict[str, str] = {}
    seen: dict[str, dict] = {}
    for r in rows:
        names.update(r.get("names") or {})
        for x in r.get("rumors") or []:
            key = x["text"].lower()
            prev = seen.get(key)
            seen[key] = {**x, "ts": r["ts"], "seen": (prev or {}).get("seen", 0) + 1,
                         "first_ts": (prev or {}).get("first_ts", r["ts"])}
    rumors = sorted(seen.values(), key=lambda x: x["ts"], reverse=True)
    stocks: dict[str, dict] = {}
    for x in rumors:
        w = x.get("credibility") if x.get("credibility") is not None else 0.3
        for t in x.get("tickers") or []:
            s = stocks.setdefault(t, {"ticker": t, "name": names.get(t), "rumors": 0, "bull": 0.0, "bear": 0.0,
                                      "latest": x["ts"]})
            s["rumors"] += 1
            if x.get("direction") == "up":
                s["bull"] += w
            elif x.get("direction") == "down":
                s["bear"] += w
            s["latest"] = max(s["latest"], x["ts"])
    for s in stocks.values():
        s["bull"], s["bear"] = round(s["bull"], 3), round(s["bear"], 3)
        s["lean"] = lean(s["bull"], s["bear"])
        s["avg_believable"] = round((s["bull"] + s["bear"]) / s["rumors"], 3) if s["rumors"] else None
    ranked = sorted(stocks.values(), key=lambda s: (s["bull"] + s["bear"], s["rumors"]), reverse=True)
    return {"days": days, "stocks": ranked, "rumors": rumors[:limit], "names": names}


def latest_full() -> dict | None:
    rows = _read(LOG)
    return rows[-1] if rows else None
