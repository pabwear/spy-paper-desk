"""Pattern projection: what followed the past setups that looked most like the last few candles.

An analog ("nearest neighbours") method, kept honest by a walk-forward record:
  1. Take the last `window` candle-to-candle moves, each divided by that stretch's own volatility,
     so the shape matters and not the size.
  2. Find the `neighbours` most similar stretches earlier in history.
  3. Collect what price did over the next `horizon` candles after each one, scaled to today's
     volatility, and report the 10th/25th/50th/75th/90th percentile path.
  4. Re-run the same recipe at many past points, using only data available at the time, and
     count how often it called the direction and how often price ended inside the bands.
  5. Widen the bands (never narrow them) by how far price actually landed from the middle path in
     those tests, so the 50% and 80% bands mean what they say.

It is a range of outcomes from history, not a prediction. The gate never reads it.
Pure: candles in, dict out. numpy for speed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from common import ET

QUANTILES = (10, 25, 50, 75, 90)
# candles ahead per timeframe: about an hour, a session, a few sessions, two weeks
HORIZON = {"1m": 30, "5m": 12, "15m": 8, "30m": 8, "1h": 7, "4h": 6, "1D": 10}
DEFAULTS = {"window": 20, "neighbours": 25, "tests": 60, "min_history": 120}


def _shapes(r: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """Every `window`-long stretch of log returns, each scaled by its own volatility."""
    idx = np.arange(window)[None, :] + np.arange(len(r) - window + 1)[:, None]
    seg = r[idx]
    vol = seg.std(axis=1)
    vol[vol == 0] = np.nan
    return seg / vol[:, None], vol


def _project(r: np.ndarray, shapes: np.ndarray, vols: np.ndarray, end: int, window: int, horizon: int,
             k: int) -> np.ndarray | None:
    """Percentile paths (cumulative log return, len(QUANTILES) × horizon) for the stretch ending at return `end`.

    Only stretches whose whole outcome was known before `end` started are eligible.
    """
    cur = end - window + 1  # index of the current stretch in `shapes`
    if cur < 0 or np.isnan(vols[cur]):
        return None
    last_ok = cur - horizon  # a stretch ending at j has its outcome finished by j + horizon < cur's start
    if last_ok < 1:
        return None
    cand = shapes[:last_ok]
    d = np.sqrt(np.nansum((cand - shapes[cur]) ** 2, axis=1))
    d[np.isnan(vols[:last_ok])] = np.inf
    k = min(k, int(np.isfinite(d).sum()))
    if k < 5:
        return None
    best = np.argpartition(d, k - 1)[:k]
    paths = []
    for j in best:
        after = r[j + window: j + window + horizon]
        paths.append(np.cumsum(after) * (vols[cur] / vols[j]))
    return np.percentile(np.array(paths), QUANTILES, axis=0)


def future_times(last: str, n: int, minutes: int | None, regular_hours_only: bool = True) -> list[str]:
    """The next `n` candle start times after `last` (ET wall clock), skipping nights and weekends."""
    out = []
    if minutes is None:
        d = datetime.fromisoformat(last[:10]).date()
        while len(out) < n:
            d += timedelta(days=1)
            if d.weekday() < 5:
                out.append(d.isoformat())
        return out
    t = datetime.fromisoformat(last).replace(tzinfo=ET)
    while len(out) < n:
        t += timedelta(minutes=minutes)
        if regular_hours_only:
            if t.weekday() >= 5 or t.hour * 60 + t.minute >= 16 * 60:
                nxt = t + timedelta(days=1)
                while nxt.weekday() >= 5:
                    nxt += timedelta(days=1)
                t = nxt.replace(hour=9, minute=30)
            elif t.hour * 60 + t.minute < 9 * 60 + 30:
                t = t.replace(hour=9, minute=30)
        out.append(t.strftime("%Y-%m-%dT%H:%M"))
    return out


def project(closes: list[float], times: list[str], timeframe: str, minutes: int | None,
            cfg: dict | None = None, regular_hours_only: bool = True) -> dict | None:
    """The projection for the next candles plus its walk-forward record, or None without enough history."""
    cfg = {**DEFAULTS, **(cfg or {})}
    window, k, horizon = int(cfg["window"]), int(cfg["neighbours"]), HORIZON.get(timeframe, 10)
    c = np.asarray(closes, dtype=float)
    if len(c) < max(int(cfg["min_history"]), window + horizon * 2 + 10) or np.any(c <= 0):
        return None
    r = np.diff(np.log(c))
    shapes, vols = _shapes(r, window)
    q = _project(r, shapes, vols, len(r) - 1, window, horizon, k)
    if q is None:
        return None
    last = float(c[-1])

    # walk-forward record: same recipe at past points, scored against what actually happened
    hits = in50 = in80 = tests = 0
    miss50, miss80 = [], []
    for end in range(len(r) - 1 - horizon, window + horizon, -max(1, horizon // 2)):
        if tests >= int(cfg["tests"]):
            break
        past = _project(r[: end + 1], shapes[: end - window + 2], vols[: end - window + 2], end, window, horizon, k)
        if past is None:
            continue
        actual = float(np.sum(r[end + 1: end + 1 + horizon]))
        med = float(past[2][-1])
        tests += 1
        hits += int(np.sign(med) == np.sign(actual) and actual != 0)
        in50 += int(past[1][-1] <= actual <= past[3][-1])
        in80 += int(past[0][-1] <= actual <= past[4][-1])
        miss50.append(abs(actual - med) / max((past[3][-1] - past[1][-1]) / 2, 1e-9))
        miss80.append(abs(actual - med) / max((past[4][-1] - past[0][-1]) / 2, 1e-9))
    s50 = s80 = 1.0
    if tests >= 20:
        s50 = float(np.clip(np.percentile(miss50, 50), 1.0, 3.0))
        s80 = float(np.clip(np.percentile(miss80, 80), 1.0, 3.0))
    mid = q[2]
    q = np.array([mid - (mid - q[0]) * s80, mid - (mid - q[1]) * s50, mid, mid + (q[3] - mid) * s50,
                  mid + (q[4] - mid) * s80])
    pct = lambda n: round(100.0 * n / tests, 1) if tests else None  # noqa: E731
    return {
        "t": future_times(times[-1], horizon, minutes, regular_hours_only),
        **{f"q{p}": [round(last * float(np.exp(v)), 4) for v in q[i]] for i, p in enumerate(QUANTILES)},
        "horizon": horizon,
        "neighbours": k,
        "window": window,
        "record": {"tests": tests, "direction_hit_pct": pct(hits), "inside_50_pct": pct(in50),
                   "inside_80_pct": pct(in80), "widened_50": round(s50, 2), "widened_80": round(s80, 2)},
    }
