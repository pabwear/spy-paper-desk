"""Paper desk runner. Trading books (rules.json books) run side by side: SPY shares, SNDK shares, SPY options.

    python3 run_study.py eval                 # evaluate and log. NEVER sends an order.
    python3 run_study.py paper                # manage exits, then at most one gated paper entry per book
    python3 run_study.py manage               # exits only: 0.35% stop, flatten from risk.json flatten_start
    python3 run_study.py sync                 # pull paper fills into trades.csv, refresh account.json
    python3 run_study.py review               # 4:15 PM ET end-of-day review + learning
    python3 run_study.py learn                # retrain the learner and print what it found
    python3 run_study.py aoi set --zone red:571.20:572.05 --zone green:578.40:579.10 [--tag 1:CHoCH]
    python3 run_study.py aoi clear --reason "Mxwll boxes not readable by 09:55"
    python3 run_study.py pulse set bearish --note "CPI hot; yields up"

Every run writes to journal.jsonl and rebuilds the console state.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta

import instruments
import journal
import learning
import pulse
import rebuild_dashboard
import re

from common import (ET, SYMBOL_RE, aoi_file, focus_symbols, hhmm, load_json, now_et, save_json, session_state,
                    to_et, watchlist)
from gate import check_exit, check_order, failures, is_approximate, override_freshness, passed
from studies import auto as auto_study
from signals import bars_today, pulse_contradicts, rank_zones, rsi, session_vwap, volume_above_average

# Checks only the paper account can answer. `eval` never asks it, so these show as "checked at submit".
BROKER_CHECKS = {"paper_client", "paper_1000_account", "market_clock", "one_position", "option_contract"}
CLOSED_STATES = {"weekend": "weekend", "pre_open": "before_open", "after_close": "after_close",
                 "flatten_window": "flatten_window"}


def load_all(symbol: str = "SPY") -> dict:
    return {
        "config": load_json("alpaca_config.json", {}),
        "rules": load_json("rules.json", {}),
        "risk": load_json("risk.json", {}),
        "watch": watchlist(),
        "override": load_json(aoi_file(symbol), None),
        "pulse": load_json("market_pulse.json", {}),
        "weights": load_json("learning_weights.json", {}),
        "account": load_json("account.json", {}),
    }


def pulse_today(pulse: dict, now: datetime) -> str | None:
    if not pulse or pulse.get("date") != now.astimezone(ET).date().isoformat():
        return None
    bias = pulse.get("bias")
    return bias if bias in ("bullish", "bearish", "neutral") else None


def _positions_from_account(account: dict) -> list[dict] | None:
    if account.get("positions") is not None:
        return account["positions"]
    return [account["position"]] if account.get("position") else None


class Bars:
    """Minute bars per symbol, fetched at most once per run.

    Tests and demos inject bars: a list means SPY's bars, a dict maps symbol → bars. With bars
    injected and no fetch function, nothing touches the network.
    """

    def __init__(self, now: datetime, bars=None, fetch=None, minutes: int = 1):
        self.now, self.minutes = now, minutes
        self.fetch = fetch
        self.cache: dict[str, list | None] = {}
        self.errors: dict[str, str] = {}
        if isinstance(bars, dict):
            self.cache.update(bars)
        elif bars is not None:
            self.cache["SPY"] = bars
        self.offline = bars is not None and fetch is None
        self.memo: dict = {}  # per-heartbeat results reused by every book on the same stock (the study read)

    def get(self, symbol: str) -> tuple[list | None, str | None]:
        if symbol not in self.cache:
            if self.offline:
                return None, "no bars supplied"
            try:
                self.cache[symbol] = (self.fetch or _fetch)(self.now, self.minutes, symbol)
            except Exception as e:  # noqa: BLE001 - no data means no trade
                self.cache[symbol] = None
                self.errors[symbol] = str(e)[:300]
        return self.cache[symbol], self.errors.get(symbol)

    def history(self, symbol: str, unit: str) -> list | None:
        """Longer history ("30Min" or "1Day") for the chart's bigger timeframes. Injected as "SYM|unit" in tests."""
        key = f"{symbol}|{unit}"
        if key not in self.cache:
            if self.offline or self.fetch is not None:  # injected data: never reach for the network
                return None
            try:
                from alpaca_client import fetch_history

                self.cache[key] = fetch_history(self.now, symbol, unit)
            except Exception as e:  # noqa: BLE001 - no history means fewer timeframes, never a failed run
                self.cache[key] = None
                self.errors[key] = str(e)[:300]
        return self.cache[key]


def _bars(now: datetime, bars, fetch) -> Bars:
    if isinstance(bars, Bars):
        return bars
    minutes = int((load_json("learning_weights.json", {}) or {}).get("indicators", {}).get("timeframe_minutes", 1))
    return Bars(now, bars, fetch, minutes)


def market_read(now: datetime, source: Bars, symbol: str, weights: dict, risk: dict) -> tuple[dict | None, str | None]:
    bars, err = source.get(symbol)
    if not bars:
        return None, err or "no bars"
    today = bars_today(bars, now)
    if not today:
        return None, "no bars for today"
    ind = weights.get("indicators", {})
    rsi_v = rsi([b["c"] for b in bars], int(ind.get("rsi_period", 14)))
    vwap = session_vwap(today, risk["rth_open"])
    return {
        "price": round(today[-1]["c"], 4),
        "bar_time": today[-1]["t"].astimezone(ET).isoformat(timespec="seconds"),
        "rsi": round(rsi_v, 2) if rsi_v is not None else None,
        "vwap": round(vwap, 4) if vwap is not None else None,
        "volume_above_avg": volume_above_average([b["v"] for b in today], int(ind.get("volume_avg_bars", 20))),
    }, None


def study_read(now: datetime, source: Bars, symbol: str, rules: dict) -> dict | None:
    """The ported Mxwll study on this symbol's chart right now. A study problem never stops the desk."""
    memo = getattr(source, "memo", None)
    key = ("study", symbol)
    if memo is not None and key in memo:
        return memo[key]
    try:
        out = auto_study.read(source.get(symbol)[0], now, auto_study.desk_config())
    except Exception as e:  # noqa: BLE001
        out = {"error": f"{type(e).__name__}: {e}"[:200]}
    if memo is not None:
        memo[key] = out
    return out


def book_stop_pct(book: dict, source: Bars, symbol: str, now: datetime) -> tuple[float | None, str]:
    """This book's stop, in % of the stock against the entry: a fixed % or a share of the stock's average daily
    range over the finished days before today (signals.range_stop_pct)."""
    from signals import range_stop_pct

    stop = book.get("stop") or {}
    if stop.get("pct") is not None:
        return float(stop["pct"]), f"{float(stop['pct']):g}% (fixed)"
    if stop.get("range_fraction") is not None:
        today = now.astimezone(ET).date()
        days = [b for b in (source.history(symbol, "1Day") or []) if b["t"].astimezone(ET).date() < today]
        n = int(stop.get("days", 20))
        pct = range_stop_pct(days, float(stop["range_fraction"]), n)
        if pct is None:
            return None, f"Not enough daily history to size {symbol}'s stop ({len(days)} days; it needs {min(n, 10)})."
        return pct, f"{pct:g}% = {float(stop['range_fraction']):g} × {symbol}'s average daily range over {n} days"
    return None, f"{book['id']} has no stop rule"


