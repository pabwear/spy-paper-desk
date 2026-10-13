"""Daily credit spreads on SPY: sell a small out-of-the-money spread each morning, keep the credit unless the
price runs through it by the close. Research only here; the desk's paper book uses the same rules.

Rules (set before any test):
    when     every day SPY has an expiry that same day (every weekday since Nov 14 2022; Mon/Wed/Fri before),
             at the 10:00 check; other days: no trade
    strikes  the short strike sits `sd` "remaining-day moves" away (VIX × price × √(market time left)), rounded
             away from the price to a whole dollar; the long strike `width` dollars further (it caps the loss)
    puts     a put spread below the price; calls (optional) a call spread above it (together: an iron condor)
    exit     held to the close (settles on the 16:00 price) or, as the desk does, bought back at 15:40 (close_at, so
             no shares can ever be assigned); or closed early at the 10-minute checks when its
             buy-back cost reaches `stop_x` × the credit, or falls to (1 − take) × the credit
    prices   Black-Scholes at the day's VIX on market time; `credit_factor` < 1 shrinks every price (a stress test);
             `vol_mult` prices at that share of the VIX (strikes still placed by the VIX). Real same-day SPY quotes
             on Oct 7-8 2026 matched 0.47-0.63 x VIX (mid prices about a fifth of the VIX model's), so the
             "real_" variants price at 0.5 x VIX
    costs    $3 per spread to open (two legs, about a penny each plus fees), $3 more to close early; settling at
             expiry costs nothing

Worst case per spread: width × 100 − the credit. The book sizes 1 spread per side.

Day filters (set Oct 9 2026, before running them, after real prices showed the live rule loses; the
"filt_" variants). Each uses only what is known at 10:00: the VIX's last two closes, SPY's last close and
its 20-day average, SPY at 10:00. A filter counts only if it makes money in ALL of train, check and exam:
    vix_high      the VIX closed at 20 or more            (richer premium)
    vix_low       the VIX closed under 15                 (quiet markets)
    no_down_open  SPY at 10:00 not more than 0.5% under yesterday's close
    uptrend       yesterday's close above its 20-day average
    vix_calm      the VIX did not rise more than 10% on its last close
    uptrend_no_down_open   uptrend and no_down_open together

    python3 spreads.py --source alpaca --since 2020-07-01 --json spreads.json --md spreads.md
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta

import backtest_areas as bt
from common import ET, hhmm

OPEN_COST = 3.0
CLOSE_COST = 3.0
SPLITS = {"train": (None, date(2024, 3, 13)), "check": (date(2024, 3, 13), date(2025, 10, 7)),
          "exam": (date(2025, 10, 7), None)}

BASE = {"sd": 1.0, "width": 5, "puts": True, "calls": False, "stop_x": 0, "take": 0, "credit_factor": 1.0}
REAL = {**BASE, "vol_mult": 0.5, "close_at": "15:40"}
FILTERS = {
    "vix_high": lambda f: f["vix"] >= 20,
    "vix_low": lambda f: f["vix"] < 15,
    "no_down_open": lambda f: f["price"] >= f["prev_close"] * 0.995,
    "uptrend": lambda f: f["ma20"] is not None and f["prev_close"] > f["ma20"],
    "vix_calm": lambda f: f["vix_prev"] is None or f["vix"] <= f["vix_prev"] * 1.10,
    "uptrend_no_down_open": lambda f: f["ma20"] is not None and f["prev_close"] > f["ma20"]
    and f["price"] >= f["prev_close"] * 0.995,
}
VARIANTS = [
    {"name": "put_1sd_hold", **BASE},
    {"name": "put_half_sd_hold", **BASE, "sd": 0.5},
    {"name": "put_1_5sd_hold", **BASE, "sd": 1.5},
    {"name": "put_1sd_stop2x", **BASE, "stop_x": 2.0},
    {"name": "put_1sd_take50_stop2x", **BASE, "stop_x": 2.0, "take": 0.5},
    {"name": "condor_1sd_hold", **BASE, "calls": True},
    {"name": "put_1sd_hold_stress70", **BASE, "credit_factor": 0.7},
    # as the desk runs it: bought back at 15:40 every day
    {"name": "desk_put_1sd", **BASE, "close_at": "15:40"},
    {"name": "desk_put_half_sd", **BASE, "sd": 0.5, "close_at": "15:40"},
    {"name": "desk_put_1sd_take50_stop2x", **BASE, "stop_x": 2.0, "take": 0.5, "close_at": "15:40"},
    {"name": "desk_condor_1sd", **BASE, "calls": True, "close_at": "15:40"},
    {"name": "desk_put_1sd_stress70", **BASE, "credit_factor": 0.7, "close_at": "15:40"},
    # priced like the real quotes (0.5 x VIX); the desk's live rule is real_desk_half_sd
    {"name": "real_desk_half_sd", **REAL, "sd": 0.5},
    {"name": "real_desk_half_sd_vol60", **REAL, "sd": 0.5, "vol_mult": 0.6},
    {"name": "real_desk_half_sd_stop2x", **REAL, "sd": 0.5, "stop_x": 2.0},
    {"name": "real_desk_half_sd_stop3x", **REAL, "sd": 0.5, "stop_x": 3.0},
    {"name": "real_desk_half_sd_take50_stop3x", **REAL, "sd": 0.5, "take": 0.5, "stop_x": 3.0},
    {"name": "real_desk_quarter_sd", **REAL, "sd": 0.25},
    {"name": "real_desk_1sd", **REAL, "sd": 1.0},
    {"name": "real_half_sd_hold_to_expiry", **BASE, "sd": 0.5, "vol_mult": 0.5},
    *({"name": f"filt_{k}", **REAL, "sd": 0.5, "filter": k} for k in FILTERS),
]


def legs(spec: dict, price: float, sigma: float) -> list[tuple[str, float, float]]:
    """(right, short strike, long strike) for each side."""
    out = []
    w, k = float(spec["width"]), float(spec["sd"])
    if spec.get("puts", True):
        short = math.floor(price - k * sigma)
        out.append(("put", short, short - w))
    if spec.get("calls"):
        short = math.ceil(price + k * sigma)
        out.append(("call", short, short + w))
    return out


def implied_vol_mult(price: float, short: float, long_: float, t: datetime, vix: float, mid: float) -> float | None:
    """The share of the VIX at which a same-day put spread's real mid price matches Black-Scholes
    (what the backtests' vol_mult should be). None when the price can't be matched."""
    years = bt.trading_years(t, t.astimezone(ET).date())
    lg = [("put", short, long_)]
    f = lambda k: spread_value(lg, price, years, k * vix / 100)  # noqa: E731
    lo, hi = 0.02, 3.0
    if mid <= 0 or not f(lo) <= mid <= f(hi):
        return None
    for _ in range(60):
        m = (lo + hi) / 2
        lo, hi = (m, hi) if f(m) < mid else (lo, m)
    return round((lo + hi) / 2, 3)


def spread_value(lg: list[tuple], price: float, years: float, vol: float) -> float:
    """What buying the spreads back costs, per share."""
    v = 0.0
    for right, short, long_ in lg:
        call = right == "call"
        v += bt.bs_price(price, short, years, vol, call) - bt.bs_price(price, long_, years, vol, call)
    return max(v, 0.0)


def settle(lg: list[tuple], price: float) -> float:
    """What the spreads owe at expiry, per share."""
    owe = 0.0
    for right, short, long_ in lg:
        if right == "put":
            owe += max(0.0, short - price) - max(0.0, long_ - price)
        else:
            owe += max(0.0, price - short) - max(0.0, price - long_)
    return owe


def day_trade(spec: dict, d: date, bars: list[dict], vol: float) -> dict | None:
    """One day's spread on that day's regular-hours minutes, or None (no same-day expiry, no 10:00 bar, no credit)."""
    if bt.expiry_for(d) != d or not bars:
        return None
    ten = next((b for b in bars if (b["t"].astimezone(ET) + timedelta(minutes=1)).time() >= hhmm("10:00")), None)
    if ten is None:
        return None
    t_in = ten["t"] + timedelta(minutes=1)
    price = ten["c"]
    years = bt.trading_years(t_in, d)
    sigma = price * vol * math.sqrt(years)
    lg = legs(spec, price, sigma)
    vol = vol * float(spec.get("vol_mult", 1.0))  # what the options actually price at (strikes stay VIX-placed)
    f = float(spec.get("credit_factor", 1.0))
    credit = spread_value(lg, price, years, vol) * f
    n = len(lg)
    if credit * 100 < 5.0:
        return None  # not worth the costs
    exit_why, owe = "expiry", None
    k0 = bars.index(ten)
    close_at = hhmm(spec["close_at"]) if spec.get("close_at") else None
    for b in bars[k0 + 1:]:
        t = b["t"].astimezone(ET)
        if close_at is not None and t.time() >= close_at:  # the desk's flatten: buy it back at that minute's open
            exit_why, owe = "close", spread_value(lg, b["o"], bt.trading_years(b["t"], d), vol) * f
            break
        if (t.minute + 1) % 10 or t.time() >= hhmm("15:50"):
            continue  # the desk looks every 10 minutes; the last few minutes ride to the close
        v = spread_value(lg, b["c"], bt.trading_years(b["t"] + timedelta(minutes=1), d), vol) * f
        if spec.get("stop_x") and v >= float(spec["stop_x"]) * credit:
            exit_why, owe = "stop", v
            break
        if spec.get("take") and v <= (1 - float(spec["take"])) * credit:
            exit_why, owe = "take", v
            break
    if owe is None:
        owe = settle(lg, bars[-1]["c"])
    pnl = (credit - owe) * 100 - OPEN_COST * n - (CLOSE_COST * n if exit_why != "expiry" else 0)
    worst = max(float(spec["width"]) - credit, 0) * 100 + OPEN_COST * n + CLOSE_COST * n
    return {"t": ten["t"].astimezone(ET).isoformat(timespec="minutes"), "price": round(price, 2),
            "legs": [[r, s, lo] for r, s, lo in lg], "credit": round(credit * 100, 2), "why": exit_why,
            "close": round(bars[-1]["c"], 2), "usd": round(pnl, 2), "net": round(pnl, 2), "opt": round(pnl, 2),
            "worst_case": round(worst, 2)}


def _periods(trades: list[dict], key) -> dict:
    by: dict[str, float] = {}
    for t in trades:
        k = key(date.fromisoformat(t["t"][:10]))
        by[k] = by.get(k, 0.0) + t["net"]
    return by


def summary(trades: list[dict]) -> dict:
    s = bt.summarize(trades)
    for k in ("gross", "delta_net", "target_pct", "stopped_pct"):
        s.pop(k, None)
    if not trades:
        return s
    weeks = _periods(trades, lambda d: f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}")
    months = _periods(trades, lambda d: d.isoformat()[:7])
    s.update(days_won_pct=s["win_pct"], weeks=len(weeks),
             weeks_won_pct=round(100 * sum(v > 0 for v in weeks.values()) / len(weeks), 1),
             months=len(months), months_won_pct=round(100 * sum(v > 0 for v in months.values()) / len(months), 1),
             worst_week=round(min(weeks.values()), 2), worst_month=round(min(months.values()), 2),
             avg_credit=round(sum(t["credit"] for t in trades) / len(trades), 2),
             worst_case=round(max(t["worst_case"] for t in trades), 2),
             exits={w: sum(1 for t in trades if t["why"] == w) for w in ("expiry", "close", "stop", "take")},
             by_year={y: round(sum(t["net"] for t in trades if t["t"][:4] == y), 2)
                      for y in sorted({t["t"][:4] for t in trades})})
    return s


def day_features(session: list[tuple[date, list[dict]]], vix: dict[date, float]) -> dict[date, dict]:
    """What is known before each day's 10:00 sale: the VIX's last two closes (in points), SPY's last close and
    the average of its last 20 closes (None until there are 20). Days without a prior session are left out."""
    out, closes = {}, []
    vdays = sorted(vix)
    for d, bars in session:
        prior = [k for k in vdays if k < d]
        if closes and prior:
            out[d] = {"vix": vix[prior[-1]], "vix_prev": vix[prior[-2]] if len(prior) > 1 else None,
                      "prev_close": closes[-1], "ma20": sum(closes[-20:]) / 20 if len(closes) >= 20 else None}
        if bars:
            closes.append(bars[-1]["c"])
    return out


def run(session: list[tuple[date, list[dict]]], vix: dict[date, float]) -> dict:
    """session: [(day, regular-hours minutes)] in order."""
    out = {"variants": [], "splits": {k: [a.isoformat() if a else None, b.isoformat() if b else None]
                                      for k, (a, b) in SPLITS.items()}}
    feats = day_features(session, vix)
    for spec in VARIANTS:
        keep = FILTERS[spec["filter"]] if spec.get("filter") else None
        trades = [t for d, bars in session if (t := day_trade(spec, d, bars, bt._vol_for(vix, d)))
                  and (keep is None or (d in feats and keep({**feats[d], "price": t["price"]})))]
        row = {"name": spec["name"], "spec": {k: v for k, v in spec.items() if k != "name"}, "all": summary(trades)}
        for part, (a, b) in SPLITS.items():
            row[part] = summary([t for t in trades if (a is None or t["t"][:10] >= a.isoformat())
                                 and (b is None or t["t"][:10] < b.isoformat())])
        out["variants"].append(row)
    # the yardstick's daily record: $4,000 of SPY close to close
    days_up = sum(1 for (_, a), (_, b) in zip(session, session[1:]) if a and b and b[-1]["c"] > a[-1]["c"])
    out["spy_days_up_pct"] = round(100 * days_up / max(1, len(session) - 1), 1)
    out["days"] = len(session)
    out["first"], out["last"] = session[0][0].isoformat(), session[-1][0].isoformat()
    return out


def markdown(res: dict) -> str:
    lines = ["# Daily SPY credit spreads", "",
             f"{res['days']:,} trading days, {res['first']} to {res['last']}. Same-day expiries only, sold at 10:00. "
             f"For comparison SPY closed up on {res['spy_days_up_pct']}% of days. Rules set before the test.", "",
             "| Variant | Period | Days traded | Days won | Weeks won | Months won | Net | Avg credit | Worst day | "
             "Worst week | Worst month | Max loss per day |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for v in res["variants"]:
        named = False
        for part in ("train", "check", "exam", "all"):
            s = v[part]
            if not s.get("trades"):
                continue
            label, named = ("" if named else f"**{v['name']}**"), True
            lines.append(f"| {label} | {part} | {s['trades']} | {s['days_won_pct']}% | "
                         f"{s['weeks_won_pct']}% | {s['months_won_pct']}% | ${s['total']:,.0f} | ${s['avg_credit']:,.0f} | "
                         f"${s['worst_day']:,.0f} | ${s['worst_week']:,.0f} | ${s['worst_month']:,.0f} | ${s['worst_case']:,.0f} |")
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
    session = [(d, ideas.rth(days[d])) for d in sorted(days) if len(days[d]) >= 60]
    vix = bt.vix_closes(session[0][0] - timedelta(days=10)) if session else {}
    res = run(session, vix)
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
