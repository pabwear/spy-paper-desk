"""SPY paper desk runner.

    python3 run_study.py eval                 # evaluate and log. NEVER sends an order.
    python3 run_study.py paper                # evaluate; submit ONE paper order only if the gate passes
    python3 run_study.py sync                 # pull paper fills into trades.csv, refresh account.json
    python3 run_study.py review               # 4:15 PM ET end-of-day review
    python3 run_study.py aoi set --zone red:571.20:572.05 --zone green:578.40:579.10 [--tag 1:CHoCH]
    python3 run_study.py aoi clear --reason "Mxwll boxes not readable by 09:55"
    python3 run_study.py pulse set bearish --note "CPI hot; yields up"

Every run writes to journal.jsonl and rebuilds the console state.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

import journal
import rebuild_dashboard
from common import ET, hhmm, load_json, now_et, save_json, session_state, to_et
from gate import check_order, failures, is_approximate, override_freshness, passed
from signals import (bars_today, pulse_contradicts, rank_zones, rsi, session_vwap, size_qty,
                     volume_above_average, whole_shares)

BROKER_CHECKS = {"paper_client", "paper_1000_account", "market_clock"}
CLOSED_STATES = {"weekend": "weekend", "pre_open": "before_open", "after_close": "after_close"}


def load_all() -> dict:
    return {
        "config": load_json("alpaca_config.json", {}),
        "rules": load_json("rules.json", {}),
        "risk": load_json("risk.json", {}),
        "override": load_json("aoi_override.json", None),
        "pulse": load_json("market_pulse.json", {}),
        "weights": load_json("learning_weights.json", {}),
        "account": load_json("account.json", {}),
    }


def pulse_today(pulse: dict, now: datetime) -> str | None:
    if not pulse or pulse.get("date") != now.astimezone(ET).date().isoformat():
        return None
    bias = pulse.get("bias")
    return bias if bias in ("bullish", "bearish", "neutral") else None


def evaluate(now: datetime, bars: list[dict] | None, position: dict | None = None,
             fetch=None, account_number: str | None = None, market_open: bool | None = None,
             client_is_paper: bool = True, base_url: str | None = None) -> dict:
    """Evaluate the setup. Pure decision logic over the desk files and the bars. Never orders."""
    f = load_all()
    risk, override, weights = f["risk"], f["override"], f["weights"]
    state = session_state(now, risk)
    result: dict = {"session": state, "decision": "skip", "reasons": []}

    if state in CLOSED_STATES:
        result["reasons"] = [CLOSED_STATES[state]]
        return result

    if bars is None:
        try:
            bars = (fetch or _fetch)(now, int(weights.get("indicators", {}).get("timeframe_minutes", 1)))
        except Exception as e:  # noqa: BLE001 - no data means no trade
            result["reasons"] = ["no_market_data"]
            result["error"] = str(e)[:300]
            return result
    today = bars_today(bars, now)
    if not today:
        result["reasons"] = ["no_market_data"]
        return result

    ind = weights.get("indicators", {})
    closes = [b["c"] for b in bars]
    price = today[-1]["c"]
    rsi_v = rsi(closes, int(ind.get("rsi_period", 14)))
    vwap = session_vwap(today, risk["rth_open"])
    vol_up = volume_above_average([b["v"] for b in today], int(ind.get("volume_avg_bars", 20)))
    result["market"] = {
        "price": round(price, 4),
        "bar_time": today[-1]["t"].astimezone(ET).isoformat(timespec="seconds"),
        "rsi": round(rsi_v, 2) if rsi_v is not None else None,
        "vwap": round(vwap, 4) if vwap is not None else None,
        "volume_above_avg": vol_up,
    }

    zones = (override or {}).get("zones") or []
    tol = float(risk.get("zone_midpoint_tolerance_pct", 0.15))
    ranked = rank_zones(zones, price, rsi_v, vwap, vol_up, weights.get("weights", {}), tol)
    result["zones"] = ranked
    fresh, fresh_why = override_freshness(override, now, risk)
    result["aoi"] = {"tradable": bool(override and override.get("tradable")), "fresh": fresh,
                     "freshness": fresh_why, "approximate": is_approximate(override)}

    if state == "watch_only":
        result["decision"] = "watch"
        result["reasons"] = ["watch_only"]
        return result

    if not zones:
        result["reasons"] = ["no_aoi"]
        return result
    best = next((r for r in ranked if r["near"] and r["side"]), None)
    if best is None:
        result["reasons"] = ["outside_zones"]
        return result

    side, zone, tags = best["side"], best["zone"], best["tags"]
    bias = pulse_today(f["pulse"], now)
    factor, notes = 1.0, []
    if bias and pulse_contradicts(side, bias):
        pc = risk.get("pulse_contradiction", {"action": "reduce", "size_factor": 0.5})
        if pc.get("action") == "skip":
            result.update(candidate=best, pulse_bias=bias)
            result["reasons"] = ["pulse_contradicts"]
            result["note"] = f"Market Pulse is {bias}; {side} in {zone['color']} skipped."
            return result
        factor = float(pc.get("size_factor", 0.5))
        notes.append(f"Market Pulse is {bias} against a {side}; size × {factor}.")

    pos_qty = float((position or {}).get("qty") or 0)
    if side == "buy" and pos_qty > 0:
        result.update(candidate=best, reasons=["already_long"])
        return result
    if side == "sell" and pos_qty < 0:
        result.update(candidate=best, reasons=["already_short"])
        return result
    if side == "sell" and pos_qty > 0:
        qty = pos_qty
        notes.append("Take-profit: sells the open long.")
    elif side == "sell":
        qty = float(whole_shares(price, risk, factor))
        if qty < 1:
            result.update(candidate=best, reasons=["short_needs_whole_share"],
                          note="Fractional shares cannot be sold short; the 25% cap is under one SPY share.")
            return result
    else:
        qty = size_qty(price, risk, factor)

    checks = check_order(
        now=now, base_url=base_url or f["config"].get("base_url", ""), client_is_paper=client_is_paper,
        config=f["config"], rules=f["rules"], risk=risk, override=override, symbol="SPY", side=side,
        qty=qty, price=price, zone=zone, tags=tags,
        account_number=account_number or f["account"].get("account_number"), market_open=market_open,
    )
    result.update(candidate=best, side=side, qty=qty, notional=round(qty * price, 2), pulse_bias=bias,
                  gate=[c.to_dict() for c in checks], note=" ".join(notes))
    if side == "buy" and zone.get("low") is not None:
        result["risk_if_zone_fails"] = round(qty * max(price - float(zone["low"]), 0.0), 2)
    failed = failures(checks)
    if passed(checks):
        result["decision"] = "enter"
        result["reasons"] = []
    elif set(failed) <= BROKER_CHECKS:
        result["decision"] = "would_enter"
        result["reasons"] = []
    else:
        result["reasons"] = [r for r in failed if r not in BROKER_CHECKS]
    return result


def _fetch(now: datetime, minutes: int) -> list[dict]:
    from alpaca_client import fetch_bars

    return fetch_bars(now, minutes)


def _log_eval(now: datetime, mode: str, r: dict) -> dict:
    event = "skip" if r["decision"] == "skip" else "eval"
    cand = r.get("candidate") or {}
    return journal.log(
        event, now=now, mode=mode, session=r["session"], decision=r["decision"], reasons=r["reasons"],
        market=r.get("market"), aoi=r.get("aoi"), side=r.get("side"), qty=r.get("qty"),
        notional=r.get("notional"), zone=cand.get("zone"), tags=cand.get("tags"), score=cand.get("score"),
        pulse_bias=r.get("pulse_bias"), gate=r.get("gate"), note=r.get("note"), error=r.get("error"),
        zones=[{"color": z["zone"].get("color"), "low": z["zone"].get("low"), "high": z["zone"].get("high"),
                "near": z["near"], "distance_pct": z["distance_pct"], "tags": z["tags"], "score": z["score"]}
               for z in r.get("zones", [])],
    )


def describe(r: dict) -> str:
    if r["decision"] == "skip":
        return f"PASS — {', '.join(r['reasons'])}" + (f". {r['note']}" if r.get("note") else "")
    if r["decision"] == "watch":
        return "WATCH ONLY (09:30–09:59) — logged, no orders"
    z = r["candidate"]["zone"]
    head = {"enter": "ENTER", "would_enter": "WOULD ENTER (eval only)"}[r["decision"]]
    return (f"{head} — {r['side']} {r['qty']} SPY ≈ ${r['notional']:,.2f} in {z['color']} "
            f"{z['low']}–{z['high']}; tags {', '.join(r['candidate']['tags'])}")


# ------------------------------------------------------------------ commands

def cmd_eval(now: datetime, bars=None, fetch=None) -> dict:
    """Evaluate and log. No broker client is constructed here, so no order can be sent."""
    account = load_json("account.json", {})
    r = evaluate(now, bars, position=account.get("position"), fetch=fetch)
    _log_eval(now, "eval", r)
    rebuild_dashboard.write_state(now)
    print(describe(r))
    return r


def cmd_paper(now: datetime, broker_factory=None, bars=None, fetch=None) -> dict:
    """Evaluate; if the setup clears every non-broker check, ask the paper account, gate, submit."""
    account = load_json("account.json", {})
    pre = evaluate(now, bars, position=account.get("position"), fetch=fetch)
    if pre["decision"] not in ("would_enter", "enter"):
        _log_eval(now, "paper", pre)
        rebuild_dashboard.write_state(now)
        print(describe(pre))
        return pre

    if broker_factory is None:
        from alpaca_client import PaperBroker as broker_factory  # noqa: N813
    broker = broker_factory()
    snap = broker.account_snapshot()
    if broker.open_orders():
        pre.update(decision="skip", reasons=["open_order_pending"])
        _log_eval(now, "paper", pre)
        rebuild_dashboard.write_state(now)
        print(describe(pre))
        return pre

    # Second pass with the live paper account, its clock and fresh bars (when not injected).
    r = evaluate(now, bars, position=snap.get("position"),
                 fetch=fetch, account_number=snap.get("account_number"), market_open=broker.market_open(),
                 client_is_paper=broker.is_paper, base_url=broker.base_url)
    if r["decision"] != "enter":
        if r["decision"] == "would_enter":
            r.update(decision="skip", reasons=[c["name"] for c in r.get("gate", []) if not c["ok"]])
        _log_eval(now, "paper", r)
        rebuild_dashboard.write_state(now)
        print(describe(r))
        return r

    cand = r["candidate"]
    client_order_id = f"spy-{now.astimezone(ET):%Y%m%d-%H%M%S}-{r['side']}"
    order = broker.submit_market(r["side"], r["qty"], client_order_id)
    _log_eval(now, "paper", r)
    journal.log("order", now=now, order_id=order["order_id"], client_order_id=client_order_id,
                status=order.get("status"), side=r["side"], qty=r["qty"], price_at_submit=r["market"]["price"],
                notional=r["notional"], zone=cand["zone"], tags=cand["tags"], score=cand["score"],
                pulse_bias=r.get("pulse_bias"), note=r.get("note"))
    print(f"Roy: paper order sent — {r['side']} {r['qty']} SPY (~${r['notional']:,.2f}) in the "
          f"{cand['zone']['color']} zone {cand['zone']['low']}–{cand['zone']['high']}. "
          f"Confluence: {', '.join(cand['tags'])}. Order {order['order_id']} ({order.get('status')}).")
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
        ctx = journal.order_context(fill["order_id"], events)
        row = journal.record_fill(filled_at=fill["filled_at"], order_id=fill["order_id"], side=fill["side"],
                                  qty=fill["qty"], price=fill["price"], context=ctx)
        if not row:
            continue
        new_rows.append(row)
        kind = "close" if row["realized_pnl"] != "" else "fill"
        journal.log(kind, now=now, order_id=fill["order_id"], side=fill["side"], qty=fill["qty"],
                    price=fill["price"], filled_at=fill["filled_at"], realized_pnl=row["realized_pnl"] or None,
                    position_qty_after=row["position_qty_after"])
        pnl = f", realized P&L ${float(row['realized_pnl']):+,.2f}" if row["realized_pnl"] != "" else ""
        print(f"Roy: paper {kind} — {fill['side']} {fill['qty']} SPY @ {fill['price']:.2f}{pnl}. "
              f"Position now {row['position_qty_after']}.")
    snap = broker.account_snapshot()
    save_json("account.json", snap)
    journal.log("account", now=now, equity=snap["equity"], cash=snap["cash"],
                buying_power=snap["buying_power"], position=snap.get("position"))
    rebuild_dashboard.write_state(now)
    if not new_rows:
        print(f"No new fills. Equity ${snap['equity']:,.2f}, cash ${snap['cash']:,.2f}.")
    return new_rows


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
    summary = {
        "date": day.isoformat(),
        "evaluated": sum(1 for e in events if e["event"] in ("eval", "skip")),
        "watch_only": sum(1 for e in events if e.get("decision") == "watch"),
        "would_enter": sum(1 for e in events if e.get("decision") == "would_enter"),
        "skipped": sum(skips.values()),
        "skip_reasons": dict(sorted(skips.items(), key=lambda kv: -kv[1])),
        "orders": sum(1 for e in events if e["event"] == "order"),
        "fills": len(trades),
        "realized_pnl": realized,
        "unrealized_pnl": pos.get("unrealized_pl"),
        "equity": account.get("equity"),
        "position_qty": pos.get("qty", 0),
    }
    journal.log("review", now=now, **summary)
    rebuild_dashboard.write_state(now)
    print(f"End-of-day review {summary['date']}")
    print(f"  Evaluated {summary['evaluated']}  (watch-only {summary['watch_only']}, "
          f"would-enter {summary['would_enter']}, passes {summary['skipped']})")
    for reason, n in summary["skip_reasons"].items():
        print(f"    pass: {reason} × {n}")
    print(f"  Orders {summary['orders']}  Fills {summary['fills']}")
    print(f"  Realized P&L ${realized:+,.2f}  Unrealized "
          f"{'$%+.2f' % summary['unrealized_pnl'] if summary['unrealized_pnl'] is not None else 'n/a'}")
    print(f"  Equity {'$%.2f' % summary['equity'] if summary['equity'] is not None else 'n/a'}  "
          f"Position {summary['position_qty']} SPY")
    return summary


def cmd_aoi_set(now: datetime, zone_specs: list[str], tag_specs: list[str], source: str) -> dict:
    """Scout: publish today's boxes as Ops read them. Only inside 09:30–09:59 ET on a weekday."""
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
        "symbol": "SPY",
        "tradable": bool(zones),
        "written_at": t.isoformat(timespec="seconds"),
        "source": source,
        "approximate": False,
        "zones": zones,
        "note": "Same-day Mxwll read from the 09:30–09:59 ET open." if zones else "No readable boxes.",
    }
    save_json("aoi_override.json", data)
    journal.log("aoi", now=now, tradable=data["tradable"], zones=zones, source=source)
    rebuild_dashboard.write_state(now)
    print(f"aoi_override.json: {len(zones)} zone(s), tradable={data['tradable']}")
    return data


