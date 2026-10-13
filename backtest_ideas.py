"""Backtest four ideas for beating the desk's costs, against the desk as it runs and buying and holding SPY.

Research only: nothing here places orders or changes the desk. Same data, pricing, costs and day split as
backtest_areas.py (1-minute SPY bars from Alpaca IEX, options priced with Black-Scholes at that day's VIX,
the first 70 % of days "train", the last 30 % "test"). These rules were set before any run, not tuned.

    desk_now   the desk as it runs: a call when price touches the red area, ~2-week option, stop 0.25 %,
               sell after 30 minutes, checks every 10 minutes ($5 a trade)
    bounce     1. wait for the bounce: a call only after a 5-minute candle dips into the red area and closes
               back above it; otherwise the desk's rules
    shares     2. the desk's entries, but $1,000 of SPY shares with the stop held at Alpaca (it fills the
               minute SPY reaches it); same 0.25 % stop and 30-minute limit
    swing      3. let winners run: the desk's entries in shares, a stop 0.5 % under the highest close since
               the buy that rises as SPY rises, sold on the stop or after 10 trading days; held overnight
    overnight  4. the overnight hold: $1,000 of SPY bought at the 15:59 close, sold at the next 09:30 open
    buy_hold   the yardstick: $1,000 in SPY every day, close to close (reset to $1,000 daily, no costs)

Share costs: about a penny each way per share (Alpaca charges no commission). Every idea risks $1,000 at
most per trade and never adds up winnings (no compounding), so dollars compare fairly.

    python3 backtest_ideas.py --source alpaca --since 2020-01-01 --json ideas.json --md ideas.md
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta

import backtest_areas as bt
from common import ET, hhmm

BOOK = bt.SHARES_BOOK
SHARE_COST = bt.SHARE_COST
TRAIL_PCT = 0.5
SWING_DAYS = 10
STOP_RANGE_FRACTION = 0.25  # other stocks: the stop is this share of the average daily range (SPY: ~0.25%)

DESK_NOW = bt.CURRENT
BOUNCE = {**bt.CURRENT, "rule": "confirm", "kinds": ["bounce"], "calls_only": True}
SHARES = {**bt.CURRENT, "instrument": "shares", "exit_poll": 0}

IDEAS = [
    ("desk_now", "Desk now (options, touch)", "A call when SPY touches the red area; sold on a 0.25% stop or after 30 minutes."),
    ("bounce", "1. Wait for the bounce", "A call only after SPY dips into the red area and a 5-minute candle closes back above it."),
    ("shares", "2. Shares, stop held at Alpaca", "The desk's entries with $1,000 of SPY shares; the stop fills the minute it's hit."),
    ("swing", "3. Let winners run", "The desk's entries in shares; a stop 0.5% under the best close rises with SPY; up to 10 days."),
    ("overnight", "4. Overnight hold", "$1,000 of SPY bought at the close, sold at the next morning's open, every day."),
    ("buy_hold", "Buy and hold SPY", "$1,000 in SPY every day, close to close. The yardstick: no skill needed."),
]


def rth(minutes: list[dict]) -> list[dict]:
    """The regular session only (09:30–16:00 ET)."""
    lo, hi = hhmm("09:30"), hhmm("16:00")
    return [b for b in minutes if lo <= b["t"].astimezone(ET).time() < hi]


def _trade(t: datetime, entry: float, exit_: float, why: str, **extra) -> dict:
    qty = BOOK / entry
    usd = (exit_ - entry) * qty
    net = usd - SHARE_COST * qty
    return {"t": t.astimezone(ET).isoformat(timespec="minutes"), "entry": round(entry, 2), "exit": round(exit_, 2),
            "why": why, "usd": round(usd, 2), "net": round(net, 2), "opt": round(net, 2), **extra}


def overnight(days: list[tuple[date, list[dict]]]) -> list[dict]:
    """Buy at each day's last regular-hours close, sell at the next day's first regular-hours open."""
    out = []
    for (_, a), (_, b) in zip(days, days[1:]):
        if not a or not b or b[0]["t"].astimezone(ET).time() > hhmm("09:35"):
            continue  # a missing open: no trade rather than a made-up price
        out.append(_trade(a[-1]["t"], a[-1]["c"], b[0]["o"], "open"))
    return out


def buy_hold(days: list[tuple[date, list[dict]]]) -> list[dict]:
    """$1,000 in SPY each day, close to close (reset daily), no costs. One 'trade' per day."""
    out = []
    for (_, a), (d, b) in zip(days, days[1:]):
        if a and b:
            usd = BOOK * (b[-1]["c"] / a[-1]["c"] - 1)
            out.append({"t": b[-1]["t"].astimezone(ET).isoformat(timespec="minutes"), "entry": round(a[-1]["c"], 2),
                        "exit": round(b[-1]["c"], 2), "why": "close", "usd": round(usd, 2), "net": round(usd, 2),
                        "opt": round(usd, 2)})
    return out


def swing(days: list[tuple[date, list[dict]]], entries: dict[date, list[tuple]],
          trail_pct: float = TRAIL_PCT, max_days: int = SWING_DAYS) -> list[dict]:
    """One position at a time: buy at the first entry signal while flat; a trailing stop trail_pct under the
    highest close since the buy (a gap below it fills at the open); sold at the close of the max_days-th
    trading day after the buy if the stop hasn't hit."""
    flat: list[dict] = []
    day_of: list[int] = []
    for n, (_, bars) in enumerate(days):
        flat += bars
        day_of += [n] * len(bars)
    index = {b["t"]: k for k, b in enumerate(flat)}
    last_of_day: dict[int, int] = {}
    for k, n in enumerate(day_of):
        last_of_day[n] = k
    out: list[dict] = []
    busy_until = -1
    for n, (d, _) in enumerate(days):
        for entry in entries.get(d, []):
            t, px = entry[0], entry[1]
            trail = entry[2] if len(entry) > 2 and entry[2] else trail_pct  # a per-stock trail, when given
            k0 = index.get(t)
            if k0 is None or k0 <= busy_until:
                continue
            stop, best = px * (1 - trail / 100), px
            end = last_of_day.get(min(n + max_days, len(days) - 1), len(flat) - 1)
            # still open when the data runs out: valued at the last close ("end"), not a real sale
            exit_px, why, k_out = flat[end]["c"], "time" if n + max_days <= len(days) - 1 else "end", end
            for k in range(k0 + 1, end + 1):
                b = flat[k]
                if b["l"] <= stop:
                    exit_px, why, k_out = min(b["o"], stop), "stop", k
                    break
                best = max(best, b["c"])
                stop = max(stop, best * (1 - trail / 100))
            out.append(_trade(t, px, exit_px, why, days_held=day_of[k_out] - n))
            busy_until = k_out
            break  # at most one buy a day
    return out


