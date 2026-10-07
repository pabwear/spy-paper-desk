"""SPY paper desk runner. Active instrument: SPY options (1 contract). SPY shares and SNDK are off.

    python3 run_study.py eval                 # evaluate and log. NEVER sends an order.
    python3 run_study.py paper                # manage exits, then at most ONE gated paper entry
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
    try:
        return auto_study.read(source.get(symbol)[0], now, auto_study.desk_config())
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:200]}


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
             base_url: str | None = None, open_orders: int | None = None, contract_picker=None) -> dict:
    """Decide on an entry for one symbol. Pure decision logic over the desk files, the bars and what the caller passes in.

    Never orders. contract_picker(right, price, today, underlying) -> listed contracts; only `paper` passes one.
    """
    f = load_all(symbol)
    risk, rules, override, weights, watch = f["risk"], f["rules"], f["override"], f["weights"], f["watch"]
    state = session_state(now, risk)
    inst = instruments.for_symbol(symbol, rules, watch)
    result: dict = {"symbol": symbol, "session": state, "decision": "skip", "reasons": [],
                    "instrument": inst.get("name")}

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
    if not inst["enabled"]:
        result.update(reasons=["instrument_off"], note=inst["why"])
        return result

    events = journal.read_events()
    entries_today = journal.entries_on(events, now.astimezone(ET).date())
    bias = pulse_today(f["pulse"], now)
    result["pulse_bias"] = bias
    plan = instruments.plan_entry(inst, signal, price, risk, rules)
    notes = []
    if bias and pulse_contradicts(signal, bias):
        pc = risk.get("pulse_contradiction", {"action": "reduce", "size_factor": 0.5})
        if plan["asset"] == "option" or pc.get("action") == "skip":
            # A contract cannot be cut in half, so a contradicting pulse means skip.
            result.update(reasons=["pulse_contradicts"],
                          note=f"Market Pulse is {bias}; {signal} in {zone['color']} skipped.")
            return result
        factor = float(pc.get("size_factor", 0.5))
        plan["qty"] = round(plan["qty"] * factor, 4) if signal == "buy" else float(int(plan["qty"] * factor))
        plan["notional"] = round(plan["qty"] * price, 2)
        notes.append(f"Market Pulse is {bias} against a {signal}; size × {factor}.")
    if plan["asset"] == "shares" and signal == "sell" and plan["qty"] < 1:
        result.update(reasons=["short_needs_whole_share"], plan=plan,
                      note=f"Fractional shares cannot be sold short and the cap is under one {symbol} share.")
        return result

    if plan["asset"] == "option" and contract_picker is not None:
        today = now.astimezone(ET).date()
        try:
            listed = contract_picker(plan["right"], price, today, symbol)
        except Exception as e:  # noqa: BLE001
            result.update(reasons=["no_contract"], plan=plan, error=f"contract lookup: {type(e).__name__}: {e}"[:300])
            return result
        picked = instruments.pick_contract(listed, price, plan["right"], today)
        if picked is None:
            result.update(reasons=["no_contract"], plan=plan, note=f"No listed {symbol} contract for that right.")
            return result
        plan.update(symbol=picked["symbol"], strike=picked["strike"], expiry=picked["expiry"].isoformat(),
                    expiry_is_nearest=picked["expiry_is_nearest"], strike_is_nearest=picked["strike_is_nearest"])

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
        entries_today=entries_today, open_positions=positions, open_orders=open_orders, watch=watch,
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
    """Evaluate every focus symbol, in focus order."""
    source = _bars(now, bars, fetch)
    return [evaluate(now, source, symbol=sym, **kw) for sym in focus_symbols()]


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
        event, now=now, mode=mode, symbol=r.get("symbol"), session=r["session"], decision=r["decision"],
        reasons=r["reasons"],
        instrument=r.get("instrument"), signal=r.get("signal"), market=r.get("market"), aoi=r.get("aoi"),
        plan={k: plan.get(k) for k in ("asset", "symbol", "right", "contracts", "target_strike", "strike", "expiry",
                                         "order_side", "qty", "notional")} if plan else None,
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
    sym = r.get("symbol") or "—"
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
    """Close a desk position when its stock moves 0.35% against the entry, or from the flatten time on.

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
    pct = float(rules.get("stop_underlying_pct", 0.35))
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
        ref = entry.get("underlying_price") or (p["avg_entry_price"] if p["symbol"] == underlying else None)
        level = instruments.stop_level(exposure, float(ref), pct) if ref else None
        opened = to_et(entry.get("ts"))
        reason = None
        if flatten:
            reason = "flatten"
        elif opened is not None and opened.date() < t.date():
            reason = "overnight"  # should never exist; close it at the first chance
        elif level is not None and market is not None and instruments.stop_hit(exposure, market["price"], level):
            reason = "stop"
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
        coid = f"desk-{t:%Y%m%d-%H%M%S}-{underlying.lower()}-exit-{reason}"
        px = market["price"] if market else None
        try:
            order = broker.submit_market(side, abs(qty), coid, symbol=p["symbol"], intent=intent)
        except Exception as e:  # noqa: BLE001 - a failed exit must be loud
            journal.log("exit_failed", now=now, symbol=p["symbol"], reason=reason, reasons=["order_error"],
                        error=f"{type(e).__name__}: {e}"[:300])
            print(f"Roy: exit order for {p['symbol']} FAILED ({type(e).__name__}). Close it by hand.")
            continue
        journal.log("order", now=now, role="exit", reason=reason, order_id=order["order_id"], client_order_id=coid,
                    status=order.get("status"), symbol=p["symbol"], underlying=underlying, side=side, qty=abs(qty),
                    underlying_price=px, stop_level=level)
        msg = {"stop": "stop hit", "flatten": "16:00 flatten", "overnight": "held overnight — closing"}[reason]
        print(f"Roy: closing {p['symbol']} ({msg}) — {side} {abs(qty):g}"
              + (f"; {underlying} {px:.2f} vs stop {level:.2f}" if reason == "stop" else "") + ".")
        sent.append(order)
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


