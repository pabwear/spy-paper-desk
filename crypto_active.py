"""More active crypto rules on Alpaca's free hourly bars (BTC/USD, ETH/USD), tested before any change to the
crypto desk. Research only. Long or cash, no leverage; same costs and periods as crypto_research.py.

Rules (set Oct 10 2026, before any run; not tuned afterwards). Each decides on a bar's close and trades at the
next bar's open:
    trend4h      4-hour bars: hold while the close is above its 50-bar exponential average (about 8 days)
    cross1h      1-hour bars: hold while the 20-hour EMA is above the 50-hour EMA
    donchian4h   4-hour bars: buy on a close above the prior 20-bar high, sell on a close below the prior 10-bar low
    dip_uptrend  1-hour bars, only while the last daily close is above its 200-day average: buy when the hourly
                 RSI(14) falls under 30, sell when it rises over 55 or after 48 hours
    mom7d        daily: hold while the close is above the close 7 days earlier
    (hold and the daily trend200 rule are the yardsticks)

Costs: 0.30% each buy or sell. $1,000 compounding. Passes = makes money in train, check AND exam on BOTH
coins, and trades at least 30 times a year (the point of this test is a rule that trades regularly).

    python3 crypto_active.py --md crypto_active.md --json crypto_active.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import crypto_research as base

RULES = ["hold", "trend200", "trend4h", "cross1h", "donchian4h", "dip_uptrend", "mom7d"]


def ema(values: list[float], n: int) -> list[float]:
    k, out = 2 / (n + 1), []
    for i, v in enumerate(values):
        out.append(v if i == 0 else v * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    gain = loss = 0.0
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g, l_ = max(ch, 0.0), max(-ch, 0.0)
        if i <= n:
            gain += g / n
            loss += l_ / n
            if i < n:
                continue
        else:
            gain = (gain * (n - 1) + g) / n
            loss = (loss * (n - 1) + l_) / n
        out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def resample(bars: list[dict], hours: int) -> list[dict]:
    """Hourly bars -> `hours`-hour bars on the UTC clock (00:00, 04:00, ...)."""
    out: list[dict] = []
    for b in bars:
        t = b["t"]
        k = t.replace(hour=t.hour - t.hour % hours, minute=0, second=0, microsecond=0)
        if out and out[-1]["t"] == k:
            out[-1]["c"] = b["c"]
        else:
            out.append({"t": k, "o": b["o"], "c": b["c"]})
    return out


def daily_trend_ok(bars: list[dict]) -> list[bool]:
    """For each hourly bar: was the LAST COMPLETE day's close above its 200-day average?"""
    closes, days, out, ok = [], [], [], False
    for b in bars:
        d = b["t"].date()
        if not days or d != days[-1]:
            if closes:  # yesterday is complete: decide for today
                ok = len(closes) >= 200 and closes[-1] > sum(closes[-200:]) / 200
            days.append(d)
            closes.append(b["c"])
        else:
            closes[-1] = b["c"]
        out.append(ok)
    return out


def wants(bars: list[dict], rule: str) -> list[bool]:
    """want[i]: hold during bar i+1, from bar i's close and earlier only."""
    c = [b["c"] for b in bars]
    if rule in ("trend4h",):
        e = ema(c, 50)
        return [i >= 50 and c[i] > e[i] for i in range(len(c))]
    if rule == "cross1h":
        f, s = ema(c, 20), ema(c, 50)
        return [i >= 50 and f[i] > s[i] for i in range(len(c))]
    if rule == "donchian4h":
        return base.signals(c, "donchian")
    if rule == "dip_uptrend":
        r, up, out, inside, since = rsi(c), daily_trend_ok(bars), [], False, 0
        for i in range(len(c)):
            if not inside and up[i] and r[i] is not None and r[i] < 30:
                inside, since = True, 0
            elif inside:
                since += 1
                if (r[i] is not None and r[i] > 55) or since >= 48:
                    inside = False
            out.append(inside)
        return out
    if rule == "mom7d":
        return [i >= 7 and c[i] > c[i - 7] for i in range(len(c))]
    return base.signals(c, rule)  # hold and trend200 (daily)


def simulate(bars: list[dict], want: list[bool], start=None, end=None, money: float = 1000.0) -> dict:
    eq, peak, worst, trades, held, years = money, money, 0.0, 0, False, set()
    first = last = None
    for i in range(1, len(bars)):
        d = bars[i]["t"].date()
        if (start and d < start) or (end and d >= end):
            continue
        first, last = first or d, d
        if want[i - 1] != held:
            eq *= (1 - base.COST)
            trades += 1
            held = want[i - 1]
        if held:
            eq *= bars[i]["c"] / bars[i]["o"]
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1)
    if held:
        eq *= (1 - base.COST)
    span = max(1, (last - first).days) / 365.25 if first and last else 1
    return {"return_pct": round((eq / money - 1) * 100, 1), "end": round(eq, 2), "max_drop_pct": round(worst * 100, 1),
            "trades": trades, "trades_per_year": round(trades / span, 1)}


def run(hourly: dict[str, list[dict]]) -> dict:
    out = {"results": {}, "range": {}}
    for sym, h in hourly.items():
        frames = {"1h": h, "4h": resample(h, 4), "1d": resample(h, 24)}
        rows = {}
        for rule in RULES:
            fr = frames["4h"] if rule.endswith("4h") else frames["1h"] if rule in ("cross1h", "dip_uptrend") else frames["1d"]
            w = wants(fr, rule)
            row = {p: simulate(fr, w, a, b) for p, (a, b) in base.SPLITS.items()}
            row["all"] = simulate(fr, w)
            row["passes"] = rule not in ("hold", "trend200") and all(row[p]["return_pct"] > 0 for p in base.SPLITS) \
                and row["all"]["trades_per_year"] >= 30
            rows[rule] = row
        out["results"][sym] = rows
        out["range"][sym] = [h[0]["t"].date().isoformat(), h[-1]["t"].date().isoformat()]
    return out


def markdown(res: dict) -> str:
    lines = ["# Active crypto rules (hourly / 4-hour / daily, long or cash)", "",
             "$1,000 compounding, 0.30% a trade. Rules set before the run. Passes = makes money in train, check AND "
             "exam, and trades 30+ times a year.", ""]
    for sym, rows in res["results"].items():
        a, b = res["range"][sym]
        lines += [f"## {sym} ({a} to {b})", "", "| Rule | Train | Check | Exam | All | Worst drop | Trades a year |",
                  "|---|---|---|---|---|---|---|"]
        for rule, r in rows.items():
            lines.append(f"| **{rule}**{' ✓' if r['passes'] else ''} | {r['train']['return_pct']:+.1f}% | "
                         f"{r['check']['return_pct']:+.1f}% | {r['exam']['return_pct']:+.1f}% | "
                         f"{r['all']['return_pct']:+.1f}% | {r['all']['max_drop_pct']:.1f}% | {r['all']['trades_per_year']} |")
        lines.append("")
    return "\n".join(lines)


def fetch() -> dict[str, list[dict]]:
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client, out = CryptoHistoricalDataClient(), {}
    for sym in base.SYMBOLS:
        bars = client.get_crypto_bars(CryptoBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Hour,
                                                        start=datetime(2020, 6, 1, tzinfo=timezone.utc))).data[sym]
        out[sym] = [{"t": b.timestamp.astimezone(timezone.utc), "o": float(b.open), "c": float(b.close)} for b in bars]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args(argv)
    res = run(fetch())
    md = markdown(res)
    print(md)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f)
    if a.md:
        with open(a.md, "w") as f:
            f.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
