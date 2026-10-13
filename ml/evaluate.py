"""Scoring: how good the probabilities are, how the trades did after costs, and whether a candidate passes the
rules written in ml/acceptance.json BEFORE any result was seen.

Definitions (also in docs/ML.md):
    Brier          mean (p - y)^2; lower is better. Brier skill = 1 - Brier / Brier(training base rate); > 0 beats
                   "always predict the usual share".
    log loss       mean -[y log p + (1-y) log(1-p)].
    AUC            chance a random 1 gets a higher p than a random 0 (0.5 = no ranking skill).
    expectancy     net P&L per trade in dollars, after slippage and per-share costs.
    profit factor  gross wins / gross losses.
    max drawdown   the largest fall of the per-minute equity curve from its running peak, in % of the peak.
    Sharpe         mean / std of DAILY equity returns x sqrt(252), risk-free rate 0, days with no trade count as 0.
                   With few trades a day this number is noisy; read it with the trade count.
    exposure       share of regular-hours minutes with a position open.
    CI             bootstrap that resamples whole trading DAYS (trades on one day move together), 2000 draws.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ACCEPTANCE = json.loads((Path(__file__).resolve().parent / "acceptance.json").read_text())


def prob_metrics(y: np.ndarray, p: np.ndarray, base_p, bins: int = 10) -> dict:
    """base_p: the training base rate (a number, or one per row when folds are pooled)."""
    y, p = np.asarray(y, float), np.asarray(p, float)
    base_p = np.broadcast_to(np.asarray(base_p, float), y.shape)
    if not len(y):
        return {"n": 0}
    from ml.models import log_loss

    brier = float(np.mean((p - y) ** 2))
    brier_base = float(np.mean((base_p - y) ** 2))
    out = {"n": int(len(y)), "base_rate": round(float(y.mean()), 4), "train_base_rate": round(float(base_p.mean()), 4),
           "brier": round(brier, 5), "brier_base": round(brier_base, 5),
           "brier_skill": round(1 - brier / brier_base, 5) if brier_base > 0 else None,
           "log_loss": round(log_loss(y, p), 5), "auc": _auc(y, p)}
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    cal = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (p >= a) & (p <= b)
        if m.sum():
            cal.append({"p_mean": round(float(p[m].mean()), 4), "observed": round(float(y[m].mean()), 4), "n": int(m.sum())})
    out["calibration"] = cal
    return out


def _auc(y, p) -> float | None:
    if len(np.unique(y)) < 2:
        return None
    from sklearn.metrics import roc_auc_score

    return round(float(roc_auc_score(y, p)), 4)


def bootstrap_expectancy(trades: pd.DataFrame, level: float = 0.95, draws: int = 2000, seed: int = 7) -> dict:
    if trades is None or not len(trades):
        return {"lo": None, "hi": None, "mean": None, "days": 0}
    day = pd.to_datetime(trades["entry_t"]).dt.date
    g = trades.groupby(day)["pnl"].agg(["sum", "count"])
    s, c = g["sum"].to_numpy(), g["count"].to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), size=(draws, len(g)))
    means = s[idx].sum(axis=1) / c[idx].sum(axis=1)
    a = (1 - level) / 2
    return {"mean": round(float(s.sum() / c.sum()), 4), "lo": round(float(np.quantile(means, a)), 4),
            "hi": round(float(np.quantile(means, 1 - a)), 4), "days": int(len(g)), "level": level}


def trade_metrics(result: dict, minutes_total: int | None = None) -> dict:
    tr, eq, start = result["trades"], result["equity"], result["start_equity"]
    n = int(len(tr))
    out = {"trades": n, "start_equity": start, "end_equity": round(result["end_equity"], 2),
           "net_pnl": round(result["end_equity"] - start, 2), "rejections": result.get("rejections", {})}
    peak = eq.cummax()
    out["max_drawdown_pct"] = round(float(((eq - peak) / peak).min() * 100), 3) if len(eq) else 0.0
    daily = eq.groupby(eq.index.date).last()
    rets = daily.pct_change().dropna()
    first = daily.iloc[0] / start - 1 if len(daily) else 0.0
    rets = pd.concat([pd.Series([first]), rets]) if len(daily) else rets
    out["sharpe"] = round(float(rets.mean() / rets.std() * np.sqrt(252)), 3) if len(rets) > 2 and rets.std() > 0 else None
    out["days"] = int(len(daily))
    if not n:
        return {**out, "win_rate": None, "expectancy": None, "profit_factor": None, "exposure": 0.0}
    wins, losses = tr.loc[tr["pnl"] > 0, "pnl"], tr.loc[tr["pnl"] <= 0, "pnl"]
    out.update({
        "win_rate": round(float((tr["pnl"] > 0).mean()), 4),
        "avg_win": round(float(wins.mean()), 4) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 4) if len(losses) else None,
        "expectancy": round(float(tr["pnl"].mean()), 4),
        "profit_factor": round(float(wins.sum() / -losses.sum()), 3) if len(losses) and losses.sum() < 0 else None,
        "costs": round(float(tr["costs"].sum()), 2),
        "turnover": round(float((tr["qty"] * tr["entry"]).sum() * 2 / start), 2),
        "exposure": round(float(tr["minutes"].sum() / minutes_total), 4) if minutes_total else None,
        "exits": tr["why"].value_counts().to_dict(),
        "ci": bootstrap_expectancy(tr),
    })
    return out


def by_regime(trades: pd.DataFrame, regimes: pd.Series) -> dict:
    """Net P&L by the market regime at the decision (regimes: index = decision time, values = label)."""
    if trades is None or not len(trades):
        return {}
    lab = regimes.reindex(pd.to_datetime(trades["decision_t"])).to_numpy()
    out = {}
    for r in pd.unique(lab):
        m = lab == r
        t = trades[m]
        out[str(r)] = {"trades": int(m.sum()), "net_pnl": round(float(t["pnl"].sum()), 2),
                       "win_rate": round(float((t["pnl"] > 0).mean()), 4)}
    return out


def check(c: dict, logistic: dict | None, every: dict, folds: list[dict]) -> dict:
    """Apply ml/acceptance.json to a pooled candidate. Returns each rule's verdict and the overall result."""
    r = ACCEPTANCE["rules"]
    t, p = c["trade"], c["prob"]
    verdict = {
        "min_trades": t["trades"] >= r["min_trades"],
        "expectancy_ci_lower_gt_0": bool(t.get("ci", {}).get("lo") is not None and t["ci"]["lo"] > 0),
        "beats_no_trade": t["net_pnl"] > 0,
        "beats_every_decision": t["net_pnl"] > every["trade"]["net_pnl"],
        "brier_skill_gt_0": bool(p.get("brier_skill") is not None and p["brier_skill"] > 0),
        "profitable_fold_share": (np.mean([f["net_pnl"] > 0 for f in folds]) if folds else 0) >= r["profitable_fold_share"],
        "profit_factor": bool(t.get("profit_factor") is not None and t["profit_factor"] >= r["profit_factor"]),
    }
    if logistic is not None and c["model"] != "logistic":
        verdict["beats_logistic"] = bool(t["net_pnl"] > logistic["trade"]["net_pnl"]
                                         and p.get("brier", 9) < logistic["prob"].get("brier", 9))
    return {"rules": verdict, "passed": all(verdict.values())}
