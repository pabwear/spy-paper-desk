"""Turns a walk-forward result into a readable Markdown report. Pure: dict in, text out."""

from __future__ import annotations


def _f(v, fmt="{:+.2f}", none="—"):
    return none if v is None else fmt.format(v)


def markdown(rep: dict, audit: dict, cfg_hash: str) -> str:
    L = [f"# Walk-forward report — {audit.get('symbol', '?')} ({rep['target']})", "",
         f"Data: {audit.get('provider')} 1-minute bars, {audit.get('first', '?')[:10]} → {audit.get('last', '?')[:10]} "
         f"({rep['sessions']} sessions used, {audit.get('missing_minutes', 0):,} missing minutes kept as missing, "
         f"{audit.get('duplicates', 0)} duplicates and {audit.get('invalid', 0)} impossible bars removed). "
         f"Locked holdout from {rep['holdout_start']}: not used. Config {cfg_hash}. {len(rep['folds'])} folds, "
         f"{rep['seconds']} s.", "",
         "Every number is OUT-OF-SAMPLE: each fold's test months were never used for training, settings, calibration "
         "or the entry threshold. Each fold's test starts from $1,000; P&L is after slippage and per-share costs. "
         "A backtest is not a promise.", ""]
    for h, res in rep["results"].items():
        L += [f"## {h}-minute horizon", "",
              "| Model | Trades | Net P&L | Per trade (95% range) | Win rate | Profit factor | Brier skill | AUC | Folds +$ | Passes |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for name, e in res.items():
            t, p = e["trade"], e.get("prob", {})
            ci = t.get("ci") or {}
            rng = f"{_f(t.get('expectancy'))} ({_f(ci.get('lo'))} to {_f(ci.get('hi'))})" if t.get("expectancy") is not None else "—"
            acc = e.get("acceptance")
            L.append(f"| {name} | {t['trades']} | {_f(t['net_pnl'])} | {rng} | {_f(t.get('win_rate'), '{:.1%}')} | "
                     f"{_f(t.get('profit_factor'), '{:.2f}')} | {_f(p.get('brier_skill'), '{:+.4f}')} | "
                     f"{_f(p.get('auc'), '{:.3f}')} | {t['profitable_folds']}/{t['folds']} | "
                     f"{'**yes**' if acc and acc['passed'] else ('no' if acc else 'n/a')} |")
        L.append("")
        for name, e in res.items():
            acc = e.get("acceptance")
            if acc:
                failed = [k for k, v in acc["rules"].items() if not v]
                L.append(f"- {name}: " + ("passes every rule." if not failed else "fails " + ", ".join(failed) + "."))
        ev = res.get("every_decision", {}).get("folds", [])
        if ev:
            L += ["", "Buy-and-hold over each test window (context, bp): " +
                  ", ".join(f"{x['buy_hold_bps']:+.0f}" for x in ev if x.get("buy_hold_bps") is not None) + "."]
        best = max((e for n, e in res.items() if e.get("regimes")), key=lambda e: e["trade"]["net_pnl"], default=None)
        if best and best["regimes"]:
            L += ["", f"By market regime ({best['model']}, at the decision):", "", "| Regime | Trades | Net P&L | Win rate |",
                  "|---|---|---|---|"]
            for k, v in sorted(best["regimes"].items()):
                L.append(f"| {k} | {v['trades']} | {_f(v['net_pnl'])} | {_f(v['win_rate'], '{:.1%}')} |")
        L.append("")
    L += ["## Limits", "",
          "- One price per minute (IEX feed; its volume is a small slice of the whole market). No quotes, so the "
          "spread is a fixed cost assumption ($0.02 a share round trip + 0.5 bp slippage each side).",
          "- When one minute reaches both the stop and the target, the stop is assumed first.",
          "- Several models and horizons are compared, so one can look good by luck; only the locked holdout, used "
          "once, can confirm a winner.", ""]
    return "\n".join(L)


def summary(rep: dict, name: str, note: str) -> dict:
    """The compact verdict the dashboard shows (ml/research_summary.json)."""
    hs = []
    for h, res in rep["results"].items():
        hs.append({"horizon": int(h), "models": [
            {"model": m, "trades": e["trade"]["trades"], "net_pnl": e["trade"]["net_pnl"],
             "passed": bool((e.get("acceptance") or {}).get("passed"))}
            for m, e in res.items() if m != "base_rate"]})
    return {"report": name, "holdout_start": rep["holdout_start"], "folds": len(rep["folds"]), "horizons": hs,
            "note": note}
