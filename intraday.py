"""Intraday SPY share strategies from published research, tested on the desk's own minute bars.
Research only; shares, so there is no option-pricing model to get wrong. Flat every night.

Rules (set Oct 9 2026, before any run; not tuned afterwards):
    noise_both   "noise area" momentum (Zarattini, Aziz & Barbon 2024, "Beat the Market: An Effective
                 Intraday Momentum Strategy for S&P500 ETF (SPY)"): each minute's band is the day's open
                 (the higher of open / yesterday's close above, the lower below) +/- the average absolute
                 move from the open at that minute over the last 14 days. At the :00 and :30 checks from
                 10:00, buy above the band, sell short below it; get out when the price crosses back past the
                 band or the day's VWAP, whichever is nearer; flat at the close.
    noise_long   the same, buys only (the desk's shares books only buy)
    last_half    "market intraday momentum" (Gao, Han, Li & Zhou 2018, Journal of Financial Economics): if SPY
                 is above yesterday's close at 10:00, buy at 15:30 and sell at the close; if below, sell short
    last_half_long  the same, buys only
    buy_hold     the yardstick: the same money in SPY, held close to close every day

Money and costs: $4,000 a trade (the SPY shares book), whole shares, $0.02 a share for each round trip
(spread and fees; Alpaca charges no commission). The 1x size is the desk's; the noise-area paper used up to
4x with volatility targeting, which this does not copy. A rule counts only if it makes money in ALL of
train, check and exam, and beats nothing-at-all after costs.

    python3 intraday.py --source alpaca --since 2020-07-01 --json intraday.json --md intraday.md
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime

import backtest_areas as bt
from common import ET

MONEY = 4000.0
COST_PER_SHARE = 0.02
LOOKBACK = 14
SPLITS = {"train": (None, date(2024, 3, 13)), "check": (date(2024, 3, 13), date(2025, 10, 7)),
          "exam": (date(2025, 10, 7), None)}
STRATEGIES = ["noise_both", "noise_long", "last_half", "last_half_long", "buy_hold"]


def _minute(b: dict) -> int:
    """Minutes from 09:30 to the END of this one-minute bar (the 09:59 bar ends at minute 30 = 10:00)."""
    t = b["t"].astimezone(ET)
    return t.hour * 60 + t.minute + 1 - (9 * 60 + 30)


def _trade(d: date, side: str, entry: float, exit_: float, why: str) -> dict:
    shares = math.floor(MONEY / entry)
    gross = (exit_ - entry) * shares * (1 if side == "long" else -1)
    net = gross - COST_PER_SHARE * shares
    return {"t": d.isoformat(), "side": side, "entry": round(entry, 4), "exit": round(exit_, 4), "shares": shares,
            "why": why, "net": round(net, 2)}


def noise_sigma(history: list[list[dict]]) -> dict[int, float]:
    """minute -> average |close at that minute / that day's open - 1| over the given days."""
    sums: dict[int, list[float]] = {}
    for bars in history:
        o = bars[0]["o"]
        for b in bars:
            sums.setdefault(_minute(b), []).append(abs(b["c"] / o - 1))
    return {m: sum(v) / len(v) for m, v in sums.items() if len(v) >= max(1, len(history) // 2)}


def noise_day(d: date, bars: list[dict], prev_close: float, sigma: dict[int, float], allow_short: bool) -> list[dict]:
    o = bars[0]["o"]
    top, bottom = max(o, prev_close), min(o, prev_close)
    pv = vol = 0.0
    side, entry, out = None, 0.0, []
    for b in bars:
        pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
        vol += b["v"]
        vwap = pv / vol if vol else b["c"]
        m = _minute(b)
        if m >= 390:  # the close
            break
        if m < 30 or m % 30 or m not in sigma:
            continue
        ub, lb, px = top * (1 + sigma[m]), bottom * (1 - sigma[m]), b["c"]
        if side == "long" and px < max(ub, vwap):
            out.append(_trade(d, side, entry, px, "band")); side = None
        elif side == "short" and px > min(lb, vwap):
            out.append(_trade(d, side, entry, px, "band")); side = None
        if side is None:
            if px > ub:
                side, entry = "long", px
            elif allow_short and px < lb:
                side, entry = "short", px
    if side is not None:
        out.append(_trade(d, side, entry, bars[-1]["c"], "close"))
    return out


def last_half_day(d: date, bars: list[dict], prev_close: float, allow_short: bool) -> list[dict]:
    at = {_minute(b): b for b in bars}
    if 30 not in at or 360 not in at:
        return []
    up = at[30]["c"] > prev_close
    if not up and not allow_short:
        return []
    return [_trade(d, "long" if up else "short", at[360]["c"], bars[-1]["c"], "close")]


def run_all(session: list[tuple[date, list[dict]]]) -> dict[str, list[dict]]:
    """session: [(day, regular-hours minutes)] in order. Every strategy's trades."""
    trades: dict[str, list[dict]] = {k: [] for k in STRATEGIES}
    for i, (d, bars) in enumerate(session):
        if i < LOOKBACK or not bars:
            continue
        prev = session[i - 1][1]
        if not prev:
            continue
        prev_close = prev[-1]["c"]
        sigma = noise_sigma([b for _, b in session[i - LOOKBACK:i] if b])
        trades["noise_both"] += noise_day(d, bars, prev_close, sigma, True)
        trades["noise_long"] += noise_day(d, bars, prev_close, sigma, False)
        trades["last_half"] += last_half_day(d, bars, prev_close, True)
        trades["last_half_long"] += last_half_day(d, bars, prev_close, False)
        trades["buy_hold"].append(_trade(d, "long", prev_close, bars[-1]["c"], "hold") | {"net": round(
            (bars[-1]["c"] - prev_close) * math.floor(MONEY / prev_close), 2)})
    return trades


def summary(trades: list[dict], days: int) -> dict:
    if not trades:
        return {"trades": 0, "total": 0.0}
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t["t"]] = by_day.get(t["t"], 0.0) + t["net"]
    run = peak = dd = 0.0
    for k in sorted(by_day):
        run += by_day[k]
        peak = max(peak, run)
        dd = min(dd, run - peak)
    total = sum(by_day.values())
    return {"trades": len(trades), "days_traded": len(by_day), "days": days,
            "days_won_pct": round(100 * sum(v > 0 for v in by_day.values()) / len(by_day), 1),
            "share_of_all_days_won_pct": round(100 * sum(v > 0 for v in by_day.values()) / max(1, days), 1),
            "total": round(total, 2), "per_day_traded": round(total / len(by_day), 2),
            "worst_day": round(min(by_day.values()), 2), "best_day": round(max(by_day.values()), 2),
            "max_drawdown": round(dd, 2),
            "by_year": {y: round(sum(v for k, v in by_day.items() if k[:4] == y), 2) for y in sorted({k[:4] for k in by_day})}}


def run(session: list[tuple[date, list[dict]]]) -> dict:
    trades = run_all(session)
    out = {"first": session[0][0].isoformat(), "last": session[-1][0].isoformat(), "days": len(session),
           "splits": {k: [a.isoformat() if a else None, b.isoformat() if b else None] for k, (a, b) in SPLITS.items()},
           "strategies": {}}
    for name in STRATEGIES:
        row = {"all": summary(trades[name], len(session))}
        for part, (a, b) in SPLITS.items():
            inside = lambda s: (a is None or s >= a.isoformat()) and (b is None or s < b.isoformat())  # noqa: E731
            row[part] = summary([t for t in trades[name] if inside(t["t"])],
                                sum(1 for d, _ in session if inside(d.isoformat())))
        row["passes"] = name != "buy_hold" and all(row[p].get("total", 0) > 0 for p in SPLITS)
        out["strategies"][name] = row
    return out


def markdown(res: dict) -> str:
    lines = ["# Intraday SPY share strategies", "",
             f"{res['days']:,} trading days, {res['first']} to {res['last']}. $4,000 a trade, $0.02 a share costs. "
             "Rules set before the run. A rule passes only if it makes money in train, check AND exam.", "",
             "| Strategy | Period | Days traded | Days won | Net | Per day traded | Worst day | Max drawdown |",
             "|---|---|---|---|---|---|---|---|"]
    for name, row in res["strategies"].items():
        first = True
        for part in ("train", "check", "exam", "all"):
            s = row[part]
            if not s.get("trades"):
                continue
            label = f"**{name}**{' ✓' if row['passes'] else ''}" if first else ""
            first = False
            lines.append(f"| {label} | {part} | {s['days_traded']} | {s['days_won_pct']}% | ${s['total']:,.0f} | "
                         f"${s['per_day_traded']:,.2f} | ${s['worst_day']:,.0f} | ${s['max_drawdown']:,.0f} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import backtest_ideas as ideas

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=("alpaca", "yfinance"), default="yfinance")
    ap.add_argument("--since", default="2020-07-01")
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args(argv)
    bars = (bt.alpaca_minutes(date.fromisoformat(a.since), datetime.now(ET).date(), "SPY") if a.source == "alpaca"
            else bt.yfinance_minutes("SPY"))
    days = bt.group_days(bars, True)
    del bars
    session = [(d, ideas.rth(days[d])) for d in sorted(days) if len(days[d]) >= 300]
    res = run(session)
    md = markdown(res)
    print(md)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, default=str)
    if a.md:
        with open(a.md, "w") as f:
            f.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
