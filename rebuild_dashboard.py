"""Build the console's state from the desk files.

    python3 rebuild_dashboard.py      # writes dashboard_state.json (gitignored)

The PIN-locked console (console.py) serves the same state live at /api/state;
dashboard.html itself holds no account data.
"""

from __future__ import annotations

import sys
from datetime import datetime

import instruments
import journal
from common import ET, SESSION_LABELS, aoi_file, hhmm, load_json, now_et, save_json, session_state, to_et, watchlist
from gate import is_approximate, override_freshness

FILES = ["watchlist.json", "alpaca_config.json", "rules.json", "risk.json", "study.json", "account.json", "aoi_override.json",
         "learning_weights.json", "market_pulse.json", "trades.csv"]


def _desk_positions(account: dict, events: list[dict], rules: dict, risk: dict, now: datetime) -> list[dict]:
    """Open SPY/SPY-option positions with their stop level and flatten time."""
    entered = [e.get("symbol") for e in events if e.get("event") == "order" and e.get("role") == "entry"]
    managed = instruments.desk_underlyings(rules, watchlist(), entered)
    raw = account.get("positions")
    if raw is None:
        raw = [account["position"]] if account.get("position") else []
    out = []
    pct = float(rules.get("stop_underlying_pct", 0.35))
    all_books = instruments.books(rules, watchlist())
    for p in raw:
        if not p or not instruments.is_desk_symbol(p.get("symbol", ""), managed) or not float(p.get("qty") or 0):
            continue
        entry = next((e for e in reversed(events) if e.get("event") == "order" and e.get("role") == "entry"
                      and e.get("symbol") == p["symbol"]), {})
        exposure = instruments.direction(p["symbol"], float(p["qty"]))
        underlying = instruments.underlying_of(p["symbol"])
        ref = entry.get("underlying_price") or (p.get("avg_entry_price") if p["symbol"] == underlying else None)
        occ = instruments.parse_occ(p["symbol"])
        book = instruments.book_of(p["symbol"], all_books) or {}
        level = (float(entry["stop_level"]) if entry.get("stop_level") is not None
                 else instruments.stop_level(exposure, float(ref), pct) if ref else None)
        opened = to_et(entry.get("ts"))
        out.append({
            **p,
            "underlying": underlying,
            "asset": "option" if occ else "shares",
            "right": occ["right"] if occ else None,
            "strike": occ["strike"] if occ else None,
            "expiry": occ["expiry"].isoformat() if occ else None,
            "multiplier": 100 if occ else 1,
            "exposure": exposure,
            "underlying_entry": ref,
            "stop_level": level,
            "stop_at_broker": bool(entry.get("stop_at_broker")),
            "book": book.get("id"),
            "book_label": book.get("label"),
            "flatten_at": risk.get("flatten_start"),
            "overnight": bool(opened and opened.date() < now.date()),
            "entry_tags": entry.get("tags"),
            "entry_zone": entry.get("zone"),
            "p_loss": entry.get("p_loss"),
        })
    return out


def _files_status() -> list[dict]:
    from common import path

    out = []
    for name in FILES:
        p = path(name)
        status = "ok" if p.exists() else "missing"
        if p.exists() and name.endswith(".json"):
            try:
                load_json(name)
            except ValueError:
                status = "invalid JSON"
        out.append({"name": name, "status": status})
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _aoi_state(symbol: str, now: datetime, risk: dict) -> dict:
    override = load_json(aoi_file(symbol), None)
    fresh, why = override_freshness(override, now, risk) if risk else (False, "risk.json missing")
    return {
        "symbol": symbol,
        "file": aoi_file(symbol),
        "tradable": bool(override and override.get("tradable")),
        "fresh": fresh,
        "freshness": why,
        "approximate": is_approximate(override),
        "written_at": (override or {}).get("written_at"),
        "source": (override or {}).get("source"),
        "note": (override or {}).get("note"),
        "zones": (override or {}).get("zones") or [],
    }


def _stop_text(stop: dict) -> str:
    if (stop or {}).get("pct") is not None:
        return f"{float(stop['pct']):g}% against the entry"
    if (stop or {}).get("range_fraction") is not None:
        return f"{float(stop['range_fraction']):g} × the stock's average daily range ({int(stop.get('days', 20))} days)"
    return "—"


