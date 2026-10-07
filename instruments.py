"""Instruments the desk knows about, which one each focus symbol uses, and what an entry looks like.

Pure: no I/O. Paths that are switched off stay here so turning them back on is a
config change, not a rewrite.

    options  one long call (buy signal) or put (sell signal), 1 contract,
             strike nearest the dollar to the stock; the nearest listed expiry, or (rules.json
             option.expiry_target_days) the expiry about that many days out that the account can afford
    shares   shares sized off risk.json (80% of the $1,000 book = $800)

Which one a symbol uses, and whether it may trade:
    SPY    rules.json "active" (spy_options / spy_shares) and its switch
           (spy_options_enabled / shares_enabled)
    SNDK   watchlist.json instrument, switched by rules.json sndk_enabled
    other  watchlist.json instrument, switched by that entry's "trading_enabled"
           (absent → watch only)
"""

from __future__ import annotations

import re
from datetime import date

from signals import size_qty, whole_shares

INSTRUMENTS = {
    "spy_options": {"flag": "spy_options_enabled", "underlying": "SPY", "asset": "option", "implemented": True},
    "spy_shares": {"flag": "shares_enabled", "underlying": "SPY", "asset": "shares", "implemented": True},
    "sndk_shares": {"flag": "sndk_enabled", "underlying": "SNDK", "asset": "shares", "implemented": True},
    "sndk_options": {"flag": "sndk_enabled", "underlying": "SNDK", "asset": "option", "implemented": True},
}

OCC = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<cp>[CP])(?P<strike>\d{8})$")


def active(rules: dict) -> dict:
    """SPY's instrument (rules.json "active") and whether it may trade."""
    return for_symbol("SPY", rules, {"symbols": {"SPY": {}}})


def for_symbol(symbol: str, rules: dict, watch: dict) -> dict:
    """The instrument this symbol uses and whether it may trade. Unknown or switched off → enabled False."""
    entry = (watch.get("symbols") or {}).get(symbol)
    if entry is None:
        return {"name": None, "underlying": symbol, "enabled": False, "why": f"{symbol} is not on the watchlist"}
    if symbol == "SPY":
        name = rules.get("active")
        spec = INSTRUMENTS.get(name)
        if spec is None or spec["underlying"] != "SPY":
            return {"name": name, "underlying": "SPY", "enabled": False, "why": f"unknown SPY instrument {name!r}"}
        if rules.get(spec["flag"]) is not True:
            return {"name": name, **spec, "enabled": False, "why": f"{spec['flag']} is false"}
        return {"name": name, **spec, "enabled": True, "why": f"{name} on ({spec['flag']} true)"}
    kind = entry.get("instrument")
    if kind not in ("options", "shares"):
        return {"name": None, "underlying": symbol, "asset": None, "enabled": False,
                "why": f"{symbol} has no instrument set (watch only)"}
    asset = "option" if kind == "options" else "shares"
    name = f"{symbol.lower()}_{kind}"
    base = {"name": name, "underlying": symbol, "asset": asset, "implemented": True}
    if symbol == "SNDK":
        on = rules.get("sndk_enabled") is True
        return {**base, "flag": "sndk_enabled", "enabled": on,
                "why": f"{name} on (sndk_enabled true)" if on else "sndk_enabled is false"}
    on = entry.get("trading_enabled") is True
    return {**base, "flag": "trading_enabled", "enabled": on,
            "why": f"{name} on (watchlist trading_enabled true)" if on else f"{symbol} is watch only (trading_enabled not true)"}


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


def is_desk_symbol(symbol: str, underlyings=("SPY",)) -> bool:
    """Shares or an option on one of the desk's underlyings. Anything else is never touched."""
    return underlying_of(symbol) in set(underlyings)


