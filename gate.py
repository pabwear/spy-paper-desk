"""The order gates. A paper order may be sent only if every check passes.

check_order  — entries (SPY option or SPY shares, whichever is active and enabled)
check_exit   — closing a desk position (stop or the 16:00 flatten)

Pure functions: the caller supplies the clock, the files' contents and what the
paper account reported. Nothing here can place an order.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from urllib.parse import urlparse

import instruments
from common import ET, LIVE_HOST, PAPER_HOST, hhmm, to_et
from signals import COLOR_FOR_SIDE, price_near_zone


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def host_of(url: str | None) -> str:
    return (urlparse(url or "").hostname or "").lower()


def override_freshness(override: dict | None, now: datetime, risk: dict) -> tuple[bool, str]:
    """Was the override written today between the open and the end of watch-only?"""
    if not override:
        return False, "aoi_override.json is missing"
    written = to_et(override.get("written_at"))
    if written is None:
        return False, "No written_at timestamp (with time zone) on the override"
    now = now.astimezone(ET)
    if written.date() != now.date():
        return False, f"Written {written:%Y-%m-%d}, not today ({now:%Y-%m-%d})"
    if written.time() < hhmm(risk["rth_open"]):
        return False, f"Written {written:%H:%M} ET, before the {risk['rth_open']} open"
    if written.time() >= hhmm(risk["rth_watch_only_until"]):
        return False, f"Written {written:%H:%M} ET, after the {risk['rth_watch_only_until']} cutoff"
    if written > now:
        return False, "Written in the future"
    return True, f"Written today {written:%H:%M} ET from the open"


def is_approximate(override: dict | None) -> bool:
    if not override:
        return False
    if override.get("approximate"):
        return True
    if "approx" in str(override.get("source", "")).lower():
        return True
    return any(z.get("approximate") for z in override.get("zones", []) or [])


def _paper_checks(add, *, base_url, client_is_paper, config, rules, account_number) -> None:
    host = host_of(base_url)
    add("paper_client", client_is_paper and host == PAPER_HOST and host != LIVE_HOST,
        f"host {host or '(none)'}, paper={client_is_paper}")
    live_off = (config.get("live_unlocked") is False and config.get("mode") == "paper"
                and rules.get("live_trading") is False and rules.get("mode") == "paper")
    add("live_locked", live_off,
        "live_unlocked false, mode paper" if live_off else "live_unlocked/mode is not paper-only — stop")
    expected = config.get("account_number")
    add("paper_1000_account", bool(expected) and account_number == expected,
        f"account {account_number or '(unknown)'} vs expected {expected}")


def check_order(
    *,
    now: datetime,
    base_url: str,
    client_is_paper: bool,
    config: dict,
    rules: dict,
    risk: dict,
    override: dict | None,
    plan: dict,
    price: float,
    zone: dict | None,
    tags: list[str],
    account_number: str | None,
    market_open: bool | None,
    entries_today: int,
    open_positions: list[dict] | None,
    open_orders: int | None,
    watch: dict | None = None,
) -> list[Check]:
    checks: list[Check] = []
    watch = watch or {"focus": ["SPY"], "symbols": {"SPY": {}}}
    add = lambda name, ok, detail: checks.append(Check(name, bool(ok), detail))  # noqa: E731

    _paper_checks(add, base_url=base_url, client_is_paper=client_is_paper, config=config, rules=rules,
                  account_number=account_number)

    underlying = plan.get("underlying")
    inst = instruments.for_symbol(underlying, rules, watch)
    add("instrument_enabled", inst["enabled"] and plan.get("instrument") == inst["name"],
        inst["why"] if plan.get("instrument") == inst.get("name") else
        f"plan is {plan.get('instrument')}, {underlying} uses {inst.get('name')}")

    add("symbol_in_focus", underlying in (watch.get("focus") or []) and underlying in (watch.get("symbols") or {}),
        f"{underlying} {'is' if underlying in (watch.get('focus') or []) else 'is not'} on the focus list")
    if underlying == "SNDK":
        add("sndk_off", rules.get("sndk_enabled") is True, "sndk_enabled is false")

    t = now.astimezone(ET)
    add("weekday", t.weekday() < 5, t.strftime("%A"))
    cutoff = risk.get("entry_cutoff", risk["rth_close"])
    in_window = hhmm(risk["rth_watch_only_until"]) <= t.time() < hhmm(cutoff)
    add("time_window", t.weekday() < 5 and in_window,
        f"{t:%H:%M} ET; entries {risk['rth_watch_only_until']}–{cutoff} ET")

    if market_open is None:
        add("market_clock", False, "Alpaca market clock not read")
    else:
        add("market_clock", market_open, "Alpaca says the market is open" if market_open
            else "Alpaca says the market is closed (holiday or halt)")

    tradable = bool(override and override.get("tradable") is True and override.get("zones")
                    and override.get("symbol") == underlying)
    add("aoi_tradable", tradable, "tradable with zones" if tradable else "tradable is false or zones empty")
    fresh, why = override_freshness(override, now, risk)
    add("aoi_from_today_open", fresh, why)
    approx = is_approximate(override)
    add("aoi_not_approximate", not approx, "approximate/stand-in levels" if approx else "real read")
    in_override = bool(zone and override and zone in (override.get("zones") or []))
    add("zone_published", in_override, "zone is in today's override" if in_override else "zone not in override")

    tol = float(risk.get("zone_midpoint_tolerance_pct", 0.15))
    near = bool(zone) and price_near_zone(price, zone, tol)
    add("price_at_zone", near, f"{underlying} {price:.2f} vs zone {zone['low']}–{zone['high']}" if zone else "no zone")

    signal = plan.get("signal")
    want = COLOR_FOR_SIDE.get(signal)
    add("zone_color_matches_side", bool(zone) and want is not None and zone.get("color") == want,
        f"{signal} needs {want}; zone is {zone.get('color') if zone else '(none)'}")

    need = int(rules.get("min_confluence", 2))
    add("confluence", len(tags) >= need, f"{len(tags)} of {need} needed: {', '.join(tags) or 'none'}")

    cap = int(rules.get("max_entries_per_day", 2))
    add("entries_today", entries_today < cap, f"{entries_today} of {cap} entries used today")

    if open_positions is None or open_orders is None:
        add("one_position", False, "paper positions/orders not read")
    else:
        busy = [p["symbol"] for p in open_positions if float(p.get("qty") or 0) != 0]
        add("one_position", not busy and open_orders == 0,
            "flat, no open orders" if not busy and open_orders == 0 else
            f"open: {', '.join(busy) or '—'}; open orders {open_orders}")

    if plan.get("asset") == "option":
        opt = rules.get("option", {})
        right_ok = plan.get("right") == (opt.get("right_on_buy") if signal == "buy" else opt.get("right_on_sell"))
        add("option_right", right_ok, f"{signal} → {plan.get('right')}")
        add("option_one_contract", plan.get("contracts") == 1 and plan.get("qty") == 1
            and plan.get("order_side") == "buy", f"{plan.get('qty')} contract(s), {plan.get('order_side')} to open")
        occ = instruments.parse_occ(plan.get("symbol") or "")
        contract_ok = bool(occ and occ["underlying"] == underlying and occ["right"] == plan.get("right")
                           and plan.get("expiry_is_nearest") and plan.get("strike_is_nearest"))
        add("option_contract", contract_ok,
            f"{plan.get('symbol')} (strike {occ['strike']:g}, exp {occ['expiry']})" if occ else "no contract chosen")
    else:
        sizing = float(risk["size_as_if_equity_usd"]) * float(risk["max_notional_pct_of_sizing_equity"]) / 100.0
        qty = float(plan.get("qty") or 0)
        notional = qty * price
        # round(..., 4) can land a hair over the cap; allow half a 0.0001-share step.
        add("size_within_cap", qty > 0 and notional <= sizing + price * 0.00005,
            f"qty {qty} ≈ ${notional:,.2f} vs cap ${sizing:,.2f}")

    return checks


def check_exit(
    *,
    now: datetime,
    base_url: str,
    client_is_paper: bool,
    config: dict,
    rules: dict,
    position: dict,
    account_number: str | None,
    market_open: bool | None,
    underlyings=("SPY",),
) -> list[Check]:
    """Exits stay allowed even if the instrument was switched off: closing only removes risk."""
    checks: list[Check] = []
    add = lambda name, ok, detail: checks.append(Check(name, bool(ok), detail))  # noqa: E731
    _paper_checks(add, base_url=base_url, client_is_paper=client_is_paper, config=config, rules=rules,
                  account_number=account_number)
    t = now.astimezone(ET)
    add("weekday", t.weekday() < 5, t.strftime("%A"))
    add("market_clock", market_open is True, "market open" if market_open else "market closed or not read")
    add("desk_position", instruments.is_desk_symbol(position.get("symbol", ""), underlyings)
        and float(position.get("qty") or 0) != 0,
        f"{position.get('symbol')} qty {position.get('qty')}")
    return checks


def passed(checks: list[Check]) -> bool:
    return bool(checks) and all(c.ok for c in checks)


def failures(checks: list[Check]) -> list[str]:
    return [c.name for c in checks if not c.ok]
