"""Backtest: how the call and put areas would have traded under each entry rule, and which settings hold up.

Research only: nothing here places orders or changes the desk. It replays 1-minute SPY bars (the desk's
own Alpaca IEX feed in the cloud) through the desk's pieces:

- areas: the desk's 09:39 Mxwll read (Areas of Interest on its candles and session), red and green;
- the desk's 2-signal check (signals.confluence_tags + studies.auto.zone_tags): RSI, VWAP, volume,
  structure and support/resistance near the area, pointed the way of the trade;
- the desk's trade rules: decisions at each 5-minute mark 10:00–15:40 ET, at most 2 entries a day and one
  at a time, a stop on SPY, flat from 15:40. Exits are walked minute by minute.

Entry rules:
    touch    today's rule: a call when price is at the red area, a put at the green area
    confirm  Roy's option 3: once a 5-minute candle closes after touching an area, closed through it →
             trade the breakout; closed back on the side it came from → trade the bounce

Each variant = entry rule × areas (both / red only / green only) × 2-signal check (off / on) × stop
(0.25 / 0.35 / 0.5 %) × take profit (none / 1× / 2× the stop). Days are split in time order: the
first 70 % pick the settings ("train"), the last 30 % judge them on days they never saw ("test").

Option dollars, two ways, both after $5 a trade for spreads and fees:
    opt    the option itself, priced with Black-Scholes at entry and exit: the at-the-money strike (nearest
           dollar), the nearest listed expiry (every weekday since Nov 14 2022; Mon/Wed/Fri before), and that
           day's VIX as the volatility. Time decay and big-move gains are in. This is the headline number.
    net    the desk's quick estimate: $50 per $1 of SPY (delta 0.5), no time decay.
Benchmarks: a call (or a put) bought at 10:00 every day with the same stop, wherever price is.

    python3 backtest_areas.py --source yfinance            # ~7 days of 1-minute bars, for a quick check
    python3 backtest_areas.py --source alpaca --since 2020-01-01 --json out.json --md out.md   # in the cloud
"""

from __future__ import annotations

import argparse
import itertools
import json
from datetime import date, datetime, timedelta

from common import ET, hhmm

PER_DOLLAR = 50.0      # option dollars per $1 of SPY, delta 0.5 × 100 shares
COST_PER_TRADE = 5.0   # spreads and fees, round trip, per contract (a rough allowance)
HISTORY_CANDLES = 600  # 5-minute candles the study reads (about three extended-hours days)
RSI_WARMUP = 300       # 1-minute closes for the desk's RSI

RULES = ("touch", "confirm")
AREAS = ("both", "red", "green")
MIN_TAGS = (0, 2)
STOPS = (0.25, 0.35, 0.5)
TARGETS = (0, 1, 2)  # take profit at this many stop distances (0 = hold to the stop or 15:40)

DESK_NOW = {"rule": "touch", "areas": "both", "min_tags": 2, "stop": 0.35, "target": 0}
OPTION_3 = {"rule": "confirm", "areas": "both", "min_tags": 2, "stop": 0.35, "target": 0}


def _ncdf(x: float) -> float:
    from math import erf, sqrt

    return 0.5 * (1 + erf(x / sqrt(2)))


def bs_price(spot: float, strike: float, years: float, vol: float, call: bool) -> float:
    """Black-Scholes, no rates or dividends (fine for a few hours or days)."""
    from math import log, sqrt

    if years <= 0 or vol <= 0:
        return max(0.0, spot - strike) if call else max(0.0, strike - spot)
    sd = vol * sqrt(years)
    d1 = (log(spot / strike) + 0.5 * sd * sd) / sd
    c = spot * _ncdf(d1) - strike * _ncdf(d1 - sd)
    return c if call else c - spot + strike


def expiry_for(d: date) -> date:
    """SPY's nearest listed expiry on day d: daily since Nov 14 2022, Mon/Wed/Fri before."""
    days = (0, 1, 2, 3, 4) if d >= date(2022, 11, 14) else (0, 2, 4)
    e = d
    while e.weekday() not in days:
        e += timedelta(days=1)
    return e


