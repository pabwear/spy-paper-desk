"""Exported models for the desk's run: numpy only (no scikit-learn, no PyTorch at decision time).

A model folder holds model.json (+ weights.npz for the TCN):
    kind, version, horizon, target, feature_version + names, training/validation windows, threshold and the
    expected net return at it (both from validation), approved (false until acceptance + holdout pass),
    config hash, data hash, and the model's own numbers (logistic: scaler + coefficients; TCN: architecture,
    scaler, weights file).
`load()` refuses a folder whose feature schema differs from the code. Every prediction also returns an
out-of-distribution flag (a standardised input beyond +/-8).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from ml import features as featmod
from ml.models import Calibrator
from ml.tcn import OOD_Z, ModelLoadError, NumpyTCN


def export_logistic(m, folder: str | Path, meta: dict) -> dict:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    nums = {"mean": m.scaler.mean_.tolist(), "scale": m.scaler.scale_.tolist(), "coef": m.m.coef_[0].tolist(),
            "intercept": float(m.m.intercept_[0])}
    digest = hashlib.sha256(json.dumps(nums, sort_keys=True).encode()).hexdigest()[:12]
    info = {**meta, "kind": "logistic", "version": f"logistic-{digest}", "feature_version": featmod.FEATURE_VERSION,
            "features": featmod.NAMES, "calibration": {"a": m.cal.a, "b": m.cal.b}, **nums}
    (folder / "model.json").write_text(json.dumps(info, indent=1))
    return info


class NumpyLogistic:
    sequence = False

    def __init__(self, meta: dict) -> None:
        self.meta = meta
        self.mean, self.scale = np.array(meta["mean"]), np.array(meta["scale"])
        self.coef, self.b = np.array(meta["coef"]), float(meta["intercept"])
        self.cal = Calibrator()
        self.cal.a, self.cal.b = meta["calibration"]["a"], meta["calibration"]["b"]

    def predict_rows(self, F, sess, idx) -> tuple[np.ndarray, np.ndarray]:
        Z = (F[idx] - self.mean) / self.scale
        ood = np.nan_to_num(np.abs(Z), nan=np.inf).max(axis=1) > OOD_Z
        Z = np.nan_to_num(Z, nan=0.0)
        p = 1 / (1 + np.exp(-(Z @ self.coef + self.b)))
        return self.cal(p), ood


def load(folder: str | Path):
    folder = Path(folder)
    try:
        meta = json.loads((folder / "model.json").read_text())
    except Exception as e:  # noqa: BLE001
        raise ModelLoadError(f"no model at {folder}: {type(e).__name__}") from e
    if meta.get("feature_version") != featmod.FEATURE_VERSION or meta.get("features") != featmod.NAMES:
        raise ModelLoadError("feature schema differs from the code's: retrain the model")
    if meta.get("kind") == "logistic":
        return NumpyLogistic(meta)
    if meta.get("kind") == "tcn":
        m = NumpyTCN(folder)
        m.sequence = True
        return m
    raise ModelLoadError(f"unknown model kind {meta.get('kind')!r}")
