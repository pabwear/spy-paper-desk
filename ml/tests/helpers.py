"""Shared fixtures for the ML tests: the repo on sys.path, a config, and synthetic sessions (never market data)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml import config as mlconfig  # noqa: E402
from ml import data  # noqa: E402

CFG = mlconfig.load()


def bars(days: int = 15, seed: int = 1, **kw):
    """Cleaned synthetic sessions starting Mon 2026-03-02 (weekdays only)."""
    start = date(2026, 3, 2)
    end = date(2026, 3, 2 + days) if days < 28 else date(2026, 3 + days // 28, 1)
    raw = data.SyntheticProvider(seed=seed, **kw).fetch("SPY", start, end)
    b, audit = data.clean(raw, CFG["session"])
    return b, audit
