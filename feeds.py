"""The desk's own sentiment feed: free sources it can read from GitHub Actions without connectors.

    stocktwits  the public SPY stream: the share of tagged messages marked Bullish (0-100)
    headlines   Yahoo Finance's SPY news headlines (RSS)
    vix         the VIX's last close and day change (yfinance)

collect() turns them into a readings object for run_study.cmd_pulse_ingest. The hourly AI reader (a
scheduled Claude routine) sends richer readings with Reddit and news tone, rumors and plays; while its
reading is fresh, the desk leaves the pulse alone (see due()).

Parsers are pure; the fetchers do the network calls and never raise (a dead source is just missing).
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timedelta
from xml.etree import ElementTree

from common import ET, to_et

UA = "Mozilla/5.0 (spy-paper-desk; paper-trading research)"
STOCKTWITS = "https://api.stocktwits.com/api/2/streams/symbol/{sym}.json"
YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"
EVERY_MIN = 30          # the desk's own read at most this often
READER_FRESH_MIN = 70   # an AI reader's reading this recent wins


def _get(url: str, timeout: float = 12.0) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URLs
            return r.read()
    except Exception:  # noqa: BLE001 - a dead source is just missing
        return None


# ---------------------------------------------------------------- parsers (pure)

def parse_stocktwits(payload: dict) -> dict | None:
    """Bullish share of the messages that carry a Bullish/Bearish tag, and a few message texts."""
    msgs = (payload or {}).get("messages") or []
    if not msgs:
        return None
    bull = bear = 0
    for m in msgs:
        s = (((m.get("entities") or {}).get("sentiment") or {}) or {}).get("basic")
        bull += s == "Bullish"
        bear += s == "Bearish"
    tagged = bull + bear
    texts = [re.sub(r"\s+", " ", str(m.get("body") or ""))[:160] for m in msgs[:5]]
    return {"score": round(100 * bull / tagged, 1) if tagged else None, "bullish_pct": round(100 * bull / tagged, 1) if tagged else None,
            "messages": len(msgs), "tagged": tagged, "sample": texts}


def parse_rss(xml_bytes: bytes, limit: int = 10) -> list[str]:
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        t = (item.findtext("title") or "").strip()
        if t:
            out.append(t[:200])
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- fetchers

def stocktwits(sym: str = "SPY") -> dict | None:
    raw = _get(STOCKTWITS.format(sym=sym))
    try:
        return parse_stocktwits(json.loads(raw)) if raw else None
    except ValueError:
        return None


def headlines(sym: str = "SPY") -> list[str]:
    raw = _get(YAHOO_RSS.format(sym=sym))
    return parse_rss(raw) if raw else []


def vix() -> dict | None:
    try:
        import yfinance as yf

        df = yf.download("^VIX", period="5d", interval="1d", progress=False, auto_adjust=False, multi_level_index=False)
        closes = [float(c) for c in df["Close"].dropna().tolist()]
        if not closes:
            return None
        chg = (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) > 1 else None
        return {"vix": round(closes[-1], 2), "vix_change_pct": round(chg, 2) if chg is not None else None}
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- when, and the readings object

def due(now: datetime, last_reading: dict | None) -> bool:
    """Read again when the newest reading is older than EVERY_MIN, and no AI reader's reading is fresh."""
    if not last_reading:
        return True
    ts = to_et(last_reading.get("ts"))
    if ts is None:
        return True
    age = now.astimezone(ET) - ts
    if last_reading.get("source") != "desk_feeds" and age < timedelta(minutes=READER_FRESH_MIN):
        return False
    return age >= timedelta(minutes=EVERY_MIN)


def collect(now: datetime, spy_bars: list[dict] | None, fetch: dict | None = None) -> dict:
    """A readings object from the free sources (fetch lets tests pass canned results)."""
    fetch = fetch or {}
    st = fetch["stocktwits"]() if "stocktwits" in fetch else stocktwits()
    hl = fetch["headlines"]() if "headlines" in fetch else headlines()
    vx = fetch["vix"]() if "vix" in fetch else vix()
    out: dict = {"as_of": now.astimezone(ET).isoformat(timespec="seconds"), "source": "desk_feeds",
                 "sources": {"sentiment_flow": "stocktwits public stream" if st else None,
                             "news": "yahoo finance rss" if hl else None, "vix": "yfinance" if vx else None}}
    bars = sorted(spy_bars or [], key=lambda b: b["t"])
    if bars:
        today = [b for b in bars if b["t"].astimezone(ET).date() == now.astimezone(ET).date()]
        before = [b for b in bars if b["t"].astimezone(ET).date() < now.astimezone(ET).date()
                  and 570 <= b["t"].astimezone(ET).hour * 60 + b["t"].astimezone(ET).minute < 960]
        last = (today or bars)[-1]["c"]
        out["spy_price"] = round(float(last), 4)
        if before:
            prev = float(before[-1]["c"])
            out["spy_change_pct"] = round((float(last) - prev) / prev * 100, 3)
    if st:
        out["stocktwits"] = {"SPY": {"score": st["score"], "bullish_pct": st["bullish_pct"]}}
    if hl:
        out["news"] = {"headlines": hl}
    if vx:
        out.update(vx)
    return out
