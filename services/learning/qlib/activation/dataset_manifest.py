"""Governed dataset manifest helper for Qlib admission.

The manifest is a review artifact for the dataset gate only. It does not fetch
market data, write registry truth, start Qlib training, or open an execution
route.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

MIN_INSTRUMENTS = 50
MIN_HISTORY_YEARS = 2.0
MIN_DAILY_PERIODS = 504
REQUIRED_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
ALLOWED_DATA_FREQUENCIES = frozenset({"daily", "intraday"})
ALLOWED_SOURCE_CLASSES = frozenset({"research_grade", "internal_can"})
REQUIRED_ALLOWED_USE = frozenset({"research", "model_training"})
ORDER_CAPABLE_TARGETS = frozenset(
    {
        "broker",
        "capital",
        "canary",
        "lean",
        "live",
        "order",
        "order_routing",
        "paper",
        "runtime",
    }
)


class DatasetManifestError(ValueError):
    """Raised when a Qlib dataset manifest cannot satisfy the dataset gate."""


def build_dataset_manifest(
    proof: Mapping[str, Any],
    *,
    task_id: str = "MGMT-QLIB-001",
    created_at: str | None = None,
    min_periods_per_instrument: int | None = None,
    period_count_source: str | None = None,
) -> dict[str, Any]:
    """Build a normalized Qlib dataset manifest from governed proof fields."""
    if not isinstance(proof, Mapping):
        raise DatasetManifestError("dataset proof must be a mapping")

    provider = _mapping(proof.get("provider"))
    entitlement = _mapping(proof.get("entitlement"))
    freshness = _mapping(proof.get("freshness"))
    pit = _mapping(proof.get("pit"))
    storage = _mapping(proof.get("storage"))
    audit = _mapping(proof.get("audit"))
    controls = _mapping(proof.get("controls"))
    floor = _mapping(proof.get("dataset_floor_summary"))

    strategy_id = _required_text(proof, "strategy_id")
    source_strategy_spec_id = _required_text(proof, "source_strategy_spec_id")
    dataset_id = _required_text(proof, "dataset_id")
    provider_dataset_id = _required_text(provider, "dataset_id", prefix="provider")
    provider_name = _required_text(provider, "name", prefix="provider")
    source_class = _required_text(provider, "source_class", prefix="provider")
    market = _required_text(provider, "market", prefix="provider")
    data_frequency = _required_text(provider, "data_frequency", prefix="provider").lower()
    history_start = _required_date_text(provider, "history_start", prefix="provider")
    history_end = _required_date_text(provider, "history_end", prefix="provider")
    history_years = _history_years(history_start, history_end)
    exchange_segments = _strings(provider.get("exchange_segments"))
    instrument_count = _positive_int(provider.get("instrument_count"))
    periods = (
        min_periods_per_instrument
        if min_periods_per_instrument is not None
        else _positive_int(
            floor.get("min_periods_per_instrument")
            or provider.get("min_periods_per_instrument")
        )
    )
    periods_source = (
        period_count_source
        or _text(floor.get("period_count_source"))
        or _text(provider.get("period_count_source"))
    )

    entitlement_ref = _text(entitlement.get("entitlement_ref"))
    entitlement_tags = _strings(entitlement.get("entitlement_tags"))
    license_scope = _required_text(entitlement, "license_scope", prefix="entitlement")
    allowed_use = sorted(set(_strings(entitlement.get("allowed_use"))))

    freshness_status = _required_text(freshness, "status", prefix="freshness")
    freshness_as_of = _required_text(freshness, "as_of", prefix="freshness")
    last_ingested_at = _required_text(
        freshness,
        "last_ingested_at",
        prefix="freshness",
    )
    freshness_sla_seconds = _positive_int(freshness.get("freshness_sla_seconds"))

    event_time_field = _required_text(pit, "event_time_field", prefix="pit")
    available_time_field = _required_text(pit, "available_time_field", prefix="pit")
    source_watermark = _required_text(pit, "source_watermark", prefix="pit")

    storage_backend = _required_text(storage, "backend", prefix="storage")
    dataset_ref = _required_text(storage, "dataset_ref", prefix="storage")
    snapshot_ref = _required_text(storage, "snapshot_ref", prefix="storage")
    storage_path = _required_text(storage, "path", prefix="storage")
    checksum = _required_text(storage, "checksum", prefix="storage")

    ingest_run_id = _required_text(audit, "ingest_run_id", prefix="audit")
    normalization_run_id = _required_text(audit, "normalization_run_id", prefix="audit")
    evidence_bundle_ref = _required_text(audit, "evidence_bundle_ref", prefix="audit")
    rate_limit_policy_ref = _required_text(audit, "rate_limit_policy_ref", prefix="audit")
    execution_targets = sorted(set(_strings(controls.get("execution_targets"))))

    problems: list[str] = []
    if source_class not in ALLOWED_SOURCE_CLASSES:
        problems.append(
            "provider.source_class must be one of "
            f"{sorted(ALLOWED_SOURCE_CLASSES)}"
        )
    if not exchange_segments:
        problems.append("provider.exchange_segments missing")
    if instrument_count < MIN_INSTRUMENTS:
        problems.append(f"provider.instrument_count={instrument_count}, need >= {MIN_INSTRUMENTS}")
    if history_years < MIN_HISTORY_YEARS:
        problems.append(f"history_years={history_years:.2f}, need >= {MIN_HISTORY_YEARS:.1f}")
    if data_frequency not in ALLOWED_DATA_FREQUENCIES:
        problems.append(
            "provider.data_frequency must be one of "
            f"{sorted(ALLOWED_DATA_FREQUENCIES)}"
        )
    if periods < MIN_DAILY_PERIODS:
        problems.append(
            f"min_periods_per_instrument={periods}, need >= {MIN_DAILY_PERIODS}"
        )
    if not periods_source:
        problems.append("period_count_source missing")
    if not entitlement_ref and not entitlement_tags:
        problems.append("entitlement_ref or entitlement_tags missing")
    if not REQUIRED_ALLOWED_USE.issubset(set(allowed_use)):
        problems.append("allowed_use must include research and model_training")
    if set(allowed_use) & ORDER_CAPABLE_TARGETS:
        problems.append("allowed_use includes order-capable target")
    if freshness_status != "fresh":
        problems.append("freshness.status must be fresh")
    if freshness_sla_seconds <= 0:
        problems.append("freshness.freshness_sla_seconds must be positive")
    if pit.get("point_in_time") is not True:
        problems.append("pit.point_in_time=True not proven")
    if storage.get("durable") is not True:
        problems.append("storage.durable=True not proven")
    if dataset_ref != dataset_id:
        problems.append("storage.dataset_ref must match dataset_id")
    if not checksum.startswith("sha256:"):
        problems.append("storage.checksum must be sha256-prefixed")
    if controls.get("no_order_route") is not True:
        problems.append("controls.no_order_route=True not proven")
    if set(execution_targets) & ORDER_CAPABLE_TARGETS:
        problems.append("controls.execution_targets includes order-capable target")

    if problems:
        raise DatasetManifestError("Qlib dataset manifest failed: " + "; ".join(problems))

    governed_dataset = {
        "source_dataset_refs": [dataset_ref],
        "source_dataset_ref": dataset_ref,
        "governed": True,
        "num_instruments": instrument_count,
        "history_start": history_start,
        "history_end": history_end,
        "start_date": history_start,
        "end_date": history_end,
        "history_years": round(history_years, 4),
        "min_periods_per_instrument": periods,
        "period_count_source": periods_source,
        "data_frequency": data_frequency,
        "ohlcv_fields": list(REQUIRED_OHLCV_FIELDS),
        "market": market,
        "exchange_segments": exchange_segments,
        "provider_dataset_id": provider_dataset_id,
    }

    manifest = {
        "schema_version": "1.0",
        "manifest_id": f"qlib-dataset-manifest:{_manifest_slug(dataset_ref)}",
        "task_id": task_id,
        "created_at": created_at or _utc_now(),
        "strategy_id": strategy_id,
        "source_strategy_spec_id": source_strategy_spec_id,
        "dataset_id": dataset_id,
        "governed_dataset": governed_dataset,
        "qlib_preflight_governed_dataset": copy.deepcopy(governed_dataset),
        "activation_floor": {
            "required_min_instruments": MIN_INSTRUMENTS,
            "required_min_history_years": MIN_HISTORY_YEARS,
            "required_min_daily_periods": MIN_DAILY_PERIODS,
            "instrument_floor_satisfied": True,
            "history_floor_satisfied": True,
            "period_floor_satisfied": True,
            "dataset_gate_satisfied": True,
        },
        "production_dataset_proof": {
            "provider": {
                "name": provider_name,
                "source_class": source_class,
                "dataset_id": provider_dataset_id,
                "market": market,
                "exchange_segments": exchange_segments,
                "instrument_count": instrument_count,
                "history_start": history_start,
                "history_end": history_end,
                "data_frequency": data_frequency,
            },
            "entitlement": {
                "entitlement_ref": entitlement_ref,
                "entitlement_tags": entitlement_tags,
                "license_scope": license_scope,
                "allowed_use": allowed_use,
            },
            "freshness": {
                "status": freshness_status,
                "as_of": freshness_as_of,
                "last_ingested_at": last_ingested_at,
                "freshness_sla_seconds": freshness_sla_seconds,
            },
            "pit": {
                "point_in_time": True,
                "event_time_field": event_time_field,
                "available_time_field": available_time_field,
                "source_watermark": source_watermark,
            },
            "storage": {
                "durable": True,
                "backend": storage_backend,
                "dataset_ref": dataset_ref,
                "snapshot_ref": snapshot_ref,
                "path": storage_path,
                "checksum": checksum,
            },
            "audit": {
                "ingest_run_id": ingest_run_id,
                "normalization_run_id": normalization_run_id,
                "evidence_bundle_ref": evidence_bundle_ref,
                "rate_limit_policy_ref": rate_limit_policy_ref,
            },
            "controls": {
                "no_order_route": True,
                "execution_targets": execution_targets,
            },
        },
        "downstream_scope": {
            "dataset_gate_only": True,
            "registry_write_authority": "registry_service_only",
            "registry_write_performed": False,
            "training_performed": False,
            "broker_session_opened": False,
            "order_route": "none",
            "deployment_stage": "none",
        },
    }
    validate_dataset_manifest(manifest)
    return manifest


def validate_dataset_manifest(manifest: Mapping[str, Any]) -> None:
    """Validate a normalized dataset manifest without side effects."""
    if not isinstance(manifest, Mapping):
        raise DatasetManifestError("dataset manifest must be a mapping")

    governed = _mapping(manifest.get("governed_dataset"))
    proof = _mapping(manifest.get("production_dataset_proof"))
    floor = _mapping(manifest.get("activation_floor"))
    scope = _mapping(manifest.get("downstream_scope"))

    problems: list[str] = []
    if _text(manifest.get("schema_version")) != "1.0":
        problems.append("schema_version must be 1.0")
    for key in ("manifest_id", "task_id", "strategy_id", "source_strategy_spec_id", "dataset_id"):
        if not _text(manifest.get(key)):
            problems.append(f"{key} missing")
    if governed.get("governed") is not True:
        problems.append("governed_dataset.governed=True not proven")
    if _strings(governed.get("ohlcv_fields")) != list(REQUIRED_OHLCV_FIELDS):
        problems.append("governed_dataset.ohlcv_fields must be open/high/low/close/volume")
    if not _strings(governed.get("source_dataset_refs")):
        problems.append("governed_dataset.source_dataset_refs missing")
    if _positive_int(governed.get("num_instruments")) < MIN_INSTRUMENTS:
        problems.append("governed_dataset.num_instruments below floor")
    if _float(governed.get("history_years")) < MIN_HISTORY_YEARS:
        problems.append("governed_dataset.history_years below floor")
    if _positive_int(governed.get("min_periods_per_instrument")) < MIN_DAILY_PERIODS:
        problems.append("governed_dataset.min_periods_per_instrument below floor")
    if _text(governed.get("data_frequency")) not in ALLOWED_DATA_FREQUENCIES:
        problems.append("governed_dataset.data_frequency invalid")
    if floor.get("dataset_gate_satisfied") is not True:
        problems.append("activation_floor.dataset_gate_satisfied=True not proven")
    if not _mapping(proof.get("provider")):
        problems.append("production_dataset_proof.provider missing")
    if not _mapping(proof.get("storage")):
        problems.append("production_dataset_proof.storage missing")
    if scope.get("dataset_gate_only") is not True:
        problems.append("downstream_scope.dataset_gate_only=True not proven")
    if scope.get("registry_write_performed") is not False:
        problems.append("downstream_scope.registry_write_performed must be false")
    if scope.get("training_performed") is not False:
        problems.append("downstream_scope.training_performed must be false")
    if scope.get("broker_session_opened") is not False:
        problems.append("downstream_scope.broker_session_opened must be false")
    if _text(scope.get("order_route")) != "none":
        problems.append("downstream_scope.order_route must be none")

    if problems:
        raise DatasetManifestError("Qlib dataset manifest failed: " + "; ".join(problems))


def governed_dataset_for_preflight(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the governed_dataset probe consumed by services.research.qlib.preflight."""
    validate_dataset_manifest(manifest)
    return copy.deepcopy(_mapping(manifest.get("qlib_preflight_governed_dataset")) or _mapping(manifest.get("governed_dataset")))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _required_text(payload: Mapping[str, Any], key: str, *, prefix: str | None = None) -> str:
    value = _text(payload.get(key))
    if not value:
        label = f"{prefix}.{key}" if prefix else key
        raise DatasetManifestError(f"{label} missing")
    return value


