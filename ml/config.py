"""Configuration for the ML engine: one JSON file (ml/default_config.json), checked before anything runs.

Everything a result depends on (horizons, costs, delays, splits, seed) lives here, and its hash is written next to
every dataset, model and report, so a number can always be traced to the settings that made it.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

DEFAULT = Path(__file__).resolve().parent / "default_config.json"


class ConfigError(ValueError):
    pass


def load(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    cfg = json.loads(Path(path or DEFAULT).read_text(encoding="utf-8"))
    if overrides:
        cfg = merge(cfg, overrides)
    validate(cfg)
    return cfg


def merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def validate(cfg: dict) -> None:
    """Refuse settings that would make results meaningless or unsafe. Raises ConfigError."""
    def need(cond: bool, msg: str) -> None:
        if not cond:
            raise ConfigError(msg)

    need(cfg.get("schema") == 1, "unknown config schema")
    need(isinstance(cfg.get("symbol"), str) and cfg["symbol"].isalpha(), "symbol must be a ticker")
    need(cfg.get("bar_minutes") == 1, "only 1-minute bars are supported")
    hz = cfg.get("horizons_minutes") or []
    need(bool(hz) and all(isinstance(h, int) and 1 <= h <= 240 for h in hz), "horizons must be 1-240 minutes")
    need(int(cfg.get("entry_delay_bars", -1)) >= 0, "entry_delay_bars must be >= 0 (0 = next bar's open)")
    c = cfg.get("costs") or {}
    need(float(c.get("per_share_round_trip_usd", -1)) >= 0 and float(c.get("slippage_bps_each_side", -1)) >= 0,
         "costs must be >= 0 (never free trading by accident)")
    need(int(cfg.get("lookback_bars", 0)) >= 5, "lookback_bars must be >= 5")
    s = cfg.get("session") or {}
    o, cl, first, last = (_hhmm(s[k]) for k in ("open", "close", "first_decision", "last_entry"))
    need(o < first <= last < cl, "session times must be open < first_decision <= last_entry < close")
    sp = cfg.get("splits") or {}
    for k in ("train_months", "validation_months", "test_months", "step_months", "holdout_months"):
        need(int(sp.get(k, 0)) >= 1, f"splits.{k} must be >= 1")
    need(int(sp.get("embargo_minutes", -1)) >= max(hz), "embargo must be at least the longest horizon (no label overlap)")
    need(int(cfg.get("decision_every_minutes", 0)) >= 1, "decision_every_minutes must be >= 1")


def digest(cfg: dict) -> str:
    """Short, stable hash of the settings (key order does not matter)."""
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]


def get(cfg: dict, dotted: str, default: Any = None) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