def desk_entries(day: "bt.Day", start: str = "10:00", cutoff: str = "15:40") -> list[tuple[datetime, float]]:
    """The desk's call signals that day: price at the red area at a 10-minute check, 10:00 to 15:40.
    Each is (the 1-minute bar the check read, its close)."""
    s_t, c_t = hhmm(start), hhmm(cutoff)
    out = []
    for c in day.candidates("touch", poll=10):
        b = day.m[c["i"]]
        if c["long"] and c["zone"]["color"] == "red" and s_t <= (b["t"].astimezone(ET) + timedelta(minutes=1)).time() < c_t:
            out.append((b["t"], c["entry"]))
    return out


def run(bars_by_day: dict[date, list[dict]], cfg: dict, train_share: float = 0.7, progress=None,
        vix: dict[date, float] | None = None, symbol: str = "SPY") -> dict:
    """symbol other than SPY: the share ideas only (the option ideas price with SPY's VIX), and each day's stop
    is a quarter of the stock's average daily range over the 20 days before (signals.range_stop_pct);
    the swing's trail is twice that, as SPY's 0.5% is twice its 0.25%."""
    from signals import range_stop_pct
    from studies import mxwll

    spy = symbol == "SPY"
    dailies: list[dict] = []
    stops: dict[date, float] = {}

    tf, rth_only = int(cfg["timeframe_minutes"]), bool(cfg.get("regular_hours_only"))
    history: list[dict] = []
    warm: list[dict] = []
    session: list[tuple[date, list[dict]]] = []
    per_day: dict[str, dict[date, list[dict]]] = {"desk_now": {}, "bounce": {}, "shares": {}} if spy else {"shares": {}}
    swing_entries: dict[date, list[tuple[datetime, float]]] = {}
    ds = sorted(bars_by_day)
    for n, d in enumerate(ds):
        mins = bars_by_day[d]
        if len(mins) >= 60 and len(history) > int(cfg.get("aoi_lookback", 50)):
            session.append((d, rth(mins)))
            day = bt.Day(d, mins, warm, history, cfg, vol=bt._vol_for(vix, d))
            stop = None if spy else range_stop_pct(dailies, STOP_RANGE_FRACTION, 20)
            if day.zones and (spy or stop):
                if stop:
                    stops[d] = stop
                plays = (("desk_now", DESK_NOW), ("bounce", BOUNCE), ("shares", SHARES)) if spy else (
                    ("shares", {**SHARES, "stop": stop}),)
                for name, v in plays:
                    per_day[name][d] = day.trades(v)
                swing_entries[d] = [(t, px, 2 * stop if stop else None) for t, px in desk_entries(day)]
            day_rth = rth(mins)
            if day_rth:
                dailies.append({"h": max(b["h"] for b in day_rth), "l": min(b["l"] for b in day_rth), "c": day_rth[-1]["c"]})
        history = (history + mxwll.resample(mins, tf, rth_only))[-bt.HISTORY_CANDLES:]
        warm = (warm + mins)[-bt.RSI_WARMUP:]
        if progress and n % 50 == 0:
            progress(n, len(ds))
    trades = {name: [t for d in sorted(v) for t in v[d]] for name, v in per_day.items()}
    trades["swing"] = swing(session, swing_entries)
    trades["overnight"] = overnight(session)
    trades["buy_hold"] = buy_hold(session)
    split_at = session[int(len(session) * train_share)][0] if session else None
    first, last = (session[0][0], session[-1][0]) if session else (None, None)

    def part(ts, which):
        if split_at is None:
            return ts
        return [t for t in ts if (t["t"][:10] < split_at.isoformat()) == (which == "train")]

    years = sorted({t["t"][:4] for ts in trades.values() for t in ts})
    return {
        "days": len(session), "first": first.isoformat() if first else None, "last": last.isoformat() if last else None,
        "train_range": [first.isoformat(), (split_at - timedelta(days=1)).isoformat()] if split_at else None,
        "test_range": [split_at.isoformat(), last.isoformat()] if split_at else None,
        "book": BOOK, "share_cost": SHARE_COST, "option_cost": bt.COST_PER_TRADE, "trail_pct": TRAIL_PCT,
        "swing_days": SWING_DAYS,
        "symbol": symbol,
        "stop_pct": {"median": _median(list(stops.values())), "low": min(stops.values()), "high": max(stops.values())}
        if stops else None,
        "ideas": [{"name": k, "label": label.replace("SPY", symbol), "what": what.replace("SPY", symbol),
                   "train": bt.summarize(part(trades[k], "train")), "test": bt.summarize(part(trades[k], "test")),
                   "all": bt.summarize(trades[k])} for k, label, what in IDEAS if k in trades],
        "monthly": {k: bt._monthly(trades[k]) for k, _, _ in IDEAS if k in trades},
        "by_year": {k: {y: bt.summarize([t for t in trades[k] if t["t"][:4] == y]) for y in years}
                    for k, _, _ in IDEAS if k in trades},
        "exits": {k: _count([t["why"] for t in trades[k]]) for k, _, _ in IDEAS if k in trades},
    }