def book_entries_on(events: list[dict], day, book: dict, all_books: list[dict]) -> int:
    """Entries this book made on this day (entries logged before books carry no book: matched by their symbol)."""
    n = 0
    for e in journal.events_on(events, day):
        if e.get("event") != "order" or e.get("role") != "entry":
            continue
        owner = e.get("book") or (instruments.book_of(e.get("symbol") or "", all_books) or {}).get("id")
        n += owner == book["id"]
    return n


def cmd_auto_zones(now: datetime, source: Bars) -> list[str]:
    """After the snapshot (09:39 ET), compute today's Mxwll zones for each focus symbol that has none for today.

    Zones Ops published today (or a clear) are never replaced. Returns the symbols written.
    """
    cfg = auto_study.desk_config()
    if not cfg.get("auto_zones"):
        return []
    written = []
    for sym in focus_symbols():
        current = load_json(aoi_file(sym), None)
        when = to_et((current or {}).get("written_at"))
        if when is not None and when.date() == now.astimezone(ET).date():
            continue
        try:
            data = auto_study.zones_file(sym, now, source.get(sym)[0], cfg)
        except Exception as e:  # noqa: BLE001
            journal.log("study_failed", now=now, symbol=sym, error=f"{type(e).__name__}: {e}"[:300])
            continue
        if not data:
            continue
        save_json(aoi_file(sym), data)
        journal.log("aoi", now=now, symbol=sym, tradable=data["tradable"], zones=data["zones"],
                    source=data["source"], approximate=data["approximate"], as_of=data["written_at"])
        written.append(sym)
    return written


def evaluate(now: datetime, bars=None, *, symbol: str = "SPY", positions: list[dict] | None = None, fetch=None,
             account_number: str | None = None, market_open: bool | None = None, client_is_paper: bool = True,
             base_url: str | None = None, open_orders: int | None = None, contract_picker=None, quote_picker=None,
             buying_power: float | None = None, book: dict | None = None, money: float | None = None) -> dict:
    """Decide on an entry for one book (one stock, shares or options). Pure decision logic over the desk files,
    the bars and what the caller passes in. book None: the stock's first book (none → watch only).

    positions / open_orders: this book's own. money: what the account can spend on it (cash for shares, options
    buying power for options; buying_power is the older name for it). Never orders. contract_picker(right, price,
    today, underlying) -> listed contracts; only `paper` passes one.
    """
    f = load_all(symbol)
    risk, rules, override, weights, watch = f["risk"], f["rules"], f["override"], f["weights"], f["watch"]
    state = session_state(now, risk)
    all_books = instruments.books(rules, watch)
    if book is None:
        book = next((b for b in all_books if b["symbol"] == symbol), None)
    if money is None:
        money = buying_power
    result: dict = {"symbol": symbol, "session": state, "decision": "skip", "reasons": [],
                    "instrument": book["id"] if book else None, "book": book["id"] if book else None}

    if state in CLOSED_STATES:
        result["reasons"] = [CLOSED_STATES[state]]
        return result

    source = _bars(now, bars, fetch)
    market, err = market_read(now, source, symbol, weights, risk)
    if market is None:
        result.update(reasons=["no_market_data"], error=err)
        return result
    result["market"] = market
    price = market["price"]

    zones = (override or {}).get("zones") or []
    tol = float(risk.get("zone_midpoint_tolerance_pct", 0.15))
    study = study_read(now, source, symbol, rules)
    result["study"] = study
    ranked = rank_zones(zones, price, market["rsi"], market["vwap"], market["volume_above_avg"],
                        weights.get("weights", {}), tol, weights.get("learned_multipliers"),
                        study_tags=auto_study.zone_tags(study, zones, tol))
    result["zones"] = ranked
    fresh, fresh_why = override_freshness(override, now, risk)
    result["aoi"] = {"tradable": bool(override and override.get("tradable")), "fresh": fresh,
                     "freshness": fresh_why, "approximate": is_approximate(override)}

    if state == "watch_only":
        result.update(decision="watch", reasons=["watch_only"])
        return result
    if not zones:
        result["reasons"] = ["no_aoi"]
        return result
    best = next((r for r in ranked if r["near"] and r["side"]), None)
    if best is None:
        result["reasons"] = ["outside_zones"]
        return result

    signal, zone, tags = best["side"], best["zone"], best["tags"]
    result.update(candidate=best, signal=signal)
    if book is None or not book["enabled"]:
        result.update(reasons=["instrument_off"], note=book["why"] if book else f"{symbol} is watch only")
        return result

    events = journal.read_events()
    entries_today = book_entries_on(events, now.astimezone(ET).date(), book, all_books)
    bias = pulse_today(f["pulse"], now)
    result["pulse_bias"] = bias
    plan = instruments.plan_book_entry(book, signal, price, money, rules)
    notes = []
    if plan["asset"] == "shares" and signal != "buy":
        result.update(reasons=["no_short"], plan=plan, note=f"The {book['label']} book only buys; no short selling.")
        return result
    if bias and pulse_contradicts(signal, bias):
        pc = risk.get("pulse_contradiction", {"action": "reduce", "size_factor": 0.5})
        if plan["asset"] == "option" or pc.get("action") == "skip":
            # A contract cannot be cut in half, so a contradicting pulse means skip.
            result.update(reasons=["pulse_contradicts"],
                          note=f"Market Pulse is {bias}; {signal} in {zone['color']} skipped.")
            return result
        factor = float(pc.get("size_factor", 0.5))
        plan["qty"] = int(plan["qty"] * factor)
        plan["notional"] = round(plan["qty"] * price, 2)
        if plan["qty"] < 1:  # halving one whole share leaves nothing to buy
            result.update(reasons=["pulse_contradicts"], plan=plan,
                          note=f"Market Pulse is {bias}; size × {factor} leaves under one {symbol} share.")
            return result
        notes.append(f"Market Pulse is {bias} against a {signal}; size × {factor}.")
    if plan["asset"] == "shares" and plan["qty"] < 1:
        room = min(float(book.get("budget_usd") or 0), money if money is not None else float("inf"))
        result.update(reasons=["budget_too_small"], plan=plan,
                      note=f"One {symbol} share costs ${price:,.2f}; the {book['label']} book can spend ${room:,.2f}.")
        return result
    stop_pct, stop_why = book_stop_pct(book, source, symbol, now)
    if stop_pct is None:
        result.update(reasons=["no_stop"], plan=plan, note=stop_why)
        return result
    exposure = "long" if signal == "buy" else "short"  # a buy is shares or a call; a sell is a put (bets on a fall)
    plan.update(stop_pct=stop_pct, stop_level=instruments.stop_level(exposure, price, stop_pct), stop_rule=stop_why)

    if plan["asset"] == "option" and contract_picker is not None:
        today = now.astimezone(ET).date()
        try:
            listed = contract_picker(plan["right"], price, today, symbol)
        except Exception as e:  # noqa: BLE001
            result.update(reasons=["no_contract"], plan=plan, error=f"contract lookup: {type(e).__name__}: {e}"[:300])
            return result
        opt = rules.get("option", {})
        target_days, min_days = int(opt.get("expiry_target_days", 0) or 0), int(opt.get("expiry_min_days", 0) or 0)
        max_cost = min(float(opt.get("max_cost_usd") or book.get("budget_usd") or 1000),
                       float(book.get("budget_usd") or opt.get("max_cost_usd") or 1000))
        if money is not None and not positions:
            # one contract must fit the money in the account, not just the rule (while one is open the
            # cash is in it, and one_position is the reason to wait)
            max_cost = min(max_cost, float(money))
        asks = {}
        if target_days:
            # the nearest-dollar strike on each expiry in the window, priced before choosing
            near = {}
            for c in listed:
                k = c["expiry"]
                if k not in near or abs(c["strike"] - round(price)) < abs(near[k]["strike"] - round(price)):
                    near[k] = c
            try:
                asks = quote_picker([c["symbol"] for c in near.values()]) if quote_picker else {}
            except Exception as e:  # noqa: BLE001
                result.update(reasons=["no_contract"], plan=plan, error=f"option quotes: {type(e).__name__}: {e}"[:300])
                return result
        picked = instruments.pick_contract(listed, price, plan["right"], today, target_days, min_days, max_cost, asks)
        if picked is None:
            why = (f"No {symbol} {plan['right']} at least {min_days} days out costs ${max_cost:,.0f} or less right now."
                   if target_days else f"No listed {symbol} contract for that right.")
            result.update(reasons=["no_contract"], plan=plan, note=why)
            return result
        plan.update(symbol=picked["symbol"], strike=picked["strike"], expiry=picked["expiry"].isoformat(),
                    expiry_is_nearest=picked["expiry_is_nearest"], strike_is_nearest=picked["strike_is_nearest"],
                    expiry_ok=picked["expiry_ok"], expiry_rule=picked["expiry_rule"], ask=picked.get("ask"),
                    cost=picked.get("cost"))

    features = learning.features_at_entry(signal=signal, tags=tags, market=market, ranked=best, now=now,
                                          pulse_bias=bias, entries_today=entries_today)
    model = learning.load_model()
    p_loss = learning.predict(model, features)
    veto, veto_why = learning.may_veto(model, p_loss, learning.ml_config())
    result.update(plan=plan, features=features, p_loss=p_loss, ml=veto_why, entries_today=entries_today)

    if positions is None:
        positions = _positions_from_account(f["account"])
    checks = check_order(
        now=now, base_url=base_url or f["config"].get("base_url", ""), client_is_paper=client_is_paper,
        config=f["config"], rules=rules, risk=risk, override=override, plan=plan, price=price, zone=zone,
        tags=tags, account_number=account_number or f["account"].get("account_number"), market_open=market_open,
        entries_today=entries_today, open_positions=positions, open_orders=open_orders, watch=watch, book=book,
    )
    result.update(gate=[c.to_dict() for c in checks], note=" ".join(notes))
    failed = failures(checks)
    if veto and not failed:
        result["reasons"] = ["ml_veto"]
        result["note"] = (result["note"] + f" Learner veto: {veto_why}.").strip()
        return result
    if passed(checks):
        result.update(decision="enter", reasons=[])
    elif set(failed) <= BROKER_CHECKS:
        result.update(decision="would_enter", reasons=[])
    else:
        result["reasons"] = [r for r in failed if r not in BROKER_CHECKS]
    return result


