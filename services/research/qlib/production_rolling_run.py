"""Production-scale Qlib rolling run for TWSE OHLCV model admission.

This entrypoint is offline and review-only. It consumes the governed
MGMT-QLIB-001 dataset manifest plus the repo-local TWSE OHLCV materialization,
returns a schema-valid ExperimentRun, and attaches a draft model_artifact
projection for registry admission. It does not write registry state, open a
broker session, or route orders.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.research.experiments.models import validate_experiment_run_payload

try:  # Allows both package imports and direct script execution.
    from . import rolling_pipeline
except ImportError:  # pragma: no cover - direct script fallback
    import rolling_pipeline  # type: ignore


TASK_ID = "OSS-QLIB-V2-001"
BACKEND_ID = "qlib_production_rolling"
CODE_VERSION = "pantheon:services/research/qlib@OSS-QLIB-V2-001"
ARTIFACT_VERSION = "2.0.0"
DEFAULT_LABEL_HORIZON_DAYS = 5
DEFAULT_DATASET_PATH = Path(__file__).resolve().parent / "examples" / "smoke_dataset.json"
DEFAULT_DATASET_MANIFEST_PATH = (
    REPO_ROOT / "support" / "evidence" / "MGMT-QLIB-001" / "dataset_manifest.json"
)
DEFAULT_STRATEGY_SPEC_PACKET_PATH = (
    REPO_ROOT / "support" / "evidence" / "MGMT-QLIB-002" / "strategy_spec_packet.json"
)
REGISTRY_ENTRY_SCHEMA_PATH = REPO_ROOT / "services" / "registry" / "registry_entry_schema.json"
REQUIRED_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
MIN_PRODUCTION_INSTRUMENTS = 50
MIN_PRODUCTION_HISTORY_YEARS = 2.0
MIN_PRODUCTION_PERIODS = 504


class ProductionRollingRunError(ValueError):
    """Raised when a production rolling run would be incomplete or unsafe."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_production(
    instrument_universe: Sequence[str] | str,
    start_date: str,
    end_date: str,
    window_days: int,
    *,
    label_horizon_days: int = DEFAULT_LABEL_HORIZON_DAYS,
    dataset: Mapping[str, Any] | None = None,
    dataset_path: str | Path = DEFAULT_DATASET_PATH,
    dataset_manifest: Mapping[str, Any] | None = None,
    dataset_manifest_path: str | Path = DEFAULT_DATASET_MANIFEST_PATH,
    strategy_spec_packet: Mapping[str, Any] | None = None,
    strategy_spec_packet_path: str | Path = DEFAULT_STRATEGY_SPEC_PACKET_PATH,
    created_at: str | None = None,
    require_production_scale: bool = True,
) -> dict[str, Any]:
    """Run a production-scale rolling OOS evaluation over the governed TWSE dataset.

    ``instrument_universe`` may be ``"manifest"``, ``"all"``, or an explicit
    instrument sequence. The returned object conforms to ExperimentRun and
    carries per-window ``rolling_sharpe`` and ``rolling_ic`` under
    ``metadata.production_rolling_windows``.
    """

    created = created_at or utc_now()
    normalized_window = _positive_int(window_days, "window_days")
    normalized_horizon = _positive_int(label_horizon_days, "label_horizon_days")
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    if end < start:
        raise ProductionRollingRunError("end_date must be on or after start_date")

    manifest = _payload_or_file(dataset_manifest, dataset_manifest_path, "dataset_manifest")
    spec_packet = _payload_or_file(
        strategy_spec_packet,
        strategy_spec_packet_path,
        "strategy_spec_packet",
        required=False,
    )
    raw_dataset = _payload_or_file(dataset, dataset_path, "dataset")
    _assert_manifest_dataset_binding(raw_dataset, manifest, spec_packet)

    filtered_dataset = _filter_dataset(
        raw_dataset,
        instrument_universe=instrument_universe,
        start=start,
        end=end,
    )
    production_summary = _assert_production_scale(
        filtered_dataset,
        manifest,
        start=start,
        end=end,
        required=require_production_scale,
    )

    strategy_spec_id = _required_text(manifest.get("source_strategy_spec_id"), "source_strategy_spec_id")
    experiment = rolling_pipeline.run(
        strategy_spec_id,
        normalized_window,
        dataset=filtered_dataset,
        dataset_manifest=manifest,
        strategy_spec_packet=spec_packet,
        label_horizon_days=normalized_horizon,
        created_at=created,
        code_version=CODE_VERSION,
        task_id=TASK_ID,
    )

    windows = _rolling_window_metrics(experiment["metadata"]["oos_observations"])
    if not windows:
        raise ProductionRollingRunError("rolling run produced no production windows")
    model_artifact = _build_model_artifact(
        experiment,
        filtered_dataset,
        manifest,
        spec_packet,
        windows,
        production_summary,
        created_at=created,
    )
    model_ref = _build_model_artifact_ref(model_artifact)

    experiment["backend_id"] = BACKEND_ID
    experiment["artifact_refs"] = list(
        dict.fromkeys(
            [
                *experiment.get("artifact_refs", []),
                model_ref["artifact_ref"],
                f"artifact://qlib/{model_artifact['registry_id']}/{model_artifact['version']}/production_rolling_summary",
            ]
        )
    )
    metadata = experiment.setdefault("metadata", {})
    metadata["production_dataset"] = production_summary
    metadata["production_rolling_config"] = {
        "instrument_universe": _universe_label(instrument_universe),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "window_days": normalized_window,
        "label_horizon_days": normalized_horizon,
        "dataset_manifest_ref": _relative_or_uri(Path(dataset_manifest_path)),
        "strategy_spec_packet_ref": _relative_or_uri(Path(strategy_spec_packet_path)),
        "dataset_materialization_ref": _relative_or_uri(Path(dataset_path)),
        "cpu_only": True,
        "gpu_required": False,
    }
    metadata["production_rolling_windows"] = windows
    metadata["rolling_metric_summary"] = _rolling_metric_summary(windows)
    metadata["model_artifact"] = model_artifact
    metadata["model_artifact_ref"] = model_ref
    metadata["lineage"]["model_artifact_registry_id"] = model_artifact["registry_id"]
    metadata["lineage"]["dataset_manifest_id"] = manifest.get("manifest_id")
    metadata["safety_assertions"].update(
        {
            "cpu_only": True,
            "no_gpu": True,
            "registry_write_authority": "registry_service_only",
            "model_artifact_state": "draft",
        }
    )
    validate_experiment_run_payload(experiment)
    _validate_registry_entry(model_artifact)
    return experiment


