"""Rolling-window out-of-sample pipeline for governed Qlib research runs."""
from __future__ import annotations

import copy
import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.research.experiments.models import ExperimentRun

try:  # Allows both package imports and direct script execution.
    from .oos_eval import evaluate
except ImportError:  # pragma: no cover - direct script fallback
    from oos_eval import evaluate


DEFAULT_DATASET_PATH = Path(__file__).resolve().parent / "examples" / "smoke_dataset.json"
DEFAULT_DATASET_MANIFEST_PATH = REPO_ROOT / "support" / "evidence" / "MGMT-QLIB-001" / "dataset_manifest.json"
DEFAULT_STRATEGY_SPEC_PACKET_PATH = REPO_ROOT / "support" / "evidence" / "MGMT-QLIB-002" / "strategy_spec_packet.json"
BACKEND_ID = "qlib_rolling_oos"
DEFAULT_LABEL_HORIZON_DAYS = 5
MIN_INSTRUMENTS = 2
REQUIRED_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")


class QlibRollingPipelineError(ValueError):
    """Raised when a rolling-window Qlib ExperimentRun cannot be built."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(
    strategy_spec_ref: str | Mapping[str, Any],
    window_days: int,
    *,
    dataset: Mapping[str, Any] | None = None,
    dataset_manifest: Mapping[str, Any] | None = None,
    strategy_spec_packet: Mapping[str, Any] | None = None,
    label_horizon_days: int = DEFAULT_LABEL_HORIZON_DAYS,
    created_at: str | None = None,
    code_version: str = "pantheon:services/research/qlib@OSS-QLIB-002",
    task_id: str = "OSS-QLIB-002",
) -> dict[str, Any]:
    """Build a completed ExperimentRun for rolling-window Qlib OOS evaluation.

    The returned payload is schema-valid as an ExperimentRun. Task-specific
    producer_run_id, lineage, observations, and evaluation_summary are stored in
    metadata so downstream ExperimentRun writeback can consume them without
    broadening the canonical ExperimentRun schema.
    """

    normalized_window = _positive_int(window_days, "window_days")
    normalized_horizon = _positive_int(label_horizon_days, "label_horizon_days")
    strategy_spec_id = _strategy_spec_id(strategy_spec_ref)
    created = created_at or utc_now()

    manifest = _payload_or_file(dataset_manifest, DEFAULT_DATASET_MANIFEST_PATH, "dataset_manifest")
    spec_packet = _payload_or_file(
        strategy_spec_packet,
        DEFAULT_STRATEGY_SPEC_PACKET_PATH,
        "strategy_spec_packet",
        required=False,
    )
    dataset_payload = _payload_or_file(dataset, DEFAULT_DATASET_PATH, "dataset")
    _assert_lineage_compatible(strategy_spec_id, dataset_payload, manifest, spec_packet)

    observations = _build_oos_observations(
        dataset_payload,
        window_days=normalized_window,
        label_horizon_days=normalized_horizon,
    )
    run_id = f"qlib-oos-{uuid.uuid4().hex[:12]}"
    strategy_id = _required_text(
        dataset_payload.get("strategy_id") or manifest.get("strategy_id"),
        "strategy_id",
    )
    dataset_version_id = _required_text(
        manifest.get("dataset_id") or dataset_payload.get("dataset_id"),
        "dataset_version_id",
    )
    strategy_spec_version = _strategy_spec_version(spec_packet) or "1.0.0"
    dataset_manifest_ref = _relative_or_uri(DEFAULT_DATASET_MANIFEST_PATH)
    output_manifest_ref = f"inline://research/qlib/{run_id}/rolling_oos_output"
    evaluation_artifact_ref = (
        f"artifact://qlib/{strategy_spec_id}/rolling-oos/{run_id}/evaluation_summary"
    )

    lineage = {
        "strategy_spec_artifact_id": strategy_spec_id,
        "source_strategy_spec_id": strategy_spec_id,
        "dataset_manifest_id": manifest.get("manifest_id"),
        "dataset_manifest_ref": dataset_manifest_ref,
        "source_dataset_refs": _source_dataset_refs(dataset_payload, manifest),
        "source_run_ids": [str(manifest.get("task_id") or "MGMT-QLIB-001"), task_id],
    }
    metadata = {
        "producer_run_id": run_id,
        "lineage": {key: value for key, value in lineage.items() if value not in (None, "", [])},
        "oos_config": {
            "window_days": normalized_window,
            "label_horizon_days": normalized_horizon,
            "evaluation_kind": "rolling_window_oos",
        },
        "oos_observations": observations,
        "safety_assertions": {
            "no_registry_write": True,
            "no_broker_session": True,
            "no_order_route": True,
            "deployment_stage_remains_none": True,
            "scoring_only_not_direct_action": True,
        },
    }

    experiment = ExperimentRun(
        run_id=run_id,
        task_id=task_id,
        strategy_id=strategy_id,
        strategy_spec_version=strategy_spec_version,
        backend_id=BACKEND_ID,
        runtime_env="research",
        status="completed",
        started_at=created,
        finished_at=created,
        dataset_version_id=dataset_version_id,
        code_version=code_version,
        input_manifest_ref=dataset_manifest_ref,
        output_manifest_ref=output_manifest_ref,
        metric_bundle_id=f"metric-bundle:{run_id}",
        artifact_refs=[evaluation_artifact_ref],
        logs_ref=f"inline://research/qlib/{run_id}/rolling_oos_log",
        trace_id=f"trace:{run_id}",
        created_at=created,
        updated_at=created,
        metadata=metadata,
    )
    payload = experiment.to_dict()
    oos_metrics = evaluate(payload)
    payload["metadata"]["oos_metrics"] = oos_metrics
    payload["metadata"]["evaluation_summary"] = {
        "evaluation_kind": "rolling_window_oos",
        "producer_run_id": run_id,
        "strategy_spec_artifact_id": strategy_spec_id,
        "source_strategy_spec_id": strategy_spec_id,
        "dataset_manifest_id": manifest.get("manifest_id"),
        "dataset_version_id": dataset_version_id,
        "window_days": normalized_window,
        "label_horizon_days": normalized_horizon,
        "num_oos_samples": oos_metrics["num_oos_samples"],
        "oos_metrics": oos_metrics,
        "registry_hints": {
            "artifact_type": "evaluation_result",
            "artifact_state": "draft",
            "deployment_stage": "none",
            "source_strategy_spec_id": strategy_spec_id,
            "source_dataset_refs": _source_dataset_refs(dataset_payload, manifest),
        },
    }
    return payload


def _payload_or_file(
    payload: Mapping[str, Any] | None,
    path: Path,
    label: str,
    *,
    required: bool = True,
) -> dict[str, Any]:
    if payload is not None:
        if not isinstance(payload, Mapping):
            raise QlibRollingPipelineError(f"{label} must be a mapping")
        return copy.deepcopy(dict(payload))
    if not path.exists():
        if required:
            raise QlibRollingPipelineError(f"{label} file not found: {path}")
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise QlibRollingPipelineError(f"{label} file must contain a JSON object")
    return dict(loaded)


def _strategy_spec_id(strategy_spec_ref: str | Mapping[str, Any]) -> str:
    if isinstance(strategy_spec_ref, Mapping):
        for key in (
            "strategy_spec_id",
            "source_strategy_spec_id",
            "canonical_strategy_spec_id",
            "artifact_id",
            "registry_id",
            "id",
        ):
            text = _text(strategy_spec_ref.get(key))
            if text:
                return text
    else:
        text = _text(strategy_spec_ref)
        if text:
            return text
    raise QlibRollingPipelineError("strategy_spec_ref must identify a StrategySpec artifact")


def _strategy_spec_version(packet: Mapping[str, Any]) -> str:
    registry_entry = _mapping(packet.get("registry_entry"))
    strategy_spec = _mapping(packet.get("strategy_spec"))
    return (
        _text(registry_entry.get("version"))
        or _text(strategy_spec.get("spec_version"))
        or _text(packet.get("version"))
    )


def _assert_lineage_compatible(
    strategy_spec_id: str,
    dataset: Mapping[str, Any],
    manifest: Mapping[str, Any],
    spec_packet: Mapping[str, Any],
) -> None:
    manifest_strategy = _text(manifest.get("source_strategy_spec_id"))
    dataset_strategy = _text(dataset.get("source_strategy_spec_id"))
    packet_ids = _packet_strategy_ids(spec_packet)

    for label, value in (
        ("dataset_manifest.source_strategy_spec_id", manifest_strategy),
        ("dataset.source_strategy_spec_id", dataset_strategy),
    ):
        if value and value != strategy_spec_id:
            raise QlibRollingPipelineError(
                f"{label}={value!r} does not match strategy_spec_ref={strategy_spec_id!r}"
            )
    if packet_ids and strategy_spec_id not in packet_ids:
        raise QlibRollingPipelineError(
            f"strategy_spec_ref={strategy_spec_id!r} is not present in strategy_spec_packet"
        )

    manifest_refs = set(_manifest_dataset_refs(manifest))
    dataset_refs = set(_dataset_refs(dataset))
    if manifest_refs and dataset_refs and not manifest_refs.intersection(dataset_refs):
        raise QlibRollingPipelineError(
            "dataset source_dataset_refs do not intersect dataset_manifest source refs"
        )


def _packet_strategy_ids(packet: Mapping[str, Any]) -> set[str]:
    if not packet:
        return set()
    registry_entry = _mapping(packet.get("registry_entry"))
    binding = _mapping(packet.get("strategy_spec_binding"))
    preflight = _mapping(packet.get("preflight_packet"))
    preflight_binding = _mapping(preflight.get("strategy_spec_binding"))
    values = [
        packet.get("packet_id"),
        registry_entry.get("registry_id"),
        binding.get("strategy_spec_id"),
        binding.get("source_strategy_spec_id"),
        binding.get("canonical_strategy_spec_id"),
        preflight_binding.get("strategy_spec_id"),
        preflight_binding.get("source_strategy_spec_id"),
        preflight_binding.get("canonical_strategy_spec_id"),
    ]
    return {text for text in (_text(value) for value in values) if text}


def _build_oos_observations(
    dataset: Mapping[str, Any],
    *,
    window_days: int,
    label_horizon_days: int,
) -> list[dict[str, Any]]:
    grouped = _group_records(dataset)
    if len(grouped) < MIN_INSTRUMENTS:
        raise QlibRollingPipelineError(
            f"dataset must have at least {MIN_INSTRUMENTS} instruments; got {len(grouped)}"
        )

    required_periods = window_days + label_horizon_days + 1
    observations: list[dict[str, Any]] = []
    for instrument, records in sorted(grouped.items()):
        if len(records) < required_periods:
            raise QlibRollingPipelineError(
                "insufficient data for rolling OOS: "
                f"instrument {instrument} has {len(records)} records, "
                f"need at least {required_periods} for window_days={window_days} "
                f"and label_horizon_days={label_horizon_days}"
            )
        closes = [row["close"] for row in records]
        dates = [row["date"] for row in records]
        for idx in range(window_days, len(records) - label_horizon_days):
            history_returns = [
                _safe_return(closes[j], closes[j - 1])
                for j in range(idx - window_days + 1, idx + 1)
            ]
            prediction = sum(history_returns) / len(history_returns) * label_horizon_days
            actual_return = _safe_return(closes[idx + label_horizon_days], closes[idx])
            direction = 1.0 if prediction >= 0.0 else -1.0
            observations.append(
                {
                    "instrument": instrument,
                    "as_of_date": dates[idx],
                    "train_start_date": dates[idx - window_days],
                    "train_end_date": dates[idx],
                    "oos_end_date": dates[idx + label_horizon_days],
                    "window_days": window_days,
                    "label_horizon_days": label_horizon_days,
                    "prediction": round(prediction, 10),
                    "actual_return": round(actual_return, 10),
                    "strategy_return": round(actual_return * direction, 10),
                }
            )
    if not observations:
        raise QlibRollingPipelineError("insufficient data for rolling OOS observations")
    return observations


def _group_records(dataset: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    records = dataset.get("records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)) or not records:
        raise QlibRollingPipelineError("dataset.records must be a non-empty array")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in records:
        if not isinstance(raw, Mapping):
            raise QlibRollingPipelineError("each dataset record must be an object")
        instrument = _required_text(raw.get("instrument"), "record.instrument")
        record_date = _required_text(raw.get("date"), "record.date")
        _assert_iso_date(record_date, instrument)
        normalized: dict[str, Any] = {"instrument": instrument, "date": record_date}
        for field_name in REQUIRED_OHLCV_FIELDS:
            normalized[field_name] = _numeric(raw.get(field_name), f"{instrument}.{field_name}")
        grouped[instrument].append(normalized)
    for rows in grouped.values():
        rows.sort(key=lambda row: row["date"])
    return dict(grouped)


def _source_dataset_refs(dataset: Mapping[str, Any], manifest: Mapping[str, Any]) -> list[str]:
    refs = _dataset_refs(dataset)
    if refs:
        return refs
    return _manifest_dataset_refs(manifest)


def _dataset_refs(dataset: Mapping[str, Any]) -> list[str]:
    return _strings(dataset.get("source_dataset_refs")) or _strings(dataset.get("source_dataset_ref"))


def _manifest_dataset_refs(manifest: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for source in (
        manifest,
        _mapping(manifest.get("governed_dataset")),
        _mapping(manifest.get("qlib_preflight_governed_dataset")),
    ):
        for ref in _strings(source.get("source_dataset_refs")) or _strings(source.get("source_dataset_ref")):
            if ref not in refs:
                refs.append(ref)
    return refs


def _safe_return(close_now: float, close_prev: float) -> float:
    if abs(close_prev) <= 1e-12:
        return 0.0
    return (close_now - close_prev) / close_prev


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise QlibRollingPipelineError(f"{field_name} must be a positive integer")
    return value


def _numeric(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QlibRollingPipelineError(f"{field_name} must be numeric")
    return float(value)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return []


def _required_text(value: Any, field_name: str) -> str:
    text = _text(value)
    if not text:
        raise QlibRollingPipelineError(f"{field_name} is required")
    return text


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _assert_iso_date(value: str, instrument: str) -> None:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise QlibRollingPipelineError(
            f"record for {instrument} has invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def _relative_or_uri(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run governed Qlib rolling-window OOS evaluation.")
    parser.add_argument("strategy_spec_ref")
    parser.add_argument("--window-days", type=int, required=True)
    parser.add_argument("--label-horizon-days", type=int, default=DEFAULT_LABEL_HORIZON_DAYS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--dataset-manifest", type=Path, default=DEFAULT_DATASET_MANIFEST_PATH)
    parser.add_argument("--strategy-spec-packet", type=Path, default=DEFAULT_STRATEGY_SPEC_PACKET_PATH)
    args = parser.parse_args(argv)

    payload = run(
        args.strategy_spec_ref,
        args.window_days,
        dataset=_payload_or_file(None, args.dataset, "dataset"),
        dataset_manifest=_payload_or_file(None, args.dataset_manifest, "dataset_manifest"),
        strategy_spec_packet=_payload_or_file(
            None,
            args.strategy_spec_packet,
            "strategy_spec_packet",
            required=False,
        ),
        label_horizon_days=args.label_horizon_days,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


__all__ = ["QlibRollingPipelineError", "run", "utc_now"]


if __name__ == "__main__":
    raise SystemExit(main())
