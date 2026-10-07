"""The projection's memory: log what it said, score it once the candles close, learn from the score.

  record()  — each heartbeat, keep the projection the dashboard is showing for every timeframe
              (one per timeframe per candle) in projections.jsonl.
  score()   — once a projection's last candle has closed, look up the real price and mark
              direction right/wrong, inside the 50% / 80% ranges, and each expert's error.
  learn()   — feed every newly scored projection into projection_model.json, oldest first:
              multiplicative weights for the experts, band widths from the recent misses,
              and a running record (overall and per day).

Runs on every heartbeat that read prices, and its daily summary is part of the 16:10 review.
Display only: the gate never reads any of it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np

from common import ET, load_json, path, save_json
from studies import forecast

LOG = "projections.jsonl"
MODEL = "projection_model.json"
KEEP_DAYS = 45
TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1D": None}
RECENT = 60  # misses kept for band widths


def read() -> list[dict]:
    p = path(LOG)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def write(records: list[dict]) -> None:
    path(LOG).write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))


def record(now: datetime, charts: dict, extended: bool) -> int:
    """Log the projection shown for each stock and timeframe, once per candle. Returns how many were new."""
    records = read()
    seen = {(r["symbol"], r["tf"], r["issued"]) for r in records}
    added = 0
    for sym, c in (charts or {}).items():
        frames = (c.get("frames_eth") if extended and c.get("frames_eth") else c.get("frames")) or {}
        for tf, fr in frames.items():
            p = fr.get("projection") or {}
            rows = fr.get("c") or []
            if not p.get("t") or not rows or p.get("version") != 2:
                continue
            key = (sym, tf, rows[-1][0])
            if key in seen:
                continue
            h = p["horizon"] - 1
            records.append({
                "symbol": sym, "tf": tf, "issued": rows[-1][0], "logged_at": now.astimezone(ET).isoformat(timespec="seconds"),
                "extended": extended, "last": p["last"], "target": p["t"][h],
                **{f"q{q}": p[f"q{q}"][h] for q in forecast.QUANTILES},
                "experts": p.get("experts") or {}, "weights": p.get("weights") or {},
                "half80": (p.get("bands") or {}).get("half80"), "s50": (p.get("bands") or {}).get("s50"),
                "s80": (p.get("bands") or {}).get("s80"),
            })
            seen.add(key)
            added += 1
    cutoff = (now.astimezone(ET) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    write([r for r in records if r["issued"][:10] >= cutoff])
    return added


def _closes(bars: list[dict] | None) -> list[tuple[str, float]]:
    return [(b["t"].astimezone(ET).strftime("%Y-%m-%dT%H:%M"), float(b["c"])) for b in (bars or [])]


def price_at(rec: dict, minute: list[tuple[str, float]], half: list[tuple[str, float]],
             daily: list[tuple[str, float]], now: datetime) -> float | None:
    """The close of the projection's last candle, once it has closed; None until then (or without data)."""
    minutes = TF_MIN.get(rec["tf"])
    nowk = now.astimezone(ET).strftime("%Y-%m-%dT%H:%M")
    if minutes is None:  # a daily target: that day's last regular-hours price
        day = rec["target"][:10]
        if nowk[:10] < day or (nowk[:10] == day and nowk[11:] < "16:00"):
            return None
        same = [c for t, c in minute if t[:10] == day and "09:30" <= t[11:] < "16:00"]
        if same:
            return same[-1]
        hit = [c for t, c in daily if t[:10] == day]
        return hit[-1] if hit else None
    start = datetime.fromisoformat(rec["target"])
    end = (start + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M")
    if nowk < end:
        return None
    for series in (minute, half):
        inside = [c for t, c in series if rec["target"] <= t < end]
        if inside:
            return inside[-1]
    return None


def score(now: datetime, source) -> int:
    """Score every projection whose candles have closed. Returns how many were scored this time."""
    records = read()
    todo = [r for r in records if "actual" not in r]
    if not todo:
        return 0
    cache: dict = {}
    scored = 0
    for r in todo:
        sym = r["symbol"]
        if sym not in cache:
            history = getattr(source, "history", None)
            cache[sym] = (_closes(source.get(sym)[0]) if source else [],
                          _closes(history(sym, "30Min")) if history else [],
                          _closes(history(sym, "1Day")) if history else [])
        actual = price_at(r, *cache[sym], now)
        if actual is None:
            stale = (now.astimezone(ET) - datetime.fromisoformat(r["logged_at"])).days > 14
            if stale:
                r["actual"], r["unscorable"] = None, True
            continue
        last = r["last"]
        r["actual"] = round(actual, 4)
        r["scored_at"] = now.astimezone(ET).isoformat(timespec="seconds")
        move, called = actual - last, r["q50"] - last
        r["direction_hit"] = bool(move != 0 and np.sign(move) == np.sign(called))
        r["inside_50"] = bool(r["q25"] <= actual <= r["q75"])
        r["inside_80"] = bool(r["q10"] <= actual <= r["q90"])
        half80 = max(abs(r["q90"] - r["q10"]) / 2, 1e-9)
        half50 = max(abs(r["q75"] - r["q25"]) / 2, 1e-9)
        r["z80"] = round(abs(actual - r["q50"]) / half80, 4)
        r["z50"] = round(abs(actual - r["q50"]) / half50, 4)
        r["expert_loss"] = {e: round(abs(p - actual) / half80, 4) for e, p in (r.get("experts") or {}).items()}
        scored += 1
    write(records)
    return scored


def learn(now: datetime) -> dict:
    """Fold newly scored projections into the model, oldest first. Returns the model."""
    records = read()
    model = load_json(MODEL, {}) or {}
    fresh = sorted([r for r in records if r.get("actual") is not None and not r.get("learned")], key=lambda r: r["scored_at"])
    for r in fresh:
        m = model.setdefault(r["tf"], {"n": 0, "hits": 0, "in50": 0, "in80": 0, "z50": [], "z80": [], "days": {}})
        if not m.get("weights"):
            m["weights"] = r.get("weights") or {e: 1 / len(forecast.EXPERTS) for e in forecast.EXPERTS}
        if r.get("expert_loss"):
            m["weights"] = forecast.hedge(m["weights"], r["expert_loss"])
        m["n"] += 1
        m["hits"] += int(r["direction_hit"])
        m["in50"] += int(r["inside_50"])
        m["in80"] += int(r["inside_80"])
        # what the band width should have been: the width used, scaled by how far the misses ran
        if r.get("s50") and r.get("s80"):
            m["z50"] = (m["z50"] + [r["z50"] * r["s50"]])[-RECENT:]
            m["z80"] = (m["z80"] + [r["z80"] * r["s80"]])[-RECENT:]
        day = m["days"].setdefault(r["issued"][:10], {"n": 0, "hits": 0, "in80": 0})
        day["n"] += 1
        day["hits"] += int(r["direction_hit"])
        day["in80"] += int(r["inside_80"])
        r["learned"] = True
    for tf, m in model.items():
        n = m["n"] or 1
        m["direction_hit_pct"] = round(100 * m["hits"] / n, 1)
        m["inside_50_pct"] = round(100 * m["in50"] / n, 1)
        m["inside_80_pct"] = round(100 * m["in80"] / n, 1)
        if len(m["z80"]) >= forecast.MIN_LIVE_BANDS:
            m["s50"] = round(float(np.clip(np.percentile(m["z50"], 50), 0.6, 3.0)), 3)
            m["s80"] = round(float(np.clip(np.percentile(m["z80"], 80), 0.6, 3.0)), 3)
        m["days"] = dict(sorted(m["days"].items())[-KEEP_DAYS:])
        m["leader"] = max(m["weights"], key=m["weights"].get) if m.get("weights") else None
    if fresh:
        for m in model.values():
            m["updated"] = now.astimezone(ET).isoformat(timespec="seconds")
        write(records)
        save_json(MODEL, model)
    return model


def models() -> dict:
    return load_json(MODEL, {}) or {}


def heartbeat(now: datetime, source, charts: dict | None, extended: bool) -> dict:
    """Score, learn, then log what is showing now. A projection problem never stops the desk."""
    out = {"scored": 0, "logged": 0}
    try:
        out["scored"] = score(now, source)
        learn(now)
        if charts:
            out["logged"] = record(now, charts, extended)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    return out


def daily_summary(now: datetime) -> dict:
    """Today's scored projections per timeframe, for the 16:10 review."""
    day = now.astimezone(ET).date().isoformat()
    out = {}
    for tf, m in models().items():
        d = (m.get("days") or {}).get(day)
        if d and d["n"]:
            out[tf] = {"scored": d["n"], "direction_hit_pct": round(100 * d["hits"] / d["n"], 1),
                       "inside_80_pct": round(100 * d["in80"] / d["n"], 1), "leader": m.get("leader")}
    return out