def _payload_or_file(
    payload: Mapping[str, Any] | None,
    path: str | Path,
    label: str,
    *,
    required: bool = True,
) -> dict[str, Any]:
    if payload is not None:
        if not isinstance(payload, Mapping):
            raise ProductionRollingRunError(f"{label} must be a mapping")
        return copy.deepcopy(dict(payload))
    source = Path(path)
    if not source.exists():
        if required:
            raise ProductionRollingRunError(f"{label} file not found: {source}")
        return {}
    loaded = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise ProductionRollingRunError(f"{label} file must contain a JSON object")
    return dict(loaded)


def _assert_manifest_dataset_binding(
    dataset: Mapping[str, Any],
    manifest: Mapping[str, Any],
    strategy_spec_packet: Mapping[str, Any],
) -> None:
    dataset_id = _required_text(dataset.get("dataset_id"), "dataset.dataset_id")
    manifest_dataset_id = _required_text(manifest.get("dataset_id"), "manifest.dataset_id")
    if dataset_id != manifest_dataset_id:
        raise ProductionRollingRunError(
            f"dataset_id {dataset_id!r} does not match MGMT-QLIB-001 manifest {manifest_dataset_id!r}"
        )
    strategy_id = _required_text(manifest.get("strategy_id"), "manifest.strategy_id")
    if _required_text(dataset.get("strategy_id"), "dataset.strategy_id") != strategy_id:
        raise ProductionRollingRunError("dataset.strategy_id does not match manifest.strategy_id")
    strategy_spec_id = _required_text(manifest.get("source_strategy_spec_id"), "manifest.source_strategy_spec_id")
    if _required_text(dataset.get("source_strategy_spec_id"), "dataset.source_strategy_spec_id") != strategy_spec_id:
        raise ProductionRollingRunError(
            "dataset.source_strategy_spec_id does not match manifest.source_strategy_spec_id"
        )

    manifest_refs = set(_source_dataset_refs(manifest))
    dataset_refs = set(_source_dataset_refs(dataset))
    if not manifest_refs or not dataset_refs or not manifest_refs.intersection(dataset_refs):
        raise ProductionRollingRunError("dataset source_dataset_refs must match MGMT-QLIB-001 manifest refs")

    if strategy_spec_packet:
        binding = _mapping(strategy_spec_packet.get("strategy_spec_binding"))
        if binding.get("strategy_spec_id") != strategy_spec_id:
            raise ProductionRollingRunError("strategy_spec_packet.strategy_spec_binding.strategy_spec_id mismatch")
        if binding.get("strategy_id") != strategy_id:
            raise ProductionRollingRunError("strategy_spec_packet.strategy_spec_binding.strategy_id mismatch")
        if not manifest_refs.intersection(set(_source_dataset_refs(binding))):
            raise ProductionRollingRunError("strategy_spec_packet dataset refs do not match manifest")


