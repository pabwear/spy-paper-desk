"""Learning from the desk's own mistakes and failures.

Three parts, all trained only on closed paper round trips and the journal:

1. Mistake catalog. Every closed trade is tagged with what went wrong or
   was weak: stopped out, flattened at 16:00 for a loss, entered against
   Market Pulse, only the minimum confluence, a second entry right after a
   loss, a late entry, outside the box. For each tag it shows how many trades,
   how many lost and the average P&L. Operational failures (rejected orders,
   failed exits, missing data) are counted from the journal.
2. Signal multipliers. A Beta-smoothed win rate per confluence tag becomes a
   multiplier, clipped to [0.5, 1.5], on that tag's weight. It is written to
   learning_weights.json once a tag has enough closed trades. It changes
   which zone ranks first. It never changes the 2-signal minimum or the gate.
3. Loss model. An L2 logistic regression (pure Python) estimates P(loss)
   from the setup at entry. It is scored walk-forward against the base rate.
   In "shadow" mode it only logs. In "veto" mode it may only SKIP an entry,
   and only once it has min_trades_to_act trades and beats the base rate
   out-of-sample. It can never add an order, add contracts or bypass the gate.

    python3 learning.py      # retrain from the ledger and print the report
"""

from __future__ import annotations

import math
import sys
from datetime import datetime

import journal
from common import ET, load_json, now_et, save_json, to_et

TAG_KEYS = ["structure", "order_blocks", "rsi", "vwap", "volume", "session"]
FEATURES = [*(f"tag_{t}" for t in TAG_KEYS), "signal_buy", "rsi_edge", "vwap_edge_pct", "dist_mid_pct",
            "inside_box", "minutes_after_10", "pulse", "entry_number", "n_tags"]
MODEL_FILE = "ml_model.json"
MIN_TRAIN = 10


# ---------------------------------------------------------------- features

def features_at_entry(*, signal: str, tags: list[str], market: dict, ranked: dict, now: datetime,
                      pulse_bias: str | None, entries_today: int) -> dict:
    """The setup as numbers, captured when the entry is evaluated (no hindsight)."""
    price = float(market.get("price") or 0)
    rsi = market.get("rsi")
    vwap = market.get("vwap")
    buy = signal == "buy"
    if rsi is None:
        rsi_edge = 0.0
    else:
        rsi_edge = ((50 - rsi) if buy else (rsi - 50)) / 50.0
    if vwap is None or not price:
        vwap_edge = 0.0
    else:
        vwap_edge = ((vwap - price) if buy else (price - vwap)) / price * 100.0
    t = now.astimezone(ET)
    minutes = (t.hour * 60 + t.minute) - 600
    agrees = {"buy": "bullish", "sell": "bearish"}[signal]
    against = {"buy": "bearish", "sell": "bullish"}[signal]
    pulse = 1.0 if pulse_bias == agrees else -1.0 if pulse_bias == against else 0.0
    f = {f"tag_{k}": 1.0 if k in tags else 0.0 for k in TAG_KEYS}
    f.update(signal_buy=1.0 if buy else 0.0, rsi_edge=round(rsi_edge, 4), vwap_edge_pct=round(vwap_edge, 4),
             dist_mid_pct=round(abs(float(ranked.get("distance_pct") or 0)), 4),
             inside_box=1.0 if ranked.get("inside") else 0.0, minutes_after_10=round(minutes / 360.0, 4),
             pulse=pulse, entry_number=float(entries_today), n_tags=float(len(tags)))
    return f


def _vec(features: dict) -> list[float]:
    return [float(features.get(k, 0.0) or 0.0) for k in FEATURES]


# ---------------------------------------------------------------- logistic regression