def _fetch(now: datetime, minutes: int, symbol: str = "SPY") -> list[dict]:
    from alpaca_client import fetch_bars

    return fetch_bars(now, minutes, symbol)


def evaluate_focus(now: datetime, bars=None, fetch=None, **kw) -> list[dict]:
    """Evaluate every book of every focus symbol, in focus order (a watch-only stock once, with no book)."""
    source = _bars(now, bars, fetch)
    rules, watch = load_json("rules.json", {}), watchlist()
    return [evaluate(now, source, symbol=sym, book=b, **kw)
            for sym in focus_symbols() for b in (instruments.books_for(sym, rules, watch) or [None])]


def pick(results: list[dict]) -> dict:
    """The setup to act on: an enter beats a would-enter; higher confluence score wins; focus order breaks ties."""
    rank = {"enter": 0, "would_enter": 1}
    live = [r for r in results if r["decision"] in rank]
    if live:
        return min(live, key=lambda r: (rank[r["decision"]], -float((r.get("candidate") or {}).get("score") or 0)))
    return results[0] if results else {"symbol": None, "session": "?", "decision": "skip", "reasons": ["no_focus"]}


def _log_eval(now: datetime, mode: str, r: dict) -> dict:
    event = "skip" if r["decision"] == "skip" else "eval"
    cand = r.get("candidate") or {}
    plan = r.get("plan") or {}
    return journal.log(
        event, now=now, mode=mode, symbol=r.get("symbol"), book=r.get("book"), session=r["session"],
        decision=r["decision"], reasons=r["reasons"],
        instrument=r.get("instrument"), signal=r.get("signal"), market=r.get("market"), aoi=r.get("aoi"),
        plan={k: plan.get(k) for k in ("asset", "symbol", "right", "contracts", "target_strike", "strike", "expiry",
                                         "order_side", "qty", "notional", "cost", "stop_pct", "stop_level")}
        if plan else None,
        zone=cand.get("zone"), tags=cand.get("tags"), score=cand.get("score"), pulse_bias=r.get("pulse_bias"),
        p_loss=r.get("p_loss"), ml=r.get("ml"), entries_today=r.get("entries_today"),
        gate=r.get("gate"), note=r.get("note"), error=r.get("error"),
        study={k: (r.get("study") or {}).get(k) for k in ("as_of", "aoi", "internal", "external", "error")}
        if r.get("study") else None,
        zones=[{"color": z["zone"].get("color"), "low": z["zone"].get("low"), "high": z["zone"].get("high"),
                "near": z["near"], "distance_pct": z["distance_pct"], "tags": z["tags"], "score": z["score"]}
               for z in r.get("zones", [])],
    )


def describe(r: dict) -> str:
    sym = (r.get("symbol") or "—") + (f" [{r['book']}]" if r.get("book") else "")
    if r["decision"] == "skip":
        return f"{sym}: PASS — {', '.join(r['reasons'])}" + (f". {r['note']}" if r.get("note") else "")
    if r["decision"] == "watch":
        return f"{sym}: WATCH ONLY (09:30–09:59) — logged, no orders"
    z, plan = r["candidate"]["zone"], r["plan"]
    head = {"enter": "ENTER", "would_enter": "WOULD ENTER (eval only)"}[r["decision"]]
    if plan["asset"] == "option":
        what = (f"buy 1 {sym} {plan['right']} "
                + (plan["symbol"] if plan.get("symbol") else f"(nearest expiry, strike ≈ {plan['target_strike']:g})"))
    else:
        what = f"{plan['order_side']} {plan['qty']} {sym} ≈ ${plan['notional']:,.2f}"
    ml = f"; P(loss) {r['p_loss']:.0%}" if r.get("p_loss") is not None else ""
    return (f"{sym}: {head} — {what} on a {r['signal']} in {z['color']} {z['low']}–{z['high']}; "
            f"tags {', '.join(r['candidate']['tags'])}{ml}")