def _books_state(rules: dict, events: list[dict], trips: list[dict], positions: list[dict], now: datetime) -> list[dict]:
    """Each trading book on its own: its rules, today's entries, what it holds, and its closed trades."""
    from run_study import book_entries_on

    w = watchlist()
    all_books = instruments.books(rules, w)
    out = []
    for b in all_books:
        mine = [t for t in trips if (t.get("book") or (instruments.book_of(t.get("symbol") or "", all_books) or {}).get("id"))
                == b["id"] and t.get("closed_at")]
        pnl = [float(t.get("pnl") or 0) for t in mine]
        cum, series = 0.0, []
        for t, v in zip(mine, pnl):
            cum += v
            series.append({"t": t["closed_at"], "v": round(cum, 2)})
        out.append({
            "id": b["id"], "label": b["label"], "symbol": b["symbol"], "asset": b["asset"], "enabled": b["enabled"],
            "budget_usd": b.get("budget_usd"), "stop": _stop_text(b.get("stop")), "stop_at_broker": bool(b.get("stop_at_broker")),
            "max_hold_minutes": b.get("max_hold_minutes"), "max_entries_per_day": b.get("max_entries_per_day"),
            "purpose": b.get("purpose"), "entries_today": book_entries_on(events, now.date(), b, all_books),
            "positions": [p for p in positions if p.get("book") == b["id"]],
            "closed_trades": len(mine), "wins": sum(1 for v in pnl if v > 0), "realized": round(sum(pnl), 2),
            "win_rate_pct": round(100 * sum(1 for v in pnl if v > 0) / len(pnl), 1) if pnl else None,
            "cum_pnl": series[-300:],
            "trades": [{k: t.get(k) for k in ("symbol", "opened_at", "closed_at", "pnl", "result", "exit_reason",
                                               "entry_price", "exit_price", "underlying_entry", "qty")} for t in mine][-50:],
        })
    return out


def _focus_state(rules: dict, risk: dict, events: list[dict], now: datetime) -> list[dict]:
    w = watchlist()
    out = []
    for sym, entry in w["symbols"].items():
        inst = instruments.for_symbol(sym, rules, w)
        mine = instruments.books_for(sym, rules, w)
        if mine:
            inst = {"name": ", ".join(b["id"] for b in mine), "asset": "/".join(sorted({b["asset"] for b in mine})),
                    "enabled": any(b["enabled"] for b in mine),
                    "why": "; ".join(f"{b['label']} {'on' if b['enabled'] else 'off'}" for b in mine)}
        last = next((e for e in reversed(events) if e.get("event") in ("eval", "skip")
                     and (e.get("symbol") or "SPY") == sym), None)
        out.append({
            "symbol": sym,
            "focus": sym in w["focus"],
            "rank": w["focus"].index(sym) + 1 if sym in w["focus"] else None,
            "instrument": inst.get("name"),
            "asset": inst.get("asset"),
            "trading": inst["enabled"],
            "why": inst["why"],
            "chart": entry.get("chart") or (rules.get("chart") if sym == "SPY" else None),
            "aoi": _aoi_state(sym, now, risk),
            "last_eval": last,
        })
    out.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0, r["symbol"]))
    return out


def _sentiment_state(now: datetime) -> dict:
    """The hourly reader's history: numbers over time, the newest reading in full, and the plays' record."""
    try:
        import sentiment

        return {"series": sentiment.series(45, now), "latest": sentiment.latest_full(), "plays": sentiment.plays_summary(),
                "rumor_mill": sentiment.rumor_mill(3, now)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:200]}


BACKTEST_ROWS = [("current", "Desk now (checks every 10 min)"), ("current_every_minute", "Same rules, checked every minute"),
                 ("current_benchmark", "Just a call at 10:00 every day"), ("desk_now", "Original rules (before Oct 7)")]
_PART = ("trades", "win_pct", "total", "per_trade", "max_drawdown", "avg_win", "avg_loss")


