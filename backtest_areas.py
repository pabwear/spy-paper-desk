"""Backtest: how the call and put areas would have traded under each entry rule.

Research only: nothing here places orders or changes the desk. Uses the desk's own 09:39 Mxwll read
(Areas of Interest on its timeframe and session) and its trade rules: entries 10:00–15:40 ET, at most
2 a day and one at a time, a stop 0.35 % away on SPY, flat from 15:40.

Rules compared:
    touch       today's rule: a call when price reaches the red area, a put at the green area
    confirm     Roy's option 3: wait for a candle to close after touching an area. Closed through it →
                trade the breakout; closed back on the side it came from → trade the bounce
    confirm_t1  confirm, and take profit at Target 1 (1× the stop distance)

Option results use the desk's estimate: an at-the-money option moves about $50 per $1 of SPY per
contract. Time decay, spreads and fills are left out, so real results would be lower.

    python3 backtest_areas.py [--days 60]      # downloads 5-minute SPY bars (yfinance), prints the table
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta

from common import ET, hhmm

RULES = ("touch", "confirm", "confirm_t1")
PER_DOLLAR = 50.0  # option dollars per $1 of SPY, delta 0.5 × 100 shares


def day_zones(candles: list[dict], cfg: dict) -> list[dict]:
    """The visible red and green Areas of Interest from the candles before the snapshot."""
    from studies import mxwll

    if len(candles) <= int(cfg.get("aoi_lookback", 50)):
        return []
    a = (mxwll.analyze(candles, cfg) or {}).get("aoi")
    if not a:
        return []
    return [{"color": k, "low": a[k]["low"], "high": a[k]["high"]} for k in ("red", "green") if a[k]["visible"]]


def _pos(price: float, z: dict) -> str:
    return "below" if price < z["low"] else "above" if price > z["high"] else "inside"


def _exit(candles: list[dict], i0: int, entry: float, long: bool, stop_pct: float, target: bool,
          cutoff) -> tuple[float, str, datetime]:
    """Walk forward from candle i0 (the first candle after entry) to the stop, the target or the cutoff."""
    d = entry * stop_pct / 100
    stop, tgt = (entry - d, entry + d) if long else (entry + d, entry - d)
    last = None
    for k in candles[i0:]:
        t = k["t"].astimezone(ET)
        if t.time() >= cutoff:
            return k["o"], "close", t
        hit_stop = k["l"] <= stop if long else k["h"] >= stop
        hit_tgt = target and (k["h"] >= tgt if long else k["l"] <= tgt)
        if hit_stop:  # a candle that touches both counts as stopped: the cautious reading
            px = min(k["o"], stop) if long else max(k["o"], stop)
            return px, "stop", t
        if hit_tgt:
            px = max(k["o"], tgt) if long else min(k["o"], tgt)
            return px, "target", t
        last = k
    return (last["c"], "close", last["t"].astimezone(ET)) if last else (entry, "close", None)


def simulate_day(candles: list[dict], zones: list[dict], rule: str, stop_pct: float = 0.35,
                 tol_pct: float = 0.15, start: str = "10:00", cutoff: str = "15:40", max_entries: int = 2,
                 minutes: int = 5) -> list[dict]:
    """One day's trades. `candles` are that day's candles (start times, ET), oldest first."""
    s_t, c_t = hhmm(start), hhmm(cutoff)
    trades: list[dict] = []
    if not zones:
        return trades
    origin = {z["color"]: None for z in zones}
    prev_pos = {z["color"]: None for z in zones}
    i, n = 0, len(candles)
    while i < n and len(trades) < max_entries:
        k = candles[i]
        t = k["t"].astimezone(ET)
        close_t = (t + timedelta(minutes=minutes)).time()
        signal = None
        if rule == "touch":
            if s_t <= t.time() < c_t:
                best = None
                for z in zones:
                    mid = (z["low"] + z["high"]) / 2
                    lo, hi = min(z["low"], mid * (1 - tol_pct / 100)), max(z["high"], mid * (1 + tol_pct / 100))
                    if k["l"] <= hi and k["h"] >= lo:
                        entry = min(max(k["o"], lo), hi)
                        dist = abs(entry - k["o"])
                        if best is None or dist < best[0]:
                            best = (dist, z, entry)
                if best:
                    _, z, entry = best
                    signal = {"zone": z["color"], "long": z["color"] == "red", "entry": entry, "kind": "touch",
                              "next": i + 1}
        else:
            for z in zones:
                c = z["color"]
                pos = _pos(k["c"], z)
                touched = k["l"] <= z["high"] and k["h"] >= z["low"]
                if (signal is None and pos != "inside" and origin[c] and (touched or prev_pos[c] == "inside")
                        and s_t <= close_t <= c_t):
                    kind = "break" if pos != origin[c] else "bounce"
                    signal = {"zone": c, "long": pos == "above", "entry": k["c"], "kind": kind, "next": i + 1}
                if pos != "inside":
                    origin[c] = pos
                prev_pos[c] = pos
        if signal:
            px, why, out_t = _exit(candles, signal["next"], signal["entry"], signal["long"], stop_pct,
                                   rule == "confirm_t1", c_t)
            move = (px - signal["entry"]) if signal["long"] else (signal["entry"] - px)
            trades.append({"t": t.isoformat(timespec="minutes"), "zone": signal["zone"], "kind": signal["kind"],
                           "contract": "call" if signal["long"] else "put", "entry": round(signal["entry"], 2),
                           "exit": round(px, 2), "why": why, "move": round(move, 3),
                           "usd": round(move * PER_DOLLAR, 2)})
            if out_t is None:
                break
            # flat again: carry on from the candle after the exit
            while i < n and candles[i]["t"].astimezone(ET) <= out_t:
                i += 1
            if rule != "touch":  # a fresh look at each area after a trade
                for z in zones:
                    p = _pos(candles[i - 1]["c"], z) if i else None
                    origin[z["color"]] = p if p != "inside" else origin[z["color"]]
                    prev_pos[z["color"]] = p
            continue
        i += 1
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    usd = [t["usd"] for t in trades]
    wins = [u for u in usd if u > 0]
    losses = [u for u in usd if u <= 0]
    run = peak = dd = 0.0
    for u in usd:
        run += u
        peak = max(peak, run)
        dd = min(dd, run - peak)
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t["t"][:10]] = by_day.get(t["t"][:10], 0.0) + t["usd"]
    return {"trades": len(trades), "win_pct": round(100 * len(wins) / len(usd), 1),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "total": round(sum(usd), 2), "per_trade": round(sum(usd) / len(usd), 2),
            "worst_day": round(min(by_day.values()), 2), "best_day": round(max(by_day.values()), 2),
            "max_drawdown": round(dd, 2), "stopped_pct": round(100 * sum(t["why"] == "stop" for t in trades) / len(trades), 1)}


def run(candles: list[dict], cfg: dict, stop_pct: float = 0.35) -> dict:
    """Every rule over every day in `candles` (5-minute, ET, extended hours as the desk reads them)."""
    days = sorted({k["t"].astimezone(ET).date() for k in candles})
    snap = hhmm(cfg.get("snapshot_time", "09:39"))
    out = {r: [] for r in RULES}
    zones_by_day = {}
    for d in days:
        if d.weekday() >= 5:
            continue
        # the read the desk has at 09:39: candles that closed by then (no peeking at the forming one)
        cut = datetime(d.year, d.month, d.day, snap.hour, snap.minute, tzinfo=ET)
        before = [k for k in candles if k["t"] + timedelta(minutes=int(cfg["timeframe_minutes"])) <= cut]
        today = [k for k in candles if k["t"].astimezone(ET).date() == d]
        if not today or len(before) <= int(cfg.get("aoi_lookback", 50)):
            continue
        zones = day_zones(before, cfg)
        zones_by_day[d.isoformat()] = zones
        for r in RULES:
            out[r] += simulate_day(today, zones, r, stop_pct, minutes=int(cfg["timeframe_minutes"]))
    return {"days": len(zones_by_day), "first": min(zones_by_day, default=None), "last": max(zones_by_day, default=None),
            "zones": zones_by_day, "trades": out, "summary": {r: summarize(out[r]) for r in RULES}}


def _download(days: int, minutes: int) -> list[dict]:
    import yfinance as yf

    df = yf.download("SPY", period=f"{min(days, 59)}d", interval=f"{minutes}m", prepost=True, progress=False,
                     auto_adjust=False, multi_level_index=False)
    out = []
    for ts, r in df.iterrows():
        t = ts.to_pydatetime().astimezone(ET)
        if not (4 <= t.hour < 20):
            continue
        out.append({"t": t, "o": float(r["Open"]), "h": float(r["High"]), "l": float(r["Low"]),
                    "c": float(r["Close"]), "v": float(r["Volume"])})
    return out


def main(argv: list[str] | None = None) -> int:
    from studies import auto
    from common import load_json

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--json", help="also write the full result here")
    a = ap.parse_args(argv)
    cfg = auto.desk_config()
    candles = _download(a.days, int(cfg["timeframe_minutes"]))
    res = run(candles, cfg, float((load_json("rules.json", {}) or {}).get("stop_underlying_pct", 0.35)))
    print(f"{res['days']} trading days, {res['first']} to {res['last']}")
    for r in RULES:
        print(r, json.dumps(res["summary"][r]))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
