"""Tabular models with one interface: fit on train, choose settings and calibrate on validation, predict P(y=1).

    BaseRate   the training share of 1s, for every row: the "no skill" yardstick every model must beat
    Logistic   standardised features (scaler fitted on train only) + L2 logistic regression, C picked on validation
    GBT        histogram gradient-boosted trees (scikit-learn), a small grid picked on validation

After fitting, every model is calibrated on the validation window (Platt scaling: a 1-feature logistic on the
model's log-odds), so "0.56" means "about 56 in 100" on data it was not trained on. The test window is never seen
here. The sequence model (TCN) lives in ml/tcn.py with the same interface.
"""

from __future__ import annotations

import time

import numpy as np

EPS = 1e-6


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


class Calibrator:
    """Platt scaling fitted on validation predictions only."""

    def __init__(self) -> None:
        self.a, self.b = 1.0, 0.0

    def fit(self, p: np.ndarray, y: np.ndarray) -> "Calibrator":
        from sklearn.linear_model import LogisticRegression

        if len(np.unique(y)) < 2:
            return self
        lr = LogisticRegression(C=1e6, max_iter=1000).fit(_logit(p).reshape(-1, 1), y)
        self.a, self.b = float(lr.coef_[0, 0]), float(lr.intercept_[0])
        return self

    def __call__(self, p: np.ndarray) -> np.ndarray:
        return 1 / (1 + np.exp(-(self.a * _logit(p) + self.b)))


class Model:
    name = "model"

    def __init__(self, seed: int = 7) -> None:
        self.seed, self.params, self.cal = seed, {}, Calibrator()
        self.fit_seconds = 0.0

    def _fit(self, X, y, Xv, yv) -> None:
        raise NotImplementedError

    def _raw(self, X) -> np.ndarray:
        raise NotImplementedError

    def fit(self, X: np.ndarray, y: np.ndarray, Xv: np.ndarray, yv: np.ndarray) -> "Model":
        t0 = time.perf_counter()
        self._fit(X, y, Xv, yv)
        self.cal = Calibrator().fit(self._raw(Xv), yv)
        self.fit_seconds = time.perf_counter() - t0
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.cal(self._raw(X))

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params, "calibration": {"a": self.cal.a, "b": self.cal.b},
                "fit_seconds": round(self.fit_seconds, 2)}


class BaseRate(Model):
    name = "base_rate"

    def _fit(self, X, y, Xv, yv) -> None:
        self.p = float(np.mean(y))
        self.params = {"p": self.p}

    def _raw(self, X) -> np.ndarray:
        return np.full(len(X), self.p)

    def fit(self, X, y, Xv, yv):  # no calibration: it is the yardstick itself
        self._fit(X, y, Xv, yv)
        return self

    def predict(self, X) -> np.ndarray:
        return self._raw(X)


class Logistic(Model):
    name = "logistic"
    GRID = (0.01, 0.1, 1.0)

    def _fit(self, X, y, Xv, yv) -> None:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        self.scaler = StandardScaler().fit(X)  # train window only
        Xs, Xvs = self.scaler.transform(X), self.scaler.transform(Xv)
        best = None
        for c in self.GRID:
            m = LogisticRegression(C=c, max_iter=2000).fit(Xs, y)
            ll = log_loss(yv, m.predict_proba(Xvs)[:, 1])
            if best is None or ll < best[0]:
                best = (ll, c, m)
        self.m = best[2]
        self.params = {"C": best[1], "val_log_loss": round(best[0], 5)}

    def _raw(self, X) -> np.ndarray:
        return self.m.predict_proba(self.scaler.transform(X))[:, 1]


class GBT(Model):
    name = "gbt"
    # No internal early stopping: its hold-out would be a RANDOM slice of the training window (overlapping
    # labels -> optimistic). The number of trees is picked on the time-ordered validation window instead.
    GRID = ({"max_depth": 3, "learning_rate": 0.05, "max_iter": 100},
            {"max_depth": 3, "learning_rate": 0.05, "max_iter": 300},
            {"max_depth": 5, "learning_rate": 0.05, "max_iter": 150})

    def _fit(self, X, y, Xv, yv) -> None:
        from sklearn.ensemble import HistGradientBoostingClassifier

        best = None
        for g in self.GRID:
            m = HistGradientBoostingClassifier(**g, l2_regularization=1.0, min_samples_leaf=200, early_stopping=False,
                                               random_state=self.seed).fit(X, y)
            ll = log_loss(yv, m.predict_proba(Xv)[:, 1])
            if best is None or ll < best[0]:
                best = (ll, g, m)
        self.m = best[2]
        self.params = {**best[1], "val_log_loss": round(best[0], 5), "iterations": int(self.m.n_iter_)}

    def _raw(self, X) -> np.ndarray:
        return self.m.predict_proba(X)[:, 1]


REGISTRY = {"base_rate": BaseRate, "logistic": Logistic, "gbt": GBT}