def _filter_dataset(
    dataset: Mapping[str, Any],
    *,
    instrument_universe: Sequence[str] | str,
    start: date,
    end: date,
) -> dict[str, Any]:
    selected = _selected_instruments(dataset, instrument_universe)
    records = dataset.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ProductionRollingRunError("dataset.records must be an array")

    filtered: list[dict[str, Any]] = []
    seen_instruments: set[str] = set()
    for raw in records:
        if not isinstance(raw, Mapping):
            raise ProductionRollingRunError("each dataset record must be an object")
        instrument = _required_text(raw.get("instrument"), "record.instrument")
        if instrument not in selected:
            continue
        record_date = _parse_date(_required_text(raw.get("date"), "record.date"), "record.date")
        if record_date < start or record_date > end:
            continue
        normalized = {"instrument": instrument, "date": record_date.isoformat()}
        for field_name in REQUIRED_OHLCV_FIELDS:
            normalized[field_name] = _number(raw.get(field_name), f"{instrument}.{field_name}")
        filtered.append(normalized)
        seen_instruments.add(instrument)

    missing = sorted(selected - seen_instruments)
    if missing:
        raise ProductionRollingRunError(
            "instrument_universe records missing in selected date range: " + ", ".join(missing[:10])
        )
    if not filtered:
        raise ProductionRollingRunError("no records remain after instrument/date filtering")

    filtered.sort(key=lambda row: (row["instrument"], row["date"]))
    result = copy.deepcopy(dict(dataset))
    result["records"] = filtered
    result["production_filter"] = {
        "instrument_universe": sorted(selected),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    return result


def _assert_production_scale(
    dataset: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    start: date,
    end: date,
    required: bool,
) -> dict[str, Any]:
    grouped = _group_records(dataset)
    instrument_count = len(grouped)
    period_counts = [len(rows) for rows in grouped.values()]
    date_spans = [
        (_parse_date(rows[0]["date"], "record.date"), _parse_date(rows[-1]["date"], "record.date"))
        for rows in grouped.values()
        if rows
    ]
    history_years = _history_years(start, end)
    min_instrument_history_years = min((_history_years(first, last) for first, last in date_spans), default=0.0)
    min_periods = min(period_counts) if period_counts else 0
    governed = _mapping(manifest.get("governed_dataset"))
    manifest_floor = _mapping(manifest.get("activation_floor"))

    summary = {
        "dataset_id": dataset.get("dataset_id"),
        "manifest_id": manifest.get("manifest_id"),
        "source_dataset_refs": _source_dataset_refs(dataset),
        "source_strategy_spec_id": dataset.get("source_strategy_spec_id"),
        "strategy_id": dataset.get("strategy_id"),
        "num_instruments": instrument_count,
        "history_start": start.isoformat(),
        "history_end": end.isoformat(),
        "history_years": round(history_years, 4),
        "min_instrument_history_years": round(min_instrument_history_years, 4),
        "min_periods_per_instrument": min_periods,
        "data_frequency": dataset.get("data_frequency"),
        "ohlcv_fields": list(REQUIRED_OHLCV_FIELDS),
        "manifest_required_min_instruments": manifest_floor.get("required_min_instruments", MIN_PRODUCTION_INSTRUMENTS),
        "manifest_required_min_history_years": manifest_floor.get("required_min_history_years", MIN_PRODUCTION_HISTORY_YEARS),
        "manifest_required_min_daily_periods": manifest_floor.get("required_min_daily_periods", MIN_PRODUCTION_PERIODS),
        "manifest_history_start": governed.get("history_start") or governed.get("start_date"),
        "manifest_history_end": governed.get("history_end") or governed.get("end_date"),
        "production_scale_satisfied": True,
    }

    failures: list[str] = []
    if instrument_count < MIN_PRODUCTION_INSTRUMENTS:
        failures.append(f"instrument count {instrument_count} < {MIN_PRODUCTION_INSTRUMENTS}")
    if history_years < MIN_PRODUCTION_HISTORY_YEARS:
        failures.append(f"history years {history_years:.2f} < {MIN_PRODUCTION_HISTORY_YEARS:.1f}")
    if min_instrument_history_years < MIN_PRODUCTION_HISTORY_YEARS:
        failures.append(
            f"minimum per-instrument history {min_instrument_history_years:.2f} < {MIN_PRODUCTION_HISTORY_YEARS:.1f}"
        )
    if min_periods < MIN_PRODUCTION_PERIODS:
        failures.append(f"minimum periods per instrument {min_periods} < {MIN_PRODUCTION_PERIODS}")
    if str(dataset.get("data_frequency") or "").lower() != "daily":
        failures.append("data_frequency must be daily")
    if _mapping(manifest.get("activation_floor")).get("dataset_gate_satisfied") is not True:
        failures.append("MGMT-QLIB-001 activation_floor.dataset_gate_satisfied is not true")
    if failures:
        summary["production_scale_satisfied"] = False
        summary["production_scale_failures"] = failures
        if required:
            raise ProductionRollingRunError("production scale Qlib data gates failed: " + "; ".join(failures))
    return summary


def _rolling_window_metrics(observations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise ProductionRollingRunError("each oos observation must be an object")
        key = _required_text(observation.get("as_of_date"), "oos_observation.as_of_date")
        buckets[key].append(observation)

    windows: list[dict[str, Any]] = []
    for index, as_of_date in enumerate(sorted(buckets), start=1):
        rows = buckets[as_of_date]
        returns = [_number(row.get("strategy_return"), "strategy_return") for row in rows]
        predictions = [_number(row.get("prediction"), "prediction") for row in rows]
        actuals = [_number(row.get("actual_return"), "actual_return") for row in rows]
        windows.append(
            {
                "window_index": index,
                "as_of_date": as_of_date,
                "train_start_date": min(str(row.get("train_start_date")) for row in rows),
                "train_end_date": max(str(row.get("train_end_date")) for row in rows),
                "oos_end_date": max(str(row.get("oos_end_date")) for row in rows),
                "num_instruments": len({str(row.get("instrument")) for row in rows}),
                "mean_strategy_return": round(_mean(returns), 10),
                "rolling_sharpe": round(_sharpe(returns), 6),
                "rolling_ic": round(_pearson(predictions, actuals), 6),
            }
        )
    return windows


def _rolling_metric_summary(windows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rolling_sharpes = [_number(window.get("rolling_sharpe"), "rolling_sharpe") for window in windows]
    rolling_ics = [_number(window.get("rolling_ic"), "rolling_ic") for window in windows]
    return {
        "window_count": len(windows),
        "mean_rolling_sharpe": round(_mean(rolling_sharpes), 6),
        "max_rolling_sharpe": round(max(rolling_sharpes), 6),
        "positive_rolling_sharpe_windows": sum(1 for value in rolling_sharpes if value > 0.0),
        "mean_rolling_ic": round(_mean(rolling_ics), 6),
        "positive_rolling_ic_windows": sum(1 for value in rolling_ics if value > 0.0),
    }


def _build_model_artifact(
    experiment_run: Mapping[str, Any],
    dataset: Mapping[str, Any],
    manifest: Mapping[str, Any],
    strategy_spec_packet: Mapping[str, Any],
    windows: Sequence[Mapping[str, Any]],
    production_summary: Mapping[str, Any],
    *,
    created_at: str,
) -> dict[str, Any]:
    strategy_id = _required_text(dataset.get("strategy_id"), "strategy_id")
    registry_id = f"qlib-production-rolling-{_slug(strategy_id)}-{ARTIFACT_VERSION}"
    rolling_summary = _rolling_metric_summary(windows)
    model_payload = {
        "schema_version": "1.0",
        "artifact_family": "qlib_alpha",
        "model_family": "rolling_lightgbm_alpha",
        "framework": "qlib",
        "training_mode": "production_rolling_window",
        "strategy_id": strategy_id,
        "strategy_spec_id": manifest.get("source_strategy_spec_id"),
        "dataset_summary": dict(production_summary),
        "rolling_metric_summary": rolling_summary,
        "feature_set": _strategy_feature_set(strategy_spec_packet),
        "window_count": len(windows),
        "window_metric_sample": [dict(window) for window in list(windows)[:5]],
        "governance": {
            "direct_live_influence": False,
            "output_type": "alpha_score",
            "lean_consumption": "scoring_only_not_direct_action",
            "registry_write_authority": "registry_service_only",
        },
    }
    checksum = f"sha256:{_sha256_json(model_payload)}"
    lineage = {
        "parent_registry_ids": [_required_text(manifest.get("source_strategy_spec_id"), "source_strategy_spec_id")],
        "source_run_ids": [
            _required_text(experiment_run.get("run_id"), "experiment_run.run_id"),
            str(manifest.get("task_id") or "MGMT-QLIB-001"),
            str(strategy_spec_packet.get("task_id") or "MGMT-QLIB-002"),
            TASK_ID,
        ],
        "source_dataset_refs": _source_dataset_refs(dataset),
        "source_strategy_spec_id": manifest.get("source_strategy_spec_id"),
    }
    return {
        "registry_id": registry_id,
        "artifact_type": "model_artifact",
        "strategy_id": strategy_id,
        "version": ARTIFACT_VERSION,
        "artifact_state": "draft",
        "lineage": lineage,
        "storage_ref": {
            "backend": "inline",
            "path": "$.metadata.model_artifact",
        },
        "checksum": checksum,
        "producer_run_id": experiment_run.get("run_id"),
        "evaluation_summary": {
            "evaluation_kind": "production_rolling_window_oos",
            "rolling_sharpe": rolling_summary["mean_rolling_sharpe"],
            "rolling_ic": rolling_summary["mean_rolling_ic"],
            "max_rolling_sharpe": rolling_summary["max_rolling_sharpe"],
            "num_windows": rolling_summary["window_count"],
            "num_instruments": production_summary.get("num_instruments"),
            "dataset_manifest_id": manifest.get("manifest_id"),
        },
        "deployment_summary": {
            "current_stage": "none",
        },
        "metadata": {
            "created_at": created_at,
            "framework": "qlib",
            "model_family": "rolling_lightgbm_alpha",
            "training_mode": "production_rolling_window",
            "artifact_payload_checksum": checksum,
            "production_scale_satisfied": production_summary.get("production_scale_satisfied"),
            "registry_write_performed": False,
            "registry_write_authority": "registry_service_only",
            "model_payload": model_payload,
        },
    }


def _build_model_artifact_ref(model_artifact: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_name": "model_artifact",
        "artifact_type": model_artifact["artifact_type"],
        "artifact_ref": f"{model_artifact['registry_id']}@{model_artifact['version']}",
        "registry_id": model_artifact["registry_id"],
        "strategy_id": model_artifact["strategy_id"],
        "version": model_artifact["version"],
        "artifact_state": model_artifact["artifact_state"],
        "deployment_stage": _mapping(model_artifact.get("deployment_summary")).get("current_stage"),
        "storage_ref": copy.deepcopy(model_artifact["storage_ref"]),
        "checksum": model_artifact["checksum"],
        "source_run_id": model_artifact["producer_run_id"],
        "source_dataset_refs": copy.deepcopy(model_artifact["lineage"]["source_dataset_refs"]),
        "source_strategy_spec_id": model_artifact["lineage"]["source_strategy_spec_id"],
    }


def _validate_registry_entry(entry: Mapping[str, Any]) -> None:
    schema = json.loads(REGISTRY_ENTRY_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(dict(entry)), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ProductionRollingRunError(f"model_artifact registry schema failed at {location}: {first.message}")


def _selected_instruments(dataset: Mapping[str, Any], instrument_universe: Sequence[str] | str) -> set[str]:
    records = dataset.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ProductionRollingRunError("dataset.records must be an array")
    all_instruments = {
        _required_text(row.get("instrument"), "record.instrument")
        for row in records
        if isinstance(row, Mapping)
    }
    if isinstance(instrument_universe, str):
        raw = instrument_universe.strip()
        if raw.lower() in {"manifest", "all", "*"}:
            return all_instruments
        selected = {item.strip() for item in raw.split(",") if item.strip()}
    else:
        selected = {str(item).strip() for item in instrument_universe if str(item).strip()}
    if not selected:
        raise ProductionRollingRunError("instrument_universe must select at least one instrument")
    missing = sorted(selected - all_instruments)
    if missing:
        raise ProductionRollingRunError("instrument_universe contains unknown instruments: " + ", ".join(missing[:10]))
    return selected


def _group_records(dataset: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in dataset.get("records", []):
        if not isinstance(raw, Mapping):
            raise ProductionRollingRunError("each dataset record must be an object")
        grouped[_required_text(raw.get("instrument"), "record.instrument")].append(dict(raw))
    for rows in grouped.values():
        rows.sort(key=lambda row: row["date"])
    return dict(grouped)


def _source_dataset_refs(payload: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for source in (payload, _mapping(payload.get("governed_dataset")), _mapping(payload.get("qlib_preflight_governed_dataset"))):
        raw_values = source.get("source_dataset_refs")
        if isinstance(raw_values, str):
            raw_values = [raw_values]
        if isinstance(raw_values, Sequence) and not isinstance(raw_values, (str, bytes)):
            for item in raw_values:
                text = str(item or "").strip()
                if text and text not in refs:
                    refs.append(text)
        single = str(source.get("source_dataset_ref") or "").strip()
        if single and single not in refs:
            refs.append(single)
    return refs


def _strategy_feature_set(packet: Mapping[str, Any]) -> list[str]:
    binding = _mapping(packet.get("strategy_spec_binding"))
    features = binding.get("feature_set")
    if isinstance(features, Sequence) and not isinstance(features, (str, bytes)):
        return [str(item) for item in features if str(item or "").strip()]
    return ["momentum", "volatility", "volume"]


def _universe_label(instrument_universe: Sequence[str] | str) -> str:
    if isinstance(instrument_universe, str):
        return instrument_universe
    return ",".join(str(item) for item in instrument_universe)


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProductionRollingRunError(f"{field_name} must be a positive integer")
    return value


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProductionRollingRunError(f"{field_name} must be numeric")
    return float(value)


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ProductionRollingRunError(f"{field_name} is required")
    return text


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _parse_date(value: Any, field_name: str) -> date:
    text = _required_text(value, field_name)
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ProductionRollingRunError(f"{field_name} must be YYYY-MM-DD") from exc


def _history_years(start: date, end: date) -> float:
    return max((end - start).days / 365.25, 0.0)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return (sum((value - avg) ** 2 for value in values) / len(values)) ** 0.5


def _sharpe(values: Sequence[float]) -> float:
    std = _stdev(values)
    if std <= 1e-12:
        return 0.0
    return (_mean(values) / std) * (252**0.5)


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) != len(ys):
        raise ProductionRollingRunError("rolling IC inputs have different lengths")
    if len(xs) < 2:
        return 0.0
    mean_x = _mean(xs)
    mean_y = _mean(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    den_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    denom = den_x * den_y
    return num / denom if denom > 1e-12 else 0.0


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")


def _relative_or_uri(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run production-scale Qlib rolling OOS evaluation.")
    parser.add_argument("--instrument-universe", default="manifest")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--window-days", type=int, required=True)
    parser.add_argument("--label-horizon-days", type=int, default=DEFAULT_LABEL_HORIZON_DAYS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--dataset-manifest", type=Path, default=DEFAULT_DATASET_MANIFEST_PATH)
    parser.add_argument("--strategy-spec-packet", type=Path, default=DEFAULT_STRATEGY_SPEC_PACKET_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    payload = run_production(
        args.instrument_universe,
        args.start_date,
        args.end_date,
        args.window_days,
        label_horizon_days=args.label_horizon_days,
        dataset_path=args.dataset,
        dataset_manifest_path=args.dataset_manifest,
        strategy_spec_packet_path=args.strategy_spec_packet,
    )
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


__all__ = ["ProductionRollingRunError", "run_production", "utc_now"]


if __name__ == "__main__":
    raise SystemExit(main())