def option_pnl(entry_px: float, exit_px: float, t_in: datetime, t_out: datetime, vol: float, call: bool) -> float:
    """One contract's dollars from the option's own price change (strike: nearest dollar at entry)."""
    k = round(entry_px)
    e = expiry_for(t_in.astimezone(ET).date())
    return 100 * (bs_price(exit_px, k, trading_years(t_out, e), vol, call)
                  - bs_price(entry_px, k, trading_years(t_in, e), vol, call))


def trading_years(t: datetime, expiry: date) -> float:
    """Market time left until 16:00 on the expiry day: 390 minutes a trading day, 252 days a year
    (nearly all of a day's movement happens while the market is open). At least one minute."""
    t = t.astimezone(ET)
    today_left = max(0.0, min(390.0, (16 * 60) - (t.hour * 60 + t.minute + t.second / 60)))
    full_days, d = 0, t.date()
    while d < expiry:
        d += timedelta(days=1)
        if d.weekday() < 5:
            full_days += 1
    return max(1.0, today_left + 390.0 * full_days) / (252 * 390)


BENCHMARKS = [{"rule": "always_call", "areas": "both", "min_tags": 0, "stop": 0.35, "target": 0},
              {"rule": "always_put", "areas": "both", "min_tags": 0, "stop": 0.35, "target": 0}]


def variants() -> list[dict]:
    return [{"rule": r, "areas": a, "min_tags": m, "stop": s, "target": t}
            for r, a, m, s, t in itertools.product(RULES, AREAS, MIN_TAGS, STOPS, TARGETS)] + BENCHMARKS


def vkey(v: dict) -> str:
    tp = f"tp {v['target']}x" if v["target"] else "hold"
    return f"{v['rule']} · {v['areas']} · {v['min_tags']} signals · stop {v['stop']}% · {tp}"


def _pos(price: float, z: dict) -> str:
    return "below" if price < z["low"] else "above" if price > z["high"] else "inside"


