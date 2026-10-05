"""Market Pulse from live readings (Stocklake market pulse, Stocktwits sentiment).

The pulse agents read the connectors and hand the numbers to `run_study.py pulse ingest FILE`.
This module turns readings into a bias with the rule in rules.json "pulse_rules": each reading
votes bullish, bearish or not at all; the bias needs at least `min_net_votes` net votes, otherwise
it is neutral. Neutral never contradicts a setup. Pure: no I/O.

Readings file (any field may be missing):
    {
      "as_of": "2026-10-02T19:09:42Z",
      "spy_change_pct": 0.62, "spy_rsi": 54.55, "vix": 15.8, "vix_change_pct": null,
      "fear_greed": 31.2,
      "stocktwits": {"SPY": {"score": 72, "label": "BULLISH", "bullish_pct": 54.98}},
      "sources": {"market_pulse": "stocklake", "sentiment_flow": "stocktwits"}
    }
"""

from __future__ import annotations

DEFAULT_RULES = {
    "votes": {
        "spy_change_pct": {"bullish_at_or_above": 0.3, "bearish_at_or_below": -0.3},
        "vix_change_pct": {"bearish_at_or_above": 5.0, "bullish_at_or_below": -5.0},
        "fear_greed": {"bullish_at_or_above": 55.0, "bearish_at_or_below": 45.0},
        "stocktwits_spy_score": {"bullish_at_or_above": 60.0, "bearish_at_or_below": 40.0},
    },
    "min_net_votes": 2,
}


def _value(readings: dict, key: str):
    if key == "stocktwits_spy_score":
        return ((readings.get("stocktwits") or {}).get("SPY") or {}).get("score")
    return readings.get(key)


def votes(readings: dict, rules: dict | None = None) -> list[dict]:
    rules = rules or DEFAULT_RULES
    out = []
    for key, rule in (rules.get("votes") or {}).items():
        v = _value(readings, key)
        if v is None:
            out.append({"reading": key, "value": None, "vote": 0, "why": "missing"})
            continue
        v = float(v)
        vote, why = 0, "in the neutral band"
        if "bullish_at_or_above" in rule and v >= rule["bullish_at_or_above"]:
            vote, why = 1, f"≥ {rule['bullish_at_or_above']}"
        elif "bearish_at_or_below" in rule and v <= rule["bearish_at_or_below"]:
            vote, why = -1, f"≤ {rule['bearish_at_or_below']}"
        elif "bearish_at_or_above" in rule and v >= rule["bearish_at_or_above"]:
            vote, why = -1, f"≥ {rule['bearish_at_or_above']}"
        elif "bullish_at_or_below" in rule and v <= rule["bullish_at_or_below"]:
            vote, why = 1, f"≤ {rule['bullish_at_or_below']}"
        out.append({"reading": key, "value": v, "vote": vote, "why": why})
    return out


def derive_bias(readings: dict, rules: dict | None = None) -> dict:
    rules = rules or DEFAULT_RULES
    vs = votes(readings, rules)
    net = sum(v["vote"] for v in vs)
    need = int(rules.get("min_net_votes", 2))
    bias = "bullish" if net >= need else "bearish" if net <= -need else "neutral"
    return {"bias": bias, "net_votes": net, "min_net_votes": need, "votes": vs}