def cmd_aoi_clear(now: datetime, reason: str) -> dict:
    data = {"symbol": "SPY", "tradable": False, "zones": [],
            "written_at": now.astimezone(ET).isoformat(timespec="seconds"), "note": reason}
    save_json("aoi_override.json", data)
    journal.log("aoi", now=now, tradable=False, zones=[], note=reason)
    rebuild_dashboard.write_state(now)
    print(f"aoi_override.json cleared — {reason}. No tradable AOI today.")
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
    parser = argparse.ArgumentParser(description="SPY paper desk (paper only).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("eval", help="evaluate and log; never sends an order")
    sub.add_parser("paper", help="evaluate; submit one paper order only if the gate passes")
    sub.add_parser("sync", help="pull paper fills and the account snapshot")
    sub.add_parser("review", help="end-of-day review")
    aoi = sub.add_parser("aoi", help="scout: write aoi_override.json")
    aoi_sub = aoi.add_subparsers(dest="aoi_cmd", required=True)
    aoi_set = aoi_sub.add_parser("set")
    aoi_set.add_argument("--zone", action="append", default=[], help="color:low:high (repeatable)")
    aoi_set.add_argument("--tag", action="append", default=[], help="N:TAG, e.g. 1:CHoCH or 2:order_block")
    aoi_set.add_argument("--source", default="Ops live read of Roy's Mxwll chart")
    aoi_clear = aoi_sub.add_parser("clear")
    aoi_clear.add_argument("--reason", default="No readable Mxwll boxes from the open.")
    pulse = sub.add_parser("pulse", help="write today's Market Pulse bias")
    pulse_sub = pulse.add_subparsers(dest="pulse_cmd", required=True)
    pulse_set = pulse_sub.add_parser("set")
    pulse_set.add_argument("bias", choices=["bullish", "bearish", "neutral"])
    pulse_set.add_argument("--note", default="")
    for name in ("market_pulse", "macro_policy", "sentiment_flow", "rates_jobs"):
        pulse_set.add_argument(f"--{name.replace('_', '-')}", dest=name, default=None)
    args = parser.parse_args(argv)

    now = now_et()
    if args.cmd == "eval":
        cmd_eval(now)
    elif args.cmd == "paper":
        cmd_paper(now)
    elif args.cmd == "sync":
        cmd_sync(now)
    elif args.cmd == "review":
        cmd_review(now)
    elif args.cmd == "aoi" and args.aoi_cmd == "set":
        cmd_aoi_set(now, args.zone, args.tag, args.source)
    elif args.cmd == "aoi" and args.aoi_cmd == "clear":
        cmd_aoi_clear(now, args.reason)
    elif args.cmd == "pulse":
        sources = {k: getattr(args, k) for k in ("market_pulse", "macro_policy", "sentiment_flow", "rates_jobs")}
        cmd_pulse_set(now, args.bias, args.note, sources)
    return 0


if __name__ == "__main__":
    sys.exit(main())