class Day:
    """One trading day: its 1-minute bars, its 09:39 areas, its entry candidates and their signals."""

    def __init__(self, d: date, minutes: list[dict], warm: list[dict], history: list[dict], cfg: dict,
                 tol_pct: float = 0.15, snapshot: str = "09:39", step: int = 5, vol: float = 0.16):
        from studies import mxwll

        self.d, self.m, self.cfg, self.tol, self.step, self.vol = d, minutes, cfg, tol_pct, step, vol
        self.warm = warm          # earlier 1-minute bars, for RSI
        self.history = history    # earlier 5-minute candles, for the study
        tf = int(cfg["timeframe_minutes"])
        rth_only = bool(cfg.get("regular_hours_only"))
        snap = hhmm(snapshot)
        cut = datetime(d.year, d.month, d.day, snap.hour, snap.minute, 59, tzinfo=ET)
        self.candles_today = mxwll.resample(minutes, tf, rth_only)
        upto = [b for b in minutes if b["t"] <= cut]
        base = (history + mxwll.resample(upto, tf, rth_only))[-HISTORY_CANDLES:]
        self.zones: list[dict] = []
        if len(base) > int(cfg.get("aoi_lookback", 50)):
            a = (mxwll.analyze(base, cfg) or {}).get("aoi")
            if a:
                self.zones = [{"color": k, "low": a[k]["low"], "high": a[k]["high"]} for k in ("red", "green")
                              if a[k]["visible"]]
        self._tags: dict = {}
        self._cands: dict = {}

    # ------------------------------------------------------------ candidates (independent of positions)
    def marks(self) -> list[int]:
        """Indexes of the minutes that end on a 5-minute mark (the bar that closes at :00, :05, ...)."""
        return [i for i, b in enumerate(self.m) if (b["t"].astimezone(ET).minute + 1) % self.step == 0]

    def candidates(self, rule: str) -> list[dict]:
        if rule in self._cands:
            return self._cands[rule]
        out: list[dict] = []
        if rule in ("always_call", "always_put"):
            first = next((i for i in self.marks() if (self.m[i]["t"].astimezone(ET) + timedelta(minutes=1)).time() >= hhmm("10:00")), None)
            if first is not None:
                long = rule == "always_call"
                out.append({"i": first, "zone": {"color": "red" if long else "green"}, "long": long, "kind": "benchmark",
                            "entry": self.m[first]["c"]})
        elif rule == "touch":
            for i in self.marks():
                px = self.m[i]["c"]
                near = []
                for z in self.zones:
                    mid = (z["low"] + z["high"]) / 2
                    if z["low"] <= px <= z["high"] or abs(px - mid) / mid <= self.tol / 100:
                        near.append((abs(px - mid), z))
                if near:
                    z = min(near, key=lambda x: x[0])[1]
                    out.append({"i": i, "zone": z, "long": z["color"] == "red", "kind": "touch", "entry": px})
        else:
            origin = {z["color"]: None for z in self.zones}
            prev = {z["color"]: None for z in self.zones}
            lo_since = {z["color"]: float("inf") for z in self.zones}
            hi_since = {z["color"]: float("-inf") for z in self.zones}
            for i, b in enumerate(self.m):
                for z in self.zones:
                    c = z["color"]
                    lo_since[c], hi_since[c] = min(lo_since[c], b["l"]), max(hi_since[c], b["h"])
                if (b["t"].astimezone(ET).minute + 1) % self.step:
                    continue
                for z in self.zones:  # a 5-minute candle just closed
                    c = z["color"]
                    pos = _pos(b["c"], z)
                    touched = lo_since[c] <= z["high"] and hi_since[c] >= z["low"]
                    if pos != "inside" and origin[c] and (touched or prev[c] == "inside"):
                        out.append({"i": i, "zone": z, "long": pos == "above",
                                    "kind": "break" if pos != origin[c] else "bounce", "entry": b["c"]})
                    if pos != "inside":
                        origin[c] = pos
                    prev[c] = pos
                    lo_since[c], hi_since[c] = float("inf"), float("-inf")
        self._cands[rule] = out
        return out

    # ------------------------------------------------------------ the desk's 2-signal check at minute i
    def tags(self, i: int, zone: dict, long: bool) -> list[str]:
        key = (i, zone["color"], long)
        if key in self._tags:
            return self._tags[key]
        from signals import confluence_tags, rsi, session_vwap, volume_above_average
        from studies import auto, mxwll

        upto = self.m[: i + 1]
        price = upto[-1]["c"]
        closes = [b["c"] for b in (self.warm + upto)][-RSI_WARMUP:]
        r = rsi(closes, 14)
        vw = session_vwap(upto, "09:30")
        vol_up = volume_above_average([b["v"] for b in upto], 20)
        sk = ("study", i)
        if sk not in self._tags:
            tf = int(self.cfg["timeframe_minutes"])
            cand = (self.history + mxwll.resample(upto, tf, bool(self.cfg.get("regular_hours_only"))))[-HISTORY_CANDLES:]
            self._tags[sk] = mxwll.analyze(cand, self.cfg) if len(cand) > 30 else None
        z = {**zone, "color": "red" if long else "green"}  # the desk's tags point the trade's way
        st_tags = auto.zone_tags(self._tags[sk], [z], self.tol).get(z["color"])
        out = confluence_tags(z, price, r, vw, vol_up, st_tags)
        self._tags[key] = out
        return out

    # ------------------------------------------------------------ one variant's trades
    def trades(self, v: dict, start: str = "10:00", cutoff: str = "15:40", max_entries: int = 2) -> list[dict]:
        s_t, c_t = hhmm(start), hhmm(cutoff)
        out: list[dict] = []
        free_from = 0
        for cand in self.candidates(v["rule"]):
            if len(out) >= max_entries:
                break
            i = cand["i"]
            if i < free_from:
                continue
            close_t = (self.m[i]["t"].astimezone(ET) + timedelta(minutes=1)).time()
            if not (s_t <= close_t < c_t):
                continue
            if v["areas"] != "both" and cand["zone"]["color"] != v["areas"]:
                continue
            tags = self.tags(i, cand["zone"], cand["long"]) if v["min_tags"] else []
            if len(tags) < v["min_tags"]:
                continue
            px, why, j = exit_walk(self.m, i + 1, cand["entry"], cand["long"], v["stop"], v["target"], c_t)
            move = (px - cand["entry"]) if cand["long"] else (cand["entry"] - px)
            usd = move * PER_DOLLAR
            opt = option_pnl(cand["entry"], px, self.m[i]["t"] + timedelta(minutes=1), self.m[min(j, len(self.m) - 1)]["t"],
                             self.vol, cand["long"])
            out.append({"t": self.m[i]["t"].astimezone(ET).isoformat(timespec="minutes"), "zone": cand["zone"]["color"],
                        "kind": cand["kind"], "contract": "call" if cand["long"] else "put",
                        "entry": round(cand["entry"], 2), "exit": round(px, 2), "why": why, "tags": tags,
                        "usd": round(usd, 2), "net": round(usd - COST_PER_TRADE, 2), "opt": round(opt - COST_PER_TRADE, 2)})
            free_from = j + 1
        return out


