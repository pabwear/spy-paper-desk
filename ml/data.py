"""Market data for the ML engine: providers (where bars come from) and cleaning (what the models may see).

Timestamp convention (Alpaca's): a 1-minute bar stamped t covers [t, t+1 min). Its close is known only at
t + 1 min. Every later step (features, labels, backtest) follows this: a decision on bar i is made at
t_i + 1 min and can trade no earlier than the open of bar i + 1.

Cleaning, per regular-hours session (09:30-16:00 New York, 13:00 on half days):
  * only regular-hours bars are kept (pre/post-market dropped);
  * duplicated timestamps: the last one wins, and the count is reported;
  * impossible bars (high < low, non-positive or missing prices) are dropped and reported;
  * every session is put on a full minute grid. A minute with no bar is MISSING (prices NaN, `missing`=True),
    never filled with a made-up price. Volume on a missing minute is NaN, not zero: the IEX feed omits a minute
    when IEX printed no trade, and we cannot tell that apart from a feed gap.
  * sessions with under `min_coverage` of their minutes are dropped and reported.
Holidays need no calendar: a day without bars has no session. Split adjustment comes from the provider
(Alpaca `adjustment=split`); dividends are not adjusted (minute-level horizons, flat every night).
"""

from __future__ import annotations

import gzip
import hashlib
import io
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from common import ET

COLUMNS = ["t", "o", "h", "l", "c", "v"]