def _sigmoid(z: float) -> float:
    if z < -35:
        return 0.0
    if z > 35:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def fit(rows: list[list[float]], labels: list[int], l2: float = 1.0, iters: int = 800, lr: float = 0.2) -> dict:
    n, d = len(rows), len(FEATURES)
    means = [sum(r[j] for r in rows) / n for j in range(d)]
    stds = []
    for j in range(d):
        var = sum((r[j] - means[j]) ** 2 for r in rows) / n
        stds.append(math.sqrt(var) if var > 1e-12 else 1.0)
    X = [[(r[j] - means[j]) / stds[j] for j in range(d)] for r in rows]
    w = [0.0] * d
    base = sum(labels) / n
    b = math.log((base + 1e-3) / (1 - base + 1e-3))
    for _ in range(iters):
        gw = [0.0] * d
        gb = 0.0
        for x, y in zip(X, labels):
            err = _sigmoid(b + sum(wi * xi for wi, xi in zip(w, x))) - y
            gb += err
            for j in range(d):
                gw[j] += err * x[j]
        b -= lr * gb / n
        w = [wj - lr * (gw[j] / n + l2 * wj / n) for j, wj in enumerate(w)]
    return {"weights": dict(zip(FEATURES, [round(v, 5) for v in w])), "bias": round(b, 5),
            "means": dict(zip(FEATURES, means)), "stds": dict(zip(FEATURES, stds))}


def predict(model: dict | None, features: dict) -> float | None:
    if not model or not model.get("weights"):
        return None
    z = model["bias"]
    for k in FEATURES:
        x = (float(features.get(k, 0.0) or 0.0) - model["means"][k]) / model["stds"][k]
        z += model["weights"][k] * x
    return round(_sigmoid(z), 4)


def walk_forward(rows: list[list[float]], labels: list[int], l2: float) -> dict:
    """Train on trades 1..i, predict trade i+1. Compare with predicting the running loss rate."""
    n = len(rows)
    if n <= MIN_TRAIN:
        return {"tested": 0}
    hits = base_hits = 0
    brier = base_brier = 0.0
    for i in range(MIN_TRAIN, n):
        m = fit(rows[:i], labels[:i], l2=l2, iters=300)
        p = predict(m, dict(zip(FEATURES, rows[i])))
        rate = sum(labels[:i]) / i
        y = labels[i]
        hits += int((p >= 0.5) == bool(y))
        base_hits += int((rate >= 0.5) == bool(y))
        brier += (p - y) ** 2
        base_brier += (rate - y) ** 2
    tested = n - MIN_TRAIN
    return {"tested": tested, "accuracy": round(hits / tested, 3), "base_accuracy": round(base_hits / tested, 3),
            "brier": round(brier / tested, 4), "base_brier": round(base_brier / tested, 4),
            "beats_base_rate": brier < base_brier}


# ---------------------------------------------------------------- mistakes

def _mistake_tags(trip: dict, prev_same_day: dict | None, min_conf: int) -> list[str]:
    f = trip.get("features") or {}
    out = []
    if trip.get("exit_reason") == "stop":
        out.append("stopped_out")
    if trip.get("exit_reason") == "flatten" and trip["pnl"] < 0:
        out.append("flattened_red_at_close")
    if trip.get("exit_reason") == "overnight":
        out.append("held_overnight")
    if f.get("pulse", 0) < 0:
        out.append("against_market_pulse")
    if f and f.get("n_tags", 0) <= min_conf:
        out.append("minimum_confluence_only")
    if f and f.get("inside_box", 1) == 0:
        out.append("outside_box_entry")
    if f and f.get("minutes_after_10", 0) * 360 >= 300:
        out.append("late_entry_after_15")
    if f and abs(f.get("rsi_edge", 0)) < 0.1:
        out.append("rsi_near_50")
    if prev_same_day and prev_same_day.get("pnl", 0) < 0:
        out.append("re_entry_after_loss")
    return out


MISTAKE_LABELS = {
    "stopped_out": "Hit the 0.35% stop",
    "flattened_red_at_close": "Flattened at 16:00 for a loss",
    "held_overnight": "Held overnight (flatten missed)",
    "against_market_pulse": "Entered against Market Pulse",
    "minimum_confluence_only": "Only the minimum confluence",
    "outside_box_entry": "Entered near, not inside, the box",
    "late_entry_after_15": "Entered after 15:00",
    "rsi_near_50": "RSI within 5 of 50",
    "re_entry_after_loss": "Second entry right after a loss",
}