def cmd_paper(now: datetime, broker_factory=None, bars=None, fetch=None) -> dict:
    """Manage exits, then evaluate the focus list. Submits at most ONE entry, only if every gate check passes."""
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

    if exits:
        results = [{"symbol": s, "session": state, "decision": "skip", "reasons": ["exit_in_progress"]}
                   for s in focus_symbols()]
    else:
        snap = broker.account_snapshot()
        results = evaluate_focus(now, source, positions=snap.get("positions"),
                                 account_number=snap.get("account_number"), market_open=broker.market_open(),
                                 client_is_paper=broker.is_paper, base_url=broker.base_url,
                                 open_orders=len(broker.open_orders()), contract_picker=broker.option_contracts)
    r = pick(results)
    for other in results:
        if other is not r:
            if other["decision"] in ("enter", "would_enter"):
                other.update(decision="skip", reasons=["another_symbol_chosen"],
                             note=f"{r.get('symbol')} had the stronger setup; one position at a time.")
            _log_eval(now, "paper", other)
            print(describe(other))
    if r["decision"] != "enter":
        if r["decision"] == "would_enter":
            r.update(decision="skip", reasons=[c["name"] for c in r.get("gate", []) if not c["ok"]])
        if r.get("symbol"):
            _log_eval(now, "paper", r)
        if exits:
            cmd_sync(now, broker=broker)
        rebuild_dashboard.write_state(now)
        print(describe(r))
        return r

    cand, plan, sym = r["candidate"], r["plan"], r["symbol"]
    t = now.astimezone(ET)
    coid = f"desk-{t:%Y%m%d-%H%M%S}-{sym.lower()}-{plan['asset']}-{r['signal']}"
    _log_eval(now, "paper", r)
    try:
        order = broker.submit_market(plan["order_side"], plan["qty"], coid, symbol=plan["symbol"],
                                     intent=plan.get("intent") if plan["asset"] == "option" else None)
    except Exception as e:  # noqa: BLE001 - a rejection is logged and learned from, never retried blindly
        journal.log("order_rejected", now=now, symbol=plan.get("symbol"), underlying=sym, plan=plan,
                    error=f"{type(e).__name__}: {e}"[:300])
        rebuild_dashboard.write_state(now)
        print(f"Roy: paper order for {plan.get('symbol')} was rejected ({type(e).__name__}). Nothing is open.")
        r.update(decision="skip", reasons=["order_rejected"])
        return r
    journal.log("order", now=now, role="entry", order_id=order["order_id"], client_order_id=coid,
                status=order.get("status"), symbol=plan["symbol"], underlying=sym, asset=plan["asset"],
                side=plan["order_side"], qty=plan["qty"], right=plan.get("right"), strike=plan.get("strike"),
                expiry=plan.get("expiry"), signal=r["signal"], underlying_price=r["market"]["price"],
                notional=plan.get("notional"), zone=cand["zone"], tags=cand["tags"], score=cand["score"],
                pulse_bias=r.get("pulse_bias"), features=r.get("features"), p_loss=r.get("p_loss"),
                entry_number=r.get("entries_today", 0) + 1, note=r.get("note"))
    stop = instruments.stop_level(instruments.direction(plan["symbol"], plan["qty"] if plan["order_side"] == "buy"
                                                        else -plan["qty"]), r["market"]["price"],
                                  float(load_json("rules.json", {}).get("stop_underlying_pct", 0.35)))
    what = (f"1 {sym} {plan['right']} {plan['symbol']}" if plan["asset"] == "option"
            else f"{plan['order_side']} {plan['qty']} {sym} (~${plan['notional']:,.2f})")
    print(f"Roy: paper entry sent — {what} on a {r['signal']} in the {cand['zone']['color']} zone "
          f"{cand['zone']['low']}–{cand['zone']['high']}. Confluence: {', '.join(cand['tags'])}. "
          f"Stop: {sym} {stop:.2f} (0.35%); flat by 16:00. Order {order['order_id']} ({order.get('status')}).")
    cmd_sync(now, broker=broker)
    r["order"] = order
    return r


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
    """Refresh the dashboard's charts; a chart problem never stops the desk."""
    import charts

    try:
        charts.write(now, source)
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


def cmd_pulse_ingest(now: datetime, readings: dict) -> dict:
    """Pulse agents: hand over live readings (Stocklake, Stocktwits); the bias comes from rules.json pulse_rules."""
    rules = load_json("rules.json", {}) or {}
    derived = pulse.derive_bias(readings, rules.get("pulse_rules") or pulse.DEFAULT_RULES)
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
