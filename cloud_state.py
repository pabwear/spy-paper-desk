"""Cloud runs (GitHub Actions): keep the desk's running state on the `desk-state` branch.

The code lives on `main`. What changes while the desk runs (journal, ledger, account snapshot,
today's zones, focus list, pulse, learner) lives on `desk-state`, so `main` stays clean.

    python3 cloud_state.py restore DIR     # copy state files from DIR (the desk-state checkout) into the desk
    python3 cloud_state.py save DIR        # copy them back into DIR, ready to commit
    python3 cloud_state.py summary         # a short Markdown status for the run page
    python3 cloud_state.py zones           # apply the zones form (env: SYMBOL, RED, GREEN, TAGS, NONE)
    python3 cloud_state.py focus           # apply the focus form (env: ACTION, SYMBOL, INSTRUMENT)
    python3 cloud_state.py pulse           # apply live readings (env: READINGS, a JSON object)
    python3 cloud_state.py settings        # apply the settings form (env: EXTENDED_HOURS on/off, BOX_TIMEFRAME)

Form values arrive as environment variables, never pasted into a shell command.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import sys
from pathlib import Path

from common import desk_dir, load_json, now_et, save_json

STATE_FILES = ["journal.jsonl", "trades.csv", "account.json", "market_pulse.json", "learning_weights.json",
               "ml_model.json", "learning_report.json", "dashboard_state.json", "watchlist.json",
               "aoi_override.json", "charts.json", "settings.json", "projections.jsonl", "projection_model.json"]
STATE_GLOBS = ["aoi_override.*.json"]
RANGE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[-–to ]+\s*(\d+(?:\.\d+)?)\s*$")


def _names(folder: Path) -> list[str]:
    names = [n for n in STATE_FILES if (folder / n).exists()]
    for pattern in STATE_GLOBS:
        names += sorted(Path(p).name for p in glob.glob(str(folder / pattern)))
    return names


def copy_state(src: Path, dst: Path) -> list[str]:
    dst.mkdir(parents=True, exist_ok=True)
    names = _names(src)
    for n in names:
        shutil.copy2(src / n, dst / n)
    return names


def parse_ranges(text: str) -> list[tuple[float, float]]:
    """'571.20-572.05, 578.4 to 579.1' -> [(571.2, 572.05), (578.4, 579.1)]. Raises ValueError on junk."""
    out = []
    for part in [p for p in re.split(r"[,;\n]+", text or "") if p.strip()]:
        m = RANGE_RE.match(part)
        if not m:
            raise ValueError(f"Could not read {part.strip()!r}. Use LOW-HIGH, e.g. 571.20-572.05")
        lo, hi = sorted((float(m.group(1)), float(m.group(2))))
        if lo <= 0 or lo == hi:
            raise ValueError(f"{part.strip()!r} is not a price range")
        out.append((lo, hi))
    return out


def zones_from_env(env=os.environ) -> dict:
    """Turn the zones form into `aoi set` / `aoi clear` arguments."""
    import run_study

    symbol = run_study.valid_symbol(env.get("SYMBOL") or "SPY")
    if str(env.get("NONE", "")).lower() in ("true", "1", "yes"):
        return {"action": "clear", "symbol": symbol}
    specs = [f"red:{lo}:{hi}" for lo, hi in parse_ranges(env.get("RED", ""))]
    specs += [f"green:{lo}:{hi}" for lo, hi in parse_ranges(env.get("GREEN", ""))]
    if not specs:
        raise ValueError("No zones given. Enter at least one red or green range, or tick 'no readable boxes'.")
    tags = [t.strip() for t in re.split(r"[,\s]+", env.get("TAGS", "")) if t.strip()]
    for t in tags:
        if not re.match(r"^\d+:[A-Za-z_]{1,20}$", t):
            raise ValueError(f"Bad tag {t!r}. Use ZONE_NUMBER:TAG, e.g. 1:CHoCH")
    return {"action": "set", "symbol": symbol, "zones": specs, "tags": tags}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd in ("restore", "save"):
        other = Path(argv[2])
        names = copy_state(other, desk_dir()) if cmd == "restore" else copy_state(desk_dir(), other)
        print(f"{cmd}: {', '.join(names) or 'nothing yet'}")
        return 0
    if cmd == "summary":
        s = load_json("dashboard_state.json", {}) or {}
        a, p, st = s.get("account") or {}, s.get("pnl") or {}, s.get("stats") or {}
        le = s.get("last_eval") or {}
        print(f"### Desk at {s.get('generated_at', '—')}\n")
        print(f"- Session: {(s.get('session') or {}).get('label', '—')}")
        print(f"- Equity: {a.get('equity', '—')} · realized P&L {p.get('realized_total', '—')} · "
              f"today {p.get('realized_today', '—')}")
        print(f"- Closed trades {st.get('closed_trades', 0)} · wins {st.get('wins', 0)} · losses {st.get('losses', 0)}")
        if le:
            print(f"- Last evaluation: {le.get('symbol', 'SPY')} {le.get('decision')} "
                  f"{', '.join(le.get('reasons') or [])}")
        return 0

    import run_study

    now = now_et()
    try:
        if cmd == "zones":
            z = zones_from_env()
            if z["action"] == "clear":
                run_study.cmd_aoi_clear(now, "No readable Mxwll boxes (zones form).", z["symbol"])
            else:
                run_study.cmd_aoi_set(now, z["zones"], z["tags"], "Roy via the zones form", z["symbol"])
        elif cmd == "focus":
            action = (os.environ.get("ACTION") or "").strip().lower()
            symbol = os.environ.get("SYMBOL", "")
            if action in ("add", "remove"):
                run_study.set_focus(symbol, action == "add", now)
            elif action in ("up", "down"):
                run_study.move_focus(symbol, -1 if action == "up" else 1)
            elif action in ("trade-on", "trade-off"):
                run_study.set_trading(symbol, os.environ.get("INSTRUMENT") or None, action == "trade-on")
            else:
                raise ValueError("ACTION must be add, remove, up, down, trade-on or trade-off")
            print("Focus:", ", ".join(load_json("watchlist.json", {}).get("focus", [])))
        elif cmd == "settings":
            choice = (os.environ.get("EXTENDED_HOURS") or "").strip().lower()
            if choice not in ("on", "off"):
                raise ValueError("EXTENDED_HOURS must be on or off")
            tf = (os.environ.get("BOX_TIMEFRAME") or "unchanged").strip().lower()
            if tf not in ("unchanged", "3 minutes", "5 minutes"):
                raise ValueError("BOX_TIMEFRAME must be unchanged, 3 minutes or 5 minutes")
            settings = {**(load_json("settings.json", {}) or {}), "extended_hours": choice == "on",
                        "changed_at": now.isoformat(timespec="seconds")}
            if tf != "unchanged":
                settings["timeframe_minutes"] = int(tf.split()[0])
            save_json("settings.json", settings)
            import journal

            journal.log("settings", now=now, extended_hours=settings["extended_hours"],
                        timeframe_minutes=settings.get("timeframe_minutes"))
            print(f"Extended hours for the Mxwll read: {choice}."
                  + (f" Box candles: {settings['timeframe_minutes']} minutes." if settings.get("timeframe_minutes") else ""))
        elif cmd == "pulse":
            readings = json.loads(os.environ.get("READINGS") or "{}")
            if not isinstance(readings, dict) or not readings:
                raise ValueError("READINGS must be a non-empty JSON object")
            run_study.cmd_pulse_ingest(now, readings)
        else:
            print(__doc__)
            return 2
    except (ValueError, SystemExit) as e:
        print(f"::error::{e}")
        return 1
    import rebuild_dashboard

    rebuild_dashboard.write_state(now)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