OUTCOME_TAGS = {"stopped_out", "flattened_red_at_close", "held_overnight"}

OPS_FAILURES = {
    "order_rejected": "Entry order rejected",
    "exit_failed": "Exit order failed",
    "no_market_data": "No market data",
    "no_contract": "No listed option contract found",
}


def mistake_report(trips: list[dict], events: list[dict], min_conf: int) -> dict:
    by_tag: dict[str, list[dict]] = {k: [] for k in MISTAKE_LABELS}
    prev_by_day: dict[str, dict] = {}
    for trip in trips:
        day = (to_et(trip.get("opened_at")) or now_et()).date().isoformat()
        trip["mistakes"] = _mistake_tags(trip, prev_by_day.get(day), min_conf)
        for tag in trip["mistakes"]:
            by_tag[tag].append(trip)
        prev_by_day[day] = trip
    n = len(trips)
    losses = sum(1 for t in trips if t["pnl"] < 0)
    overall = losses / n if n else None
    rows = []
    for tag, ts in by_tag.items():
        lost = sum(1 for t in ts if t["pnl"] < 0)
        rows.append({"tag": tag, "label": MISTAKE_LABELS[tag], "trades": len(ts), "losses": lost,
                     "loss_rate": round(lost / len(ts), 3) if ts else None,
                     "avg_pnl": round(sum(t["pnl"] for t in ts) / len(ts), 2) if ts else None,
                     "total_pnl": round(sum(t["pnl"] for t in ts), 2)})
    rows.sort(key=lambda r: (r["total_pnl"] if r["trades"] else 0))
    lessons = []
    for r in rows:
        if r["tag"] in OUTCOME_TAGS:
            continue  # "stopped out → lost" restates the outcome; lessons are about the setup
        if r["trades"] >= 3 and overall is not None and r["loss_rate"] is not None and r["loss_rate"] >= overall + 0.15:
            lessons.append(f"{r['label']}: lost {r['losses']} of {r['trades']} ({r['loss_rate']:.0%}) "
                           f"vs {overall:.0%} overall, total ${r['total_pnl']:+,.2f}.")
    ops: dict[str, int] = {k: 0 for k in OPS_FAILURES}
    for e in events:
        if e.get("event") in ("order_rejected", "exit_failed"):
            ops[e["event"]] += 1
        for r in e.get("reasons") or []:
            if r in ops:
                ops[r] += 1
    return {"trades": n, "losses": losses, "loss_rate": round(overall, 3) if overall is not None else None,
            "mistakes": rows, "lessons": lessons,
            "ops_failures": [{"tag": k, "label": OPS_FAILURES[k], "count": v} for k, v in ops.items()]}


# ---------------------------------------------------------------- tag multipliers

def tag_multipliers(trips: list[dict], min_samples: int, lo: float, hi: float) -> tuple[dict, list[dict]]:
    rows, mult = [], {}
    for tag in TAG_KEYS:
        ts = [t for t in trips if tag in (t.get("tags") or [])]
        wins = sum(1 for t in ts if t["pnl"] > 0)
        rate = (wins + 1) / (len(ts) + 2)  # Beta(1,1) smoothing
        m = round(min(hi, max(lo, rate / 0.5)), 3)
        active = len(ts) >= min_samples
        if active:
            mult[tag] = m
        rows.append({"tag": tag, "trades": len(ts), "wins": wins,
                     "win_rate": round(wins / len(ts), 3) if ts else None,
                     "pnl": round(sum(t["pnl"] for t in ts), 2), "multiplier": m if active else 1.0,
                     "active": active})
    return mult, rows


# ---------------------------------------------------------------- orchestration

def ml_config() -> dict:
    return (load_json("learning_weights.json", {}) or {}).get("ml", {})


def load_model() -> dict | None:
    return load_json(MODEL_FILE, None)


