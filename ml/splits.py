"""Time-ordered splits: walk-forward folds, purging, and the locked final holdout.

Walk-forward (all by session date, never shuffled):
    fold k:  train [a, b)  ->  validation [b, c)  ->  test [c, d)
    then everything moves forward `step_months` and repeats. Train is a rolling window of `train_months`.
    Validation picks settings (thresholds, epochs, calibration); test is only scored. Results are reported per
    fold and pooled across folds.

Purging: a training row is dropped when its label ends after (next window start - embargo). Labels never
cross a session and windows start on a session, so with the desk's rules nothing overlaps; the purge is kept
as a guard and is tested.

Final holdout: the last `holdout_months` before the lock date are set aside ONCE (ml/holdout.json, committed).
Nothing may train, tune or be scored on them except `final_evaluation`, which is recorded every time it runs.
A second final evaluation with different settings is refused: re-tuning on the holdout would make it worthless.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

HOLDOUT_FILE = Path(__file__).resolve().parent / "holdout.json"


class HoldoutError(RuntimeError):
    pass


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


@dataclass(frozen=True)
class Fold:
    k: int
    train: tuple[date, date]
    validation: tuple[date, date]
    test: tuple[date, date]

    def as_dict(self) -> dict:
        return {"k": self.k, "train": [str(x) for x in self.train], "validation": [str(x) for x in self.validation],
                "test": [str(x) for x in self.test]}


def walk_forward(first: date, holdout_start: date, sp: dict) -> list[Fold]:
    """Folds whose test windows end on or before the holdout start. Month-aligned."""
    tr, va, te, step = (int(sp[k]) for k in ("train_months", "validation_months", "test_months", "step_months"))
    a = month_start(first) if first.day == 1 else add_months(first, 1)
    folds, k = [], 0
    while True:
        b = add_months(a, tr)
        c = add_months(b, va)
        d = add_months(c, te)
        if d > holdout_start:
            break
        folds.append(Fold(k, (a, b), (b, c), (c, d)))
        k += 1
        a = add_months(a, step)
    return folds


def in_window(sessions: pd.Series, win: tuple[date, date]) -> pd.Series:
    return (sessions >= win[0]) & (sessions < win[1])


def purge(mask: pd.Series, label_end: pd.Series, next_start: date, embargo_minutes: int, tz) -> pd.Series:
    """Drop rows whose label is unknown or ends after the next window's start minus the embargo."""
    boundary = pd.Timestamp(datetime(next_start.year, next_start.month, next_start.day, tzinfo=tz)) \
        - pd.Timedelta(minutes=embargo_minutes)
    return mask & label_end.notna() & (label_end <= boundary)


# ---------- the final holdout


def read_lock(path: Path = HOLDOUT_FILE) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def set_lock(last_session: date, months: int, path: Path = HOLDOUT_FILE) -> dict:
    """Fix the holdout once: the `months` months before the month after `last_session`. Never moves after that."""
    cur = read_lock(path)
    if cur:
        return cur
    end = add_months(month_start(last_session), 1)
    lock = {"start": str(add_months(end, -months)), "end": str(end), "set_at": _now(), "evaluations": []}
    path.write_text(json.dumps(lock, indent=2) + "\n")
    return lock


def holdout_start(path: Path = HOLDOUT_FILE) -> date:
    lock = read_lock(path)
    if not lock:
        raise HoldoutError("No holdout lock yet: run `python -m ml.run lock` once before any training.")
    return date.fromisoformat(lock["start"])


def guard(sessions: pd.Series, path: Path = HOLDOUT_FILE) -> None:
    """Raise if any session is inside the holdout. Every training, tuning and research scoring path calls this."""
    start = holdout_start(path)
    if len(sessions) and (pd.Series(sessions) >= start).any():
        raise HoldoutError(f"Refused: data on or after {start} is the locked final holdout.")


def record_final(config_hash: str, model_version: str, result: dict, path: Path = HOLDOUT_FILE) -> dict:
    """Log a final evaluation. A different config/model than the first one is refused (no tuning on the holdout)."""
    lock = read_lock(path)
    if not lock:
        raise HoldoutError("No holdout lock.")
    evs = lock.setdefault("evaluations", [])
    if evs and (evs[0]["config_hash"], evs[0]["model_version"]) != (config_hash, model_version):
        raise HoldoutError("Refused: the holdout was already used by another configuration. A new untouched "
                           "holdout needs new data (time has to pass).")
    evs.append({"at": _now(), "config_hash": config_hash, "model_version": model_version, "result": result})
    path.write_text(json.dumps(lock, indent=2) + "\n")
    return lock


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