# ------------------------------------------------------------------ exits

def latest_entry(symbol: str, events: list[dict]) -> dict:
    for e in reversed(events):
        if e.get("event") == "order" and e.get("role") == "entry" and e.get("symbol") == symbol:
            return e
    return {}


def desk_underlyings(events: list[dict]) -> set[str]:
    entered = [e.get("symbol") for e in events if e.get("event") == "order" and e.get("role") == "entry"]
    return instruments.desk_underlyings(load_json("rules.json", {}), watchlist(), entered)


def cmd_manage(now: datetime, broker, bars=None, fetch=None) -> list[dict]:
    """Close a desk position when its stock moves the stop % against the entry, when it has been held
    rules.json max_hold_minutes, or from the flatten time on.

    Leaving the zone is NOT an exit. Exits are allowed even if the instrument was switched off.
    Positions the desk never opened (e.g. a manual SNDK holding with SNDK switched off) are left alone.
    """
    rules, risk, config = load_json("rules.json", {}), load_json("risk.json", {}), load_json("alpaca_config.json", {})
    events = journal.read_events()
    managed = desk_underlyings(events)
    desk = [p for p in broker.positions()
            if instruments.is_desk_symbol(p["symbol"], managed) and float(p["qty"] or 0) != 0]
    if not desk:
        return []
    t = now.astimezone(ET)
    flatten = t.time() >= hhmm(risk.get("flatten_start", risk["rth_close"]))
    source = _bars(now, bars, fetch)
    weights = load_json("learning_weights.json", {})
    all_books = instruments.books(rules, watchlist())
    snap = broker.account_snapshot()
    market_open = broker.market_open()
    sent = []
    for p in desk:
        qty = float(p["qty"])
        underlying = instruments.underlying_of(p["symbol"])
        market = None
        if not flatten:
            market, err = market_read(now, source, underlying, weights, risk)
            if market is None:
                journal.log("exit_failed", now=now, reasons=["no_market_data"], error=err, symbol=p["symbol"])
                print(f"Exit check: no {underlying} data to test the stop on {p['symbol']}.")
        exposure = instruments.direction(p["symbol"], qty)
        entry = latest_entry(p["symbol"], events)
        book = instruments.book_of(p["symbol"], all_books) or {}
        # the stop the entry was sized with; else the book's fixed % (or the old rule)
        pct = float(entry.get("stop_pct") or (book.get("stop") or {}).get("pct") or rules.get("stop_underlying_pct", 0.35))
        hold_min = int(book.get("max_hold_minutes", rules.get("max_hold_minutes")) or 0)
        ref = entry.get("underlying_price") or (p["avg_entry_price"] if p["symbol"] == underlying else None)
        level = (float(entry["stop_level"]) if entry.get("stop_level") is not None
                 else instruments.stop_level(exposure, float(ref), pct) if ref else None)
        opened = to_et(entry.get("ts"))
        reason = None
        if flatten:
            reason = "flatten"
        elif opened is not None and opened.date() < t.date():
            reason = "overnight"  # should never exist; close it at the first chance
        elif level is not None and market is not None and instruments.stop_hit(exposure, market["price"], level):
            reason = "stop"
        elif hold_min and opened is not None and (t - opened).total_seconds() >= hold_min * 60:
            reason = "time"  # rules.json max_hold_minutes: out once the trade has had its time
        elif level is None:
            journal.log("exit_failed", now=now, reasons=["no_stop_reference"], symbol=p["symbol"],
                        note=f"No entry {underlying} price on record; only the flatten applies.")
        if reason is None:
            continue
        checks = check_exit(now=now, base_url=broker.base_url, client_is_paper=broker.is_paper, config=config,
                            rules=rules, position=p, account_number=snap.get("account_number"),
                            market_open=market_open, underlyings=managed)
        if not passed(checks):
            journal.log("exit_failed", now=now, symbol=p["symbol"], reason=reason, reasons=failures(checks),
                        gate=[c.to_dict() for c in checks])
            print(f"Roy: could NOT close {p['symbol']} ({reason}) — {', '.join(failures(checks))}.")
            continue
        side = "sell" if qty > 0 else "buy"
        intent = ("sell_to_close" if qty > 0 else "buy_to_close") if instruments.parse_occ(p["symbol"]) else None
        coid = f"desk-{t:%Y%m%d-%H%M%S}-{book.get('id') or underlying.lower()}-exit-{reason}"
        px = market["price"] if market else None
        try:
            if not instruments.parse_occ(p["symbol"]) and hasattr(broker, "cancel_orders"):
                broker.cancel_orders(p["symbol"])  # the stop Alpaca holds for these shares, else it blocks the sale
            order = broker.submit_market(side, abs(qty), coid, symbol=p["symbol"], intent=intent)
        except Exception as e:  # noqa: BLE001 - a failed exit must be loud
            journal.log("exit_failed", now=now, symbol=p["symbol"], reason=reason, reasons=["order_error"],
                        error=f"{type(e).__name__}: {e}"[:300])
            print(f"Roy: exit order for {p['symbol']} FAILED ({type(e).__name__}). Close it by hand.")
            continue
        journal.log("order", now=now, role="exit", reason=reason, order_id=order["order_id"], client_order_id=coid,
                    status=order.get("status"), symbol=p["symbol"], underlying=underlying, side=side, qty=abs(qty),
                    underlying_price=px, stop_level=level, book=book.get("id"))
        msg = {"stop": "stop hit", "flatten": "15:40 flatten", "overnight": "held overnight — closing",
               "time": f"{hold_min}-minute time limit"}[reason]
        print(f"Roy: closing {p['symbol']} ({msg}) — {side} {abs(qty):g}"
              + (f"; {underlying} {px:.2f} vs stop {level:.2f}" if reason == "stop" else "") + ".")
        sent.append({**order, "book": book.get("id"), "symbol": p["symbol"]})
    return sent


# ------------------------------------------------------------------ commands

def _log_all(now: datetime, mode: str, results: list[dict]) -> None:
    for r in results:
        _log_eval(now, mode, r)


def cmd_eval(now: datetime, bars=None, fetch=None) -> dict:
    """Evaluate every focus symbol and log. No broker client is constructed here, so no order can be sent."""
    results = evaluate_focus(now, bars, fetch)
    _log_all(now, "eval", results)
    rebuild_dashboard.write_state(now)
    for r in results:
        print(describe(r))
    return pick(results)


def _option_money(snap: dict) -> float | None:
    """What one option buy may spend: Alpaca's options buying power, else cash (never margin)."""
    for k in ("options_buying_power", "cash"):
        if snap.get(k) is not None:
            return float(snap[k])
    return None


