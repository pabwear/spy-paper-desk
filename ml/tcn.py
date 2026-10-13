"""A compact temporal convolutional network (TCN): reads the last `lookback` minutes of features, outputs P(y=1).

Architecture (small on purpose: ~20k weights, trains on a CPU in minutes):
    input  [batch, features, lookback]   standardised with the TRAIN window's mean/std, clipped to +/-5,
                                          unknown values (NaN, other-session minutes) set to 0 = the train mean
    4 residual blocks, each: causal Conv1d(kernel 3, dilation 1/2/4/8) -> ReLU -> dropout, + skip connection
          (receptive field 31 minutes: the output at minute t sees t-30..t, never t+1)
    last time step -> Linear -> logit
Training: binary cross-entropy, AdamW (lr 1e-3, weight decay 1e-4), batches of 512, every 2nd training minute
(labels overlap heavily anyway), early stopping on the validation log loss (patience 3, at most 15 epochs),
learning rate halved when validation stops improving, fixed seeds. No class weights: the base rate is ~45-50%
and probabilities are calibrated on validation afterwards (Platt), like the other models.

Inference has two paths that must agree (tested): PyTorch for training, and plain numpy (`NumpyTCN`) so the
desk's 10-minute run never needs PyTorch. A checkpoint = weights (.npz) + metadata (.json: feature version and
names, scaler, architecture, horizon, train window, calibration, seed, a version hash). Loading refuses a
different feature version. Inputs whose standardised value exceeds +/-8 are flagged out-of-distribution.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np

from ml import features as featmod
from ml.models import Calibrator, log_loss

ARCH = {"channels": 32, "kernel": 3, "dilations": [1, 2, 4, 8], "dropout": 0.1, "lookback": 30}
TRAIN = {"lr": 1e-3, "weight_decay": 1e-4, "batch": 512, "max_epochs": 15, "patience": 3, "stride": 2}
OOD_Z = 8.0


def windows(F: np.ndarray, sess: np.ndarray, idx: np.ndarray, lookback: int) -> np.ndarray:
    """[len(idx), n_features, lookback] windows ending at each row in idx; rows from another session -> NaN."""
    offs = np.arange(-lookback + 1, 1)
    rows = idx[:, None] + offs[None, :]
    valid = rows >= 0
    rows_c = np.clip(rows, 0, len(F) - 1)
    W = F[rows_c]                                     # [n, lookback, f]
    same = valid & (sess[rows_c] == sess[idx][:, None])
    W[~same] = np.nan
    return np.transpose(W, (0, 2, 1))


def standardise(W: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    Z = (W - mean[None, :, None]) / std[None, :, None]
    Z = np.clip(Z, -5, 5)
    return np.nan_to_num(Z, nan=0.0).astype(np.float32)


def _torch_net(n_in: int, arch: dict):
    import torch
    from torch import nn

    class Block(nn.Module):
        def __init__(self, cin, cout, k, d, p):
            super().__init__()
            self.pad = (k - 1) * d
            self.conv = nn.Conv1d(cin, cout, k, dilation=d)
            self.drop = nn.Dropout(p)
            self.skip = nn.Conv1d(cin, cout, 1) if cin != cout else None

        def forward(self, x):
            y = self.conv(nn.functional.pad(x, (self.pad, 0)))  # causal: pad on the left only
            y = self.drop(torch.relu(y))
            return y + (x if self.skip is None else self.skip(x))

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            ch = arch["channels"]
            layers, cin = [], n_in
            for d in arch["dilations"]:
                layers.append(Block(cin, ch, arch["kernel"], d, arch["dropout"]))
                cin = ch
            self.blocks = nn.Sequential(*layers)
            self.head = nn.Linear(ch, 1)

        def forward(self, x):
            return self.head(self.blocks(x)[:, :, -1]).squeeze(-1)

    return Net()


class TCN:
    """Same contract as ml.models.Model, but on windows: fit_rows / predict_rows take row indices."""

    name = "tcn"
    sequence = True

    def __init__(self, seed: int = 7, arch: dict | None = None, train: dict | None = None) -> None:
        self.seed, self.arch, self.train = seed, {**ARCH, **(arch or {})}, {**TRAIN, **(train or {})}
        self.cal, self.params, self.fit_seconds, self.net = Calibrator(), {}, 0.0, None

    def fit_rows(self, F, sess, idx_tr, y_tr, idx_va, y_va) -> "TCN":
        import torch

        t0 = time.perf_counter()
        torch.manual_seed(self.seed)
        np.random.seed(self.seed)
        torch.use_deterministic_algorithms(True, warn_only=True)
        L = self.arch["lookback"]
        idx_tr, y_tr = idx_tr[:: self.train["stride"]], y_tr[:: self.train["stride"]]
        Xtr_raw = F[idx_tr]
        self.mean = np.nanmean(Xtr_raw, axis=0)
        self.std = np.nanstd(Xtr_raw, axis=0)
        self.std[~np.isfinite(self.std) | (self.std < 1e-9)] = 1.0
        self.mean[~np.isfinite(self.mean)] = 0.0
        Xtr = torch.from_numpy(standardise(windows(F, sess, idx_tr, L), self.mean, self.std))
        Xva = torch.from_numpy(standardise(windows(F, sess, idx_va, L), self.mean, self.std))
        ytr = torch.from_numpy(y_tr.astype(np.float32))
        self.net = _torch_net(F.shape[1], self.arch)
        opt = torch.optim.AdamW(self.net.parameters(), lr=self.train["lr"], weight_decay=self.train["weight_decay"])
        sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, factor=0.5, patience=1)
        lossf = torch.nn.BCEWithLogitsLoss()
        g = torch.Generator().manual_seed(self.seed)
        best, best_state, bad, history = None, None, 0, []
        for epoch in range(self.train["max_epochs"]):
            self.net.train()
            order = torch.randperm(len(Xtr), generator=g)  # shuffles WITHIN the train window only
            for k in range(0, len(order), self.train["batch"]):
                b = order[k: k + self.train["batch"]]
                opt.zero_grad()
                loss = lossf(self.net(Xtr[b]), ytr[b])
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.net.parameters(), 1.0)
                opt.step()
            vl = log_loss(y_va, self._raw_t(Xva))
            history.append(round(vl, 5))
            sched.step(vl)
            if best is None or vl < best - 1e-5:
                best, bad = vl, 0
                best_state = {k: v.detach().clone() for k, v in self.net.state_dict().items()}
            else:
                bad += 1
                if bad >= self.train["patience"]:
                    break
        self.net.load_state_dict(best_state)
        self.cal = Calibrator().fit(self._raw_t(Xva), y_va)
        self.params = {**self.arch, "epochs": len(history), "val_log_loss": round(best, 5), "history": history,
                       "weights": int(sum(p.numel() for p in self.net.parameters()))}
        self.fit_seconds = time.perf_counter() - t0
        return self

    def _raw_t(self, X) -> np.ndarray:
        import torch

        self.net.eval()
        with torch.no_grad():
            out = []
            for k in range(0, len(X), 4096):
                out.append(torch.sigmoid(self.net(X[k: k + 4096])).numpy())
        return np.concatenate(out) if out else np.zeros(0)

    def predict_rows(self, F, sess, idx) -> np.ndarray:
        import torch

        X = torch.from_numpy(standardise(windows(F, sess, idx, self.arch["lookback"]), self.mean, self.std))
        return self.cal(self._raw_t(X))

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params, "calibration": {"a": self.cal.a, "b": self.cal.b},
                "fit_seconds": round(self.fit_seconds, 2)}

    # ---------- checkpoints

    def save(self, folder: str | Path, meta: dict) -> dict:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        w = {k: v.detach().numpy() for k, v in self.net.state_dict().items()}
        np.savez(folder / "weights.npz", **w)
        digest = hashlib.sha256(b"".join(w[k].tobytes() for k in sorted(w))).hexdigest()[:12]
        info = {**meta, "kind": "tcn", "version": f"tcn-{digest}", "feature_version": featmod.FEATURE_VERSION,
                "features": featmod.NAMES, "arch": self.arch, "mean": self.mean.tolist(), "std": self.std.tolist(),
                "calibration": {"a": self.cal.a, "b": self.cal.b}, "seed": self.seed}
        (folder / "model.json").write_text(json.dumps(info, indent=1))
        return info


class ModelLoadError(RuntimeError):
    pass


class NumpyTCN:
    """The same network, forward pass in numpy only (for the desk's run). Refuses a mismatched checkpoint."""

    def __init__(self, folder: str | Path) -> None:
        folder = Path(folder)
        try:
            self.meta = json.loads((folder / "model.json").read_text())
            raw = np.load(folder / "weights.npz", allow_pickle=False)  # no pickles: no code from a file
            self.w = {k: raw[k] for k in raw.files}
        except Exception as e:  # noqa: BLE001
            raise ModelLoadError(f"cannot load model from {folder}: {type(e).__name__}") from e
        if self.meta.get("feature_version") != featmod.FEATURE_VERSION or self.meta.get("features") != featmod.NAMES:
            raise ModelLoadError("feature schema differs from the code's: retrain the model")
        self.arch = self.meta["arch"]
        self.mean, self.std = np.array(self.meta["mean"]), np.array(self.meta["std"])
        self.cal = Calibrator()
        self.cal.a, self.cal.b = self.meta["calibration"]["a"], self.meta["calibration"]["b"]

    @staticmethod
    def _conv(x, W, b, d):
        """Causal dilated conv: x [n, cin, T], W [cout, cin, k] -> [n, cout, T]."""
        k = W.shape[2]
        pad = (k - 1) * d
        xp = np.pad(x, ((0, 0), (0, 0), (pad, 0)))
        T = x.shape[2]
        out = np.zeros((x.shape[0], W.shape[0], T), dtype=np.float64)
        for j in range(k):
            out += np.einsum("oc,nct->not", W[:, :, j], xp[:, :, j * d: j * d + T])
        return out + b[None, :, None]

    def raw(self, Z: np.ndarray) -> np.ndarray:
        x = Z.astype(np.float64)
        for i, d in enumerate(self.arch["dilations"]):
            p = f"blocks.{i}."
            y = np.maximum(self._conv(x, self.w[p + "conv.weight"], self.w[p + "conv.bias"], d), 0.0)
            if p + "skip.weight" in self.w:
                x = self._conv(x, self.w[p + "skip.weight"], self.w[p + "skip.bias"], 1)
            x = y + x
        logit = x[:, :, -1] @ self.w["head.weight"].T[:, 0] + self.w["head.bias"][0]
        return 1 / (1 + np.exp(-logit))

    def predict_rows(self, F, sess, idx) -> tuple[np.ndarray, np.ndarray]:
        """Calibrated probabilities and an out-of-distribution flag per row."""
        W = windows(F, sess, idx, self.arch["lookback"])
        z_last = (W[:, :, -1] - self.mean) / self.std
        ood = np.nan_to_num(np.abs(z_last), nan=0.0).max(axis=1) > OOD_Z
        return self.cal(self.raw(standardise(W, self.mean, self.std))), ood
