"""Parallel fanout dispatch for ExperimentTask multi-backend runs.

This module is intentionally additive: it creates multiple schema-valid
ExperimentRun records for one ExperimentTask without changing the EXP-001
ExperimentTask or ExperimentRun public schema.
"""

from __future__ import annotations

import copy
import inspect
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence

from services.research.experiments.models import ExperimentRun, ExperimentTask


class ParallelDispatchError(ValueError):
    """Raised for invalid dispatch requests or backend result contracts."""


class BackendRunner(Protocol):
    """Backend callable contract for task fanout."""

    def __call__(self, experiment_task: ExperimentTask, backend_id: str) -> ExperimentRun | Mapping[str, Any]:
        """Run a backend for the task and return an ExperimentRun payload."""


@dataclass
class ParallelDispatchResult:
    """Result envelope for one ExperimentTask fanned out to many backends."""

    runs: list[ExperimentRun]
    comparison_summary: dict[str, Any]
    failures: dict[str, str] = field(default_factory=dict)

    def __iter__(self):
        """Allow legacy tuple-style unpacking: runs, summary = result."""
        yield self.runs
        yield self.comparison_summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "runs": [run.to_dict() for run in self.runs],
            "comparison_summary": copy.deepcopy(self.comparison_summary),
            "failures": dict(self.failures),
        }


def run_parallel(
    experiment_task: ExperimentTask | Mapping[str, Any],
    backend_ids: Sequence[str],
    *,
    backend_registry: Mapping[str, BackendRunner | Callable[..., ExperimentRun | Mapping[str, Any]]] | None = None,
    max_workers: int | None = None,
    created_at: str | None = None,
) -> ParallelDispatchResult:
    """Dispatch one ExperimentTask to several independent research backends.

    Each backend returns exactly one ExperimentRun. Backend exceptions are
    converted into failed ExperimentRun records so one bad backend does not
    prevent successful backend results from returning.
    """

    task = _coerce_task(experiment_task)
    requested_backend_ids = _normalize_backend_ids(backend_ids)
    registry = default_backend_registry()
    if backend_registry:
        registry.update(dict(backend_registry))

    runs_by_backend: dict[str, ExperimentRun] = {}
    failures: dict[str, str] = {}
    worker_count = max_workers or len(requested_backend_ids)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_dispatch_one, task, backend_id, registry, created_at): backend_id
            for backend_id in requested_backend_ids
        }
        for future in as_completed(futures):
            backend_id = futures[future]
            run = future.result()
            runs_by_backend[backend_id] = run
            if run.status == "failed" and run.failure_reason:
                failures[backend_id] = run.failure_reason

    runs = [runs_by_backend[backend_id] for backend_id in requested_backend_ids]
    comparison_summary = build_comparison_summary(runs, requested_backend_ids)
    return ParallelDispatchResult(
        runs=runs,
        comparison_summary=comparison_summary,
        failures=failures,
    )