def cmd_paper(now: datetime, broker_factory=None, bars=None, fetch=None) -> dict:
    """Manage exits, then evaluate every book. Each book may send ONE entry, only if every one of its gate checks
    passes; books share the account's cash (what one spends, the next can't). Returns the main result."""
    risk = load_json("risk.json", {})
    state = session_state(now, risk)
    source = _bars(now, bars, fetch)
    if state in ("weekend", "pre_open", "after_close"):
        results = evaluate_focus(now, source)
        _log_all(now, "paper", results)
        rebuild_dashboard.write_state(now)
        for r in results:
            print(describe(r))
        return pick(results)

    if broker_factory is None:
        from alpaca_client import PaperBroker as broker_factory  # noqa: N813
    broker = broker_factory()
    exits = cmd_manage(now, broker, bars=source)
    exited = {e.get("book") for e in exits}

    rules, watch = load_json("rules.json", {}), watchlist()
    all_books = instruments.books(rules, watch)
    snap = broker.account_snapshot()
    positions = [p for p in (snap.get("positions") or []) if float(p.get("qty") or 0) != 0]
    orders = [o for o in broker.open_orders() if isinstance(o, dict)]
    owner = lambda sym: (instruments.book_of(sym or "", all_books) or {}).get("id")  # noqa: E731
    money = {"shares": _f(snap.get("cash")), "option": _option_money(snap)}
    shared = dict(account_number=snap.get("account_number"), market_open=broker.market_open(),
                  client_is_paper=broker.is_paper, base_url=broker.base_url, contract_picker=broker.option_contracts,
                  quote_picker=getattr(broker, "option_asks", None))
    results, entered = [], []
    for sym in focus_symbols():
        for book in instruments.books_for(sym, rules, watch) or [None]:
            if book is not None and book["id"] in exited:
                r = {"symbol": sym, "book": book["id"], "instrument": book["id"], "session": state,
                     "decision": "skip", "reasons": ["exit_in_progress"]}
            else:
                mine = [p for p in positions if book is not None and owner(p["symbol"]) == book["id"]]
                buys = [o for o in orders if book is not None and owner(o.get("symbol")) == book["id"]
                        and o.get("side") == "buy"]
                pool = "option" if book is not None and book["asset"] == "option" else "shares"
                r = evaluate(now, source, symbol=sym, book=book, positions=mine, open_orders=len(buys),
                             money=money[pool], **shared)
            if r["decision"] == "would_enter":
                r.update(decision="skip", reasons=[c["name"] for c in r.get("gate", []) if not c["ok"]])
            if r["decision"] == "enter":
                spent = _submit_entry(now, broker, r)
                if spent is not None:
                    entered.append(r)
                    for k in money:  # cash and options buying power both shrink by what was spent
                        if money[k] is not None:
                            money[k] = max(0.0, money[k] - spent)
            else:
                _log_eval(now, "paper", r)
            print(describe(r))
            results.append(r)
    if entered or exits:
        cmd_sync(now, broker=broker)
    rebuild_dashboard.write_state(now)
    return entered[0] if entered else pick(results)


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _submit_entry(now: datetime, broker, r: dict) -> float | None:
    """Send one book's entry. Shares go as whole shares with the stop held at Alpaca (one order that triggers
    the other); options as one contract (the desk checks their stop). Returns the dollars spent, None if refused."""
    cand, plan, sym = r["candidate"], r["plan"], r["symbol"]
    book = r.get("book") or plan.get("book")
    t = now.astimezone(ET)
    coid = f"desk-{t:%Y%m%d-%H%M%S}-{book or sym.lower()}-{r['signal']}"
    _log_eval(now, "paper", r)
    rules = load_json("rules.json", {})
    held = (plan["asset"] == "shares"
            and next((b for b in instruments.books(rules, watchlist()) if b["id"] == book), {}).get("stop_at_broker"))
    try:
        if held:
            order = broker.submit_market_with_stop(int(plan["qty"]), round(float(plan["stop_level"]), 2), coid,
                                                   symbol=plan["symbol"])
        else:
            order = broker.submit_market(plan["order_side"], plan["qty"], coid, symbol=plan["symbol"],
                                         intent=plan.get("intent") if plan["asset"] == "option" else None)
    except Exception as e:  # noqa: BLE001 - a rejection is logged and learned from, never retried blindly
        journal.log("order_rejected", now=now, symbol=plan.get("symbol"), underlying=sym, book=book, plan=plan,
                    error=f"{type(e).__name__}: {e}"[:300])
        print(f"Roy: paper order for {plan.get('symbol')} ({book}) was rejected ({type(e).__name__}). Nothing is open.")
        r.update(decision="skip", reasons=["order_rejected"])
        return None
    spent = float(plan.get("cost") or 0) if plan["asset"] == "option" else float(plan.get("notional") or 0)
    journal.log("order", now=now, role="entry", order_id=order["order_id"], client_order_id=coid,
                status=order.get("status"), symbol=plan["symbol"], underlying=sym, asset=plan["asset"], book=book,
                side=plan["order_side"], qty=plan["qty"], right=plan.get("right"), strike=plan.get("strike"),
                expiry=plan.get("expiry"), signal=r["signal"], underlying_price=r["market"]["price"],
                notional=plan.get("notional"), cost=plan.get("cost"), zone=cand["zone"], tags=cand["tags"],
                score=cand["score"], pulse_bias=r.get("pulse_bias"), features=r.get("features"), p_loss=r.get("p_loss"),
                entry_number=r.get("entries_today", 0) + 1, note=r.get("note"), context=_mood(),
                stop_pct=plan.get("stop_pct"), stop_level=plan.get("stop_level"),
                stop_order_id=order.get("stop_order_id"), stop_at_broker=bool(held))
    what = (f"1 {sym} {plan['right']} {plan['symbol']}" if plan["asset"] == "option"
            else f"buy {plan['qty']} {sym} (~${plan['notional']:,.2f})")
    print(f"Roy: paper entry sent ({book}) — {what} on a {r['signal']} in the {cand['zone']['color']} zone "
          f"{cand['zone']['low']}–{cand['zone']['high']}. Stop: {sym} {plan['stop_level']:.2f} ({plan['stop_pct']:g}%"
          f"{', held at Alpaca' if held else ', checked every 10 minutes'}); flat by 15:40. "
          f"Order {order['order_id']} ({order.get('status')}).")
    r["order"] = order
    return spent


def cmd_sync(now: datetime, broker=None, broker_factory=None) -> list[dict]:
    """Record new paper fills in trades.csv and refresh account.json. Tells Roy about each fill."""
    if broker is None:
        if broker_factory is None:
            from alpaca_client import PaperBroker as broker_factory  # noqa: N813
        broker = broker_factory()
    events = journal.read_events()
    new_rows = []
    for fill in broker.filled_orders_since(now - timedelta(days=7)):
        symbol = fill.get("symbol", "SPY")
        ctx = journal.order_context(fill["order_id"], events)
        if ctx.get("synthetic") and not any(r["order_id"] == fill["order_id"] for r in journal.read_trades()):
            # Alpaca's held stop sold the shares: record it as the desk's stop exit for that book
            ctx = journal.log("order", now=now, role="exit", reason="stop", via="alpaca_stop", order_id=fill["order_id"],
                              status="filled", symbol=symbol, underlying=ctx.get("underlying") or symbol,
                              book=ctx.get("book"), side=fill["side"], qty=fill["qty"], underlying_price=fill["price"],
                              stop_level=ctx.get("stop_level"))
            events.append(ctx)
        row = journal.record_fill(filled_at=fill["filled_at"], order_id=fill["order_id"], side=fill["side"],
                                  qty=fill["qty"], price=fill["price"], context=ctx, symbol=symbol)
        if not row:
            continue
        new_rows.append(row)
        kind = "close" if row["realized_pnl"] != "" else "fill"
        journal.log(kind, now=now, order_id=fill["order_id"], symbol=symbol, side=fill["side"], qty=fill["qty"],
                    price=fill["price"], filled_at=fill["filled_at"], realized_pnl=row["realized_pnl"] or None,
                    position_qty_after=row["position_qty_after"], reason=ctx.get("reason"))
        pnl = f", realized P&L ${float(row['realized_pnl']):+,.2f}" if row["realized_pnl"] != "" else ""
        print(f"Roy: paper {kind} — {fill['side']} {fill['qty']:g} {symbol} @ {fill['price']:.2f}{pnl}. "
              f"Position now {row['position_qty_after']:g}.")
    snap = broker.account_snapshot()
    save_json("account.json", snap)
    journal.log("account", now=now, equity=snap["equity"], cash=snap["cash"],
                buying_power=snap["buying_power"], position=snap.get("position"))
    rebuild_dashboard.write_state(now)
    if not new_rows:
        print(f"No new fills. Equity ${snap['equity']:,.2f}, cash ${snap['cash']:,.2f}.")
    _mirror_to_sheet()
    return new_rows