def _tests_state() -> dict:
    """The backtest of the current setup (backtests/current.json), the four-ideas backtest (backtests/ideas.json)
    and the newest dress rehearsal (backtests/rehearsal.json), trimmed for the Record view's charts.
    Missing files are just missing."""
    out: dict = {}
    bt = load_json("backtests/current.json", None)
    if bt and bt.get("named"):
        named, monthly, years = bt.get("named") or {}, bt.get("monthly") or {}, bt.get("by_year") or {}
        out["backtest"] = {
            "days": bt.get("days"), "first": bt.get("first"), "last": bt.get("last"),
            "train_range": bt.get("train_range"), "test_range": bt.get("test_range"),
            "cost_per_trade": bt.get("cost_per_trade"),
            "rows": [{"name": n, "label": label, **{part: {k: (named[n].get(part) or {}).get(k) for k in _PART}
                                                    for part in ("train", "test", "all")}}
                     for n, label in BACKTEST_ROWS if n in named],
            "monthly": {n: [[m["month"], m.get("total"), m.get("trades"), m.get("win_pct")] for m in monthly[n]]
                        for n, _ in BACKTEST_ROWS if n in monthly},
            "by_year": {n: {y: {k: v.get(k) for k in ("trades", "total", "win_pct")} for y, v in years[n].items()}
                        for n, _ in BACKTEST_ROWS if n in years},
        }
    ib = load_json("backtests/ideas.json", None)
    if ib and ib.get("ideas"):
        out["ideas"] = {
            **{k: ib.get(k) for k in ("days", "first", "last", "train_range", "test_range", "book", "share_cost",
                                      "option_cost", "trail_pct", "swing_days", "exits")},
            "rows": [{"name": i["name"], "label": i["label"], "what": i["what"],
                      **{part: {k: (i.get(part) or {}).get(k) for k in _PART} for part in ("train", "test", "all")}}
                     for i in ib["ideas"]],
            "monthly": {n: [[m["month"], m.get("total"), m.get("trades"), m.get("win_pct")] for m in ms]
                        for n, ms in (ib.get("monthly") or {}).items()},
            "by_year": {n: {y: {k: v.get(k) for k in ("trades", "total", "win_pct")} for y, v in ys.items()}
                        for n, ys in (ib.get("by_year") or {}).items()},
        }
    rh = load_json("backtests/rehearsal.json", None)
    if rh and rh.get("days"):
        out["rehearsal"] = {k: rh.get(k) for k in ("ran_at", "days", "heartbeats", "crashes", "failures", "equity",
                                                    "trades", "start_equity", "end_equity", "skip_reasons", "still_open")}
    return out


