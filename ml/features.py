"""Features: numbers describing the market up to and including bar i, and nothing after it.

Every feature at row i uses rows <= i only (tested: computing on data cut at row i gives the same value).
Prices on a missing minute are the last real close (what a trader would have known); `stale` counts the minutes
since the last real bar and `missing_30` the share of missing minutes in the last 30. In volume SUMS a missing
minute counts as zero (IEX printed nothing, or the feed dropped it; we cannot tell); the missing share is there
so a model can tell the difference. A value that cannot be computed yet is NaN, never 0.

Units: returns and distances are log returns in basis points (1 bp = 0.01%), volatility in bp per minute.
Bump FEATURE_VERSION whenever a definition changes: models record it and refuse a different one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_VERSION = 1

DEFINITIONS = {
    "ret_1": "log return over the last 1 minute (bp)",
    "ret_5": "log return over the last 5 minutes (bp)",
    "ret_15": "log return over the last 15 minutes (bp)",
    "ret_30": "log return over the last 30 minutes (bp)",
    "ret_open": "log return since today's first open (bp)",
    "gap": "today's first open vs the previous session's last close (bp)",
    "rv_15": "std of 1-minute log returns over the last 15 minutes (bp)",
    "rv_60": "std of 1-minute log returns over the last 60 minutes (bp)",
    "rv_ratio": "rv_15 / rv_60 (volatility rising > 1)",
    "atr_14": "average true range of the last 14 minutes / price (bp)",
    "range_x": "this minute's high-low range / atr_14 (range expansion)",
    "body": "(close - open) / (high - low) of this minute (-1..1)",
    "upper_wick": "(high - max(open, close)) / (high - low) of this minute",
    "lower_wick": "(min(open, close) - low) / (high - low) of this minute",
    "vwap_dist": "log(price / today's VWAP so far) (bp)",
    "dist_high": "log(price / today's high so far) (bp, <= 0)",
    "dist_low": "log(price / today's low so far) (bp, >= 0)",
    "rel_vol_15": "volume of the last 15 minutes / the average for the same 15 minutes over the previous 10 sessions",
    "vol_accel": "volume of the last 5 minutes / a third of the last 15 minutes' volume",
    "tod": "minutes since the open / 390",
    "tod_sin": "sin(2 pi tod)",
    "tod_cos": "cos(2 pi tod)",
    "dow": "day of week, Monday 0 .. Friday 4",
    "stale": "minutes since the last real bar (0 = this minute traded)",
    "missing_30": "share of missing minutes in the last 30",
}
NAMES = list(DEFINITIONS)


def _g(df: pd.DataFrame, col: str):
    return df.groupby("session", sort=False)[col]


def _roll(df: pd.DataFrame, col: str, n: int, how: str, min_frac: float = 0.6) -> pd.Series:
    r = _g(df, col).rolling(n, min_periods=max(2, int(n * min_frac)))
    out = getattr(r, how)()
    return out.reset_index(level=0, drop=True).sort_index()


def build(bars: pd.DataFrame) -> pd.DataFrame:
    """Cleaned bars (ml.data.clean) -> one row of features per minute, same index. Pure."""
    df = bars[["t", "o", "h", "l", "c", "v", "missing", "session", "minute"]].copy()
    df["cf"] = _g(df, "c").ffill()
    real = ~df["missing"]
    lp = np.log(df["cf"])
    df["lp"] = lp
    out = pd.DataFrame(index=df.index)
    for k in (1, 5, 15, 30):
        out[f"ret_{k}"] = (lp - _g(df, "lp").shift(k)) * 1e4
    first_open = df["o"].where(real).groupby(df["session"]).transform("first")
    # first_open is known from the first real bar on: before it the value would come from the future
    seen_first = real.astype(int).groupby(df["session"]).cumsum() > 0
    out["ret_open"] = ((lp - np.log(first_open)) * 1e4).where(seen_first)
    last_close = df.groupby("session", sort=True)["cf"].last()
    prev_close = last_close.shift(1)
    out["gap"] = ((np.log(first_open) - np.log(df["session"].map(prev_close))) * 1e4).where(seen_first)
    df["r1"] = (lp - _g(df, "lp").shift(1)).where(real)
    out["rv_15"] = _roll(df, "r1", 15, "std") * 1e4
    out["rv_60"] = _roll(df, "r1", 60, "std") * 1e4
    out["rv_ratio"] = out["rv_15"] / out["rv_60"]
    prev_c = _g(df, "cf").shift(1)
    tr = pd.concat([df["h"] - df["l"], (df["h"] - prev_c).abs(), (df["l"] - prev_c).abs()], axis=1).max(axis=1, skipna=False)
    df["tr"] = tr.where(real)
    atr = _roll(df, "tr", 14, "mean")
    out["atr_14"] = atr / df["cf"] * 1e4
    rng = (df["h"] - df["l"]).where(real)
    out["range_x"] = rng / atr
    span = rng.where(rng > 0)
    out["body"] = (df["c"] - df["o"]) / span
    out["upper_wick"] = (df["h"] - df[["o", "c"]].max(axis=1)) / span
    out["lower_wick"] = (df[["o", "c"]].min(axis=1) - df["l"]) / span
    vol0 = df["v"].fillna(0.0)
    tp = ((df["h"] + df["l"] + df["c"]) / 3).fillna(0.0)
    df["pv"], df["v0"] = tp * vol0, vol0
    cum_pv, cum_v = _g(df, "pv").cumsum(), _g(df, "v0").cumsum()
    vwap = (cum_pv / cum_v.where(cum_v > 0))
    out["vwap_dist"] = (lp - np.log(vwap)) * 1e4
    hi = _g(df, "h").cummax()
    lo = _g(df, "l").cummin()
    out["dist_high"] = (lp - np.log(hi)) * 1e4
    out["dist_low"] = (lp - np.log(lo)) * 1e4
    v15 = _g(df, "v0").rolling(15, min_periods=15).sum().reset_index(level=0, drop=True).sort_index()
    v5 = _g(df, "v0").rolling(5, min_periods=5).sum().reset_index(level=0, drop=True).sort_index()
    df["v15"] = v15
    # same minute of the session, previous 10 sessions only (shifted by one session: no peeking at today)
    table = df.pivot_table(index="session", columns="minute", values="v15", aggfunc="first", dropna=False)
    hist = table.shift(1).rolling(10, min_periods=5).mean()
    base = hist.stack(future_stack=True).rename("base")
    base.index.names = ["session", "minute"]
    joined = df[["session", "minute"]].merge(base.reset_index(), on=["session", "minute"], how="left")
    b = pd.Series(joined["base"].to_numpy(), index=df.index)
    out["rel_vol_15"] = v15 / b.where(b > 0)
    out["vol_accel"] = v5 / (v15 / 3).where(v15 > 0)
    out["tod"] = df["minute"] / 390.0
    out["tod_sin"] = np.sin(2 * np.pi * out["tod"])
    out["tod_cos"] = np.cos(2 * np.pi * out["tod"])
    out["dow"] = df["t"].dt.weekday.astype(float)
    last_real = df["minute"].where(real).groupby(df["session"]).ffill()
    out["stale"] = (df["minute"] - last_real).astype(float)
    df["m1"] = df["missing"].astype(float)
    out["missing_30"] = _g(df, "m1").rolling(30, min_periods=1).mean().reset_index(level=0, drop=True).sort_index()
    out = out.replace([np.inf, -np.inf], np.nan)
    return out[NAMES]


def usable(feats: pd.DataFrame, max_stale: int = 2) -> pd.Series:
    """Rows a model may use: every feature known and the price not stale."""
    return feats[NAMES].notna().all(axis=1) & (feats["stale"] <= max_stale)