def _mirror_to_sheet() -> None:
    import sheets_sync

    r = sheets_sync.push()
    if r["sent"]:
        print("Google Sheet updated.")
    elif r["why"] != "SHEETS_WEBHOOK_URL not set":
        print(f"Google Sheet not updated ({r['why']}); trading is unaffected.")


def cmd_review(now: datetime) -> dict:
    day = now.astimezone(ET).date()
    events = journal.events_on(journal.read_events(), day)
    trades = [t for t in journal.read_trades() if (to_et(t["filled_at"]) or now).date() == day]
    skips: dict[str, int] = {}
    for e in events:
        if e["event"] == "skip":
            for reason in e.get("reasons") or ["unspecified"]:
                skips[reason] = skips.get(reason, 0) + 1
    account = load_json("account.json", {})
    realized = round(sum(float(t["realized_pnl"]) for t in trades if t["realized_pnl"] not in ("", None)), 2)
    pos = account.get("position") or {}
    exits: dict[str, int] = {}
    for e in events:
        if e["event"] == "order" and e.get("role") == "exit":
            exits[e.get("reason", "?")] = exits.get(e.get("reason", "?"), 0) + 1
    summary = {
        "date": day.isoformat(),
        "evaluated": sum(1 for e in events if e["event"] in ("eval", "skip")),
        "watch_only": sum(1 for e in events if e.get("decision") == "watch"),
        "would_enter": sum(1 for e in events if e.get("decision") == "would_enter"),
        "skipped": sum(skips.values()),
        "skip_reasons": dict(sorted(skips.items(), key=lambda kv: -kv[1])),
        "orders": sum(1 for e in events if e["event"] == "order"),
        "entries": sum(1 for e in events if e["event"] == "order" and e.get("role") == "entry"),
        "exits": exits,
        "rejected": sum(1 for e in events if e["event"] in ("order_rejected", "exit_failed")),
        "fills": len(trades),
        "realized_pnl": realized,
        "unrealized_pnl": pos.get("unrealized_pl"),
        "equity": account.get("equity"),
        "position": pos.get("symbol"),
        "position_qty": pos.get("qty", 0),
    }
    lessons = learning.learn(now)
    summary["lessons"] = lessons["lessons"]
    journal.log("review", now=now, **summary)
    rebuild_dashboard.write_state(now)
    print(f"End-of-day review {summary['date']}")
    print(f"  Evaluated {summary['evaluated']}  (watch-only {summary['watch_only']}, "
          f"would-enter {summary['would_enter']}, passes {summary['skipped']})")
    for reason, n in summary["skip_reasons"].items():
        print(f"    pass: {reason} × {n}")
    print(f"  Entries {summary['entries']}  Exits {exits or 0}  Rejected/failed {summary['rejected']}  "
          f"Fills {summary['fills']}")
    print(f"  Realized P&L ${realized:+,.2f}  Unrealized "
          f"{'$%+.2f' % summary['unrealized_pnl'] if summary['unrealized_pnl'] is not None else 'n/a'}")
    print(f"  Equity {'$%.2f' % summary['equity'] if summary['equity'] is not None else 'n/a'}  "
          f"Position {summary['position'] or 'flat'}")
    if summary["position"]:
        print("  WARNING: a position is still open after the close — the 16:00 flatten did not complete.")
    learning.print_report(lessons)
    _mirror_to_sheet()
    return summary


def review_done(day) -> bool:
    return any(e.get("event") == "review" for e in journal.events_on(journal.read_events(), day))