def exit_walk(m: list[dict], i0: int, entry: float, long: bool, stop_pct: float, target_x: float,
              cutoff) -> tuple[float, str, int]:
    """Minute by minute from i0: the stop, the target (target_x × the stop distance) or the cutoff."""
    d = entry * stop_pct / 100
    stop = entry - d if long else entry + d
    tgt = (entry + target_x * d if long else entry - target_x * d) if target_x else None
    for j in range(i0, len(m)):
        k = m[j]
        if k["t"].astimezone(ET).time() >= cutoff:
            return k["o"], "close", j
        if (k["l"] <= stop) if long else (k["h"] >= stop):  # a minute that touches both counts as stopped
            return (min(k["o"], stop) if long else max(k["o"], stop)), "stop", j
        if tgt is not None and ((k["h"] >= tgt) if long else (k["l"] <= tgt)):
            return (max(k["o"], tgt) if long else min(k["o"], tgt)), "target", j
    last = m[-1] if m else None
    return (last["c"] if last else entry), "close", len(m) - 1


def summarize(trades: list[dict], field: str = "opt") -> dict:
    if not trades:
        return {"trades": 0, "total": 0.0, "per_trade": 0.0}
    usd = [t[field] for t in trades]
    wins = [u for u in usd if u > 0]
    losses = [u for u in usd if u <= 0]
    run = peak = dd = 0.0
    for u in usd:
        run += u
        peak = max(peak, run)
        dd = min(dd, run - peak)
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t["t"][:10]] = by_day.get(t["t"][:10], 0.0) + t[field]
    return {"trades": len(trades), "days_traded": len(by_day), "win_pct": round(100 * len(wins) / len(usd), 1),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "total": round(sum(usd), 2), "per_trade": round(sum(usd) / len(usd), 2),
            "gross": round(sum(t["usd"] for t in trades), 2),
            "delta_net": round(sum(t["net"] for t in trades), 2),
            "worst_day": round(min(by_day.values()), 2), "best_day": round(max(by_day.values()), 2),
            "max_drawdown": round(dd, 2), "stopped_pct": round(100 * sum(t["why"] == "stop" for t in trades) / len(trades), 1),
            "target_pct": round(100 * sum(t["why"] == "target" for t in trades) / len(trades), 1)}


def by_group(trades: list[dict]) -> dict:
    g: dict[str, list] = {}
    for t in trades:
        g.setdefault(f"{t['zone']} {t['kind']} {t['contract']}", []).append(t)
    return {k: summarize(v) for k, v in sorted(g.items())}


# ---------------------------------------------------------------- driving it over many days

def group_days(bars: list[dict], regular_hours_only: bool) -> dict[date, list[dict]]:
    lo, hi = (hhmm("09:30"), hhmm("16:00")) if regular_hours_only else (hhmm("04:00"), hhmm("20:00"))
    days: dict[date, list[dict]] = {}
    for b in sorted(bars, key=lambda x: x["t"]):
        t = b["t"].astimezone(ET)
        if t.weekday() < 5 and lo <= t.time() < hi:
            days.setdefault(t.date(), []).append(b)
    return days


