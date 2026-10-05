"""Mirror the ledger, closed trades and daily reviews into a Google Sheet.

After every `sync` and `review`, if SHEETS_WEBHOOK_URL is set (in the environment or .env.alpaca), the
desk POSTs three tabs to a small Apps Script attached to the sheet (code in README.md). Each post
replaces the tabs' contents, so the sheet always matches the local files. A failed post is logged
and never blocks trading.

    python3 sheets_sync.py           # push now
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

import journal

TIMEOUT = 15


def tables() -> dict:
    trades = journal.read_trades()
    events = journal.read_events()
    trips = journal.round_trips(trades, events)
    reviews = [e for e in events if e.get("event") == "review"]
    return {
        "Trades": {"header": journal.TRADE_FIELDS,
                   "rows": [[t.get(k, "") for k in journal.TRADE_FIELDS] for t in trades]},
        "Closed trades": {
            "header": ["opened_at", "closed_at", "symbol", "asset", "exposure", "entry_price", "exit_price", "pnl",
                       "result", "exit_reason", "underlying_entry", "tags", "pulse_bias", "p_loss"],
            "rows": [[t.get("opened_at"), t.get("closed_at"), t.get("symbol"), t.get("asset"), t.get("exposure"),
                      t.get("entry_price"), t.get("exit_price"), t.get("pnl"), t.get("result"), t.get("exit_reason"),
                      t.get("underlying_entry") or "", "|".join(t.get("tags") or []), t.get("pulse_bias") or "",
                      t.get("p_loss") if t.get("p_loss") is not None else ""]
                     for t in trips],
        },
        "Daily reviews": {
            "header": ["date", "evaluated", "passes", "entries", "fills", "realized_pnl", "unrealized_pnl", "equity",
                       "top_pass_reasons", "lessons"],
            "rows": [[r.get("date"), r.get("evaluated"), r.get("skipped"), r.get("entries", r.get("orders")),
                      r.get("fills"), r.get("realized_pnl"), r.get("unrealized_pnl") if r.get("unrealized_pnl") is not None else "",
                      r.get("equity") if r.get("equity") is not None else "",
                      "; ".join(f"{k} x{v}" for k, v in list((r.get("skip_reasons") or {}).items())[:3]),
                      " ".join(r.get("lessons") or [])]
                     for r in reviews],
        },
    }


def push(post=None) -> dict:
    """Send the tabs. Returns {"sent": bool, "why": str}. Never raises."""
    from alpaca_client import load_env_file

    load_env_file()
    url = os.environ.get("SHEETS_WEBHOOK_URL")
    token = os.environ.get("SHEETS_WEBHOOK_TOKEN", "")
    if not url:
        return {"sent": False, "why": "SHEETS_WEBHOOK_URL not set"}
    if not url.startswith("https://script.google.com/"):
        return {"sent": False, "why": "SHEETS_WEBHOOK_URL must be an Apps Script web app URL"}
    tabs = tables()
    body = json.dumps({"token": token, "tabs": tabs}, default=str).encode()
    try:
        if post is not None:
            status = post(url, body)
        else:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:  # noqa: S310 - https Apps Script URL only
                status = res.status
    except Exception as e:  # noqa: BLE001 - the sheet is a mirror; trading never waits on it
        journal.log("sheets_failed", error=f"{type(e).__name__}: {e}"[:300])
        return {"sent": False, "why": f"{type(e).__name__}"}
    ok = 200 <= int(status) < 400
    if not ok:
        journal.log("sheets_failed", error=f"HTTP {status}")
    return {"sent": ok, "why": f"HTTP {status}"}


if __name__ == "__main__":
    r = push()
    print(("Sheet updated" if r["sent"] else "Sheet not updated") + f" ({r['why']}).")
    sys.exit(0 if r["sent"] else 1)