def _keys_present() -> bool:
    import os

    from alpaca_client import load_env_file

    load_env_file()
    return bool(os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"))


def _charts(now: datetime, source) -> None:
    """Refresh the dashboard's charts; a chart problem never stops the desk.

    The projection learns first (score what has closed, update the model), so the new charts use
    the updated model; then the projections now showing are logged for scoring later.
    """
    import charts
    import projection_log

    try:
        projection_log.score(now, source)
        projection_log.learn(now)
    except Exception as e:  # noqa: BLE001
        journal.log("projection_failed", now=now, error=f"{type(e).__name__}: {e}"[:300])
    try:  # the reader's candidate plays: mark the ones whose time is up against SPY's real move
        import sentiment

        sentiment.score(now, source.get("SPY")[0])
    except Exception as e:  # noqa: BLE001
        journal.log("plays_failed", now=now, error=f"{type(e).__name__}: {e}"[:300])
    try:
        drawn = charts.write(now, source)
        projection_log.record(now, drawn, not auto_study.desk_config()["regular_hours_only"])
    except Exception as e:  # noqa: BLE001
        journal.log("chart_failed", now=now, error=f"{type(e).__name__}: {e}"[:300])
    rebuild_dashboard.write_state(now)


def cmd_tick(now: datetime, broker_factory=None, bars=None, fetch=None) -> str:
    """One scheduled heartbeat (the cloud runs this every 10 minutes). Does what the clock calls for.

    09:30–09:59 watch only · 10:00–cutoff exits + gated entry · cutoff–16:00 exits only
    after 16:10 once a day: sync + review · otherwise nothing.
    """
    risk = load_json("risk.json", {})
    state = session_state(now, risk)
    t = now.astimezone(ET)
    source = _bars(now, bars, fetch)  # read each stock's prices once per heartbeat
    if broker_factory is None and state in ("trade_window", "flatten_window", "after_close") and not _keys_present():
        # Setup isn't finished: watch and chart, never fail. Trading starts once the keys are added.
        journal.log("setup_needed", now=now, reasons=["no_alpaca_keys"],
                    note="Add ALPACA_API_KEY and ALPACA_SECRET_KEY as repository secrets.")
        if state != "after_close":
            cmd_auto_zones(now, source)
            cmd_eval(now, source)
        _charts(now, source)
        print("Alpaca keys aren't set yet: watched the market and drew charts; no orders.")
        return "no_keys"
    if state in ("watch_only", "trade_window", "flatten_window"):
        _own_feed(now, source, live=bars is None and fetch is None)
    if state == "watch_only":
        cmd_auto_zones(now, source)
        cmd_eval(now, source)
        _charts(now, source)
        return "watch"
    if state in ("trade_window", "flatten_window"):
        cmd_auto_zones(now, source)  # catches up if the cloud missed the watch window
        if broker_factory is None:
            from alpaca_client import PaperBroker as broker_factory  # noqa: N813
        broker = broker_factory()
        cmd_paper(now, broker_factory=lambda: broker, bars=source)
        cmd_sync(now, broker=broker)
        _charts(now, source)
        return state
    if state == "after_close" and t.time() >= hhmm("16:10") and not review_done(t.date()):
        if broker_factory is None:
            from alpaca_client import PaperBroker as broker_factory  # noqa: N813
        broker = broker_factory()
        cmd_manage(now, broker, bars=source)  # logs exit_failed if anything is still open
        cmd_sync(now, broker=broker)
        cmd_review(now)
        _charts(now, source)
        import projection_log

        journal.log("projection_review", now=now, timeframes=projection_log.daily_summary(now))
        rebuild_dashboard.write_state(now)
        return "review"
    print(f"Nothing to do at {t:%a %H:%M} ET ({state}).")
    return "idle"


def cmd_aoi_set(now: datetime, zone_specs: list[str], tag_specs: list[str], source: str, symbol: str = "SPY") -> dict:
    """Scout: publish today's boxes for one symbol as Ops read them. Only inside 09:30–09:59 ET on a weekday."""
    if symbol not in watchlist()["symbols"]:
        raise SystemExit(f"Refused: {symbol} is not on the watchlist (python3 run_study.py focus add {symbol}).")
    risk = load_json("risk.json")
    t = now.astimezone(ET)
    if t.weekday() >= 5 or not (hhmm(risk["rth_open"]) <= t.time() < hhmm(risk["rth_watch_only_until"])):
        raise SystemExit(f"Refused: zones are published only 09:30–09:59 ET on a weekday (now {t:%a %H:%M} ET).")
    zones = []
    for spec in zone_specs:
        try:
            color, low, high = spec.split(":")
            low_f, high_f = float(low), float(high)
        except ValueError:
            raise SystemExit(f"Bad zone {spec!r}; use color:low:high, e.g. red:571.20:572.05") from None
        if color not in ("red", "green") or not 0 < low_f < high_f:
            raise SystemExit(f"Bad zone {spec!r}; color red|green and 0 < low < high")
        zones.append({"color": color, "low": low_f, "high": high_f, "confluence": []})
    for spec in tag_specs:
        idx, tag = spec.split(":", 1)
        zones[int(idx) - 1]["confluence"].append(tag)
    data = {
        "symbol": symbol,
        "tradable": bool(zones),
        "written_at": t.isoformat(timespec="seconds"),
        "source": source,
        "approximate": False,
        "zones": zones,
        "note": "Same-day Mxwll read from the 09:30–09:59 ET open." if zones else "No readable boxes.",
    }
    save_json(aoi_file(symbol), data)
    journal.log("aoi", now=now, symbol=symbol, tradable=data["tradable"], zones=zones, source=source)
    rebuild_dashboard.write_state(now)
    print(f"{aoi_file(symbol)}: {len(zones)} zone(s), tradable={data['tradable']}")
    return data


def cmd_aoi_clear(now: datetime, reason: str, symbol: str = "SPY") -> dict:
    data = {"symbol": symbol, "tradable": False, "zones": [],
            "written_at": now.astimezone(ET).isoformat(timespec="seconds"), "note": reason}
    save_json(aoi_file(symbol), data)
    journal.log("aoi", now=now, symbol=symbol, tradable=False, zones=[], note=reason)
    rebuild_dashboard.write_state(now)
    print(f"{aoi_file(symbol)} cleared — {reason}. No tradable AOI today for {symbol}.")
    return data


# ------------------------------------------------------------------ focus list

def valid_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    if not re.match(SYMBOL_RE, sym):
        raise SystemExit(f"Bad symbol {symbol!r}: 1–5 letters, e.g. NVDA or BRK.B")
    return sym


def set_focus(symbol: str, on: bool, now: datetime | None = None) -> dict:
    """Add a symbol to (or take it off) the focus list. Watching only; this never switches trading on."""
    sym = valid_symbol(symbol)
    w = watchlist()
    if on:
        w["symbols"].setdefault(sym, {"chart": None})
        if sym not in w["focus"]:
            w["focus"].append(sym)
    else:
        w["focus"] = [s for s in w["focus"] if s != sym]
    save_json("watchlist.json", w)
    journal.log("focus", now=now, symbol=sym, on=on, focus=w["focus"])
    return w


def move_focus(symbol: str, step: int) -> dict:
    sym = valid_symbol(symbol)
    w = watchlist()
    if sym in w["focus"]:
        i = w["focus"].index(sym)
        j = max(0, min(len(w["focus"]) - 1, i + step))
        w["focus"].insert(j, w["focus"].pop(i))
        save_json("watchlist.json", w)
    return w


def set_trading(symbol: str, instrument: str | None, on: bool) -> dict:
    """Local CLI only: switch paper trading on or off for a non-SPY symbol (SNDK still needs sndk_enabled)."""
    sym = valid_symbol(symbol)
    if sym == "SPY":
        raise SystemExit("SPY is switched in rules.json (active, spy_options_enabled, shares_enabled).")
    w = watchlist()
    entry = w["symbols"].setdefault(sym, {"chart": None})
    if instrument:
        if instrument not in ("options", "shares"):
            raise SystemExit("instrument must be options or shares")
        entry["instrument"] = instrument
    if on and entry.get("instrument") not in ("options", "shares"):
        raise SystemExit(f"Set an instrument first: focus trade {sym} --instrument options|shares --on")
    entry["trading_enabled"] = bool(on)
    save_json("watchlist.json", w)
    journal.log("focus", symbol=sym, trading_enabled=bool(on), instrument=entry.get("instrument"))
    return w


def _own_feed(now: datetime, source, live: bool) -> None:
    """The desk's own sentiment read (Stocktwits, Yahoo headlines, VIX) every 30 minutes in market hours,
    unless the hourly AI reader sent a fresh reading. Live runs only; a feed problem never stops the desk."""
    if not live:
        return
    try:
        import feeds
        import sentiment

        if feeds.due(now, sentiment.latest_full()):
            readings = feeds.collect(now, source.get("SPY")[0])
            if len(readings) > 3:  # more than the timestamp, source and source list
                cmd_pulse_ingest(now, readings)
    except Exception as e:  # noqa: BLE001
        journal.log("feeds_failed", now=now, error=f"{type(e).__name__}: {e}"[:300])


def _mood() -> dict | None:
    """The newest sentiment reading, saved with each entry so the learner can test whether it helps."""
    try:
        import sentiment

        return sentiment.latest_brief()
    except Exception:  # noqa: BLE001
        return None


def cmd_pulse_ingest(now: datetime, readings: dict) -> dict:
    """Pulse agents: hand over live readings (Stocklake, Stocktwits); the bias comes from rules.json pulse_rules."""
    rules = load_json("rules.json", {}) or {}
    derived = pulse.derive_bias(readings, rules.get("pulse_rules") or pulse.DEFAULT_RULES)
    try:  # sentiment, rumors and candidate plays become history (sentiment.jsonl, plays.jsonl)
        import sentiment

        sentiment.record(now, readings)
    except Exception as e:  # noqa: BLE001 - a bad readings object never stops the pulse
        journal.log("sentiment_failed", now=now, error=f"{type(e).__name__}: {e}"[:300])
    data = {
        "date": now.astimezone(ET).date().isoformat(),
        "bias": derived["bias"],
        "written_at": now.astimezone(ET).isoformat(timespec="seconds"),
        "as_of": readings.get("as_of"),
        "readings": readings,
        "votes": derived["votes"],
        "net_votes": derived["net_votes"],
        "min_net_votes": derived["min_net_votes"],
        "sources": readings.get("sources") or {},
        "note": f"Derived from live readings: net {derived['net_votes']:+d} votes "
                f"(needs ±{derived['min_net_votes']}) → {derived['bias']}.",
    }
    save_json("market_pulse.json", data)
    journal.log("pulse", now=now, bias=data["bias"], note=data["note"], sources=data["sources"],
                net_votes=derived["net_votes"])
    rebuild_dashboard.write_state(now)
    print(f"Market Pulse for {data['date']}: {data['bias']} ({data['note']})")
    for v in derived["votes"]:
        print(f"  {v['reading']:<22} {v['value'] if v['value'] is not None else '—':>8}  "
              f"{'+1' if v['vote'] > 0 else '-1' if v['vote'] < 0 else ' 0'}  {v['why']}")
    return data


def cmd_pulse_set(now: datetime, bias: str, note: str, sources: dict) -> dict:
    data = {"date": now.astimezone(ET).date().isoformat(), "bias": bias,
            "written_at": now.astimezone(ET).isoformat(timespec="seconds"), "sources": sources, "note": note}
    save_json("market_pulse.json", data)
    journal.log("pulse", now=now, bias=bias, note=note, sources=sources)
    rebuild_dashboard.write_state(now)
    print(f"Market Pulse for {data['date']}: {bias}")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SPY paper desk (paper only; SPY options active).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("eval", help="evaluate and log; never sends an order")
    sub.add_parser("tick", help="one scheduled heartbeat: does whatever the New York clock calls for")
    sub.add_parser("paper", help="manage exits, then submit one paper entry only if the gate passes")
    sub.add_parser("manage", help="exits only: 0.35% SPY stop and the 16:00 flatten")
    sub.add_parser("learn", help="retrain the learner from closed paper trades")
    sub.add_parser("sync", help="pull paper fills and the account snapshot")
    sub.add_parser("review", help="end-of-day review")
    aoi = sub.add_parser("aoi", help="scout: write aoi_override.json")
    aoi_sub = aoi.add_subparsers(dest="aoi_cmd", required=True)
    aoi_set = aoi_sub.add_parser("set")
    aoi_set.add_argument("--zone", action="append", default=[], help="color:low:high (repeatable)")
    aoi_set.add_argument("--tag", action="append", default=[], help="N:TAG, e.g. 1:CHoCH or 2:order_block")
    aoi_set.add_argument("--source", default="Ops live read of Roy's Mxwll chart")
    aoi_set.add_argument("--symbol", default="SPY")
    aoi_clear = aoi_sub.add_parser("clear")
    aoi_clear.add_argument("--reason", default="No readable Mxwll boxes from the open.")
    aoi_clear.add_argument("--symbol", default="SPY")
    focus = sub.add_parser("focus", help="choose which stocks the desk watches (and, locally, trades)")
    focus_sub = focus.add_subparsers(dest="focus_cmd", required=True)
    focus_sub.add_parser("list")
    f_add = focus_sub.add_parser("add")
    f_add.add_argument("symbol")
    f_rm = focus_sub.add_parser("remove")
    f_rm.add_argument("symbol")
    f_tr = focus_sub.add_parser("trade", help="switch paper trading on/off for a non-SPY symbol")
    f_tr.add_argument("symbol")
    f_tr.add_argument("--instrument", choices=["options", "shares"])
    on_off = f_tr.add_mutually_exclusive_group(required=True)
    on_off.add_argument("--on", action="store_true")
    on_off.add_argument("--off", action="store_true")
    pulse = sub.add_parser("pulse", help="write today's Market Pulse bias")
    pulse_sub = pulse.add_subparsers(dest="pulse_cmd", required=True)
    pulse_set = pulse_sub.add_parser("set")
    pulse_set.add_argument("bias", choices=["bullish", "bearish", "neutral"])
    pulse_in = pulse_sub.add_parser("ingest", help="derive today's bias from a JSON file of live readings")
    pulse_in.add_argument("file", help="readings JSON (see pulse.py), or - for stdin")
    pulse_set.add_argument("--note", default="")
    for name in ("market_pulse", "macro_policy", "sentiment_flow", "rates_jobs"):
        pulse_set.add_argument(f"--{name.replace('_', '-')}", dest=name, default=None)
    args = parser.parse_args(argv)

    now = now_et()
    if args.cmd == "tick":
        cmd_tick(now)
    elif args.cmd == "eval":
        cmd_eval(now)
    elif args.cmd == "paper":
        cmd_paper(now)
    elif args.cmd == "manage":
        from alpaca_client import PaperBroker

        broker = PaperBroker()
        if not cmd_manage(now, broker):
            print("Nothing to close.")
        cmd_sync(now, broker=broker)
    elif args.cmd == "learn":
        learning.print_report(learning.learn(now))
        rebuild_dashboard.write_state(now)
    elif args.cmd == "sync":
        cmd_sync(now)
    elif args.cmd == "review":
        cmd_review(now)
    elif args.cmd == "aoi" and args.aoi_cmd == "set":
        cmd_aoi_set(now, args.zone, args.tag, args.source, valid_symbol(args.symbol))
    elif args.cmd == "aoi" and args.aoi_cmd == "clear":
        cmd_aoi_clear(now, args.reason, valid_symbol(args.symbol))
    elif args.cmd == "focus":
        if args.focus_cmd == "add":
            set_focus(args.symbol, True, now)
        elif args.focus_cmd == "remove":
            set_focus(args.symbol, False, now)
        elif args.focus_cmd == "trade":
            set_trading(args.symbol, args.instrument, args.on)
        rules = load_json("rules.json", {})
        w = watchlist()
        for sym in w["symbols"]:
            inst = instruments.for_symbol(sym, rules, w)
            mark = f"#{w['focus'].index(sym) + 1}" if sym in w["focus"] else "  -"
            print(f"{mark:>3} {sym:<6} {'TRADING' if inst['enabled'] else 'watch  '}  {inst['why']}")
        rebuild_dashboard.write_state(now)
    elif args.cmd == "pulse" and args.pulse_cmd == "ingest":
        raw = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        cmd_pulse_ingest(now, json.loads(raw))
    elif args.cmd == "pulse":
        sources = {k: getattr(args, k) for k in ("market_pulse", "macro_policy", "sentiment_flow", "rates_jobs")}
        cmd_pulse_set(now, args.bias, args.note, sources)
    return 0


if __name__ == "__main__":
    sys.exit(main())