def _required_date_text(payload: Mapping[str, Any], key: str, *, prefix: str | None = None) -> str:
    value = _required_text(payload, key, prefix=prefix)
    _parse_date(value)
    return value


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Sequence):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value > 0:
        return int(value)
    return 0


def _float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DatasetManifestError(f"invalid date: {value}") from exc


def _history_years(start: str, end: str) -> float:
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    if end_date <= start_date:
        raise DatasetManifestError("history_end must be after history_start")
    return (end_date - start_date).days / 365.25


def _manifest_slug(dataset_ref: str) -> str:
    return dataset_ref.replace(":", "-").replace("/", "-").replace("_", "-")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Qlib governed dataset manifest.")
    parser.add_argument("proof", help="Path to the governed production dataset proof JSON.")
    parser.add_argument("--output", required=True, help="Manifest output path.")
    parser.add_argument("--task-id", default="MGMT-QLIB-001")
    parser.add_argument("--created-at")
    parser.add_argument("--min-periods-per-instrument", type=int, required=True)
    parser.add_argument("--period-count-source", required=True)
    args = parser.parse_args(argv)

    proof = json.loads(Path(args.proof).read_text(encoding="utf-8"))
    manifest = build_dataset_manifest(
        proof,
        task_id=args.task_id,
        created_at=args.created_at,
        min_periods_per_instrument=args.min_periods_per_instrument,
        period_count_source=args.period_count_source,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