def run(bars_by_day: dict[date, list[dict]], cfg: dict, train_share: float = 0.7, progress=None,
        vix: dict[date, float] | None = None) -> dict:
    """Every variant over every day; settings picked on the train days, judged on the test days."""
    from studies import mxwll

    tf, rth_only = int(cfg["timeframe_minutes"]), bool(cfg.get("regular_hours_only"))
    vs = variants()
    per_day: list[tuple[date, dict]] = []
    history: list[dict] = []
    warm: list[dict] = []
    zones_seen = 0
    ds = sorted(bars_by_day)
    for n, d in enumerate(ds):
        mins = bars_by_day[d]
        if len(mins) >= 60 and len(history) > int(cfg.get("aoi_lookback", 50)):
            vol = _vol_for(vix, d)
            day = Day(d, mins, warm, history, cfg, vol=vol)
            if day.zones:
                zones_seen += 1
                per_day.append((d, {vkey(v): day.trades(v) for v in vs}))
        history = (history + mxwll.resample(mins, tf, rth_only))[-HISTORY_CANDLES:]
        warm = (warm + mins)[-RSI_WARMUP:]
        if progress and n % 50 == 0:
            progress(n, len(ds))
    split = int(len(per_day) * train_share)
    train, test = per_day[:split], per_day[split:]

    def collect(part, key):
        return [t for _, res in part for t in res[key]]

    table = []
    for v in vs:
        k = vkey(v)
        table.append({"variant": v, "key": k, "train": summarize(collect(train, k)), "test": summarize(collect(test, k)),
                      "all": summarize(collect(per_day, k))})
    ranked = sorted((r for r in table if r["train"]["trades"] >= 30 and not r["variant"]["rule"].startswith("always")),
                    key=lambda r: r["train"]["total"], reverse=True)
    pick = ranked[0] if ranked else None
    named = {"desk_now": vkey(DESK_NOW), "option_3": vkey(OPTION_3), "always_call": vkey(BENCHMARKS[0]),
             "always_put": vkey(BENCHMARKS[1])}
    out = {
        "days": len(per_day), "first": per_day[0][0].isoformat() if per_day else None,
        "last": per_day[-1][0].isoformat() if per_day else None,
        "train_days": len(train), "test_days": len(test),
        "train_range": [train[0][0].isoformat(), train[-1][0].isoformat()] if train else None,
        "test_range": [test[0][0].isoformat(), test[-1][0].isoformat()] if test else None,
        "cost_per_trade": COST_PER_TRADE, "per_dollar": PER_DOLLAR,
        "picked": pick["key"] if pick else None,
        "named": {name: next(r for r in table if r["key"] == k) for name, k in named.items()},
        "top_train": ranked[:10],
        "picked_groups": by_group(collect(test, pick["key"])) if pick else {},
        "desk_now_groups": by_group(collect(per_day, named["desk_now"])),
        "option_3_groups": by_group(collect(per_day, named["option_3"])),
        "table": table,
    }
    if pick:
        out["named"]["picked"] = pick
    years = sorted({d.year for d, _ in per_day})
    out["by_year"] = {name: {y: summarize([t for d, res in per_day if d.year == y for t in res[r["key"]]])
                             for y in years} for name, r in out["named"].items()}
    return out


def _vol_for(vix: dict[date, float] | None, d: date) -> float:
    """The VIX's last close before day d, as a decimal; 16 % when there is none."""
    if not vix:
        return 0.16
    prior = [k for k in vix if k < d]
    return vix[max(prior)] / 100 if prior else 0.16


