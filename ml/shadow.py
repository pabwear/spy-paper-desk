"""Shadow mode: runs inside the desk's 10-minute cloud run, predicts, decides and LOGS. It never sends an order.

Each run:
  1. loads the exported model (ml/models/active; numpy only) and the kill switch (ml_kill_switch.json)
  2. fetches the last ~20 days of 1-minute bars (Alpaca IEX, the desk's keys; enough history for every feature)
  3. uses only COMPLETE minutes (a bar stamped t counts once t + 1 min has passed), builds the features
  4. predicts P(up_net) for the latest minute, with timing and an out-of-distribution flag
  5. asks the risk engine (with a simulated $1,000 account, for display) and the decision engine
  6. scores earlier shadow decisions whose horizon has passed (forward results, clearly hypothetical)
  7. writes ml_state.json (the dashboard) and appends ml_shadow.jsonl
Fails closed: any problem gives an UNAVAILABLE decision with its reason, never an order.

    python -m ml.shadow            # in desk.yml after the desk's own run
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from common import ET, load_json, path, save_json
from ml import config as mlconfig
from ml import data, decision, execution, features, labels, risk
from ml.tcn import ModelLoadError

STATE, LOG, KILL = "ml_state.json", "ml_shadow.jsonl", "ml_kill_switch.json"
ACTIVE = Path(__file__).resolve().parent / "models" / "active"      # committed: the exported model
SUMMARY = Path(__file__).resolve().parent / "research_summary.json"  # committed: the latest walk-forward verdicts


def kill_switch() -> dict:
    k = load_json(KILL, {}) or {}
    return {"on": bool(k.get("on")), "why": k.get("why"), "at": k.get("at")}


def read_log() -> list[dict]:
    p = path(LOG)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _session_open(now: datetime, cfg: dict) -> bool:
    n = now.astimezone(ET)
    m = n.hour * 60 + n.minute
    s = cfg["session"]
    return n.weekday() < 5 and _hhmm(s["first_decision"]) <= m <= _hhmm(s["last_entry"])


def _hhmm(s: str) -> int:
    h, mm = s.split(":")
    return int(h) * 60 + int(mm)


def score_forward(entries: list[dict], bars: pd.DataFrame, cfg: dict) -> list[dict]:
    """Fill in what actually happened for earlier decisions whose horizon has passed (hypothetical trades)."""
    if not len(bars):
        return entries
    cache: dict[int, pd.DataFrame] = {}
    t_index = {pd.Timestamp(t): i for i, t in enumerate(bars["t"])}
    for e in entries:
        if e.get("result") is not None or not e.get("bar_t"):
            continue
        h = int(e.get("horizon") or 0)
        i = t_index.get(pd.Timestamp(e["bar_t"]))
        if i is None or not h:
            continue
        lab = cache.setdefault(h, labels.build(bars, h, cfg))
        if pd.notna(lab.loc[i, "net"]):
            e["result"] = {"net_bps": round(float(lab.loc[i, "net"]), 2), "up_net": int(lab.loc[i, "up_net"])}
    return entries


def forward_summary(entries: list[dict]) -> dict:
    scored = [e for e in entries if e.get("result") is not None and e.get("p") is not None]
    sig = [e for e in scored if e.get("shadow_signal")]
    out = {"decisions": len(entries), "scored": len(scored), "signals": len(sig)}
    if scored:
        y = np.array([e["result"]["up_net"] for e in scored], float)
        p = np.array([e["p"] for e in scored], float)
        out["brier"] = round(float(np.mean((p - y) ** 2)), 4)
        out["base_rate"] = round(float(y.mean()), 4)
    if sig:
        nets = [e["result"]["net_bps"] for e in sig]
        out["signal_avg_net_bps"] = round(float(np.mean(nets)), 2)
        out["signal_win_rate"] = round(float(np.mean([n > 0 for n in nets])), 4)
    return out


def run(now: datetime | None = None, provider=None, model_dir: str | None = None, cfg: dict | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cfg = cfg or mlconfig.load()
    rs = risk.settings(cfg)
    ks = kill_switch()
    symbol = cfg["symbol"]
    st = {"updated_at": now.isoformat(timespec="seconds"), "mode": (cfg.get("execution") or {}).get("mode", "shadow"),
          "symbol": symbol, "kill_switch": ks, "live": False}
    entries = read_log()
    pred = {"ok": False, "error": None}
    meta, bars = {}, pd.DataFrame()
    try:
        from ml import infer

        model = infer.load(model_dir or ACTIVE)
        meta = model.meta
        st["model"] = {k: meta.get(k) for k in ("name", "version", "horizon", "target", "train", "validation",
                                                  "threshold", "expected_net_bps", "approved", "symbol",
                                                  "config_hash", "feature_version")}
        provider = provider or data.AlpacaMinuteProvider(cfg["data"]["feed"], cfg["data"]["adjustment"])
        d1 = now.astimezone(ET).date() + timedelta(days=1)
        raw = provider.fetch(symbol, d1 - timedelta(days=20), d1)
        raw = raw[raw["t"] + pd.Timedelta(minutes=1) <= pd.Timestamp(now)]  # complete minutes only
        bars, audit = data.clean(raw, cfg["session"])
        if not len(bars):
            raise RuntimeError("no regular-hours bars")
        last_real = bars[~bars["missing"]].index[-1]
        bars = bars.loc[:last_real].reset_index(drop=True)  # the grid runs to the close: drop future minutes
        feats = features.build(bars)
        i = len(bars) - 1
        bar_t = bars["t"].iloc[i]
        age = (pd.Timestamp(now) - (bar_t + pd.Timedelta(minutes=1))).total_seconds() / 60
        st["data"] = {"last_bar": str(bar_t), "age_minutes": round(age, 1), "source": provider.name,
                      "sessions": audit.get("sessions"), "missing_minutes": audit.get("missing_minutes"),
                      "price": float(bars["c"].iloc[i])}
        if not features.usable(feats).iloc[i]:
            raise RuntimeError("features incomplete for the latest minute")
        F = feats[features.NAMES].to_numpy(float)
        sess = pd.factorize(bars["session"])[0]
        t0 = time.perf_counter()
        p, ood = model.predict_rows(F, sess, np.array([i]))
        ms = (time.perf_counter() - t0) * 1000
        pred = {"ok": True, "p": round(float(p[0]), 4), "ood": bool(ood[0]), "latency_ms": round(ms, 2),
                "threshold": meta.get("threshold"), "expected_net_bps": meta.get("expected_net_bps"),
                "approved": bool(meta.get("approved"))}
        st["features"] = {k: (None if pd.isna(v) else round(float(v), 3)) for k, v in feats.iloc[i].items()}
    except (ModelLoadError, Exception) as e:  # noqa: BLE001 - fail closed, say why
        pred = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
    in_session = _session_open(now, cfg)
    age = (st.get("data") or {}).get("age_minutes")
    atr = (st.get("features") or {}).get("atr_14")
    strat = cfg.get("strategy") or {}
    stop_bps = max(rs["min_stop_bps"], float(strat.get("stop_atr_mult", 3.0)) * atr) if atr else None
    verdict = risk.check_entry({"equity": 1000.0, "buying_power": 1000.0, "exposure_usd": 0.0, "positions": 0,
                                "day_pnl": 0.0, "consecutive_losses": 0, "orders_today": 0, "minutes_since_loss": None,
                                "data_age_minutes": age, "in_session": in_session, "model_ok": pred.get("ok"),
                                "model_ms": pred.get("latency_ms"), "kill_switch": ks["on"],
                                "price": (st.get("data") or {}).get("price"), "stop_bps": stop_bps,
                                "spread_bps": None, "side": "long", "mode": "shadow"}, rs)
    dec = decision.decide(pred, {"has_position": False, "data_age_minutes": age,
                                 "max_data_age_minutes": rs["max_data_age_minutes"]}, verdict.as_dict())
    allowed, why_not = execution.gates(cfg, meta, ks["on"])
    st.update({"prediction": pred, "decision": dec, "risk": verdict.as_dict(), "stop_bps": stop_bps,
               "in_session": in_session, "execution": {"orders_allowed": allowed, "why_not": why_not,
                                                       "orders_sent": 0}})
    shadow_signal = bool(pred.get("ok") and meta.get("threshold") is not None and pred["p"] >= meta["threshold"])
    if in_session and pred.get("ok"):
        entries.append({"ts": now.isoformat(timespec="seconds"), "bar_t": str(bars["t"].iloc[-1]),
                        "horizon": meta.get("horizon"), "p": pred["p"], "threshold": meta.get("threshold"),
                        "shadow_signal": shadow_signal, "outcome": dec["outcome"], "model": meta.get("version")})
    entries = score_forward(entries, bars, cfg)
    st["forward"] = forward_summary(entries)
    st["recent"] = entries[-12:]
    p = path(LOG)
    p.write_text("".join(json.dumps(e, default=str) + "\n" for e in entries[-5000:]), encoding="utf-8")
    if SUMMARY.exists():
        st["research"] = json.loads(SUMMARY.read_text())
    save_json(STATE, st)
    return st


if __name__ == "__main__":
    s = run()
    d = s["decision"]
    print(f"ML shadow: {d['outcome']} — {'; '.join(d['reasons'])} (p={s['prediction'].get('p')}, no order sent)")
