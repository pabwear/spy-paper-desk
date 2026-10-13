"""Walk-forward research: for every fold, horizon and model, train -> pick settings on validation -> score on test.

Per fold (dates from ml.splits.walk_forward, all before the locked holdout):
  train rows       every usable minute of the train window, labels purged before validation starts
  validation rows  the desk's decision minutes (every 10 min, 10:00-15:30) of the validation window, purged
                   before the test window starts. Used for: model settings, probability calibration, and the
                   entry threshold (the lowest p >= 0.5 whose validation backtest made money after costs with
                   at least `min_val_trades` trades; none -> the model abstains in that fold: NO TRADE)
  test rows        the decision minutes of the test window: predicted once, backtested once, never tuned on
Benchmarks per fold: the same rules entering at EVERY decision ("every_decision") and buy-and-hold for context.
Results are pooled across folds (each fold's test starts from the same $1,000) and checked against
ml/acceptance.json. Nothing here touches the holdout (ml.splits.guard is called on every window).
"""

from __future__ import annotations

import time
from datetime import date

import numpy as np
import pandas as pd

from common import ET
from ml import backtest, evaluate, features, labels, models, splits

THRESHOLDS = [round(0.50 + 0.01 * k, 2) for k in range(0, 21)]  # 0.50 .. 0.70


def regimes(feats: pd.DataFrame, bars: pd.DataFrame, train_mask: pd.Series) -> pd.Series:
    """Market regime at each minute: volatility tercile (cut points from the TRAIN window only) x trend."""
    rv = feats["rv_60"]
    lo, hi = np.nanquantile(rv[train_mask], [1 / 3, 2 / 3])
    vol = np.where(rv <= lo, "low-vol", np.where(rv <= hi, "mid-vol", "high-vol"))
    trend = np.where(feats["ret_open"].abs() >= 25, "trending", "sideways")
    lab = pd.Series([f"{v}/{t}" for v, t in zip(vol, trend)], index=bars.index)
    lab[rv.isna() | feats["ret_open"].isna()] = "unknown"
    return pd.Series(lab.to_numpy(), index=pd.DatetimeIndex(bars["t"] + pd.Timedelta(minutes=1)))


def _window_rows(bars: pd.DataFrame, win: tuple[date, date]) -> pd.Series:
    return splits.in_window(bars["session"], win)


def pick_threshold(p: np.ndarray, rows: pd.Index, bars_v: pd.DataFrame, feats_v: pd.DataFrame, cfg: dict,
                   horizon: int, min_trades: int) -> dict:
    """Lowest threshold whose validation backtest made money after costs with enough trades; else abstain."""
    tried = []
    for thr in THRESHOLDS:
        sig = pd.Series(False, index=bars_v.index)
        sig.loc[rows[p >= thr]] = True
        r = backtest.run(bars_v, sig, feats_v, cfg, horizon)
        n, pnl = len(r["trades"]), r["end_equity"] - r["start_equity"]
        tried.append({"thr": thr, "trades": n, "net_pnl": round(pnl, 2)})
        if n >= min_trades and pnl > 0:
            return {"threshold": thr, "val_trades": n, "val_net_pnl": round(pnl, 2), "tried": tried}
    return {"threshold": None, "tried": tried}


