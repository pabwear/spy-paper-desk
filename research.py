"""Research loop: ideas are searched on earlier years and judged once on a locked final year.

Research only: nothing here places orders or changes the desk.

    HOLDOUT_START  days from here on are the final exam. `search` rounds drop them before anything is computed;
                   only the `final` round, run once at the end for the chosen ideas, looks at them.
    Within the search years the first 70 % shape ideas ("train") and the last 30 % judge them ("check").

Sizes are the desk's: shares are whole shares from a $4,000 book; options are 1 call costing at most $3,000.
Costs: shares about a penny each way per share; options $5 a contract round trip. The yardstick is the same
$4,000 kept in the stock every day, close to close.

An idea (a "spec"):
    entry    touch   price at the red area at a 10-minute check, 10:00–15:40 (the desk's signal)
             bounce  a 5-minute candle dipped into the red area and closed back above it (at a 10-minute check)
             ten     10:00 every day (no signal: the "no skill" version of an intraday trade)
             close   the 15:59 close every day
    filter   {"trend": true} the prior close is above its 20-day average; {"vix_max": 25}; {"vix_min": 20}
    asset    shares | option
    budget   dollars: shares buy the whole shares that fit; an option must cost at most this
    expiry_days  options: about this many days out (the longest that fits the budget, at least 7)
    stop     {"pct": 0.25} | {"range": 0.25} (a share of the stock's 20-day average daily range) | absent
    trail    true: the stop rises with the best close since the buy
    checks   0: the stop works every minute (held at Alpaca); 10: looked at every 10 minutes, on the close
    hold     {"minutes": 30} | {"until": "close"} (15:40) | {"until": "next_open"} | {"days": 10}
    target   take profit at this many stop distances (0: none)
    max_entries  per day (2 by default for same-day holds, 1 otherwise)

Option prices: Black-Scholes at the day's VIX; same-day holds on market time (as backtest_areas), holds across
days on calendar time so nights and weekends cost their decay.

    python3 research.py --source alpaca --since 2020-07-01 --round 1 --json r1.json --md r1.md
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta

import backtest_areas as bt
import backtest_ideas as ideas
from common import ET, hhmm

HOLDOUT_START = date(2025, 10, 7)
TRAIN_SHARE = 0.7
SHARE_COST = 0.02
OPTION_COST = 5.0
CUTOFF = hhmm("15:40")
YARDSTICK_BUDGET = 4000.0

SHARES = {"asset": "shares", "budget": 4000, "checks": 0}
OPTION = {"asset": "option", "budget": 3000, "expiry_days": 30, "checks": 10}

ROUNDS: dict[str, list[dict]] = {
    # 1. Stocks vs options on the desk's own signal, held for different lengths of time
    "1": [
        {"name": "shares_30m", "entry": "touch", **SHARES, "stop": {"pct": 0.25}, "hold": {"minutes": 30}},
        {"name": "shares_to_close", "entry": "touch", **SHARES, "stop": {"pct": 0.25}, "hold": {"until": "close"}},
        {"name": "shares_overnight", "entry": "touch", **SHARES, "stop": {"pct": 0.5}, "hold": {"until": "next_open"}},
        {"name": "shares_swing10d", "entry": "touch", **SHARES, "stop": {"pct": 0.5}, "trail": True, "hold": {"days": 10}},
        {"name": "option7d_30m", "entry": "touch", **OPTION, "expiry_days": 7, "stop": {"pct": 0.25}, "hold": {"minutes": 30}},
        {"name": "option30d_30m", "entry": "touch", **OPTION, "stop": {"pct": 0.25}, "hold": {"minutes": 30}},
        {"name": "option60d_30m", "entry": "touch", **OPTION, "expiry_days": 60, "stop": {"pct": 0.25}, "hold": {"minutes": 30}},
        {"name": "option30d_to_close", "entry": "touch", **OPTION, "stop": {"pct": 0.25}, "hold": {"until": "close"}},
        {"name": "option30d_overnight", "entry": "touch", **OPTION, "stop": {"pct": 0.5}, "hold": {"until": "next_open"}},
        {"name": "option60d_swing10d", "entry": "touch", **OPTION, "expiry_days": 60, "stop": {"pct": 0.5}, "trail": True,
         "hold": {"days": 10}},
        {"name": "shares_close_to_open", "entry": "close", **SHARES, "hold": {"until": "next_open"}},
        {"name": "option30d_close_to_open", "entry": "close", **OPTION, "hold": {"until": "next_open"}},
        {"name": "shares_ten_30m", "entry": "ten", **SHARES, "stop": {"pct": 0.25}, "hold": {"minutes": 30}},
        {"name": "option30d_ten_30m", "entry": "ten", **OPTION, "stop": {"pct": 0.25}, "hold": {"minutes": 30}},
    ],
}
# 2. Round 1: options lost on every holding time; every shares version made money, most of it overnight;
#    the desk's signal held overnight made half of holding SPY with under a third of the worst drop.
#    So: the overnight family with filters and stop styles, and options given their fairest overnight shot.
OVERNIGHT = {"entry": "touch", **SHARES, "stop": {"pct": 0.5}, "hold": {"until": "next_open"}}
CLOSE_OPEN = {"entry": "close", **SHARES, "hold": {"until": "next_open"}}
ROUNDS["2"] = [
    {"name": "overnight", **OVERNIGHT},
    {"name": "overnight_trend", **OVERNIGHT, "filter": {"trend": True}},
    {"name": "overnight_no_stop", **{k: v for k, v in OVERNIGHT.items() if k != "stop"}},
    {"name": "overnight_range_stop", **OVERNIGHT, "stop": {"range": 0.5}},
    {"name": "overnight_wide_stop", **OVERNIGHT, "stop": {"pct": 1.0}},
    {"name": "overnight_bounce", **OVERNIGHT, "entry": "bounce"},
    {"name": "overnight_calm", **OVERNIGHT, "filter": {"vix_max": 20}},
    {"name": "to_next_close", **OVERNIGHT, "hold": {"days": 1}},
    {"name": "close_open_trend", **CLOSE_OPEN, "filter": {"trend": True}},
    {"name": "close_open_calm", **CLOSE_OPEN, "filter": {"vix_max": 20}},
    {"name": "close_open_fearful", **CLOSE_OPEN, "filter": {"vix_min": 20}},
    {"name": "option_itm5_60d_overnight", **OVERNIGHT, **{**OPTION, "budget": 4000, "checks": 10},
     "expiry_days": 60, "itm_pct": 5},
    {"name": "option_itm5_60d_close_open", **CLOSE_OPEN, **{**OPTION, "budget": 4000, "checks": 10},
     "expiry_days": 60, "itm_pct": 5},
]

# 3. Round 2: the bounce held overnight in shares was the steadiest idea yet (train +$756, check +$566, every
#    year positive, net about 5× its worst drop on both). In-the-money calls were leverage, not edge; calm/trend
#    close-to-open filters worked on one period only. Is the bounce real, or a lucky choice of its settings?
BOUNCE_ON = {**OVERNIGHT, "entry": "bounce"}
ROUNDS["3"] = [
    {"name": "bounce_overnight", **BOUNCE_ON},
    {"name": "bounce_overnight_tight", **BOUNCE_ON, "stop": {"pct": 0.3}},
    {"name": "bounce_overnight_wide", **BOUNCE_ON, "stop": {"pct": 1.0}},
    {"name": "bounce_overnight_no_stop", **{k: v for k, v in BOUNCE_ON.items() if k != "stop"}},
    {"name": "bounce_overnight_range", **BOUNCE_ON, "stop": {"range": 0.5}},
    {"name": "bounce_to_close", **BOUNCE_ON, "hold": {"until": "close"}},
    {"name": "bounce_next_close", **BOUNCE_ON, "hold": {"days": 1}},
    {"name": "bounce_3days_trail", **BOUNCE_ON, "trail": True, "hold": {"days": 3}},
    {"name": "bounce_any_overnight", **BOUNCE_ON, "entry": "bounce_any"},
    {"name": "touch_or_bounce_overnight", **BOUNCE_ON, "entry": "touch_or_bounce"},
    {"name": "bounce_overnight_trend", **BOUNCE_ON, "filter": {"trend": True}},
    {"name": "bounce_overnight_option30d", **BOUNCE_ON, **OPTION, "stop": {"pct": 0.5}},
]

# 4. Round 3: the overnight bounce held up under every stop setting on both periods; the gain is overnight
#    (sold the same day it barely made money); bounce entries as a 30-day option made money on both periods
#    for the first time, with big swings. Now the "too good to be true" tests: a no-signal control with the
#    same overnight hold, five times the costs, worse fills, one trade a day.
ROUNDS["4"] = [
    {"name": "bounce_overnight", **BOUNCE_ON},
    {"name": "control_ten_overnight", **BOUNCE_ON, "entry": "ten"},
    {"name": "control_touch_overnight", **BOUNCE_ON, "entry": "touch"},
    {"name": "bounce_overnight_costs_x5", **BOUNCE_ON, "share_cost": 0.10},
    {"name": "bounce_overnight_slip", **BOUNCE_ON, "slip_pct": 0.05},
    {"name": "bounce_overnight_one_a_day", **BOUNCE_ON, "max_entries": 1},
    {"name": "bounce_overnight_wide", **BOUNCE_ON, "stop": {"pct": 1.0}},
    {"name": "bounce_any_overnight", **BOUNCE_ON, "entry": "bounce_any"},
    {"name": "bounce_option30d_wide", **BOUNCE_ON, **OPTION, "stop": {"pct": 1.0}},
    {"name": "bounce_option60d", **BOUNCE_ON, **OPTION, "expiry_days": 60, "stop": {"pct": 0.5}},
    {"name": "bounce_option30d_slip", **BOUNCE_ON, **OPTION, "stop": {"pct": 0.5}, "slip_pct": 0.05},
]

# 5. Round 4: the no-signal 10:00 entry held overnight made more dollars than the bounce (check +$869 vs
#    +$566) and was positive every year; the bounce made about twice as much per trade. Most of the money is
#    being in the stock overnight; the signal adds a thin edge (fills 0.05% worse each way cut it by ~70%).
#    Options on the bounce only won in 2023–25. Last search round: does it carry to other stocks? Stops are
#    sized to each stock's range (0.5 × its 20-day average daily range; about 0.5% for SPY).
RANGE_ON = {**BOUNCE_ON, "stop": {"range": 0.5}}
ROUNDS["5"] = [
    {"name": "bounce_overnight", **RANGE_ON},
    {"name": "ten_overnight", **RANGE_ON, "entry": "ten"},
    {"name": "touch_overnight", **RANGE_ON, "entry": "touch"},
    {"name": "close_to_open", **CLOSE_OPEN},
]

FINAL_PICKS: list[str] = []  # chosen after the search rounds, then run once with --round final


# ---------------------------------------------------------------- the timeline every idea walks on

class Ctx:
    """The regular-hours minutes of every day in order, and what each day offers an idea."""

    def __init__(self) -> None:
        self.days: list[date] = []
        self.flat: list[dict] = []
        self.index: dict = {}
        self.first: list[int] = []
        self.last: list[int] = []
        self.info: dict[date, dict] = {}

    def add_day(self, d: date, bars: list[dict], info: dict) -> None:
        if not bars:
            return
        self.days.append(d)
        self.first.append(len(self.flat))
        for b in bars:
            self.index[b["t"]] = len(self.flat)
            self.flat.append(b)
        self.last.append(len(self.flat) - 1)
        self.info[d] = info

    def day_index(self, k: int) -> int:
        lo, hi = 0, len(self.first) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.first[mid] <= k:
                lo = mid
            else:
                hi = mid - 1
        return lo


def _mark_entries(day: "bt.Day") -> dict[str, list[tuple]]:
    """The day's buy signals at the desk's 10-minute checks, 10:00–15:40: (the bar it read, its close).
    touch: at the red area · bounce: closed back above the red area after dipping in · bounce_any: the same off
    either area · touch_or_bounce: whichever comes."""
    s_t, c_t = hhmm("10:00"), CUTOFF
    ok_time = lambda b: s_t <= (b["t"].astimezone(ET) + timedelta(minutes=1)).time() < c_t  # noqa: E731
    out: dict[str, list[tuple]] = {"touch": [], "bounce": [], "bounce_any": []}
    for c in day.candidates("touch", poll=10):
        b = day.m[c["i"]]
        if c["long"] and c["zone"]["color"] == "red" and ok_time(b):
            out["touch"].append((b["t"], c["entry"]))
    for c in day.candidates("confirm", poll=10):
        b = day.m[c["i"]]
        if c["long"] and c["kind"] == "bounce" and ok_time(b):
            out["bounce_any"].append((b["t"], c["entry"]))
            if c["zone"]["color"] == "red":
                out["bounce"].append((b["t"], c["entry"]))
    out["touch_or_bounce"] = sorted(set(out["touch"]) | set(out["bounce"]))
    return out


def build(bars_by_day: dict[date, list[dict]], cfg: dict, vix: dict[date, float] | None, progress=None) -> Ctx:
    from signals import range_stop_pct
    from studies import mxwll

    tf, rth_only = int(cfg["timeframe_minutes"]), bool(cfg.get("regular_hours_only"))
    ctx = Ctx()
    history: list[dict] = []
    warm: list[dict] = []
    dailies: list[dict] = []
    ds = sorted(bars_by_day)
    for n, d in enumerate(ds):
        mins = bars_by_day[d]
        session = ideas.rth(mins)
        if len(mins) >= 60 and len(history) > int(cfg.get("aoi_lookback", 50)) and session:
            day = bt.Day(d, mins, warm, history, cfg, vol=bt._vol_for(vix, d))
            closes = [x["c"] for x in dailies[-20:]]
            sig = _mark_entries(day) if day.zones else {"touch": [], "bounce": [], "bounce_any": [], "touch_or_bounce": []}
            ten = next((b for b in session if (b["t"].astimezone(ET) + timedelta(minutes=1)).time() >= hhmm("10:00")), None)
            info = {"vol": day.vol, "vix": day.vol * 100,
                    "trend_up": bool(len(closes) >= 20 and dailies[-1]["c"] > sum(closes) / len(closes)),
                    "range_pct": range_stop_pct(dailies, 1.0, 20), **sig,
                    "ten": [(ten["t"], ten["c"])] if ten else [],
                    "close": [(session[-1]["t"], session[-1]["c"])]}
            ctx.add_day(d, session, info)
        if session:
            dailies.append({"h": max(b["h"] for b in session), "l": min(b["l"] for b in session), "c": session[-1]["c"]})
        history = (history + mxwll.resample(mins, tf, rth_only))[-bt.HISTORY_CANDLES:]
        warm = (warm + mins)[-bt.RSI_WARMUP:]
        if progress and n % 100 == 0:
            progress(n, len(ds))
    return ctx


# ---------------------------------------------------------------- one idea

def _years_calendar(t: datetime, expiry: date) -> float:
    end = datetime(expiry.year, expiry.month, expiry.day, 16, 0, tzinfo=ET)
    return max(60.0, (end - t.astimezone(ET)).total_seconds()) / (365 * 86400)


def _option(spec: dict, entry_px: float, exit_px: float, t_in: datetime, t_out: datetime, vol: float) -> tuple | None:
    itm = float(spec.get("itm_pct") or 0)
    if itm:  # in the money: the listed expiry about expiry_days out, strike itm_pct under the price
        e = bt.expiry_for(t_in.astimezone(ET).date() + timedelta(days=int(spec.get("expiry_days", 30))))
        k = round(entry_px * (1 - itm / 100))
    else:
        e = bt.affordable_expiry(entry_px, t_in, vol, True, int(spec.get("expiry_days", 30)), 7, float(spec["budget"]))
        k = round(entry_px)
    if e is None:
        return None
    same_day = t_in.astimezone(ET).date() == t_out.astimezone(ET).date()
    yrs = bt.trading_years if same_day else _years_calendar
    p_in = bt.bs_price(entry_px, k, yrs(t_in, e), vol, True)
    p_out = bt.bs_price(exit_px, k, yrs(t_out, e), vol, True)
    if 100 * p_in > float(spec["budget"]):
        return None
    return round(100 * (p_out - p_in) - OPTION_COST, 2), round(100 * p_in, 2), e


def _passes(spec: dict, info: dict) -> bool:
    f = spec.get("filter") or {}
    if f.get("trend") and not info.get("trend_up"):
        return False
    if f.get("vix_max") is not None and info["vix"] > float(f["vix_max"]):
        return False
    if f.get("vix_min") is not None and info["vix"] < float(f["vix_min"]):
        return False
    return True


def simulate(spec: dict, ctx: Ctx, only: set[date] | None = None) -> list[dict]:
    """Every trade this idea would have made: one position at a time, entries only on days in `only`."""
    hold = spec.get("hold") or {"until": "close"}
    same_day = "minutes" in hold or hold.get("until") == "close"
    max_entries = int(spec.get("max_entries", 2 if same_day else 1))
    checks = int(spec.get("checks", 0))
    out: list[dict] = []
    busy_until = -1
    for n, d in enumerate(ctx.days):
        if only is not None and d not in only:
            continue
        info = ctx.info[d]
        if not _passes(spec, info):
            continue
        stop_pct = None
        st = spec.get("stop") or {}
        if st.get("pct") is not None:
            stop_pct = float(st["pct"])
        elif st.get("range") is not None:
            if info.get("range_pct") is None:
                continue
            stop_pct = float(st["range"]) * info["range_pct"]
        taken = 0
        for t, px in info.get(spec["entry"], []):
            k0 = ctx.index.get(t)
            if k0 is None or k0 <= busy_until or taken >= max_entries:
                continue
            tr = _walk(spec, ctx, n, k0, px, stop_pct, hold, same_day, checks, info["vol"])
            if tr is None:
                continue
            out.append(tr)
            busy_until = tr.pop("_k")
            taken += 1
    return out


def _walk(spec, ctx: Ctx, n: int, k0: int, px: float, stop_pct, hold, same_day, checks, vol) -> dict | None:
    flat = ctx.flat
    t_in = flat[k0]["t"] + timedelta(minutes=1)
    stop = px * (1 - stop_pct / 100) if stop_pct else None
    tgt = px * (1 + float(spec.get("target") or 0) * stop_pct / 100) if stop_pct and spec.get("target") else None
    if "minutes" in hold:
        end, until = ctx.last[n], t_in + timedelta(minutes=int(hold["minutes"]))
    elif hold.get("until") == "close":
        end, until = ctx.last[n], None
    elif hold.get("until") == "next_open":
        if n + 1 >= len(ctx.days):
            return None
        end, until = ctx.first[n + 1], None
    else:
        end, until = ctx.last[min(n + int(hold.get("days", 1)), len(ctx.days) - 1)], None
    best = px
    exit_px, why, k_out = flat[end]["c"], "time", end
    if hold.get("until") == "next_open":
        exit_px, why = flat[end]["o"], "open"
    for k in range(k0 + 1, end + 1):
        b = flat[k]
        tm = b["t"].astimezone(ET)
        if same_day and tm.time() >= CUTOFF:
            exit_px, why, k_out = b["o"], "close", k
            break
        if until is not None and b["t"] >= until:
            exit_px, why, k_out = b["o"], "time", k
            break
        if hold.get("until") == "next_open" and k == end:
            exit_px, why, k_out = (min(b["o"], stop) if stop and b["o"] <= stop else b["o"]), "open", k
            break
        looking = checks == 0 or (tm.minute + 1) % checks == 0
        if stop is not None and looking:
            if checks == 0 and b["l"] <= stop:
                exit_px, why, k_out = min(b["o"], stop), "stop", k
                break
            if checks and b["c"] <= stop:
                exit_px, why, k_out = b["c"], "stop", k
                break
        if tgt is not None and looking:
            if checks == 0 and b["h"] >= tgt:
                exit_px, why, k_out = max(b["o"], tgt), "target", k
                break
            if checks and b["c"] >= tgt:
                exit_px, why, k_out = b["c"], "target", k
                break
        if spec.get("trail") and stop is not None:
            best = max(best, b["c"])
            stop = max(stop, best * (1 - stop_pct / 100))
    # time, close and open exits fill at that minute's open; stops, targets and the last close at its end
    filled_at_open = why in ("time", "close", "open") and not (why == "time" and k_out == end and until is None)
    t_out = flat[k_out]["t"] + (timedelta(0) if filled_at_open else timedelta(minutes=1))
    slip = float(spec.get("slip_pct") or 0) / 100  # worse fills: pay more going in, get less coming out
    px_paid, exit_got = px * (1 + slip), exit_px * (1 - slip)
    if spec["asset"] == "shares":
        qty = int(float(spec["budget"]) // px)
        if qty < 1:
            return None
        pnl = round((exit_got - px_paid) * qty - float(spec.get("share_cost", SHARE_COST)) * qty, 2)
        cost = round(qty * px, 2)
    else:
        got = _option(spec, px_paid, exit_got, t_in, t_out, vol)
        if got is None:
            return None
        pnl, cost, _ = got
    return {"t": flat[k0]["t"].astimezone(ET).isoformat(timespec="minutes"), "exit_t": t_out.astimezone(ET).isoformat(timespec="minutes"),
            "entry": round(px, 2), "exit": round(exit_px, 2), "why": why, "usd": pnl, "net": pnl, "opt": pnl,
            "cost": cost, "days_held": ctx.day_index(k_out) - n, "_k": k_out}


def yardstick(ctx: Ctx, only: set[date], budget: float = YARDSTICK_BUDGET) -> list[dict]:
    """The same money kept in the stock every day, close to close (reset daily), no costs."""
    out = []
    for n in range(1, len(ctx.days)):
        d = ctx.days[n]
        if d not in only:
            continue
        a, b = ctx.flat[ctx.last[n - 1]]["c"], ctx.flat[ctx.last[n]]["c"]
        usd = round(budget * (b / a - 1), 2)
        out.append({"t": d.isoformat() + "T16:00", "usd": usd, "net": usd, "opt": usd, "why": "close"})
    return out


def _summary(trades: list[dict], budget: float) -> dict:
    s = bt.summarize(trades)
    s["return_pct"] = round(100 * s.get("total", 0) / budget, 1) if budget else None
    if trades:
        s["avg_days_held"] = round(sum(t.get("days_held", 0) for t in trades) / len(trades), 2)
        s["exits"] = ideas._count([t["why"] for t in trades])
        s["avg_cost"] = round(sum(t.get("cost", 0) for t in trades) / len(trades), 2) if "cost" in trades[0] else None
    s.pop("gross", None)
    s.pop("delta_net", None)
    return s


def run_round(ctx: Ctx, specs: list[dict], parts: dict[str, set[date]]) -> dict:
    res = []
    for spec in specs:
        trades = simulate(spec, ctx, set().union(*parts.values()))
        row = {"name": spec["name"], "spec": {k: v for k, v in spec.items() if k != "name"}}
        for p, days in parts.items():
            ts = [t for t in trades if date.fromisoformat(t["t"][:10]) in days]
            row[p] = _summary(ts, float(spec["budget"]))
            row[p]["by_year"] = {y: round(sum(t["net"] for t in ts if t["t"][:4] == y), 2)
                                 for y in sorted({t["t"][:4] for t in ts})}
        res.append(row)
    yard = {p: _summary(yardstick(ctx, days), YARDSTICK_BUDGET) for p, days in parts.items()}
    return {"results": res, "yardstick": yard}


def markdown(out: dict) -> str:
    parts = list(out["parts"])
    head = " | ".join(f"{p}: trades · won · net · per trade · worst drop · net ÷ worst drop" for p in parts)
    lines = [f"# Research round {out['round']} ({out['symbol']})", "",
             f"{out['days']} days used, {out['first']} to {out['last']}. "
             + " · ".join(f"{p} {r[0]} to {r[1]}" for p, r in out["parts"].items())
             + f". Locked final-exam days start {HOLDOUT_START}{' (included: final round)' if out['round'] == 'final' else ' (not loaded)'}.",
             "", f"| Idea | {head} |", "|---|" + "---|" * len(parts)]
    def cell(s):
        dd = abs(s.get("max_drawdown") or 0)
        ratio = f" · {s['total'] / dd:.1f}×" if dd else ""
        return (f"{s['trades']} · {s.get('win_pct', 0)}% · ${s['total']:,.0f} · ${s['per_trade']:,.2f} · "
                f"${s.get('max_drawdown', 0):,.0f}{ratio}") if s["trades"] else "—"
    for r in sorted(out["results"], key=lambda r: -r[parts[-1]]["total"]):
        lines.append(f"| {r['name']} | " + " | ".join(cell(r[p]) for p in parts) + " |")
    lines.append(f"| **Hold ${YARDSTICK_BUDGET:,.0f} of {out['symbol']}** | "
                 + " | ".join(cell(out["yardstick"][p]) for p in parts) + " |")
    return "\n".join(lines) + "\n"


def lock_holdout(days: dict[date, list]) -> dict[date, list]:
    """Search rounds see only the days before the final exam."""
    return {d: v for d, v in days.items() if d < HOLDOUT_START}


def main(argv: list[str] | None = None) -> int:
    from studies import auto

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=("alpaca", "yfinance"), default="yfinance")
    ap.add_argument("--since", default="2020-07-01")
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--round", required=True, help="a round number in ROUNDS, or final")
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args(argv)
    if a.round != "final" and a.round not in ROUNDS:
        raise SystemExit(f"unknown round {a.round!r}; rounds: {', '.join(ROUNDS)} or final")
    specs = ([s for r in ROUNDS.values() for s in r if s["name"] in FINAL_PICKS] if a.round == "final"
             else ROUNDS[a.round])
    if not specs:
        raise SystemExit("nothing to run (FINAL_PICKS is empty)")
    cfg = auto.desk_config()
    end = datetime.now(ET).date() if a.round == "final" else HOLDOUT_START
    bars = (bt.alpaca_minutes(date.fromisoformat(a.since), end, a.symbol) if a.source == "alpaca"
            else bt.yfinance_minutes(a.symbol))
    days = bt.group_days(bars, bool(cfg.get("regular_hours_only")))
    del bars
    if a.round != "final":  # the locked year is never even loaded in a search round
        days = lock_holdout(days)
    vix = bt.vix_closes(min(days) - timedelta(days=10)) if days else {}
    ctx = build(days, cfg, vix, progress=lambda n, t: print(f"  day {n} of {t}", flush=True))
    if not ctx.days:
        raise SystemExit("No days to study (a search round only uses days before the locked final year).")
    if a.round == "final":
        parts = {"exam": {d for d in ctx.days if d >= HOLDOUT_START}}
    else:
        cut = ctx.days[math.floor(len(ctx.days) * TRAIN_SHARE)]
        parts = {"train": {d for d in ctx.days if d < cut}, "check": {d for d in ctx.days if d >= cut}}
    res = run_round(ctx, specs, parts)
    out = {"round": a.round, "symbol": a.symbol, "days": len(ctx.days),
           "first": ctx.days[0].isoformat(), "last": ctx.days[-1].isoformat(),
           "parts": {p: [min(v).isoformat(), max(v).isoformat()] for p, v in parts.items() if v},
           "holdout_start": HOLDOUT_START.isoformat(), **res}
    md = markdown(out)
    print(md)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(out, f, default=str)
    if a.md:
        with open(a.md, "w") as f:
            f.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
