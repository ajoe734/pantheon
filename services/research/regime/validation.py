"""Walk-forward validation of regime classifiers (design R9).

Protocol, per axis and horizon:
- expanding training window, refit every ``refit_sessions``;
- inference is *filtered / online only*: each fold's model is run over the prefix up to the end
  of its test window and the test rows are kept, so no row sees a later row;
- the signal a consumer could act on is the label delayed by ``signal_delay_sessions``;
- reported: switches per year, expected durations (run lengths of the delayed signal and the
  model-implied durations), label stability across consecutive refits, and agreement with
  offline change points (``ruptures``) as an audit that does not gate approval;
- with less than ``min_training_sessions`` of history no fold is run and the model stays a
  candidate. Nothing is substituted for missing history.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from services.research.regime.features import AxisInputs, RegimeConfig
from services.research.regime.models import (
    AxisSession, JumpAxisModel, RegimeModelError, VolatilityModel, fit_jump_axis, fit_volatility,
)

FitFn = Callable[[pd.DataFrame, pd.Series], Any]


@dataclass
class ValidationReport:
    axis: str
    horizon: str
    status: str  # validated | failed | insufficient_history | error
    passed: bool
    reason: str
    method: str = ""
    sessions: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, dict[str, Any]] = field(default_factory=dict)
    folds: list[dict[str, Any]] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)
    protocol: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"axis": self.axis, "horizon": self.horizon, "status": self.status, "passed": self.passed,
                "reason": self.reason, "method": self.method, "sessions": self.sessions,
                "metrics": self.metrics, "gates": self.gates, "folds": self.folds, "audit": self.audit,
                "protocol": self.protocol}


def run_lengths(labels: list[str]) -> dict[str, list[int]]:
    runs: dict[str, list[int]] = {}
    if not labels:
        return runs
    current, length = labels[0], 1
    for label in labels[1:]:
        if label == current:
            length += 1
        else:
            runs.setdefault(current, []).append(length)
            current, length = label, 1
    runs.setdefault(current, []).append(length)
    return runs


def delayed_signal(sessions: list[AxisSession], delay: int) -> list[str]:
    """The label a consumer may use at session ``t`` is the one computed at ``t - delay``."""
    labels = [s.state for s in sessions]
    return labels[: len(labels) - delay] if delay > 0 else labels


def switch_indices(labels: list[str]) -> list[int]:
    return [i for i in range(1, len(labels)) if labels[i] != labels[i - 1]]


def audit_signal(inputs: AxisInputs) -> np.ndarray:
    """Offline signal for change-point detection (offline use may look at the whole span)."""
    frame = inputs.frame
    assert frame is not None
    if inputs.axis == "volatility":
        series = frame["return_pct"].abs().rolling(10, min_periods=1).mean()
        return ((series - series.mean()) / (series.std() or 1.0)).to_numpy().reshape(-1, 1)
    std = frame.std(ddof=0).replace(0.0, 1.0)
    return ((frame - frame.mean()) / std).to_numpy()


def changepoint_agreement(signal: np.ndarray, switches: list[int], *, penalty: float, tolerance: int) -> dict[str, Any]:
    """Audit: how many model switches fall near an offline change point (``ruptures``) and vice versa."""
    import ruptures as rpt

    if len(signal) < 60:
        return {"status": "skipped", "reason": "signal_short"}
    algo = rpt.Pelt(model="l2", min_size=20, jump=1).fit(signal)
    cps = [int(c) for c in algo.predict(pen=penalty)[:-1]]
    near = lambda a, bs: any(abs(a - b) <= tolerance for b in bs)  # noqa: E731
    precision = (sum(near(s, cps) for s in switches) / len(switches)) if switches else None
    recall = (sum(near(c, switches) for c in cps) / len(cps)) if cps else None
    return {"status": "computed", "offline_change_points": len(cps), "model_switches": len(switches),
            "precision": precision, "recall": recall, "tolerance_sessions": tolerance,
            "penalty": penalty, "algorithm": "ruptures.Pelt(l2)"}


def make_fit_fn(axis: str, horizon: str, config: RegimeConfig) -> FitFn:
    """The fitting recipe for one axis; the jump penalty is chosen on a trailing validation window."""
    if axis == "volatility":
        return lambda frame, returns: fit_volatility(frame, horizon=horizon)
    n_states = config.trend_states if axis == "trend" else config.risk_appetite_states

    def fit(frame: pd.DataFrame, returns: pd.Series) -> JumpAxisModel:
        penalty = select_jump_penalty(frame, returns, axis=axis, horizon=horizon, n_states=n_states, config=config)
        return fit_jump_axis(frame, returns, axis=axis, horizon=horizon, n_states=n_states,
                             jump_penalty=penalty, config=config)

    return fit


def select_jump_penalty(frame: pd.DataFrame, returns: pd.Series, *, axis: str, horizon: str, n_states: int,
                        config: RegimeConfig) -> float:
    """Smallest grid penalty whose validation-window switch rate is within the gate.

    Fits on the training window without its last ``refit_sessions`` rows and measures the
    online switches per year on those held-out rows. Falls back to the largest penalty.
    """
    grid = sorted(float(p) for p in config.jump_penalty_grid)
    holdout = min(config.refit_sessions, len(frame) // 4)
    if holdout < 40 or len(frame) - holdout < 250:
        return grid[-1]
    train, valid = frame.iloc[:-holdout], frame.iloc[-holdout:]
    for penalty in grid:
        try:
            model = fit_jump_axis(train, returns.reindex(train.index), axis=axis, horizon=horizon,
                                  n_states=n_states, jump_penalty=penalty, config=config)
            sessions = model.infer(frame)[-holdout:]
        except RegimeModelError:
            continue
        switches = len(switch_indices([s.state for s in sessions]))
        if switches / (holdout / config.sessions_per_year) <= config.max_switches_per_year:
            return penalty
    return grid[-1]


def _gate(value: float | None, threshold: float, *, at_most: bool) -> dict[str, Any]:
    if value is None:
        return {"value": None, "threshold": threshold, "passed": False}
    return {"value": value, "threshold": threshold, "passed": bool(value <= threshold if at_most else value >= threshold)}


def walk_forward_validate(inputs: AxisInputs, fit_fn: FitFn, config: RegimeConfig | None = None) -> ValidationReport:
    """Run the walk-forward protocol over ``inputs`` and return the report (never raises on bad data)."""
    config = config or RegimeConfig()
    axis, horizon = inputs.axis, inputs.horizon or ""
    protocol = {"inference": "filtered_online_only", "signal_delay_sessions": config.signal_delay_sessions,
                "refit_sessions": config.refit_sessions, "min_training_sessions": config.min_training_sessions,
                "window": "expanding", "config": config.to_dict()}
    if not inputs.available or inputs.frame is None:
        return ValidationReport(axis, horizon, "error", False, f"axis_unavailable:{inputs.reason}", protocol=protocol)
    frame, returns = inputs.frame, inputs.returns
    n = len(frame)
    # The walk-forward start is the minimum training history; the last fold may be shorter.
    start = config.min_training_sessions
    if n < start + config.refit_sessions:
        return ValidationReport(
            axis, horizon, "insufficient_history", False,
            f"history_short:{n}<{start + config.refit_sessions}", sessions=n, protocol=protocol)
    returns = returns if returns is not None else pd.Series(0.0, index=frame.index)
    fold_models: list[Any] = []
    fold_rows: list[dict[str, Any]] = []
    oos: list[AxisSession] = []
    try:
        cursor = start
        while cursor < n:
            end = min(cursor + config.refit_sessions, n)
            train = frame.iloc[:cursor]
            model = fit_fn(train, returns.reindex(train.index))
            sessions = model.infer(frame.iloc[:end])
            test = sessions[cursor:end]
            oos.extend(test)
            fold_models.append((model, cursor, end))
            fold_rows.append({"train_start": train.index[0].date().isoformat(), "train_end": train.index[-1].date().isoformat(),
                              "train_sessions": cursor, "test_sessions": len(test), "model_fingerprint": model.fingerprint(),
                              "jump_penalty": getattr(model, "jump_penalty", None),
                              "model_expected_durations": model.expected_durations()})
            cursor = end
    except RegimeModelError as exc:
        return ValidationReport(axis, horizon, "error", False, f"fit_failed:{exc}", sessions=n, protocol=protocol)
    method = fold_models[-1][0].method
    labels = delayed_signal(oos, config.signal_delay_sessions)
    years = max(len(labels), 1) / config.sessions_per_year
    switches = switch_indices(labels)
    runs = run_lengths(labels)
    durations = {state: float(np.mean(lengths)) for state, lengths in runs.items()}
    # Label stability across consecutive refits: both models label the same recent training rows.
    agreements: list[float] = []
    for (prev, _, _), (nxt, cur_start, _) in zip(fold_models, fold_models[1:]):
        window = min(config.stability_window_sessions, cur_start)
        sub = frame.iloc[:cur_start]
        a = [s.state for s in prev.infer(sub)[-window:]]
        b = [s.state for s in nxt.infer(sub)[-window:]]
        agreements.append(float(np.mean([x == y for x, y in zip(a, b)])))
    stability = float(np.mean(agreements)) if agreements else None
    mean_duration = float(np.mean([d for d in durations.values()])) if durations else None
    min_duration = min(durations.values()) if durations else None
    switches_per_year = len(switches) / years
    # Switch rows are positions in the model's own (undelayed) label timeline, i.e. frame rows.
    signal_audit = changepoint_agreement(
        audit_signal(inputs), [start + i for i in switches],
        penalty=config.changepoint_penalty, tolerance=config.changepoint_tolerance_sessions)
    gates = {
        "history": _gate(float(n), float(start), at_most=False),
        "refits": _gate(float(len(fold_models)), float(config.min_refits), at_most=False),
        "switches_per_year": _gate(switches_per_year, config.max_switches_per_year, at_most=True),
        "expected_duration_sessions": _gate(min_duration, config.min_expected_duration_sessions, at_most=False),
        "label_stability": _gate(stability, config.min_label_stability, at_most=False),
    }
    failed = [name for name, gate in gates.items() if not gate["passed"]]
    metrics = {"switches_per_year": switches_per_year, "switches": len(switches), "oos_sessions": len(labels),
               "expected_durations_sessions": durations, "mean_run_length_sessions": mean_duration,
               "model_expected_durations": fold_rows[-1]["model_expected_durations"],
               "label_stability": stability, "label_stability_pairs": len(agreements),
               "refits": len(fold_models), "oos_start": oos[0].session, "oos_end": oos[-1].session,
               "state_share": {s: sum(1 for l in labels if l == s) / max(len(labels), 1) for s in runs}}
    return ValidationReport(
        axis, horizon, "validated" if not failed else "failed", not failed,
        "validation_passed" if not failed else "gates_failed:" + ",".join(failed), method=method, sessions=n,
        metrics=metrics, gates=gates, folds=fold_rows, audit={"change_points": signal_audit}, protocol=protocol)


def fit_and_validate(inputs: AxisInputs, config: RegimeConfig | None = None) -> tuple[Any | None, ValidationReport]:
    """Validate by walk-forward, then fit the deployable model on all history.

    With too little history for the walk-forward the model is still fitted on what exists (it can
    only be registered as a candidate, never approved); an error returns no model.
    """
    config = config or RegimeConfig()
    fit_fn = make_fit_fn(inputs.axis, inputs.horizon or "", config)
    report = walk_forward_validate(inputs, fit_fn, config)
    if report.status == "error":
        return None, report
    frame = inputs.frame
    assert frame is not None
    returns = inputs.returns if inputs.returns is not None else pd.Series(0.0, index=frame.index)
    try:
        model = fit_fn(frame, returns.reindex(frame.index))
    except RegimeModelError as exc:
        if report.status == "insufficient_history":
            report.reason += f";final_fit_failed:{exc}"
        else:
            report.status, report.passed, report.reason = "error", False, f"final_fit_failed:{exc}"
        return None, report
    return model, report
