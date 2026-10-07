"""Pattern projection that learns: several forecasters ("experts") blended by how well each has done.

Experts, each a guess at the next `horizon` candles:
  analog_10 / analog_20 / analog_40 — what followed the 25 past stretches that looked most like the last
      10, 20 or 40 candles (moves divided by that stretch's own volatility, so shape matters, not size)
  momentum  — the last 40 candles' average move, carried forward
  reversion — a drift back toward the 20-candle average
  flat      — no move at all (the bar every forecaster has to beat)

How it learns:
  * Walk-forward on history first: replay the recipe at many past points, using only what was known then,
    and update the blend after each one (multiplicative weights: each expert's weight shrinks with its
    error, measured in units of the band's width). That gives today's starting weights and an
    out-of-sample record.
  * Then live: every projection the desk shows is logged, scored once its candles close, and fed back
    the same way (projection_log.py). Live weights and band widths replace the historical ones as soon
    as there are enough scored projections.
  * Bands come from the 20-candle analogs around the blended middle path, widened or narrowed so the
    50% and 80% ranges hold price about half and 8 times in 10.

A range from history, not a promise. The gate never reads it. Pure: numbers in, dict out.
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
    """The next `n` candle start times after `last` (ET wall clock), skipping nights and weekends.

    Regular hours run 09:30–16:00; extended hours 04:00–20:00.
    """
    out = []
    if minutes is None:
        d = datetime.fromisoformat(last[:10]).date()
        while len(out) < n:
            d += timedelta(days=1)
            if d.weekday() < 5:
                out.append(d.isoformat())
        return out
    open_m, close_m = (9 * 60 + 30, 16 * 60) if regular_hours_only else (4 * 60, 20 * 60)
    t = datetime.fromisoformat(last).replace(tzinfo=ET)
    while len(out) < n:
        t += timedelta(minutes=minutes)
        m = t.hour * 60 + t.minute
        if t.weekday() >= 5 or m >= close_m:
            nxt = t + timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
            t = nxt.replace(hour=open_m // 60, minute=open_m % 60)
        elif m < open_m:
            t = t.replace(hour=open_m // 60, minute=open_m % 60)
        out.append(t.strftime("%Y-%m-%dT%H:%M"))
    return out


EXPERTS = ("analog_10", "analog_20", "analog_40", "momentum", "reversion", "flat")
ETA = 0.3      # how fast weights move after each scored projection
FLOOR = 0.02   # no expert is ever written off completely; markets change
MIN_LIVE = 10  # scored live projections before live weights take over
MIN_LIVE_BANDS = 15


def hedge(weights: dict, losses: dict, eta: float = ETA, floor: float = FLOOR) -> dict:
    """One multiplicative-weights step: w_i ← w_i·e^(−η·loss_i), with a floor, normalized to 1."""
    w = {e: float(weights.get(e, 1.0 / len(EXPERTS))) * float(np.exp(-eta * min(2.0, max(0.0, losses.get(e, 1.0)))))
         for e in EXPERTS}
    total = sum(w.values()) or 1.0
    w = {e: max(floor, v / total) for e, v in w.items()}
    total = sum(w.values())
    return {e: round(v / total, 4) for e, v in w.items()}


def _experts(r: np.ndarray, logc: np.ndarray, S: dict, end: int, horizon: int, k: int) -> tuple[dict, np.ndarray | None]:
    """Each expert's middle path (cumulative log return) for the stretch ending at return `end`, plus the
    20-candle analog quantiles used for the bands."""
    paths, bands = {}, None
    for w in (10, 20, 40):
        shapes, vols = S[w]
        q = _project(r[: end + 1], shapes[: end - w + 2], vols[: end - w + 2], end, w, horizon, k)
        if q is not None:
            paths[f"analog_{w}"] = q[2]
            if w == 20:
                bands = q
    steps = np.arange(1, horizon + 1)
    look = r[max(0, end - 39): end + 1]
    paths["momentum"] = float(look.mean()) * steps if len(look) else np.zeros(horizon)
    last = logc[end + 1]
    sma = float(np.mean(np.exp(logc[max(0, end - 18): end + 2])))
    paths["reversion"] = (np.log(sma) - last) * (1 - np.exp(-steps / max(1.0, horizon / 2)))
    paths["flat"] = np.zeros(horizon)
    return paths, bands


def _blend(paths: dict, weights: dict) -> np.ndarray:
    have = [e for e in EXPERTS if e in paths]
    total = sum(weights.get(e, 0.0) for e in have) or 1.0
    return sum(paths[e] * (weights.get(e, 0.0) / total) for e in have)


def project(closes: list[float], times: list[str], timeframe: str, minutes: int | None,
            cfg: dict | None = None, regular_hours_only: bool = True, model: dict | None = None) -> dict | None:
    """The blended projection for the next candles, its walk-forward record and the learned weights.

    `model` is this timeframe's live learning state from projection_model.json (weights, band widths,
    scored count), or None before any live projection has been scored.
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    k, horizon = int(cfg["neighbours"]), HORIZON.get(timeframe, 10)
    c = np.asarray(closes, dtype=float)
    if len(c) < max(int(cfg["min_history"]), 40 + horizon * 2 + 10) or np.any(c <= 0):
        return None
    logc = np.log(c)
    r = np.diff(logc)
    S = {w: _shapes(r, w) for w in (10, 20, 40)}

    # walk-forward, oldest first: score each expert and the blend, then update the blend's weights
    ends = list(range(len(r) - 1 - horizon, 40 + horizon, -max(1, horizon // 2)))[: int(cfg["tests"])][::-1]
    weights = {e: 1.0 / len(EXPERTS) for e in EXPERTS}
    hits = in50 = in80 = tests = 0
    miss50, miss80, expert_err = [], [], {e: [] for e in EXPERTS}
    for end in ends:
        paths, bq = _experts(r, logc, S, end, horizon, k)
        if bq is None:
            continue
        actual = float(np.sum(r[end + 1: end + 1 + horizon]))
        med = float(_blend(paths, weights)[-1])
        half50 = max((bq[3][-1] - bq[1][-1]) / 2, 1e-9)
        half80 = max((bq[4][-1] - bq[0][-1]) / 2, 1e-9)
        tests += 1
        hits += int(np.sign(med) == np.sign(actual) and actual != 0)
        in50 += int(abs(actual - med) <= half50)
        in80 += int(abs(actual - med) <= half80)
        miss50.append(abs(actual - med) / half50)
        miss80.append(abs(actual - med) / half80)
        losses = {e: abs(float(paths[e][-1]) - actual) / half80 for e in paths}
        for e, v in losses.items():
            expert_err[e].append(v)
        weights = hedge(weights, losses)

    paths, bq = _experts(r, logc, S, len(r) - 1, horizon, k)
    if bq is None:
        return None
    s50 = float(np.clip(np.percentile(miss50, 50), 0.8, 3.0)) if tests >= 20 else 1.0
    s80 = float(np.clip(np.percentile(miss80, 80), 0.8, 3.0)) if tests >= 20 else 1.0
    source = "history"
    live = (model or {}).get("n", 0)
    if model and live >= MIN_LIVE and model.get("weights"):
        weights, source = {e: float(model["weights"].get(e, FLOOR)) for e in EXPERTS}, "live"
    if model and live >= MIN_LIVE_BANDS and model.get("s50") and model.get("s80"):
        s50, s80 = float(model["s50"]), float(model["s80"])
    mid = _blend(paths, weights)
    raw = bq - bq[2]  # analog spread around its own middle
    q = np.array([mid + raw[0] * s80, mid + raw[1] * s50, mid, mid + raw[3] * s50, mid + raw[4] * s80])
    last = float(c[-1])
    pct = lambda n: round(100.0 * n / tests, 1) if tests else None  # noqa: E731
    ranked = sorted(EXPERTS, key=lambda e: -weights.get(e, 0))
    return {
        "t": future_times(times[-1], horizon, minutes, regular_hours_only),
        **{f"q{p}": [round(last * float(np.exp(v)), 4) for v in q[i]] for i, p in enumerate(QUANTILES)},
        "horizon": horizon,
        "neighbours": k,
        "window": 20,
        "last": round(last, 4),
        "experts": {e: round(last * float(np.exp(paths[e][-1])), 4) for e in EXPERTS if e in paths},
        "weights": {e: round(float(weights.get(e, 0)), 4) for e in EXPERTS},
        "weights_source": source,
        "leader": ranked[0],
        "bands": {"s50": round(s50, 2), "s80": round(s80, 2), "source": "live" if live >= MIN_LIVE_BANDS and model and model.get("s80") else "history",
                  "half80": round(float(raw[4][-1] - raw[0][-1]) / 2 * s80, 6)},
        "record": {"tests": tests, "direction_hit_pct": pct(hits), "inside_50_pct": pct(in50),
                   "inside_80_pct": pct(in80),
                   "expert_error": {e: round(float(np.mean(v)), 3) for e, v in expert_err.items() if v}},
        "live": {key: (model or {}).get(key) for key in ("n", "direction_hit_pct", "inside_50_pct", "inside_80_pct", "updated")} if model else None,
        "version": 2,
    }


# ---------------------------------------------------------------- projected trades

OPTION_DELTA = 0.5  # an at-the-money option moves about half as much as the stock; a rough rule, not a quote


def analog_paths(closes: list[float], horizon: int, k: int = 25, lengths: tuple = (10, 20, 40)) -> np.ndarray | None:
    """What followed the look-alike moments (10, 20 and 40 candles), as cumulative log-return paths from now,
    each scaled to today's volatility. Shape: paths × horizon."""
    c = np.asarray(closes, dtype=float)
    if len(c) < 60 + horizon or np.any(c <= 0):
        return None
    r = np.diff(np.log(c))
    end, out = len(r) - 1, []
    for w in lengths:
        shapes, vols = _shapes(r, w)
        cur = end - w + 1
        if cur < 0 or np.isnan(vols[cur]):
            continue
        last_ok = cur - horizon
        if last_ok < 1:
            continue
        d = np.sqrt(np.nansum((shapes[:last_ok] - shapes[cur]) ** 2, axis=1))
        d[np.isnan(vols[:last_ok])] = np.inf
        kk = min(k, int(np.isfinite(d).sum()))
        if kk < 5:
            continue
        for j in np.argpartition(d, kk - 1)[:kk]:
            out.append(np.cumsum(r[j + w: j + w + horizon]) * (vols[cur] / vols[j]))
    return np.array(out) if len(out) >= 10 else None


def plan_trade(paths: np.ndarray, last: float, zone_low: float, zone_high: float, side: str,
               stop_pct: float) -> dict:
    """Odds for one zone trade along the look-alike paths.

    The desk buys a call in a red zone and a put in a green zone, stops out when the stock moves
    `stop_pct` % against the entry, and is flat by the close (the end of the paths).
    Entry: the price now if it's inside the zone, else the zone's near edge when a path first reaches it.
    """
    long = side == "buy"
    lo, hi = np.log(zone_low / last), np.log(zone_high / last)
    stop = np.log(1 - stop_pct / 100) if long else np.log(1 + stop_pct / 100)
    inside = lo <= 0 <= hi
    reached, results, stopped = 0, [], 0
    for p in paths:
        if inside:
            j, entry = -1, 0.0
        else:
            edge = hi if 0 > hi else lo  # the near edge: the top if price is above the zone, the bottom if below
            hit = np.nonzero(p <= edge)[0] if 0 > hi else np.nonzero(p >= edge)[0]
            if not len(hit):
                continue
            j, entry = int(hit[0]), edge
        reached += 1
        after = p[j + 1:] - entry if j + 1 < len(p) else np.array([0.0])
        if not len(after):
            after = np.array([0.0])
        moved = after if long else -after
        stop_at = np.nonzero(moved <= (stop if long else -stop))[0]
        if len(stop_at):
            stopped += 1
            results.append(-stop_pct / 100)
        else:
            results.append(float(np.exp(moved[-1]) - 1))
    n = len(paths)
    if not reached:
        return {"paths": n, "reach_pct": 0.0, "profit_pct": 0.0}
    res = np.array(results)
    entry_price = last * float(np.exp(0.0 if inside else (hi if 0 > hi else lo)))
    per_dollar = 100 * OPTION_DELTA  # option P/L per $1 the stock moves
    dollars = res * entry_price * per_dollar
    wins = res > 0
    return {
        "paths": n,
        "entry": round(entry_price, 2),
        "stop": round(entry_price * (1 - stop_pct / 100) if long else entry_price * (1 + stop_pct / 100), 2),
        "inside_now": bool(inside),
        "reach_pct": round(100 * reached / n, 1),
        "win_if_entered_pct": round(100 * float(wins.mean()), 1),
        "stopped_if_entered_pct": round(100 * stopped / reached, 1),
        "loss_at_close_if_entered_pct": round(100 * float(((~wins) & (res > -stop_pct / 100 + 1e-12)).mean()), 1),
        "profit_pct": round(100 * float(wins.sum()) / n, 1),
        "median_move_pct": round(100 * float(np.median(res)), 3),
        "option_est": {
            "per_dollar": per_dollar,
            "avg_win": round(float(dollars[wins].mean()), 0) if wins.any() else None,
            "avg_loss": round(float(dollars[~wins].mean()), 0) if (~wins).any() else None,
            "at_stop": round(-stop_pct / 100 * entry_price * per_dollar, 0),
            "expected": round(float(dollars.mean()), 0),
        },
    }