def run(bars: pd.DataFrame, cfg: dict, horizons: list[int], model_names: list[str], target: str = "up_net",
        holdout_path=splits.HOLDOUT_FILE, min_val_trades: int = 15, max_folds: int | None = None,
        log=print) -> dict:
    t0 = time.perf_counter()
    hstart = splits.holdout_start(holdout_path)
    bars = bars[bars["session"] < hstart].reset_index(drop=True)
    splits.guard(bars["session"], holdout_path)
    feats = features.build(bars)
    ok_feat = features.usable(feats)
    dec = labels.decision_rows(bars, cfg)
    folds = splits.walk_forward(bars["session"].iloc[0], hstart, cfg["splits"])
    if max_folds:
        folds = folds[-max_folds:]
    emb = int(cfg["splits"]["embargo_minutes"])
    report = {"target": target, "horizons": horizons, "models": model_names, "folds": [f.as_dict() for f in folds],
              "holdout_start": str(hstart), "results": {}, "rows": int(len(bars)),
              "sessions": int(bars["session"].nunique())}
    X_all = feats[features.NAMES].to_numpy(float)
    sess_codes = pd.factorize(bars["session"])[0]
    for h in horizons:
        lab = labels.build(bars, h, cfg)
        y_all = lab[target].to_numpy(float)
        known = ok_feat & lab[target].notna()
        per = {m: [] for m in model_names + ["every_decision"]}
        for f in folds:
            tr = _window_rows(bars, f.train) & known
            tr = splits.purge(tr, lab["label_end"], f.validation[0], emb, ET)
            va = _window_rows(bars, f.validation) & known & dec
            va = splits.purge(va, lab["label_end"], f.test[0], emb, ET)
            te_win = _window_rows(bars, f.test)
            te = te_win & known & dec
            for w in (tr, va, te):
                splits.guard(bars.loc[w, "session"], holdout_path)
            if tr.sum() < 1000 or va.sum() < 100 or te.sum() < 100:
                log(f"h{h} fold {f.k}: too little data, skipped")
                continue
            reg = regimes(feats, bars, tr)
            vwin = _window_rows(bars, f.validation)
            bars_v, feats_v = bars[vwin], feats[vwin]
            bars_t, feats_t = bars[te_win], feats[te_win]
            minutes_t = int(te_win.sum())
            every = pd.Series(False, index=bars_t.index)
            every.loc[bars.index[te]] = True
            r_every = backtest.run(bars_t, every, feats_t, cfg, h)
            bh = float(np.log(bars_t["c"].dropna().iloc[-1] / bars_t["o"].dropna().iloc[0]) * 1e4)
            per["every_decision"].append({"fold": f.k, "trades": r_every["trades"], **_fold_summary(r_every, minutes_t),
                                          "buy_hold_bps": round(bh, 1)})
            idx_tr, idx_va, idx_te = (np.flatnonzero(w.to_numpy()) for w in (tr, va, te))
            for name in model_names:
                if name == "tcn":  # sequence model: windows of past minutes (imports PyTorch only when asked)
                    from ml.tcn import TCN

                    tc = cfg.get("tcn") or {}
                    m = TCN(seed=int(cfg["seed"]), arch=tc.get("arch"), train=tc.get("train"))
                    m.fit_rows(X_all, sess_codes, idx_tr, y_all[idx_tr], idx_va, y_all[idx_va])
                    predict = lambda idx, m=m: m.predict_rows(X_all, sess_codes, idx)  # noqa: E731
                else:
                    m = models.REGISTRY[name](seed=int(cfg["seed"]))
                    m.fit(X_all[tr], y_all[tr], X_all[va], y_all[va])
                    predict = lambda idx, m=m: m.predict(X_all[idx])  # noqa: E731
                pv = predict(idx_va)
                choice = pick_threshold(pv, bars.index[va], bars_v, feats_v, cfg, h, min_val_trades) \
                    if name != "base_rate" else {"threshold": None, "note": "yardstick: never trades"}
                t1 = time.perf_counter()
                pt = predict(idx_te)
                latency_us = (time.perf_counter() - t1) / max(1, int(te.sum())) * 1e6
                sig = pd.Series(False, index=bars_t.index)
                if choice["threshold"] is not None:
                    sig.loc[bars.index[te][pt >= choice["threshold"]]] = True
                r = backtest.run(bars_t, sig, feats_t, cfg, h)
                per[name].append({"fold": f.k, "trades": r["trades"], **_fold_summary(r, minutes_t),
                                  "threshold": choice, "model": m.describe(), "y": y_all[te], "p": pt,
                                  "base_p": float(np.mean(y_all[tr])), "latency_us_per_row": round(latency_us, 2),
                                  "regime": evaluate.by_regime(r["trades"], reg)})
                log(f"h{h} fold {f.k} {name}: thr {choice['threshold']} test trades {len(r['trades'])} "
                    f"net {r['end_equity'] - r['start_equity']:+.2f}")
        report["results"][str(h)] = _pool(per, model_names)
    report["seconds"] = round(time.perf_counter() - t0, 1)
    return report


def _fold_summary(r: dict, minutes_total: int) -> dict:
    tm = evaluate.trade_metrics(r, minutes_total)
    return {"net_pnl": tm["net_pnl"], "metrics": {k: v for k, v in tm.items() if k != "ci"}}


def _pool(per: dict, model_names: list[str]) -> dict:
    out = {}
    for name, fl in per.items():
        if not fl:
            continue
        nonempty = [x["trades"] for x in fl if len(x["trades"])]
        trades = pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame()
        tm = _pooled_trades(trades, fl)
        entry = {"model": name, "trade": tm,
                 "folds": [{"fold": x["fold"], "net_pnl": x["net_pnl"], **{k: x["metrics"].get(k) for k in
                            ("trades", "win_rate", "profit_factor", "max_drawdown_pct", "sharpe", "exposure")},
                            **({"threshold": x["threshold"]["threshold"]} if "threshold" in x else {}),
                            **({"buy_hold_bps": x["buy_hold_bps"]} if "buy_hold_bps" in x else {})} for x in fl]}
        if "y" in fl[0]:
            y = np.concatenate([x["y"] for x in fl])
            p = np.concatenate([x["p"] for x in fl])
            bp = np.concatenate([np.full(len(x["y"]), x["base_p"]) for x in fl])
            entry["prob"] = evaluate.prob_metrics(y, p, bp)
            entry["latency_us_per_row"] = round(float(np.mean([x["latency_us_per_row"] for x in fl])), 2)
            entry["model_settings"] = [x["model"] for x in fl]
            entry["regimes"] = _merge_regimes([x["regime"] for x in fl])
        out[name] = entry
    every = out.get("every_decision")
    logi = out.get("logistic")
    for name in model_names:
        if name in out and name != "base_rate" and every:
            out[name]["acceptance"] = evaluate.check(out[name], logi if name != "logistic" else None, every,
                                                     out[name]["folds"])
    return out


