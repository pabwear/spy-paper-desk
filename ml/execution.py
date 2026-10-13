"""The boundary between decisions and broker orders. Shadow by default; paper only when every gate is open;
live is not implemented at all.

Modes (ml/default_config.json "execution.mode"):
    shadow  the default. Decisions are logged; NO order is ever sent.
    paper   orders go to a SEPARATE Alpaca paper account (its number in config, its keys in
            ALPACA_ML_API_KEY / ALPACA_ML_SECRET_KEY), and only when ALL of these hold:
              - the model passed ml/acceptance.json AND its single holdout run (model.json "approved": true)
              - execution.approved_by_roy is true (set only after Roy says so in chat)
              - the account number is configured and the broker reports that exact account on the paper host
              - the kill switch is off
    live    refused. There is no live code path; enabling it would need a separate, deliberate change.
Orders: a client order id per (day, decision time, side) so a retried run cannot send a duplicate; after
sending, the order's status is read back (never assumed filled); every request, refusal and result is journaled.
Keys are read from the environment and never logged.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from common import PAPER_HOST, path


class ExecutionRefused(RuntimeError):
    pass


def gates(cfg: dict, model_meta: dict | None, kill_switch: bool) -> tuple[bool, list[str]]:
    """May a paper order be sent? Returns (allowed, reasons it may not)."""
    ex = cfg.get("execution") or {}
    mode = ex.get("mode", "shadow")
    why = []
    if mode == "live":
        why.append("live trading is not implemented")
    elif mode != "paper":
        why.append("shadow mode: decisions are logged, never sent")
    if not (model_meta or {}).get("approved"):
        why.append("model not approved (walk-forward rules + holdout)")
    if not ex.get("approved_by_roy"):
        why.append("Roy has not approved paper orders for this model")
    if not ex.get("account_number"):
        why.append("no separate paper account configured")
    if kill_switch:
        why.append("kill switch is on")
    return (not why), why


def client_order_id(symbol: str, decision_t: datetime, side: str) -> str:
    return f"ml-{symbol.lower()}-{decision_t.astimezone(timezone.utc):%Y%m%d%H%M}-{side}"


def journal(event: str, **data) -> dict:
    e = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **data}
    with open(path("ml_journal.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(e, default=str) + "\n")
    return e


class PaperExecutor:
    """Sends a long entry or an exit to the ML paper account. `client` is an alpaca TradingClient (or a fake)."""

    def __init__(self, client, cfg: dict, host: str) -> None:
        ex = cfg.get("execution") or {}
        if host != PAPER_HOST:
            raise ExecutionRefused("not the paper host")
        acct = client.get_account()
        if str(acct.account_number) != str(ex.get("account_number")):
            raise ExecutionRefused("broker account is not the configured ML paper account")
        self.c, self.cfg = client, cfg

    def submit(self, symbol: str, side: str, qty: float, decision_t: datetime, open_ids: set[str]) -> dict:
        if side not in ("buy", "sell"):
            raise ExecutionRefused("unknown side")
        if qty <= 0:
            raise ExecutionRefused("quantity must be positive")
        coid = client_order_id(symbol, decision_t, side)
        if coid in open_ids:
            journal("order_duplicate_skipped", symbol=symbol, side=side, client_order_id=coid)
            return {"status": "duplicate", "client_order_id": coid}
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        req = MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                                 time_in_force=TimeInForce.DAY, client_order_id=coid)
        journal("order_request", symbol=symbol, side=side, qty=qty, client_order_id=coid)
        try:
            o = self.c.submit_order(order_data=req)
        except Exception as e:  # noqa: BLE001 - a refusal is recorded, never retried blindly
            journal("order_rejected", symbol=symbol, side=side, client_order_id=coid, error=f"{type(e).__name__}: {e}"[:300])
            return {"status": "rejected", "client_order_id": coid}
        status = str(getattr(o.status, "value", o.status))
        journal("order_sent", symbol=symbol, side=side, client_order_id=coid, order_id=str(o.id), status=status)
        try:  # read it back: a sent request is not a fill
            o2 = self.c.get_order_by_id(o.id)
            status = str(getattr(o2.status, "value", o2.status))
            filled = float(o2.filled_qty or 0)
        except Exception as e:  # noqa: BLE001
            journal("order_status_unknown", client_order_id=coid, error=type(e).__name__)
            return {"status": "unknown", "client_order_id": coid, "order_id": str(o.id)}
        journal("order_status", client_order_id=coid, status=status, filled_qty=filled)
        return {"status": status, "filled_qty": filled, "client_order_id": coid, "order_id": str(o.id)}