def build_state(now: datetime | None = None) -> dict:
    now = (now or now_et()).astimezone(ET)
    config = load_json("alpaca_config.json", {})
    rules = load_json("rules.json", {})
    risk = load_json("risk.json", {})
    study = load_json("study.json", {})
    account = load_json("account.json", {}) or {}
    override = load_json("aoi_override.json", None)
    pulse = load_json("market_pulse.json", {}) or {}
    weights = load_json("learning_weights.json", {}) or {}
    events = journal.read_events()
    trades = journal.read_trades()
    today = now.date()
    today_events = journal.events_on(events, today)

    book = float(risk.get("target_book_usd") or study.get("starting_capital") or 1000)
    sizing = float(risk.get("size_as_if_equity_usd") or book)
    equity = _f(account.get("equity"))

    stats = journal.trade_stats(trades)
    daily = journal.daily_realized(trades)
    realized_today = next((d["pnl"] for d in daily if d["date"] == today.isoformat()), 0.0)

    # Equity curve: starting book, then every paper account snapshot.
    curve = [{"t": None, "equity": book, "label": "Start"}]
    for e in events:
        if e.get("event") == "account" and _f(e.get("equity")) is not None:
            curve.append({"t": e["ts"], "equity": float(e["equity"])})

    # Evaluations and passes (skips).
    def tally(evts):
        reasons: dict[str, int] = {}
        decisions: dict[str, int] = {}
        for e in evts:
            if e.get("event") not in ("eval", "skip"):
                continue
            decisions[e.get("decision", "?")] = decisions.get(e.get("decision", "?"), 0) + 1
            if e.get("event") == "skip":
                for r in e.get("reasons") or ["unspecified"]:
                    reasons[r] = reasons.get(r, 0) + 1
        evaluated = sum(decisions.values())
        passes = decisions.get("skip", 0)
        return {
            "evaluated": evaluated,
            "passes": passes,
            "watch": decisions.get("watch", 0),
            "would_enter": decisions.get("would_enter", 0),
            "entered": sum(1 for e in evts if e.get("event") == "order"),
            "pass_rate_pct": round(passes / evaluated * 100, 1) if evaluated else None,
            "pass_reasons": [{"reason": k, "count": v} for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])],
        }

    last_eval = next((e for e in reversed(events) if e.get("event") in ("eval", "skip")), None)
    last_price = _f(((last_eval or {}).get("market") or {}).get("price"))
    fresh, fresh_why = override_freshness(override, now, risk) if risk else (False, "risk.json missing")
    notional_cap = sizing * float(risk.get("max_notional_pct_of_sizing_equity", 25)) / 100.0

    positions = _desk_positions(account, events, rules, risk, now)
    trips = journal.round_trips(trades, events)
    unrealized = round(sum(_f(p.get("unrealized_pl")) or 0.0 for p in positions), 2) if positions else None
    inst = instruments.active(rules)
    enabled_books = [b for b in instruments.books(rules, watchlist()) if b["enabled"]]
    if enabled_books:  # books decide now; "active" names the first for older readers
        inst = {"name": enabled_books[0]["id"], "enabled": True,
                "why": ", ".join(f"{b['label']} on" for b in enabled_books)}
    learn_report = load_json("learning_report.json", None)
    model = load_json("ml_model.json", None)
    entries_today = journal.entries_on(events, today)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "session": {
            "state": session_state(now, risk) if risk else "unknown",
            "label": SESSION_LABELS.get(session_state(now, risk), "") if risk else "",
            "now_et": now.isoformat(timespec="seconds"),
        },
        "config": {
            "account_name": config.get("account_name"),
            "account_number": config.get("account_number"),
            "mode": config.get("mode"),
            "base_url": config.get("base_url"),
            "live_unlocked": config.get("live_unlocked"),
            "live_trading": rules.get("live_trading"),
            "symbol": config.get("symbol"),
            "chart": rules.get("chart"),
            "study": study.get("name"),
        },
        "account": {
            **{k: account.get(k) for k in ("source", "account_number", "status", "snapshot_at", "equity", "cash",
                                           "buying_power", "last_equity")},
            "matches_expected": (account.get("account_number") == config.get("account_number"))
            if account.get("account_number") else None,
            "change_usd": round(equity - book, 2) if equity is not None else None,
            "change_pct": round((equity - book) / book * 100, 2) if equity is not None else None,
            "day_change_usd": round(equity - float(account["last_equity"]), 2)
            if equity is not None and _f(account.get("last_equity")) is not None else None,
        },
        "position": positions[0] if positions else None,
        "positions": positions,
        "instrument": {
            "active": inst.get("name"),
            "enabled": inst["enabled"],
            "why": inst["why"],
            "flags": instruments.flags(rules),
            "option": rules.get("option"),
            "stop_underlying_pct": rules.get("stop_underlying_pct"),
            "exit_on_zone_leave": rules.get("exit_on_zone_leave"),
            "hold_to": rules.get("hold_to"),
            "overnight": rules.get("overnight"),
            "min_confluence": rules.get("min_confluence"),
            "trade_colors": rules.get("trade_colors") or ["red", "green"],
            "max_hold_minutes": rules.get("max_hold_minutes") or 0,
            "max_entries_per_day": rules.get("max_entries_per_day"),
            "entries_today": entries_today,
            "entry_cutoff": risk.get("entry_cutoff"),
            "flatten_start": risk.get("flatten_start"),
            "past_flatten": bool(risk.get("flatten_start")) and now.time() >= hhmm(risk["flatten_start"]),
        },
        "learning": {
            "config": (weights or {}).get("ml"),
            "multipliers": (weights or {}).get("learned_multipliers") or {},
            "learned_at": (weights or {}).get("learned_at"),
            "report": learn_report,
            "model": {k: model.get(k) for k in ("trained_on", "loss_rate", "walk_forward", "weights", "trained_at")}
            if model else None,
        },
        "projection_learning": {
            tf: {k: m.get(k) for k in ("n", "direction_hit_pct", "inside_50_pct", "inside_80_pct", "weights", "leader",
                                       "s50", "s80", "updated")} | {"days": dict(list((m.get("days") or {}).items())[-20:])}
            for tf, m in (load_json("projection_model.json", {}) or {}).items()
        },
        "pnl": {
            "book": book,
            "realized_total": stats["realized_pnl"],
            "realized_today": realized_today,
            "unrealized": unrealized,
            "net": round(stats["realized_pnl"] + (unrealized or 0.0), 2),
        },
        "stats": stats,
        "daily_pnl": daily,
        "equity_curve": curve,
        "round_trips": [{k: t.get(k) for k in ("symbol", "opened_at", "closed_at", "pnl", "result", "exit_reason",
                                               "entry_price", "exit_price", "underlying_entry", "zone", "context", "book")}
                        for t in trips][-300:],
        "books": _books_state(rules, events, trips, positions, now),
        "sentiment": _sentiment_state(now),
        "tests": _tests_state(),
        "risk": {
            "book": book,
            "sizing_equity": sizing,
            "max_risk_usd": round(sizing * float(risk.get("max_risk_pct_per_idea", 10)) / 100.0, 2),
            "max_risk_pct": risk.get("max_risk_pct_per_idea"),
            "max_notional_usd": round(notional_cap, 2),
            "max_notional_pct": risk.get("max_notional_pct_of_sizing_equity"),
            "last_price": last_price,
            "qty_at_last_price": round(notional_cap / last_price, 4) if last_price else None,
            "zone_tolerance_pct": risk.get("zone_midpoint_tolerance_pct", 0.15),
            "pulse_contradiction": risk.get("pulse_contradiction"),
            "windows": {k: risk.get(k) for k in ("rth_open", "rth_watch_only_until", "rth_close")},
        },
        "aoi": {
            "tradable": bool(override and override.get("tradable")),
            "fresh": fresh,
            "freshness": fresh_why,
            "approximate": is_approximate(override),
            "written_at": (override or {}).get("written_at"),
            "source": (override or {}).get("source"),
            "note": (override or {}).get("note"),
            "zones": (override or {}).get("zones") or [],
        },
        "pulse": {
            **pulse,
            "is_today": pulse.get("date") == today.isoformat(),
        },
        "last_eval": last_eval,
        "focus": _focus_state(rules, risk, events, now),
        "charts": load_json("charts.json", {}) or {},
        "evals_today": tally(today_events),
        "evals_all": tally(events),
        "trades": list(reversed(trades)),
        "orders": [e for e in reversed(events) if e.get("event") in ("order", "order_rejected", "exit_failed")][:50],
        "reviews": [e for e in reversed(events) if e.get("event") == "review"][:30],
        "activity": [{k: v for k, v in e.items() if k not in ("zones", "study", "features", "gate", "aoi")}
                     for e in list(reversed(events))[:40]],
        "weights": weights,
        "rules": {
            "buy": (rules.get("aoi") or {}).get("buy"),
            "sell": (rules.get("aoi") or {}).get("sell_short_tp"),
            "confluence": (rules.get("confluence_required") or {}).get("of"),
            "min_confluence": rules.get("min_confluence"),
            "notes": (study.get("rules") or {}).get("notes"),
        },
        "files": _files_status(),
        "first_event_at": events[0]["ts"] if events else None,
        "trading_days_logged": len({(to_et(e["ts"]) or now).date() for e in events}),
    }


CHART_FILE = "chart_{}.json"  # the charts of every focus stock after the first, one file each


def write_state(now: datetime | None = None) -> dict:
    """dashboard_state.json (compact, kept well under GitHub's 1 MB inline limit for the dashboard to read) holds the
    first focus stock's chart in full; every other stock's chart goes to chart_<SYMBOL>.json, which the dashboard
    loads when that stock is picked, and the state keeps a short stub pointing at it."""
    state = build_state(now)
    focus = watchlist().get("focus") or ["SPY"]
    charts = state.get("charts") or {}
    for sym in list(charts):
        if sym == focus[0]:
            continue
        full = charts[sym]
        save_json(CHART_FILE.format(sym), full, compact=True)
        charts[sym] = {"symbol": sym, "stub": True, "file": CHART_FILE.format(sym), "updated_at": full.get("updated_at"),
                       "last": full.get("last"), "date": full.get("date")}
    save_json("dashboard_state.json", state, compact=True)
    return state


if __name__ == "__main__":
    s = write_state()
    print(f"dashboard_state.json rebuilt at {s['generated_at']}")
    sys.exit(0)
