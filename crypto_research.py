"""Crypto rules on Alpaca's free daily bars (BTC/USD, ETH/USD), tested before any crypto trading.
Research only. Long or cash (Alpaca does not short crypto), no leverage.

Rules (set Oct 10 2026, before any run; not tuned afterwards). Each decides on a day's close and trades at the
next day's open:
    hold        buy and hold (the yardstick)
    trend50     hold while the close is above its 50-day average, else cash
    trend200    hold while the close is above its 200-day average, else cash
    mom28       hold while the 28-day return is positive (time-series momentum), else cash
    donchian    buy on a close above the prior 20-day high, sell on a close below the prior 10-day low

Costs: 0.30% each buy or sell (Alpaca's 0.25% crypto taker fee + 0.05% slippage). Money: $1,000, compounding.
Periods: train 2021-01-01..2023-12-31, check 2024-01-01..2025-06-30, exam 2025-07-01 on. A rule passes only if
it makes money in ALL three periods; it is compared with holding, which it must also beat on the worst drop.

    python3 crypto_research.py --json crypto.json --md crypto.md
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone

COST = 0.003
SPLITS = {"train": (date(2021, 1, 1), date(2024, 1, 1)), "check": (date(2024, 1, 1), date(2025, 7, 1)),
          "exam": (date(2025, 7, 1), None)}
RULES = ["hold", "trend50", "trend200", "mom28", "donchian"]
SYMBOLS = ["BTC/USD", "ETH/USD"]


def signals(closes: list[float], rule: str) -> list[bool]:
    """want[i]: hold during day i+1, decided on day i's close (never looks ahead)."""
    want, inside = [], False
    for i, c in enumerate(closes):
        if rule == "hold":
            inside = True
        elif rule in ("trend50", "trend200"):
            n = 50 if rule == "trend50" else 200
            inside = i + 1 >= n and c > sum(closes[i + 1 - n:i + 1]) / n
        elif rule == "mom28":
            inside = i >= 28 and c > closes[i - 28]
        elif rule == "donchian":
            if i >= 20 and c > max(closes[i - 20:i]):
                inside = True
            elif i >= 10 and c < min(closes[i - 10:i]):
                inside = False
        want.append(inside)
    return want


def simulate(days: list[date], opens: list[float], closes: list[float], rule: str, start: date | None = None,
             end: date | None = None, money: float = 1000.0) -> dict:
    """Compounding equity over [start, end): positions change at the open after a signal, costs on every change."""
    want = signals(closes, rule)
    eq, peak, worst, trades, held, curve = money, money, 0.0, 0, False, []
    for i in range(1, len(days)):
        d = days[i]
        if (start and d < start) or (end and d >= end):
            continue
        should = want[i - 1]
        if should != held:  # trade at today's open
            eq *= (1 - COST)
            trades += 1
            held = should
        if held:
            eq *= closes[i] / opens[i]  # open to close
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1)
        curve.append((d.isoformat(), round(eq, 2)))  # crypto trades 24/7: the next open is this close
    if held:
        eq *= (1 - COST)  # close out at the end of the period
    return {"end": round(eq, 2), "return_pct": round((eq / money - 1) * 100, 1), "max_drop_pct": round(worst * 100, 1),
            "trades": trades, "days": len(curve)}


def run(data: dict[str, dict]) -> dict:
    out = {"splits": {k: [a.isoformat(), b.isoformat() if b else None] for k, (a, b) in SPLITS.items()}, "results": {}}
    for sym, d in data.items():
        rows = {}
        for rule in RULES:
            row = {p: simulate(d["days"], d["opens"], d["closes"], rule, a, b) for p, (a, b) in SPLITS.items()}
            row["all"] = simulate(d["days"], d["opens"], d["closes"], rule)
            rows[rule] = row
        hold = rows["hold"]
        for rule, row in rows.items():
            row["passes"] = rule != "hold" and all(row[p]["return_pct"] > 0 for p in SPLITS) \
                and row["all"]["max_drop_pct"] > hold["all"]["max_drop_pct"]
        out["results"][sym] = rows
        out.setdefault("range", {})[sym] = [d["days"][0].isoformat(), d["days"][-1].isoformat()]
    return out


def markdown(res: dict) -> str:
    lines = ["# Crypto rules (daily, long or cash)", "",
             "$1,000 compounding, 0.30% a trade. Rules set before the run. Passes = makes money in train, check AND "
             "exam, with a smaller worst drop than holding.", ""]
    for sym, rows in res["results"].items():
        a, b = res["range"][sym]
        lines += [f"## {sym} ({a} to {b})", "", "| Rule | Train | Check | Exam | All | Worst drop | Trades |",
                  "|---|---|---|---|---|---|---|"]
        for rule, r in rows.items():
            mark = " ✓" if r["passes"] else ""
            lines.append(f"| **{rule}**{mark} | {r['train']['return_pct']:+.1f}% | {r['check']['return_pct']:+.1f}% | "
                         f"{r['exam']['return_pct']:+.1f}% | {r['all']['return_pct']:+.1f}% (${r['all']['end']:,.0f}) | "
                         f"{r['all']['max_drop_pct']:.1f}% | {r['all']['trades']} |")
        lines.append("")
    return "\n".join(lines)


def fetch() -> dict[str, dict]:
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = CryptoHistoricalDataClient()  # crypto bars need no key
    out = {}
    for sym in SYMBOLS:
        bars = client.get_crypto_bars(CryptoBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Day,
                                                        start=datetime(2021, 1, 1, tzinfo=timezone.utc))).data[sym]
        bars = [b for b in bars if b.timestamp.date() < datetime.now(timezone.utc).date()]  # complete days only
        out[sym] = {"days": [b.timestamp.date() for b in bars], "opens": [float(b.open) for b in bars],
                    "closes": [float(b.close) for b in bars]}
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
