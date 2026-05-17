"""Out-of-sample metric evaluator for governed Qlib rolling runs."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


class OosEvaluationError(ValueError):
    """Raised when an ExperimentRun does not contain usable OOS observations."""


def evaluate(experiment_run: Mapping[str, Any]) -> dict[str, Any]:
    """Return OOS metrics for a rolling-window Qlib ExperimentRun.

    The evaluator is intentionally local and side-effect free. It reads
    ``metadata.oos_observations`` from the ExperimentRun payload and returns the
    metric bundle expected by downstream research writeback.
    """

    if not isinstance(experiment_run, Mapping):
        raise OosEvaluationError("experiment_run must be a mapping")

    observations = _observations(experiment_run)
    if not observations:
        cached = _cached_metrics(experiment_run)
        if cached:
            return cached
        raise OosEvaluationError("experiment_run metadata does not include oos_observations")

    returns = _period_returns(observations)
    predictions = [_number(obs.get("prediction"), "prediction") for obs in observations]
    actuals = [_number(obs.get("actual_return"), "actual_return") for obs in observations]

    mean_return = _mean(returns)
    metrics = {
        "sharpe": round(_sharpe(returns), 6),
        "sortino": round(_sortino(returns), 6),
        "max_dd": round(_max_drawdown(returns), 6),
        "ic": round(_pearson(predictions, actuals), 6),
        "mean_return": round(mean_return, 8),
        "num_oos_samples": len(observations),
        "num_oos_periods": len(returns),
    }

    strategy_spec_id = _strategy_spec_id(experiment_run)
    if strategy_spec_id:
        metrics["strategy_spec_artifact_id"] = strategy_spec_id
    return metrics


def _observations(experiment_run: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    metadata = _mapping(experiment_run.get("metadata"))
    raw = metadata.get("oos_observations") or experiment_run.get("oos_observations")
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise OosEvaluationError("oos_observations must be an array")
    observations: list[Mapping[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise OosEvaluationError("each oos_observation must be an object")
        observations.append(item)
    return observations


def _cached_metrics(experiment_run: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _mapping(experiment_run.get("metadata"))
    summary = _mapping(metadata.get("evaluation_summary"))
    metrics = _mapping(summary.get("oos_metrics"))
    required = {"sharpe", "sortino", "max_dd", "ic"}
    if required.issubset(metrics.keys()):
        return dict(metrics)
    return {}


def _period_returns(observations: Sequence[Mapping[str, Any]]) -> list[float]:
    """Aggregate cross-sectional OOS returns into one return per OOS date."""
    buckets: dict[str, list[float]] = {}
    for index, obs in enumerate(observations):
        period = str(obs.get("as_of_date") or obs.get("oos_end_date") or index)
        buckets.setdefault(period, []).append(_number(obs.get("strategy_return"), "strategy_return"))
    return [_mean(buckets[period]) for period in sorted(buckets)]


def _strategy_spec_id(experiment_run: Mapping[str, Any]) -> str:
    metadata = _mapping(experiment_run.get("metadata"))
    lineage = _mapping(metadata.get("lineage"))
    for value in (
        lineage.get("strategy_spec_artifact_id"),
        lineage.get("source_strategy_spec_id"),
        metadata.get("strategy_spec_artifact_id"),
        metadata.get("source_strategy_spec_id"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OosEvaluationError(f"{field_name} must be numeric")
    return float(value)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return (sum((value - avg) ** 2 for value in values) / len(values)) ** 0.5


def _sharpe(returns: Sequence[float]) -> float:
    std = _stdev(returns)
    if std <= 1e-12:
        return 0.0
    return (_mean(returns) / std) * (252**0.5)


def _sortino(returns: Sequence[float]) -> float:
    downside = [min(value, 0.0) for value in returns if value < 0.0]
    if not downside:
        return 0.0
    downside_dev = (sum(value**2 for value in downside) / len(downside)) ** 0.5
    if downside_dev <= 1e-12:
        return 0.0
    return (_mean(returns) / downside_dev) * (252**0.5)


def _max_drawdown(returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for value in returns:
        equity *= max(0.0, 1.0 + value)
        if equity > peak:
            peak = equity
        if peak > 0.0:
            max_dd = max(max_dd, (peak - equity) / peak)
    return max_dd


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) != len(ys):
        raise OosEvaluationError("prediction and actual_return lengths differ")
    if len(xs) < 2:
        return 0.0
    mean_x = _mean(xs)
    mean_y = _mean(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    den_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    denom = den_x * den_y
    return num / denom if denom > 1e-12 else 0.0


__all__ = ["OosEvaluationError", "evaluate"]