def may_veto(model: dict | None, p_loss: float | None, cfg: dict) -> tuple[bool, str]:
    """True only in veto mode, with enough trades, a model that beats the base rate, and a high P(loss)."""
    if p_loss is None or not model:
        return False, "no model yet"
    if cfg.get("mode") != "veto":
        return False, f"shadow mode (P(loss) {p_loss:.0%} logged only)"
    if model.get("trained_on", 0) < int(cfg.get("min_trades_to_act", 30)):
        return False, f"only {model.get('trained_on', 0)} closed trades; acts from {cfg.get('min_trades_to_act', 30)}"
    if not (model.get("walk_forward") or {}).get("beats_base_rate"):
        return False, "model does not beat the base rate yet"
    if p_loss < float(cfg.get("veto_threshold", 0.65)):
        return False, f"P(loss) {p_loss:.0%} under the {float(cfg.get('veto_threshold', 0.65)):.0%} threshold"
    return True, f"P(loss) {p_loss:.0%} ≥ {float(cfg.get('veto_threshold', 0.65)):.0%}"


def learn(now: datetime | None = None) -> dict:
    now = (now or now_et()).astimezone(ET)
    events = journal.read_events()
    trips = journal.round_trips(journal.read_trades(), events)
    rules = load_json("rules.json", {}) or {}
    lw = load_json("learning_weights.json", {}) or {}
    cfg = lw.get("ml", {})
    lo, hi = (cfg.get("multiplier_range") or [0.5, 1.5])
    min_conf = int(rules.get("min_confluence", 2))

    report = mistake_report(trips, events, min_conf)
    mult, tag_rows = tag_multipliers(trips, int(cfg.get("min_tag_samples", 10)), float(lo), float(hi))

    learnable = [t for t in trips if t.get("features")]
    rows = [_vec(t["features"]) for t in learnable]
    labels = [1 if t["pnl"] < 0 else 0 for t in learnable]
    model = None
    if len(learnable) >= MIN_TRAIN and 0 < sum(labels) < len(labels):
        l2 = float(cfg.get("l2", 1.0))
        model = fit(rows, labels, l2=l2)
        model.update(trained_on=len(learnable), loss_rate=round(sum(labels) / len(labels), 3),
                     walk_forward=walk_forward(rows, labels, l2), trained_at=now.isoformat(timespec="seconds"))
        save_json(MODEL_FILE, model)

    lw["learned_multipliers"] = mult
    lw["learned_at"] = now.isoformat(timespec="seconds")
    save_json("learning_weights.json", lw)

    status = {
        "trained_at": now.isoformat(timespec="seconds"),
        "mode": cfg.get("mode", "shadow"),
        "closed_trades": len(trips),
        "learnable_trades": len(learnable),
        "min_trades_to_act": cfg.get("min_trades_to_act", 30),
        "model": {k: model[k] for k in ("trained_on", "loss_rate", "walk_forward", "weights")} if model else None,
        "tags": tag_rows,
        **report,
        "recent": [{k: t.get(k) for k in ("symbol", "opened_at", "closed_at", "pnl", "result", "exit_reason",
                                           "mistakes", "p_loss", "tags")} for t in trips[-30:]][::-1],
    }
    save_json("learning_report.json", status)
    journal.log("learn", now=now, closed_trades=len(trips), model_trained=bool(model),
                lessons=report["lessons"], multipliers=mult)
    return status


def print_report(r: dict) -> None:
    print(f"Learning — {r['closed_trades']} closed trade(s), mode {r['mode']}")
    if r.get("loss_rate") is not None:
        print(f"  Loss rate {r['loss_rate']:.0%}")
    m = r.get("model")
    if m:
        wf = m["walk_forward"]
        if wf.get("tested"):
            print(f"  Model walk-forward on {wf['tested']} trades: accuracy {wf['accuracy']:.0%} "
                  f"(base {wf['base_accuracy']:.0%}), Brier {wf['brier']} (base {wf['base_brier']}) — "
                  f"{'beats' if wf['beats_base_rate'] else 'does not beat'} the base rate")
    else:
        print(f"  Model: needs {MIN_TRAIN} closed trades with both wins and losses")
    for lesson in r["lessons"]:
        print(f"  Lesson: {lesson}")
    for f in r["ops_failures"]:
        if f["count"]:
            print(f"  Failure: {f['label']} × {f['count']}")


if __name__ == "__main__":
    print_report(learn())
    sys.exit(0)
