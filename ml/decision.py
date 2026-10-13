"""The decision engine: deterministic rules from a prediction to one outcome, with every reason written down.

Outcomes:
    BUY_CANDIDATE     a long entry the risk engine approved (still only a CANDIDATE: the execution mode decides
                      whether anything is sent; in shadow mode nothing ever is)
    EXIT_LONG         close the open long (signal turned, or the holding time is up)
    HOLD              keep the open long
    NO_TRADE          no edge, a weak/ambiguous signal (the no-trade zone), or an unapproved model
    RISK_REJECTED     the model wanted in; a risk rule said no
    UNAVAILABLE       data or model missing, stale, slow, failed or out of distribution
SELL never means "go short": shorting is not supported (risk engine: allow_short false).

An entry needs ALL of: an approved model (passed ml/acceptance.json and the holdout), p >= the threshold chosen on
validation, a positive expected net return at that threshold (also measured on validation, after costs), and the
risk engine's approval. Confidence never changes the size (that is the risk engine's job, from the stop distance).
"""

from __future__ import annotations

BUY, EXIT, HOLD, NO_TRADE, RISK_REJECTED, UNAVAILABLE = (
    "BUY_CANDIDATE", "EXIT_LONG", "HOLD", "NO_TRADE", "RISK_REJECTED", "UNAVAILABLE")


def decide(pred: dict, ctx: dict, risk_verdict: dict | None = None) -> dict:
    """pred: {ok, p, threshold, expected_net_bps, approved, ood, latency_ms, error}
    ctx:  {has_position, minutes_held, horizon, data_age_minutes, max_data_age_minutes, exit_p}
    risk_verdict: ml.risk.check_entry(...).as_dict(), only needed for an entry."""
    reasons: list[str] = []
    if ctx.get("has_position"):
        if pred.get("ok") and pred.get("p") is not None and pred["p"] < ctx.get("exit_p", 0.5):
            return _out(EXIT, ["signal turned: p %.3f < %.2f" % (pred["p"], ctx.get("exit_p", 0.5))], pred)
        if ctx.get("minutes_held", 0) >= ctx.get("horizon", 10**9):
            return _out(EXIT, ["holding time reached"], pred)
        return _out(HOLD, ["position open, no exit condition"], pred)
    if not pred.get("ok"):
        return _out(UNAVAILABLE, [pred.get("error") or "no prediction"], pred)
    age = ctx.get("data_age_minutes")
    if age is None or age > ctx.get("max_data_age_minutes", 2):
        return _out(UNAVAILABLE, [f"data too old ({age} min)"], pred)
    if pred.get("ood"):
        return _out(UNAVAILABLE, ["inputs out of the training range"], pred)
    p, thr = pred.get("p"), pred.get("threshold")
    if not pred.get("approved"):
        reasons.append("model not approved (did not pass the walk-forward rules and the holdout)")
    if thr is None:
        reasons.append("no profitable threshold on validation: the model abstains")
    elif p is None or p < thr:
        reasons.append(f"p {p:.3f} below the entry threshold {thr:.2f} (no-trade zone)" if p is not None else "no p")
    if (pred.get("expected_net_bps") or 0) <= 0:
        reasons.append("no positive expected return after costs at this threshold")
    if reasons:
        return _out(NO_TRADE, reasons, pred)
    if not risk_verdict or not risk_verdict.get("ok"):
        return _out(RISK_REJECTED, ["risk: " + ", ".join((risk_verdict or {}).get("failed") or ["no verdict"])], pred,
                    risk_verdict)
    return _out(BUY, [f"p {p:.3f} >= {thr:.2f}, expected {pred['expected_net_bps']:+.1f} bp after costs"], pred,
                risk_verdict)


def _out(outcome: str, reasons: list[str], pred: dict, risk: dict | None = None) -> dict:
    return {"outcome": outcome, "reasons": reasons, "p": pred.get("p"), "threshold": pred.get("threshold"),
            "risk": risk, "qty": (risk or {}).get("qty", 0.0) if outcome == BUY else 0.0}