class Provider(Protocol):
    name: str

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Raw 1-minute bars, columns t (tz-aware), o, h, l, c, v, for [start, end)."""


def _frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    if len(df):
        df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET)
    return df


class AlpacaMinuteProvider:
    """Alpaca market data, 1-minute bars, a month per request. Keys from the environment (never logged)."""

    name = "alpaca"

    def __init__(self, feed: str = "iex", adjustment: str = "split") -> None:
        self.feed, self.adjustment = feed, adjustment

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        from alpaca_client import _keys

        key, secret = _keys()
        client = StockHistoricalDataClient(key, secret)
        rows: list[dict] = []
        a = start
        while a < end:
            b = min(end, a + timedelta(days=31))
            req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame(1, TimeFrameUnit.Minute),
                                   start=datetime(a.year, a.month, a.day, tzinfo=ET),
                                   end=datetime(b.year, b.month, b.day, tzinfo=ET),
                                   feed=DataFeed(self.feed), adjustment=Adjustment(self.adjustment))
            for x in client.get_stock_bars(req).data.get(symbol, []):
                rows.append({"t": x.timestamp, "o": float(x.open), "h": float(x.high), "l": float(x.low),
                             "c": float(x.close), "v": float(x.volume)})
            a = b
        return _frame(rows)


class CachedProvider:
    """Wraps a provider with a month-by-month gzip CSV cache (never committed). The current month is refetched."""

    def __init__(self, inner: Provider, folder: str | Path) -> None:
        self.inner, self.folder = inner, Path(folder)
        self.name = f"{inner.name}+cache"

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self.folder.mkdir(parents=True, exist_ok=True)
        parts, m = [], date(start.year, start.month, 1)
        this_month = date.today().replace(day=1)
        while m < end:
            nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
            f = self.folder / f"{symbol}_{m:%Y-%m}.csv.gz"
            if f.exists() and m < this_month:
                df = pd.read_csv(f, compression="gzip")
                df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET)
            else:
                df = self.inner.fetch(symbol, m, nxt)
                if m < this_month and len(df):
                    with gzip.open(f, "wt", encoding="utf-8") as fh:
                        df.assign(t=df["t"].dt.tz_convert(timezone.utc)).to_csv(fh, index=False)
            parts.append(df)
            m = nxt
        out = pd.concat([p for p in parts if len(p)], ignore_index=True) if any(len(p) for p in parts) else _frame([])
        lo = pd.Timestamp(datetime(start.year, start.month, start.day, tzinfo=ET))
        hi = pd.Timestamp(datetime(end.year, end.month, end.day, tzinfo=ET))
        return out[(out["t"] >= lo) & (out["t"] < hi)].reset_index(drop=True)


class SyntheticProvider:
    """Deterministic fake sessions for tests: a random walk, optional drift, gaps and duplicates. Not market data."""

    name = "synthetic"

    def __init__(self, seed: int = 0, drift_bps: float = 0.0, vol_bps: float = 4.0, start_price: float = 400.0,
                 drop_frac: float = 0.0, duplicate_frac: float = 0.0, half_days: tuple[date, ...] = (),
                 trend_bps: float = 0.0, trend_block: int = 45) -> None:
        """trend_bps > 0 plants a REAL, learnable pattern for positive-control tests: the drift is
        +/- trend_bps per minute, its sign drawn at random every `trend_block` minutes and held."""
        self.seed, self.drift, self.vol, self.p0 = seed, drift_bps / 1e4, vol_bps / 1e4, start_price
        self.trend, self.block = trend_bps / 1e4, trend_block
        self.drop, self.dup, self.half_days = drop_frac, duplicate_frac, set(half_days)

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        rng = np.random.default_rng(self.seed)
        rows, p, d = [], self.p0, start
        while d < end:
            if d.weekday() < 5:
                n = 210 if d in self.half_days else 390
                p *= float(np.exp(rng.normal(0, self.vol * 5)))  # overnight gap
                t0 = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
                sign = 1.0
                for k in range(n):
                    if k % self.block == 0:
                        sign = 1.0 if rng.random() < 0.5 else -1.0
                    o = p
                    p *= float(np.exp(self.drift + sign * self.trend + rng.normal(0, self.vol)))
                    hi, lo = max(o, p) * (1 + abs(rng.normal(0, self.vol / 3))), min(o, p) * (1 - abs(rng.normal(0, self.vol / 3)))
                    if rng.random() < self.drop:
                        continue
                    row = {"t": t0 + timedelta(minutes=k), "o": o, "h": hi, "l": lo, "c": p, "v": float(rng.integers(100, 5000))}
                    rows.append(row)
                    if rng.random() < self.dup:
                        rows.append(dict(row))
                # a little pre-market that cleaning must drop
                rows.append({"t": t0 - timedelta(minutes=5), "o": p, "h": p, "l": p, "c": p, "v": 10.0})
            d += timedelta(days=1)
        return _frame(rows)


def _minute_of(ts: pd.Series) -> pd.Series:
    return ts.dt.hour * 60 + ts.dt.minute


def clean(raw: pd.DataFrame, session: dict, min_coverage: float = 0.5) -> tuple[pd.DataFrame, dict]:
    """Raw bars -> one row per regular-hours minute of every kept session, plus an audit of what was changed."""
    open_m = _hhmm(session["open"])
    full_close, half_close = _hhmm(session["close"]), _hhmm(session["half_day_close"])
    audit = {"raw_rows": int(len(raw)), "duplicates": 0, "invalid": 0, "outside_hours": 0, "missing_minutes": 0,
             "dropped_sessions": [], "half_days": []}
    if not len(raw):
        return _empty(), {**audit, "sessions": 0, "rows": 0}
    df = raw.copy()
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET).dt.floor("min")
    m = _minute_of(df["t"])
    inside = (m >= open_m) & (m < full_close)
    audit["outside_hours"] = int((~inside).sum())
    df = df[inside]
    bad = df[["o", "h", "l", "c"]].isna().any(axis=1) | (df[["o", "h", "l", "c"]] <= 0).any(axis=1) | (df["h"] < df["l"]) \
        | (df["h"] < df[["o", "c"]].max(axis=1)) | (df["l"] > df[["o", "c"]].min(axis=1)) | (df["v"] < 0)
    audit["invalid"] = int(bad.sum())
    df = df[~bad]
    before = len(df)
    df = df.sort_values("t").drop_duplicates("t", keep="last")
    audit["duplicates"] = int(before - len(df))
    df["session"] = df["t"].dt.date
    out = []
    for day, g in df.groupby("session", sort=True):
        last_m = int(_minute_of(g["t"]).max())
        close_m = half_close if last_m < half_close + 5 else full_close
        if close_m == half_close:
            audit["half_days"].append(str(day))
        g = g[_minute_of(g["t"]) < close_m]
        grid = pd.date_range(datetime(day.year, day.month, day.day, open_m // 60, open_m % 60, tzinfo=ET),
                             periods=close_m - open_m, freq="min")
        s = g.set_index("t")[["o", "h", "l", "c", "v"]].reindex(grid)
        coverage = 1 - s["c"].isna().mean()
        if coverage < min_coverage:
            audit["dropped_sessions"].append({"session": str(day), "coverage": round(float(coverage), 3)})
            continue
        s.index.name = "t"
        s = s.reset_index()
        s["missing"] = s["c"].isna()
        s["session"] = day
        s["minute"] = np.arange(len(s))
        s["session_len"] = len(s)
        out.append(s)
    res = pd.concat(out, ignore_index=True) if out else _empty()
    audit["missing_minutes"] = int(res["missing"].sum()) if len(res) else 0
    audit["sessions"] = int(res["session"].nunique()) if len(res) else 0
    audit["rows"] = int(len(res))
    if len(res):
        audit["first"], audit["last"] = str(res["t"].iloc[0]), str(res["t"].iloc[-1])
        audit["data_sha"] = hashlib.sha256(pd.util.hash_pandas_object(res[["t", "o", "h", "l", "c", "v"]], index=False)
                                           .values.tobytes()).hexdigest()[:12]
    return res, audit


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["t", "o", "h", "l", "c", "v", "missing", "session", "minute", "session_len"])


def _hhmm(s: str) -> int:
    h, mm = s.split(":")
    return int(h) * 60 + int(mm)


def load(provider: Provider, symbol: str, start: date, end: date, session: dict) -> tuple[pd.DataFrame, dict]:
    raw = provider.fetch(symbol, start, end)
    bars, audit = clean(raw, session)
    audit.update({"provider": provider.name, "symbol": symbol, "start": str(start), "end": str(end)})
    return bars, audit


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode()
