"""Instruments the desk knows about, which one is active, and what an entry looks like.

Pure: no I/O. Paths that are switched off stay here so turning them back on is a
config change, not a rewrite.

    spy_options  one long SPY call (buy signal) or put (sell signal), 1 contract,
                 nearest listed expiry, strike nearest the dollar to SPY
    spy_shares   SPY shares sized off risk.json (80% of the $1,000 book = $800)
    sndk_*       switch only (sndk_enabled). There is no SNDK strategy in this
                 codebase, so these always refuse.
"""

from __future__ import annotations

import re
from datetime import date

from signals import size_qty, whole_shares

INSTRUMENTS = {
    "spy_options": {"flag": "spy_options_enabled", "underlying": "SPY", "asset": "option", "implemented": True},
    "spy_shares": {"flag": "shares_enabled", "underlying": "SPY", "asset": "shares", "implemented": True},
    "sndk_shares": {"flag": "sndk_enabled", "underlying": "SNDK", "asset": "shares", "implemented": False},
    "sndk_options": {"flag": "sndk_enabled", "underlying": "SNDK", "asset": "option", "implemented": False},
}

OCC = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<cp>[CP])(?P<strike>\d{8})$")


def active(rules: dict) -> dict:
    """The active instrument and whether it may trade. Unknown or disabled → enabled False."""
    name = rules.get("active")
    spec = INSTRUMENTS.get(name)
    if spec is None:
        return {"name": name, "enabled": False, "why": f"unknown instrument {name!r}"}
    if rules.get(spec["flag"]) is not True:
        return {"name": name, **spec, "enabled": False, "why": f"{spec['flag']} is false"}
    if not spec["implemented"]:
        return {"name": name, **spec, "enabled": False, "why": f"no {name} path in this codebase"}
    return {"name": name, **spec, "enabled": True, "why": f"{name} on ({spec['flag']} true)"}


def flags(rules: dict) -> dict:
    return {name: rules.get(spec["flag"]) is True for name, spec in INSTRUMENTS.items()}


# ---------------------------------------------------------------- option symbols

def occ_symbol(root: str, expiry: date, right: str, strike: float) -> str:
    return f"{root}{expiry:%y%m%d}{'C' if right == 'call' else 'P'}{int(round(strike * 1000)):08d}"


def parse_occ(symbol: str) -> dict | None:
    m = OCC.match(symbol or "")
    if not m:
        return None
    return {
        "underlying": m["root"],
        "expiry": date(2000 + int(m["yy"]), int(m["mm"]), int(m["dd"])),
        "right": "call" if m["cp"] == "C" else "put",
        "strike": int(m["strike"]) / 1000.0,
    }


def underlying_of(symbol: str) -> str:
    occ = parse_occ(symbol)
    return occ["underlying"] if occ else symbol


def is_desk_symbol(symbol: str) -> bool:
    """SPY shares or a SPY option. SNDK (or anything else) is never managed by this desk."""
    return underlying_of(symbol) == "SPY"


def multiplier(symbol: str) -> int:
    return 100 if parse_occ(symbol) else 1


def direction(symbol: str, qty: float) -> str:
    """Which way the position is exposed to SPY: 'long' (long shares, long call) or 'short'."""
    occ = parse_occ(symbol)
    if occ:
        long_contract = qty > 0
        return "long" if (occ["right"] == "call") == long_contract else "short"
    return "long" if qty > 0 else "short"


# ---------------------------------------------------------------- entries

def plan_entry(inst: dict, signal: str, price: float, risk: dict, rules: dict) -> dict:
    """What an entry on this signal would be. Options are always exactly 1 contract."""
    base = {"instrument": inst.get("name"), "asset": inst.get("asset"), "underlying": inst.get("underlying"),
            "signal": signal}
    if inst.get("name") == "spy_options":
        opt = rules.get("option", {})
        right = opt.get("right_on_buy", "call") if signal == "buy" else opt.get("right_on_sell", "put")
        return {**base, "order_side": "buy", "intent": "buy_to_open", "right": right,
                "contracts": int(opt.get("contracts", 1)), "qty": int(opt.get("contracts", 1)),
                "target_strike": float(round(price)), "expiry": "nearest", "symbol": None}
    if inst.get("name") == "spy_shares":
        if signal == "buy":
            qty = size_qty(price, risk)
        else:
            qty = float(whole_shares(price, risk))  # fractional shares cannot be sold short
        return {**base, "order_side": signal, "intent": "open", "qty": qty, "symbol": "SPY",
                "notional": round(qty * price, 2)}
    return {**base, "order_side": None, "qty": 0, "symbol": None}


def pick_contract(contracts: list[dict], price: float, right: str, today: date) -> dict | None:
    """Nearest listed expiry on or after today, then the listed strike nearest the dollar to SPY.

    contracts: [{symbol, expiry (date), right, strike, tradable}]
    """
    usable = [c for c in contracts if c.get("tradable", True) and c["right"] == right and c["expiry"] >= today]
    if not usable:
        return None
    nearest_expiry = min(c["expiry"] for c in usable)
    target = float(round(price))
    same_day = [c for c in usable if c["expiry"] == nearest_expiry]
    best = min(same_day, key=lambda c: (abs(c["strike"] - target), abs(c["strike"] - price)))
    return {**best, "target_strike": target, "expiry_is_nearest": True,
            "strike_is_nearest": abs(best["strike"] - target) == min(abs(c["strike"] - target) for c in same_day)}


# ---------------------------------------------------------------- exits

def stop_level(exposure: str, underlying_entry: float, pct: float) -> float:
    """SPY price at which the loser exits: pct against the entry."""
    f = pct / 100.0
    return round(underlying_entry * (1 - f) if exposure == "long" else underlying_entry * (1 + f), 4)


def stop_hit(exposure: str, spy_price: float, level: float) -> bool:
    return spy_price <= level if exposure == "long" else spy_price >= level