def build_comparison_summary(
    runs: Sequence[ExperimentRun],
    backend_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build a backend comparison summary from ExperimentRun metadata."""

    ordered_backend_ids = list(backend_ids) if backend_ids is not None else [run.backend_id for run in runs]
    if len(ordered_backend_ids) != len(runs):
        raise ParallelDispatchError("backend_ids length must match runs length")

    sharpe_by_backend: dict[str, float | None] = {}
    ic_by_backend: dict[str, float | None] = {}
    status_by_backend: dict[str, str] = {}
    run_id_by_backend: dict[str, str] = {}
    for backend_id, run in zip(ordered_backend_ids, runs):
        sharpe_by_backend[backend_id] = _extract_metric(
            run,
            ("sharpe", "sharpe_ratio", "mean_sharpe_ratio"),
        )
        ic_by_backend[backend_id] = _extract_metric(
            run,
            ("ic", "information_coefficient", "mean_ic"),
        )
        status_by_backend[backend_id] = run.status
        run_id_by_backend[backend_id] = run.run_id

    successful_backends = [
        backend_id
        for backend_id, run in zip(ordered_backend_ids, runs)
        if run.status == "completed"
    ]
    failed_backends = [
        backend_id
        for backend_id, run in zip(ordered_backend_ids, runs)
        if run.status == "failed"
    ]
    return {
        "sharpe_by_backend": sharpe_by_backend,
        "ic_by_backend": ic_by_backend,
        "agreement_score": _agreement_score((sharpe_by_backend, ic_by_backend)),
        "status_by_backend": status_by_backend,
        "run_id_by_backend": run_id_by_backend,
        "successful_backends": successful_backends,
        "failed_backends": failed_backends,
        "compared_backend_count": len(successful_backends),
    }


def default_backend_registry() -> dict[str, BackendRunner | Callable[..., ExperimentRun | Mapping[str, Any]]]:
    """Return CI-safe default backend adapters keyed by common backend ids."""

    return {
        "vectorbt": _run_vectorbt_backend,
        "vectorbt_portfolio": _run_vectorbt_backend,
        "qlib": _run_qlib_backend,
        "qlib_rolling_oos": _run_qlib_backend,
        "statsmodels": _run_statsmodels_backend,
    }


def _dispatch_one(
    task: ExperimentTask,
    backend_id: str,
    registry: Mapping[str, BackendRunner | Callable[..., ExperimentRun | Mapping[str, Any]]],
    created_at: str | None,
) -> ExperimentRun:
    runner = registry.get(backend_id)
    if runner is None:
        return _failed_experiment_run(
            task,
            backend_id,
            f"backend {backend_id!r} is not registered",
            created_at=created_at,
        )

    try:
        raw_result = _invoke_runner(runner, task, backend_id)
        run = _coerce_run(raw_result)
        _assert_run_preserves_task_pins(run, task)
        return run
    except Exception as exc:  # noqa: BLE001 - backend isolation is the contract.
        return _failed_experiment_run(
            task,
            backend_id,
            str(exc) or exc.__class__.__name__,
            created_at=created_at,
        )


def _invoke_runner(
    runner: BackendRunner | Callable[..., ExperimentRun | Mapping[str, Any]],
    task: ExperimentTask,
    backend_id: str,
) -> ExperimentRun | Mapping[str, Any]:
    target = getattr(runner, "run", runner)
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return target(task, backend_id)  # type: ignore[misc]

    positional = [
        param
        for param in signature.parameters.values()
        if param.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    has_varargs = any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in signature.parameters.values())
    if has_varargs or len(positional) >= 2:
        return target(task, backend_id)  # type: ignore[misc]
    return target(task)  # type: ignore[misc]


def _coerce_task(experiment_task: ExperimentTask | Mapping[str, Any]) -> ExperimentTask:
    if isinstance(experiment_task, ExperimentTask):
        return experiment_task
    if isinstance(experiment_task, Mapping):
        return ExperimentTask.from_dict(experiment_task)
    raise ParallelDispatchError("experiment_task must be an ExperimentTask or mapping")


def _coerce_run(raw_result: ExperimentRun | Mapping[str, Any] | Any) -> ExperimentRun:
    if isinstance(raw_result, ExperimentRun):
        return raw_result
    if isinstance(raw_result, Mapping):
        return ExperimentRun.from_dict(raw_result)
    to_dict = getattr(raw_result, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, Mapping):
            return ExperimentRun.from_dict(payload)
    raise ParallelDispatchError("backend must return ExperimentRun or ExperimentRun payload")


def _normalize_backend_ids(backend_ids: Sequence[str]) -> list[str]:
    if isinstance(backend_ids, (str, bytes)) or not isinstance(backend_ids, Sequence):
        raise ParallelDispatchError("backend_ids must be a non-empty sequence")
    normalized: list[str] = []
    for backend_id in backend_ids:
        text = str(backend_id or "").strip()
        if not text:
            raise ParallelDispatchError("backend_ids must not contain blank ids")
        normalized.append(text)
    if not normalized:
        raise ParallelDispatchError("backend_ids must be non-empty")
    if len(set(normalized)) != len(normalized):
        raise ParallelDispatchError("backend_ids must be unique")
    return normalized


def _assert_run_preserves_task_pins(run: ExperimentRun, task: ExperimentTask) -> None:
    expected = {
        "task_id": task.task_id,
        "strategy_id": task.strategy_id,
        "strategy_spec_version": task.strategy_spec_version,
        "dataset_version_id": task.dataset_version_id,
        "code_version": task.code_version,
    }
    for field_name, expected_value in expected.items():
        actual_value = getattr(run, field_name)
        if actual_value != expected_value:
            raise ParallelDispatchError(
                f"backend run {run.run_id} {field_name}={actual_value!r} "
                f"does not match task {field_name}={expected_value!r}"
            )


def _failed_experiment_run(
    task: ExperimentTask,
    backend_id: str,
    failure_reason: str,
    *,
    created_at: str | None = None,
) -> ExperimentRun:
    timestamp = created_at or utc_now()
    safe_backend_id = backend_id.replace("/", "_").replace(":", "_")
    run_id = f"erun-{task.task_id}-{safe_backend_id}-{uuid.uuid4().hex[:12]}"
    return ExperimentRun(
        run_id=run_id,
        task_id=task.task_id,
        strategy_id=task.strategy_id,
        strategy_spec_version=task.strategy_spec_version,
        backend_id=backend_id,
        runtime_env="research",
        status="failed",
        dataset_version_id=task.dataset_version_id,
        code_version=task.code_version,
        input_manifest_ref=f"inline://research/experiments/{task.task_id}/{safe_backend_id}/input",
        artifact_refs=[],
        failure_reason=failure_reason,
        trace_id=f"{task.trace_id}:{backend_id}:failed",
        created_at=timestamp,
        updated_at=timestamp,
        metadata={
            "parallel_dispatch": {
                "parent_task_id": task.task_id,
                "requested_backend_id": backend_id,
                "dispatch_status": "failed",
            }
        },
    )


def _completed_experiment_run(
    task: ExperimentTask,
    backend_id: str,
    *,
    run_id: str,
    output_manifest_ref: str,
    metric_bundle_id: str,
    artifact_refs: Sequence[str],
    metrics: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    created_at: str | None = None,
    input_manifest_ref: str | None = None,
    logs_ref: str | None = None,
) -> ExperimentRun:
    timestamp = created_at or utc_now()
    merged_metadata = dict(metadata or {})
    merged_metrics = dict(metrics or {})
    if merged_metrics:
        merged_metadata["metrics"] = merged_metrics
    merged_metadata.setdefault(
        "parallel_dispatch",
        {
            "parent_task_id": task.task_id,
            "requested_backend_id": backend_id,
            "dispatch_status": "completed",
        },
    )
    return ExperimentRun(
        run_id=run_id,
        task_id=task.task_id,
        strategy_id=task.strategy_id,
        strategy_spec_version=task.strategy_spec_version,
        backend_id=backend_id,
        runtime_env="research",
        status="completed",
        started_at=timestamp,
        finished_at=timestamp,
        dataset_version_id=task.dataset_version_id,
        code_version=task.code_version,
        input_manifest_ref=input_manifest_ref or f"inline://research/experiments/{task.task_id}/{backend_id}/input",
        output_manifest_ref=output_manifest_ref,
        metric_bundle_id=metric_bundle_id,
        artifact_refs=list(artifact_refs),
        logs_ref=logs_ref or f"inline://research/experiments/{task.task_id}/{backend_id}/log",
        trace_id=f"{task.trace_id}:{backend_id}:completed",
        created_at=timestamp,
        updated_at=timestamp,
        metadata=merged_metadata,
    )


def _run_vectorbt_backend(task: ExperimentTask, backend_id: str) -> ExperimentRun:
    from services.research.vectorbt.adapter.vectorbt_adapter import BacktestConfig, run_vectorbt_workflow

    payload = _backend_payload(task, backend_id, aliases=("vectorbt", "vectorbt_portfolio"))
    dataset = _mapping(payload.get("dataset")) or payload
    config_payload = _mapping(payload.get("config"))
    result = run_vectorbt_workflow(
        dataset,
        config=BacktestConfig(**config_payload) if config_payload else None,
    )
    aggregate_metrics = dict(result.backtest_result.aggregate_metrics)
    sharpe = _number_or_none(aggregate_metrics.get("mean_sharpe_ratio"))
    registry_entry = result.registry_entry
    registry_lineage = _mapping(registry_entry.get("lineage"))
    source_strategy_spec_id = _first_text(
        registry_lineage.get("source_strategy_spec_id"),
        payload.get("source_strategy_spec_id"),
        _mapping(task.metadata).get("source_strategy_spec_id"),
        f"{task.strategy_id}@{task.strategy_spec_version}",
    )
    source_dataset_refs = _text_list(
        registry_lineage.get("source_dataset_refs"),
        payload.get("source_dataset_refs"),
        task.dataset_version_id,
    )
    lineage = {
        "source_run_ids": _text_list(registry_lineage.get("source_run_ids"), result.backtest_result.run_id),
        "source_dataset_refs": source_dataset_refs,
        "source_strategy_spec_id": source_strategy_spec_id,
    }
    evaluation_summary = {
        "evaluation_kind": "vectorbt_backtest",
        "producer_run_id": result.backtest_result.run_id,
        "source_strategy_spec_id": source_strategy_spec_id,
        "dataset_version_id": task.dataset_version_id,
        "backtest_backend": result.backtest_result.backend,
        "aggregate_metrics": aggregate_metrics,
        "registry_hints": {
            "artifact_type": registry_entry.get("artifact_type"),
            "artifact_state": registry_entry.get("artifact_state"),
            "deployment_stage": _mapping(registry_entry.get("deployment_summary")).get("current_stage"),
            "source_strategy_spec_id": source_strategy_spec_id,
            "source_dataset_refs": source_dataset_refs,
        },
    }
    artifact_ref = str(registry_entry.get("registry_id") or f"artifact://vectorbt/{result.backtest_result.run_id}")
    output_ref = _storage_ref(registry_entry) or f"inline://research/vectorbt/{result.backtest_result.run_id}/output"
    return _completed_experiment_run(
        task,
        backend_id,
        run_id=result.backtest_result.run_id,
        output_manifest_ref=output_ref,
        metric_bundle_id=f"metric-bundle:{result.backtest_result.run_id}",
        artifact_refs=[artifact_ref],
        metrics={"sharpe": sharpe, "sharpe_ratio": sharpe, "ic": _number_or_none(payload.get("ic"))},
        metadata={
            "adapter_backend": "vectorbt",
            "producer_run_id": result.backtest_result.run_id,
            "lineage": lineage,
            "evaluation_summary": evaluation_summary,
            "aggregate_metrics": aggregate_metrics,
            "registry_entry": copy.deepcopy(registry_entry),
        },
    )


def _run_qlib_backend(task: ExperimentTask, backend_id: str) -> ExperimentRun:
    from services.research.qlib.rolling_pipeline import run as run_qlib_rolling

    payload = _backend_payload(task, backend_id, aliases=("qlib", "qlib_rolling_oos"))
    window_days = payload.get("window_days")
    if not isinstance(window_days, int):
        raise ParallelDispatchError("qlib backend input requires integer window_days")
    run_payload = run_qlib_rolling(
        str(payload.get("strategy_spec_ref") or task.strategy_id),
        window_days,
        dataset=_optional_mapping(payload.get("dataset")),
        dataset_manifest=_optional_mapping(payload.get("dataset_manifest")),
        strategy_spec_packet=_optional_mapping(payload.get("strategy_spec_packet")),
        label_horizon_days=int(payload.get("label_horizon_days") or 5),
        created_at=utc_now(),
        code_version=task.code_version,
        task_id=task.task_id,
    )
    patched_payload = dict(run_payload)
    patched_payload.update(
        {
            "task_id": task.task_id,
            "strategy_id": task.strategy_id,
            "strategy_spec_version": task.strategy_spec_version,
            "backend_id": backend_id,
            "dataset_version_id": task.dataset_version_id,
            "code_version": task.code_version,
        }
    )
    metadata = dict(patched_payload.get("metadata") or {})
    metadata.setdefault(
        "parallel_dispatch",
        {
            "parent_task_id": task.task_id,
            "requested_backend_id": backend_id,
            "dispatch_status": "completed",
        },
    )
    metadata["adapter_backend"] = "qlib_rolling_oos"
    patched_payload["metadata"] = metadata
    return ExperimentRun.from_dict(patched_payload)


def _run_statsmodels_backend(task: ExperimentTask, backend_id: str) -> ExperimentRun:
    from services.research.statsmodels.adapter.statsmodels_adapter import (
        GovernedDataset,
        run_statsmodels_workflow,
    )

    payload = _backend_payload(task, backend_id, aliases=("statsmodels",))
    raw_dataset = payload.get("dataset") if payload.get("dataset") is not None else payload
    dataset = (
        raw_dataset
        if isinstance(raw_dataset, GovernedDataset)
        else GovernedDataset(
            price_series=dict(_mapping(raw_dataset).get("price_series") or {}),
            factor_series=dict(_mapping(raw_dataset).get("factor_series") or {}),
            metadata=dict(_mapping(raw_dataset).get("metadata") or {}),
        )
    )
    analysis_paths = payload.get("analysis_paths")
    if analysis_paths is not None and not isinstance(analysis_paths, list):
        raise ParallelDispatchError("statsmodels analysis_paths must be a list")
    artifact = run_statsmodels_workflow(dataset, analysis_paths=analysis_paths)
    artifact_id = str(artifact.get("artifact_id") or f"statsmodels-{uuid.uuid4().hex[:12]}")
    metrics = _mapping(payload.get("metrics"))
    return _completed_experiment_run(
        task,
        backend_id,
        run_id=f"statsmodels-{artifact_id}",
        output_manifest_ref=f"inline://research/statsmodels/{artifact_id}/output",
        metric_bundle_id=f"metric-bundle:statsmodels:{artifact_id}",
        artifact_refs=[f"artifact://statsmodels/{artifact_id}"],
        metrics=metrics,
        metadata={
            "adapter_backend": "statsmodels",
            "artifact_bundle": copy.deepcopy(artifact),
        },
    )


def _backend_payload(task: ExperimentTask, backend_id: str, *, aliases: Sequence[str]) -> dict[str, Any]:
    metadata = dict(task.metadata)
    backend_inputs = _mapping(metadata.get("backend_inputs"))
    for key in (backend_id, *aliases):
        value = backend_inputs.get(key)
        if isinstance(value, Mapping):
            return copy.deepcopy(dict(value))
    for key in (backend_id, *aliases):
        direct = metadata.get(f"{key}_input") or metadata.get(f"{key}_dataset")
        if isinstance(direct, Mapping):
            return copy.deepcopy(dict(direct))
    raise ParallelDispatchError(
        f"{backend_id} backend input is required in task.metadata.backend_inputs"
    )


def _extract_metric(run: ExperimentRun, metric_names: Sequence[str]) -> float | None:
    metadata = dict(run.metadata)
    sources = [
        _mapping(metadata.get("metrics")),
        _mapping(metadata.get("oos_metrics")),
        _mapping(_mapping(metadata.get("evaluation_summary")).get("oos_metrics")),
        _mapping(metadata.get("aggregate_metrics")),
        metadata,
    ]
    for source in sources:
        for metric_name in metric_names:
            value = _number_or_none(source.get(metric_name))
            if value is not None:
                return value
    return None


def _agreement_score(metric_maps: Sequence[Mapping[str, float | None]]) -> float | None:
    pair_scores: list[float] = []
    for metric_map in metric_maps:
        values = [value for value in metric_map.values() if value is not None]
        for left_index, left in enumerate(values):
            for right in values[left_index + 1 :]:
                pair_scores.append(1.0 if _sign(left) == _sign(right) else 0.0)
    if not pair_scores:
        return None
    return round(sum(pair_scores) / len(pair_scores), 6)


def _sign(value: float) -> int:
    if value > 1e-12:
        return 1
    if value < -1e-12:
        return -1
    return 0


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _text_list(*values: Any) -> list[str]:
    refs: list[str] = []
    for value in values:
        if value in (None, ""):
            continue
        items = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else [value]
        for item in items:
            text = str(item or "").strip()
            if text and text not in refs:
                refs.append(text)
    return refs


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_mapping(value: Any) -> dict[str, Any] | None:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else None


def _storage_ref(registry_entry: Mapping[str, Any]) -> str:
    storage = _mapping(registry_entry.get("storage_ref"))
    backend = str(storage.get("backend") or "").strip()
    path = str(storage.get("path") or "").strip()
    if backend and path:
        return f"{backend}://{path}"
    return ""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "BackendRunner",
    "ParallelDispatchError",
    "ParallelDispatchResult",
    "build_comparison_summary",
    "default_backend_registry",
    "run_parallel",
    "utc_now",
]