def _median(xs: list[float]) -> float | None:
    xs = sorted(xs)
    return round(xs[len(xs) // 2], 4) if xs else None


def _count(xs: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


def markdown(res: dict) -> str:
    lines = [f"# Four ideas vs the desk and buy-and-hold ({res.get('symbol', 'SPY')})", "",
             f"{res['days']:,} trading days, {res['first']} to {res['last']}. Test days {res['test_range']}. "
             f"Options $5 a trade; shares ${res['share_cost']:.2f} a share. Rules set before the run, not tuned."
             + (f" Stop: a quarter of the day range, median {res['stop_pct']['median']}% "
                f"({res['stop_pct']['low']}–{res['stop_pct']['high']}%)." if res.get("stop_pct") else ""), "",
             "| Idea | Trades (test) | Won | Net (test) | Per trade | Worst drop (test) | Net (all) |",
             "|---|---|---|---|---|---|---|"]
    for i in res["ideas"]:
        te, al = i["test"], i["all"]
        lines.append(f"| {i['label']} | {te['trades']} | {te.get('win_pct', 0)}% | ${te['total']:,.0f} | "
                     f"${te['per_trade']:,.2f} | ${te.get('max_drawdown', 0):,.0f} | ${al['total']:,.0f} |")
    yrs = sorted({y for v in res["by_year"].values() for y in v})
    lines += ["", "| Net by year | " + " | ".join(yrs) + " |", "|---|" + "---|" * len(yrs)]
    for i in res["ideas"]:
        by = res["by_year"][i["name"]]
        lines.append(f"| {i['label']} | " + " | ".join(f"${by[y]['total']:,.0f}" if y in by else "—" for y in yrs) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from studies import auto

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=("alpaca", "yfinance"), default="yfinance")
    ap.add_argument("--since", default="2020-01-01")
    ap.add_argument("--symbol", default="SPY", help="SPY: all ideas; another stock: the share ideas")
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args(argv)
    cfg = auto.desk_config()
    bars = (bt.alpaca_minutes(date.fromisoformat(a.since), datetime.now(ET).date(), a.symbol) if a.source == "alpaca"
            else bt.yfinance_minutes(a.symbol))
    print(f"{len(bars):,} one-minute bars", flush=True)
    days = bt.group_days(bars, bool(cfg.get("regular_hours_only")))
    del bars
    vix = bt.vix_closes(min(days) - timedelta(days=10)) if days else {}
    res = run(days, cfg, progress=lambda n, t: print(f"  day {n} of {t}", flush=True), vix=vix, symbol=a.symbol)
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
