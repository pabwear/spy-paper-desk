"""The order gate. A paper order may be sent only if every check here passes.

Pure function: the caller supplies the clock, the files' contents and the
client's host. Nothing here can place an order.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from urllib.parse import urlparse

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


def check_order(
    *,
    now: datetime,
    base_url: str,
    client_is_paper: bool,
    config: dict,
    rules: dict,
    risk: dict,
    override: dict | None,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    zone: dict | None,
    tags: list[str],
    account_number: str | None,
    market_open: bool | None,
) -> list[Check]:
    checks: list[Check] = []
    add = lambda name, ok, detail: checks.append(Check(name, bool(ok), detail))  # noqa: E731

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

    sym_ok = symbol == "SPY" and config.get("symbol") == "SPY" and risk.get("symbol") == "SPY"
    add("symbol_spy", sym_ok, f"symbol {symbol}")

    t = now.astimezone(ET)
    add("weekday", t.weekday() < 5, t.strftime("%A"))
    in_window = hhmm(risk["rth_watch_only_until"]) <= t.time() < hhmm(risk["rth_close"])
    add("time_window", t.weekday() < 5 and in_window,
        f"{t:%H:%M} ET; orders only {risk['rth_watch_only_until']}–{risk['rth_close']} ET")

    if market_open is None:
        add("market_clock", False, "Alpaca market clock not read")
    else:
        add("market_clock", market_open, "Alpaca says the market is open" if market_open
            else "Alpaca says the market is closed (holiday or halt)")

    tradable = bool(override and override.get("tradable") is True and override.get("zones")
                    and override.get("symbol") == "SPY")
    add("aoi_tradable", tradable, "tradable with zones" if tradable else "tradable is false or zones empty")

    fresh, why = override_freshness(override, now, risk)
    add("aoi_from_today_open", fresh, why)

    approx = is_approximate(override)
    add("aoi_not_approximate", not approx, "approximate/withdrawn levels" if approx else "real read")

    in_override = bool(zone and override and zone in (override.get("zones") or []))
    add("zone_published", in_override, "zone is in today's override" if in_override else "zone not in override")

    tol = float(risk.get("zone_midpoint_tolerance_pct", 0.15))
    near = bool(zone) and price_near_zone(price, zone, tol)
    add("price_at_zone", near,
        f"price {price:.2f} vs zone {zone['low']}–{zone['high']}" if zone else "no zone")

    want = COLOR_FOR_SIDE.get(side)
    color_ok = bool(zone) and want is not None and zone.get("color") == want
    add("zone_color_matches_side", color_ok,
        f"{side} needs {want}; zone is {zone.get('color') if zone else '(none)'}")

    add("confluence", len(tags) >= 1, ", ".join(tags) if tags else "no extra signal agrees")

    cap = float(risk["size_as_if_equity_usd"]) * float(risk["max_notional_pct_of_sizing_equity"]) / 100.0
    notional = qty * price
    # round(..., 4) can land a hair over the cap; allow half a 0.0001-share step.
    size_ok = qty > 0 and notional <= cap + price * 0.00005
    add("size_within_cap", size_ok, f"qty {qty} ≈ ${notional:,.2f} vs cap ${cap:,.2f}")

    return checks


def passed(checks: list[Check]) -> bool:
    return bool(checks) and all(c.ok for c in checks)


def failures(checks: list[Check]) -> list[str]:
    return [c.name for c in checks if not c.ok]