def desk_underlyings(rules: dict, watch: dict, entry_symbols=()) -> set[str]:
    """Underlyings whose positions the desk manages: SPY, anything switched on, anything it entered."""
    out = {"SPY"}
    out |= {s for s in (watch.get("symbols") or {}) if for_symbol(s, rules, watch)["enabled"]}
    out |= {underlying_of(x) for x in entry_symbols if x}
    return out


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
    if inst.get("asset") == "option":
        opt = rules.get("option", {})
        right = opt.get("right_on_buy", "call") if signal == "buy" else opt.get("right_on_sell", "put")
        return {**base, "order_side": "buy", "intent": "buy_to_open", "right": right,
                "contracts": int(opt.get("contracts", 1)), "qty": int(opt.get("contracts", 1)),
                "target_strike": float(round(price)), "expiry": "nearest", "symbol": None}
    if inst.get("asset") == "shares":
        if signal == "buy":
            qty = size_qty(price, risk)
        else:
            qty = float(whole_shares(price, risk))  # fractional shares cannot be sold short
        return {**base, "order_side": signal, "intent": "open", "qty": qty, "symbol": inst.get("underlying"),
                "notional": round(qty * price, 2)}
    return {**base, "order_side": None, "qty": 0, "symbol": None}


def pick_contract(contracts: list[dict], price: float, right: str, today: date, target_days: int = 0,
                  min_days: int = 0, max_cost: float | None = None, asks: dict[str, float] | None = None) -> dict | None:
    """The strike nearest the dollar to the stock, on the expiry the rules ask for.

    target_days 0: the nearest listed expiry on or after today (the desk's original rule).
    target_days N: the expiry closest to N days out (a later one wins a tie), at least min_days out, whose ask
    × 100 is at most max_cost; an expiry without a quote is skipped. None when nothing qualifies.

    contracts: [{symbol, expiry (date), right, strike, tradable}]; asks: {symbol: ask per share}
    """
    usable = [c for c in contracts if c.get("tradable", True) and c["right"] == right and c["expiry"] >= today]
    if not usable:
        return None
    if target_days:
        target = float(round(price))
        options = []
        for exp in sorted({c["expiry"] for c in usable}):
            days = (exp - today).days
            if days < min_days:
                continue
            on_day = [c for c in usable if c["expiry"] == exp]
            best = min(on_day, key=lambda c: (abs(c["strike"] - target), abs(c["strike"] - price)))
            ask = (asks or {}).get(best["symbol"])
            if ask is None or (max_cost is not None and ask * 100 > max_cost):
                continue
            options.append((abs(days - target_days), -days, best, ask, on_day))
        if not options:
            return None
        _, _, best, ask, on_day = min(options, key=lambda o: (o[0], o[1]))
        return {**best, "target_strike": target, "ask": ask, "cost": round(ask * 100, 2), "expiry_is_nearest": False,
                "expiry_ok": True, "expiry_rule": f"about {target_days} days out (at least {min_days}), affordable",
                "strike_is_nearest": abs(best["strike"] - target) == min(abs(c["strike"] - target) for c in on_day)}
    nearest_expiry = min(c["expiry"] for c in usable)
    target = float(round(price))
    same_day = [c for c in usable if c["expiry"] == nearest_expiry]
    best = min(same_day, key=lambda c: (abs(c["strike"] - target), abs(c["strike"] - price)))
    return {**best, "target_strike": target, "expiry_is_nearest": True, "expiry_ok": True, "expiry_rule": "nearest listed",
            "strike_is_nearest": abs(best["strike"] - target) == min(abs(c["strike"] - target) for c in same_day)}


# ---------------------------------------------------------------- exits

def stop_level(exposure: str, underlying_entry: float, pct: float) -> float:
    """SPY price at which the loser exits: pct against the entry."""
    f = pct / 100.0
    return round(underlying_entry * (1 - f) if exposure == "long" else underlying_entry * (1 + f), 4)


def stop_hit(exposure: str, spy_price: float, level: float) -> bool:
    return spy_price <= level if exposure == "long" else spy_price >= level
