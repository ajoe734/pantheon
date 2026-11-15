"""Regime axis models (design R5, R6).

- Volatility: statsmodels ``MarkovRegression`` with switching variance. Only
  ``filtered_marginal_probabilities`` are ever read; the smoothed probabilities of a results
  object are never touched, so none can reach an output.
- Trend and risk appetite: the vendored continuous statistical jump model with *online*
  prediction (row ``i`` uses rows up to ``i`` only).
- Macro: rules, see ``features.macro_state``.

Per session every axis reports the state, the filtered probability vector, the transition
matrix, expected durations, sessions in the current state and the last switch session.
States are ordered by a fixed statistic (variance, or cumulative return) so labels do not
flip between refits (design K4).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd

from services.research.regime import JUMPMODELS_VERSION, jump_module
from services.research.regime.features import RegimeConfig

VOLATILITY_LABELS = ("low", "high")
TREND_LABELS_3 = ("down", "mean_reverting", "up")
RISK_LABELS_2 = ("off", "on")


class RegimeModelError(ValueError):
    """The model cannot be fitted or evaluated; callers fail closed with the message as the reason."""


@dataclass
class AxisSession:
    """One session of one axis. Built only from filtered / online probabilities."""

    session: str
    state: str
    probabilities: dict[str, float]
    transition_matrix: list[list[float]]
    expected_durations: dict[str, float]
    sessions_in_state: int
    last_switch_session: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"session": self.session, "state": self.state, "probabilities": self.probabilities,
                "transition_matrix": self.transition_matrix, "expected_durations": self.expected_durations,
                "sessions_in_state": self.sessions_in_state, "last_switch_session": self.last_switch_session}


def _expected_durations(matrix: np.ndarray, labels: Sequence[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i, label in enumerate(labels):
        stay = float(matrix[i, i])
        out[label] = float("inf") if stay >= 1.0 else 1.0 / (1.0 - stay)
    return {k: (v if math.isfinite(v) else 1e9) for k, v in out.items()}


def _sessions(index: pd.Index, probs: np.ndarray, labels: Sequence[str], matrix: np.ndarray,
              durations: dict[str, float]) -> list[AxisSession]:
    """Turn a filtered probability matrix into per-session records with run-length bookkeeping."""
    if probs.ndim != 2 or probs.shape[1] != len(labels) or len(probs) != len(index):
        raise RegimeModelError("probability_shape_mismatch")
    if not np.isfinite(probs).all():
        raise RegimeModelError("nonfinite_probabilities")
    argmax = probs.argmax(axis=1)
    out: list[AxisSession] = []
    run = 0
    last_switch: str | None = None
    previous = -1
    for i, state in enumerate(argmax):
        stamp = pd.Timestamp(index[i]).date().isoformat()
        if state == previous:
            run += 1
        else:
            if previous != -1:
                last_switch = stamp
            run = 1
        previous = int(state)
        out.append(AxisSession(
            session=stamp, state=labels[int(state)],
            probabilities={label: float(probs[i, j]) for j, label in enumerate(labels)},
            transition_matrix=[[float(x) for x in row] for row in matrix],
            expected_durations=dict(durations), sessions_in_state=run, last_switch_session=last_switch))
    return out


def _fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class VolatilityModel:
    """Two-state Markov-switching variance model; parameters fixed after fit, inference filtered."""

    horizon: str
    params: list[float]
    param_names: list[str]
    labels: tuple[str, ...]
    order: list[int]  # statsmodels regime index for each ordered label (low variance first)
    transition_matrix: list[list[float]]  # row-stochastic, ordered as ``labels``
    train_sessions: int
    train_start: str
    train_end: str
    method: str = "markov_switching"
    library: str = field(default="")

    def expected_durations(self) -> dict[str, float]:
        return _expected_durations(np.array(self.transition_matrix), self.labels)

    def infer(self, frame: pd.DataFrame) -> list[AxisSession]:
        """Filtered probabilities for every row of ``frame`` under the fitted parameters."""
        from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

        y = frame["return_pct"].astype(float)
        if len(y) < 30:
            raise RegimeModelError("volatility_inference_history_short")
        model = MarkovRegression(y.to_numpy(), k_regimes=2, trend="c", switching_variance=True)
        results = model.filter(np.asarray(self.params))
        probs = np.asarray(results.filtered_marginal_probabilities)[:, self.order]
        # Only filtered output is read above; the results object is dropped here.
        return _sessions(y.index, probs, self.labels, np.array(self.transition_matrix), self.expected_durations())

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "volatility_markov_switching", "horizon": self.horizon, "params": self.params,
                "param_names": self.param_names, "labels": list(self.labels), "order": self.order,
                "transition_matrix": self.transition_matrix, "train_sessions": self.train_sessions,
                "train_start": self.train_start, "train_end": self.train_end, "method": self.method,
                "library": self.library}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "VolatilityModel":
        return cls(horizon=raw["horizon"], params=list(raw["params"]), param_names=list(raw["param_names"]),
                   labels=tuple(raw["labels"]), order=list(raw["order"]),
                   transition_matrix=[list(r) for r in raw["transition_matrix"]],
                   train_sessions=int(raw["train_sessions"]), train_start=raw["train_start"],
                   train_end=raw["train_end"], method=raw.get("method", "markov_switching"),
                   library=raw.get("library", ""))

    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())


def fit_volatility(frame: pd.DataFrame, *, horizon: str) -> VolatilityModel:
    """Fit ``MarkovRegression(k_regimes=2, trend='c', switching_variance=True)`` on a return series."""
    import statsmodels
    from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

    if "return_pct" not in frame or frame["return_pct"].isna().any():
        raise RegimeModelError("volatility_input_invalid")
    y = frame["return_pct"].astype(float)
    if len(y) < 100:
        raise RegimeModelError(f"volatility_training_history_short:{len(y)}")
    model = MarkovRegression(y.to_numpy(), k_regimes=2, trend="c", switching_variance=True)
    try:
        results = model.fit(disp=False, maxiter=200)
    except Exception as exc:  # noqa: BLE001 - optimiser failures are a fail-closed reason, not a crash
        raise RegimeModelError(f"volatility_fit_failed:{type(exc).__name__}") from exc
    params = np.asarray(results.params, dtype=float)
    if not np.isfinite(params).all():
        raise RegimeModelError("volatility_fit_nonfinite_params")
    names = list(model.param_names)
    sigma = [float(params[names.index(f"sigma2[{k}]")]) for k in range(2)]
    order = [int(np.argmin(sigma)), int(np.argmax(sigma))]
    if order[0] == order[1] or not sigma[order[1]] > sigma[order[0]]:
        raise RegimeModelError("volatility_states_not_separated")
    # statsmodels stores P(to i | from j) in regime_transition[i, j, 0]; make rows "from".
    raw = np.asarray(results.regime_transition)[:, :, 0]
    row_stochastic = raw.T[np.ix_(order, order)]
    return VolatilityModel(
        horizon=horizon, params=[float(p) for p in params], param_names=names, labels=VOLATILITY_LABELS,
        order=order, transition_matrix=[[float(x) for x in r] for r in row_stochastic],
        train_sessions=len(y), train_start=pd.Timestamp(y.index[0]).date().isoformat(),
        train_end=pd.Timestamp(y.index[-1]).date().isoformat(), library=f"statsmodels {statsmodels.__version__}")


@dataclass
class JumpAxisModel:
    """Continuous jump model with online prediction (trend and risk-appetite axes)."""

    axis: str
    horizon: str
    labels: tuple[str, ...]
    feature_names: list[str]
    feature_mean: list[float]
    feature_std: list[float]
    centers: list[list[float]]
    jump_penalty: float
    grid_size: float
    transition_matrix: list[list[float]]
    train_sessions: int
    train_start: str
    train_end: str
    method: str = "jumpmodels-cjm"
    library: str = f"jumpmodels {JUMPMODELS_VERSION}"
    _estimator: Any = field(default=None, repr=False, compare=False)

    def expected_durations(self) -> dict[str, float]:
        return _expected_durations(np.array(self.transition_matrix), self.labels)

    def _rebuild(self) -> Any:
        if self._estimator is None:
            jump = jump_module()
            est = jump.JumpModel(n_components=len(self.labels), jump_penalty=self.jump_penalty, cont=True,
                                 grid_size=self.grid_size, mode_loss=True)
            est.check_jump_penalty_mx()
            est.centers_ = np.asarray(self.centers, dtype=float)
            est.feat_weights = None
            self._estimator = est
        return self._estimator

    def _standardise(self, frame: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in self.feature_names if c not in frame]
        if missing:
            raise RegimeModelError("jump_features_missing:" + ",".join(missing))
        x = frame[self.feature_names].astype(float)
        if x.isna().any().any() or not np.isfinite(x.to_numpy()).all():
            raise RegimeModelError("jump_features_nonfinite")
        return (x - np.asarray(self.feature_mean)) / np.asarray(self.feature_std)

    def infer(self, frame: pd.DataFrame) -> list[AxisSession]:
        """Online probabilities: row ``i`` is computed from rows up to ``i`` only."""
        x = self._standardise(frame)
        est = self._rebuild()
        proba = est.predict_proba_online(x)
        probs = np.asarray(proba, dtype=float)
        return _sessions(x.index, probs, self.labels, np.array(self.transition_matrix), self.expected_durations())

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "jump_continuous", "axis": self.axis, "horizon": self.horizon, "labels": list(self.labels),
                "feature_names": self.feature_names, "feature_mean": self.feature_mean,
                "feature_std": self.feature_std, "centers": self.centers, "jump_penalty": self.jump_penalty,
                "grid_size": self.grid_size, "transition_matrix": self.transition_matrix,
                "train_sessions": self.train_sessions, "train_start": self.train_start,
                "train_end": self.train_end, "method": self.method, "library": self.library}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "JumpAxisModel":
        return cls(axis=raw["axis"], horizon=raw["horizon"], labels=tuple(raw["labels"]),
                   feature_names=list(raw["feature_names"]), feature_mean=list(raw["feature_mean"]),
                   feature_std=list(raw["feature_std"]), centers=[list(r) for r in raw["centers"]],
                   jump_penalty=float(raw["jump_penalty"]), grid_size=float(raw["grid_size"]),
                   transition_matrix=[list(r) for r in raw["transition_matrix"]],
                   train_sessions=int(raw["train_sessions"]), train_start=raw["train_start"],
                   train_end=raw["train_end"], method=raw.get("method", "jumpmodels-cjm"),
                   library=raw.get("library", f"jumpmodels {JUMPMODELS_VERSION}"))

    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())


def fit_jump_axis(frame: pd.DataFrame, returns: pd.Series, *, axis: str, horizon: str, n_states: int,
                  jump_penalty: float, config: RegimeConfig | None = None) -> JumpAxisModel:
    """Fit the continuous jump model; states are ordered by cumulative return (fixed statistic)."""
    config = config or RegimeConfig()
    if n_states == 3:
        labels = TREND_LABELS_3
    elif n_states == 2:
        labels = RISK_LABELS_2
    else:
        raise RegimeModelError("jump_states_unsupported")
    if frame.isna().any().any() or not np.isfinite(frame.to_numpy(dtype=float)).all():
        raise RegimeModelError("jump_features_nonfinite")
    if len(frame) < 250:
        raise RegimeModelError(f"jump_training_history_short:{len(frame)}")
    names = list(frame.columns)
    mean = frame.mean().to_numpy(dtype=float)
    std = frame.std(ddof=0).replace(0.0, 1.0).to_numpy(dtype=float)
    x = (frame - mean) / std
    jump = jump_module()
    est = jump.JumpModel(n_components=n_states, jump_penalty=float(jump_penalty), cont=True,
                         grid_size=config.jump_grid_size, mode_loss=True, n_init=config.jump_n_init)
    ret = returns.reindex(frame.index)
    if ret.isna().any():
        raise RegimeModelError("jump_return_series_misaligned")
    try:
        est.fit(x, ret, sort_by="cumret")
    except Exception as exc:  # noqa: BLE001
        raise RegimeModelError(f"jump_fit_failed:{type(exc).__name__}") from exc
    centers = np.asarray(est.centers_, dtype=float)
    # Row-stochastic empirical transition matrix from the in-sample labels; smoothed in-sample
    # output is a fit diagnostic only and is never stored as a session probability.
    matrix = np.asarray(est.transmat_, dtype=float)
    rows = matrix.sum(axis=1, keepdims=True)
    matrix = np.divide(matrix, rows, out=np.full_like(matrix, 1.0 / n_states), where=rows > 0)
    return JumpAxisModel(
        axis=axis, horizon=horizon, labels=labels, feature_names=names, feature_mean=[float(v) for v in mean],
        feature_std=[float(v) for v in std], centers=[[float(v) for v in r] for r in centers],
        jump_penalty=float(jump_penalty), grid_size=config.jump_grid_size,
        transition_matrix=[[float(v) for v in r] for r in matrix], train_sessions=len(frame),
        train_start=pd.Timestamp(frame.index[0]).date().isoformat(),
        train_end=pd.Timestamp(frame.index[-1]).date().isoformat(), _estimator=est)


def model_from_dict(raw: dict[str, Any]):
    if raw.get("kind") == "volatility_markov_switching":
        return VolatilityModel.from_dict(raw)
    if raw.get("kind") == "jump_continuous":
        return JumpAxisModel.from_dict(raw)
    raise RegimeModelError("unknown_model_kind")