def markdown(res: dict) -> str:
    def row(name, r):
        a, b = r["train"], r["test"]
        return (f"| {name} | {r['key']} | {a['trades']} | {a.get('win_pct', '—')}% | ${a['total']:,.0f} | "
                f"{b['trades']} | {b.get('win_pct', '—')}% | ${b['total']:,.0f} | ${b['per_trade']:,.2f} | ${b.get('max_drawdown', 0):,.0f} |")

    lines = ["# Call/put area backtest", "",
             f"{res['days']} trading days with areas, {res['first']} to {res['last']}. "
             f"Train {res['train_range']}, test {res['test_range']}. Option priced with Black-Scholes at that day's VIX "
             f"(time decay in), after ${res['cost_per_trade']:.0f} a trade.", "",
             "| | Settings | Train trades | Train won | Train net | Test trades | Test won | Test net | Test per trade | Test worst drop |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, label in (("desk_now", "Desk today"), ("option_3", "Option 3"), ("picked", "Picked on train"),
                        ("always_call", "Benchmark: call at 10:00 daily"), ("always_put", "Benchmark: put at 10:00 daily")):
        if name in res["named"]:
            lines.append(row(label, res["named"][name]))
    lines += ["", "Top 10 on the train days, and how they did on the test days:", "",
              "| Settings | Train net | Test trades | Test won | Test net |", "|---|---|---|---|---|"]
    for r in res["top_train"]:
        lines.append(f"| {r['key']} | ${r['train']['total']:,.0f} | {r['test']['trades']} | {r['test'].get('win_pct', '—')}% | ${r['test']['total']:,.0f} |")
    yrs = sorted({y for v in res.get("by_year", {}).values() for y in v})
    if yrs:
        lines += ["", "Net by year (option priced, after costs):", "", "| | " + " | ".join(str(y) for y in yrs) + " |",
                  "|---|" + "---|" * len(yrs)]
        for name, label in (("desk_now", "Desk today"), ("option_3", "Option 3"), ("picked", "Picked"),
                            ("always_call", "Call at 10:00"), ("always_put", "Put at 10:00")):
            if name in res["by_year"]:
                lines.append(f"| {label} | " + " | ".join(f"${res['by_year'][name][y]['total']:,.0f} ({res['by_year'][name][y]['trades']})" for y in yrs) + " |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- data

def alpaca_minutes(since: date, until: date, symbol: str = "SPY") -> list[dict]:
    """1-minute IEX bars from Alpaca (the desk's own feed), a month at a time. Keys from the environment."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    from alpaca_client import _keys

    key, secret = _keys()
    client = StockHistoricalDataClient(key, secret)
    out: list[dict] = []
    a = since
    while a < until:
        b = min(until, a + timedelta(days=31))
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame(1, TimeFrameUnit.Minute),
                               start=datetime(a.year, a.month, a.day, tzinfo=ET), end=datetime(b.year, b.month, b.day, tzinfo=ET),
                               feed=DataFeed.IEX)
        for x in client.get_stock_bars(req).data.get(symbol, []):
            out.append({"t": x.timestamp.astimezone(ET), "o": float(x.open), "h": float(x.high), "l": float(x.low),
                        "c": float(x.close), "v": float(x.volume)})
        print(f"  {a} → {b}: {len(out):,} bars so far", flush=True)
        a = b
    return out


def vix_closes(since: date) -> dict[date, float]:
    """Daily VIX closes (yfinance); empty when unavailable (the backtest then uses 16 %)."""
    try:
        import yfinance as yf

        df = yf.download("^VIX", start=since.isoformat(), interval="1d", progress=False, auto_adjust=False,
                         multi_level_index=False)
        return {ts.date(): float(r["Close"]) for ts, r in df.iterrows()}
    except Exception:  # noqa: BLE001
        return {}


def yfinance_minutes(symbol: str = "SPY") -> list[dict]:
    import yfinance as yf

    df = yf.download(symbol, period="7d", interval="1m", prepost=True, progress=False, auto_adjust=False,
                     multi_level_index=False)
    return [{"t": ts.to_pydatetime().astimezone(ET), "o": float(r["Open"]), "h": float(r["High"]), "l": float(r["Low"]),
             "c": float(r["Close"]), "v": float(r["Volume"])} for ts, r in df.iterrows()]


def main(argv: list[str] | None = None) -> int:
    from studies import auto

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=("alpaca", "yfinance"), default="yfinance")
    ap.add_argument("--since", default="2020-01-01", help="first day (Alpaca)")
    ap.add_argument("--json", help="write the full result here")
    ap.add_argument("--md", help="write the summary table here (Markdown)")
    a = ap.parse_args(argv)
    cfg = auto.desk_config()
    if a.source == "alpaca":
        bars = alpaca_minutes(date.fromisoformat(a.since), datetime.now(ET).date())
    else:
        bars = yfinance_minutes()
    print(f"{len(bars):,} one-minute bars", flush=True)
    days = group_days(bars, bool(cfg.get("regular_hours_only")))
    del bars
    vix = vix_closes(min(days) - timedelta(days=10)) if days else {}
    print(f"{len(vix):,} VIX closes", flush=True)
    res = run(days, cfg, progress=lambda n, t: print(f"  day {n} of {t}", flush=True), vix=vix)
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