def _pooled_trades(trades: pd.DataFrame, fl: list[dict]) -> dict:
    n = int(len(trades))
    net = float(sum(x["net_pnl"] for x in fl))
    out = {"trades": n, "net_pnl": round(net, 2), "folds": len(fl),
           "profitable_folds": int(sum(x["net_pnl"] > 0 for x in fl)),
           "worst_fold_drawdown_pct": min((x["metrics"]["max_drawdown_pct"] for x in fl), default=0.0)}
    if not n:
        return {**out, "win_rate": None, "expectancy": None, "profit_factor": None, "ci": {"lo": None}}
    wins, losses = trades.loc[trades["pnl"] > 0, "pnl"], trades.loc[trades["pnl"] <= 0, "pnl"]
    out.update({"win_rate": round(float((trades["pnl"] > 0).mean()), 4),
                "avg_win": round(float(wins.mean()), 4) if len(wins) else None,
                "avg_loss": round(float(losses.mean()), 4) if len(losses) else None,
                "expectancy": round(float(trades["pnl"].mean()), 4),
                "profit_factor": round(float(wins.sum() / -losses.sum()), 3) if len(losses) and losses.sum() < 0 else None,
                "costs": round(float(trades["costs"].sum()), 2),
                "ci": evaluate.bootstrap_expectancy(trades)})
    return out


def _merge_regimes(rs: list[dict]) -> dict:
    out: dict = {}
    for r in rs:
        for k, v in r.items():
            o = out.setdefault(k, {"trades": 0, "net_pnl": 0.0, "wins": 0.0})
            o["trades"] += v["trades"]
            o["net_pnl"] = round(o["net_pnl"] + v["net_pnl"], 2)
            o["wins"] += v["win_rate"] * v["trades"]
    for o in out.values():
        o["win_rate"] = round(o.pop("wins") / o["trades"], 4) if o["trades"] else None
    return out


def train_final(bars: pd.DataFrame, cfg: dict, name: str, horizon: int, folder, target: str = "up_net",
                holdout_path=splits.HOLDOUT_FILE, extra_meta: dict | None = None, min_val_trades: int = 15) -> dict:
    """The model the desk would use: trained on the last train window before the holdout, calibrated and
    thresholded on the last validation window. Never sees the holdout. Saved for numpy inference; approved
    stays False until the acceptance rules AND the single holdout run say otherwise."""
    from ml import infer

    hstart = splits.holdout_start(holdout_path)
    bars = bars[bars["session"] < hstart].reset_index(drop=True)
    sp = cfg["splits"]
    va_win = (splits.add_months(hstart, -int(sp["validation_months"])), hstart)
    tr_win = (splits.add_months(va_win[0], -int(sp["train_months"])), va_win[0])
    feats = features.build(bars)
    lab = labels.build(bars, horizon, cfg)
    known = features.usable(feats) & lab[target].notna()
    tr = splits.purge(_window_rows(bars, tr_win) & known, lab["label_end"], va_win[0],
                      int(sp["embargo_minutes"]), ET)
    va = _window_rows(bars, va_win) & known & labels.decision_rows(bars, cfg)
    splits.guard(bars.loc[tr | va, "session"], holdout_path)
    X = feats[features.NAMES].to_numpy(float)
    y = lab[target].to_numpy(float)
    sess = pd.factorize(bars["session"])[0]
    idx_tr, idx_va = np.flatnonzero(tr.to_numpy()), np.flatnonzero(va.to_numpy())
    if name == "tcn":
        from ml.tcn import TCN

        tc = cfg.get("tcn") or {}
        m = TCN(seed=int(cfg["seed"]), arch=tc.get("arch"), train=tc.get("train"))
        m.fit_rows(X, sess, idx_tr, y[idx_tr], idx_va, y[idx_va])
        pv = m.predict_rows(X, sess, idx_va)
    else:
        m = models.REGISTRY[name](seed=int(cfg["seed"]))
        m.fit(X[idx_tr], y[idx_tr], X[idx_va], y[idx_va])
        pv = m.predict(X[idx_va])
    vwin = _window_rows(bars, va_win)
    choice = pick_threshold(pv, bars.index[idx_va], bars[vwin], feats[vwin], cfg, horizon, min_val_trades)
    thr = choice["threshold"]
    net = lab["net"].to_numpy(float)[idx_va]
    exp_net = float(np.nanmean(net[pv >= thr])) if thr is not None and (pv >= thr).any() else None
    meta = {"name": name, "horizon": horizon, "target": target, "train": [str(x) for x in tr_win],
            "validation": [str(x) for x in va_win], "threshold": thr, "expected_net_bps": exp_net,
            "val_trades": choice.get("val_trades"), "approved": False, "holdout_start": str(hstart),
            **(extra_meta or {})}
    if name == "tcn":
        return m.save(folder, meta)
    if name == "logistic":
        return infer.export_logistic(m, folder, meta)
    raise ValueError(f"{name} cannot be exported for numpy inference (logistic and tcn can)")
