"""Profit and loss by period for one account: today, this week, this month and since the start.

From Alpaca's daily account history (each day's closing equity), the live equity and the previous close.
"This week" counts from the last close before Monday, "this month" from the last close before the 1st;
when the account is newer than that, from its starting money.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from common import ET


def history_from_alpaca(h) -> list[dict]:
    """alpaca-py PortfolioHistory -> [{"date": "YYYY-MM-DD", "equity": float}] (New York dates; days with no money yet are left out)."""
    out = []
    for ts, eq in zip(getattr(h, "timestamp", None) or [], getattr(h, "equity", None) or []):
        if eq is None or float(eq) <= 0:  # Alpaca reports 0 for the days before the account had money
            continue
        d = datetime.fromtimestamp(int(ts), tz=ET).date().isoformat()
        if out and out[-1]["date"] == d:
            out[-1]["equity"] = float(eq)
        else:
            out.append({"date": d, "equity": float(eq)})
    return out


def _close_before(history: list[dict], d: date) -> float | None:
    prior = [h for h in history if (h.get("equity") or 0) > 0 and h["date"] < d.isoformat()]
    return float(prior[-1]["equity"]) if prior else None


def periods(history: list[dict], equity: float | None, last_equity: float | None, start_usd: float,
            today: date) -> dict:
    if equity is None:
        return {}
    monday = today - timedelta(days=today.weekday())
    first = today.replace(day=1)
    week_base = _close_before(history, monday)
    month_base = _close_before(history, first)
    return {
        "as_of": today.isoformat(),
        "equity": round(equity, 2),
        "today": round(equity - last_equity, 2) if last_equity is not None else None,
        "week": round(equity - (week_base if week_base is not None else start_usd), 2),
        "month": round(equity - (month_base if month_base is not None else start_usd), 2),
        "all": round(equity - start_usd, 2),
        "start_usd": start_usd,
    }
