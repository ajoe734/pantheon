"""Production-data activation packet helpers for governed Qlib runs.

This module validates the evidence that allows Qlib to consume production
market data. It does not fetch external data, write registry truth, or route
anything to execution.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .qlib_adapter import (
    ACTIVATION_READY_MIN_DAILY_PERIODS,
    ACTIVATION_READY_MIN_HISTORY_YEARS,
    ACTIVATION_READY_MIN_INSTRUMENTS,
    PRIMARY_BACKEND,
    STUB_BACKEND,
    PreparedQlibDataset,
    QlibRunResult,
    QlibWorkflowError,
    utc_now,
    validate_activation_ready_dataset,
)

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


def validate_production_dataset_proof(
    proof: Mapping[str, Any],
    dataset: PreparedQlibDataset,
) -> dict[str, Any]:
    """Validate provider, entitlement, freshness, PIT, storage, and audit proof."""
    validate_activation_ready_dataset(dataset)
    problems: list[str] = []

    provider = _mapping(proof.get("provider"))
    entitlement = _mapping(proof.get("entitlement"))
    freshness = _mapping(proof.get("freshness"))
    pit = _mapping(proof.get("pit"))
    storage = _mapping(proof.get("storage"))
    audit = _mapping(proof.get("audit"))
    controls = _mapping(proof.get("controls"))

    provider_name = _text(provider.get("name"))
    source_class = _text(provider.get("source_class"))
    provider_dataset_id = _text(provider.get("dataset_id"))
    if not provider_name:
        problems.append("provider.name missing")
    if source_class not in {"research_grade", "internal_can"}:
        problems.append(
            "provider.source_class must be research_grade or internal_can"
        )
    if not provider_dataset_id:
        problems.append("provider.dataset_id missing")

    entitlement_ref = _text(entitlement.get("entitlement_ref"))
    entitlement_tags = _strings(entitlement.get("entitlement_tags"))
    license_scope = _text(entitlement.get("license_scope"))
    allowed_use = set(_strings(entitlement.get("allowed_use")))
    if not entitlement_ref and not entitlement_tags:
        problems.append("entitlement_ref or entitlement_tags missing")
    if not license_scope:
        problems.append("license_scope missing")
    if not REQUIRED_ALLOWED_USE.issubset(allowed_use):
        problems.append("allowed_use must include research and model_training")
    if allowed_use & ORDER_CAPABLE_TARGETS:
        problems.append("allowed_use includes order-capable targets")

    freshness_status = _text(freshness.get("status"))
    if freshness_status != "fresh":
        problems.append("freshness.status must be fresh")
    if not _text(freshness.get("as_of")):
        problems.append("freshness.as_of missing")
    if not _text(freshness.get("last_ingested_at")):
        problems.append("freshness.last_ingested_at missing")
    if _positive_int(freshness.get("freshness_sla_seconds")) <= 0:
        problems.append("freshness.freshness_sla_seconds must be positive")

    if pit.get("point_in_time") is not True:
        problems.append("pit.point_in_time=True not proven")
    if not _text(pit.get("event_time_field")):
        problems.append("pit.event_time_field missing")
    if not _text(pit.get("available_time_field")):
        problems.append("pit.available_time_field missing")
    if not _text(pit.get("source_watermark")):
        problems.append("pit.source_watermark missing")

    storage_dataset_ref = _text(storage.get("dataset_ref"))
    if storage.get("durable") is not True:
        problems.append("storage.durable=True not proven")
    if not _text(storage.get("backend")):
        problems.append("storage.backend missing")
    if not storage_dataset_ref:
        problems.append("storage.dataset_ref missing")
    elif storage_dataset_ref not in set(dataset.source_dataset_refs):
        problems.append("storage.dataset_ref must match a workflow source_dataset_ref")
    if not _text(storage.get("snapshot_ref")):
        problems.append("storage.snapshot_ref missing")
    if not _text(storage.get("path")):
        problems.append("storage.path missing")
    checksum = _text(storage.get("checksum"))
    if not checksum.startswith("sha256:"):
        problems.append("storage.checksum must be sha256-prefixed")

    for key in (
        "ingest_run_id",
        "normalization_run_id",
        "evidence_bundle_ref",
        "rate_limit_policy_ref",
    ):
        if not _text(audit.get(key)):
            problems.append(f"audit.{key} missing")

    execution_targets = set(_strings(controls.get("execution_targets")))
    if controls.get("no_order_route") is not True:
        problems.append("controls.no_order_route=True not proven")
    if execution_targets & ORDER_CAPABLE_TARGETS:
        problems.append("controls.execution_targets includes order-capable targets")

    if problems:
        raise QlibWorkflowError("Qlib production dataset proof failed: " + "; ".join(problems))

    return {
        "provider": {
            "name": provider_name,
            "source_class": source_class,
            "dataset_id": provider_dataset_id,
        },
        "entitlement": {
            "entitlement_ref": entitlement_ref,
            "entitlement_tags": entitlement_tags,
            "license_scope": license_scope,
            "allowed_use": sorted(allowed_use),
        },
        "freshness": {
            "status": freshness_status,
            "as_of": _text(freshness.get("as_of")),
            "last_ingested_at": _text(freshness.get("last_ingested_at")),
            "freshness_sla_seconds": _positive_int(freshness.get("freshness_sla_seconds")),
        },
        "pit": {
            "point_in_time": True,
            "event_time_field": _text(pit.get("event_time_field")),
            "available_time_field": _text(pit.get("available_time_field")),
            "source_watermark": _text(pit.get("source_watermark")),
        },
        "storage": {
            "backend": _text(storage.get("backend")),
            "dataset_ref": storage_dataset_ref,
            "snapshot_ref": _text(storage.get("snapshot_ref")),
            "path": _text(storage.get("path")),
            "checksum": checksum,
            "durable": True,
        },
        "audit": {
            "ingest_run_id": _text(audit.get("ingest_run_id")),
            "normalization_run_id": _text(audit.get("normalization_run_id")),
            "evidence_bundle_ref": _text(audit.get("evidence_bundle_ref")),
            "rate_limit_policy_ref": _text(audit.get("rate_limit_policy_ref")),
        },
        "controls": {
            "no_order_route": True,
            "execution_targets": sorted(execution_targets),
        },
    }


def build_production_activation_packet(
    result: QlibRunResult,
    dataset_proof: Mapping[str, Any],
    *,
    task_id: str = "P2-QLIB-PROD-DATA-ACTIVATION-001",
) -> dict[str, Any]:
    """Build a reviewable Qlib production-data activation handoff packet."""
    normalized_proof = validate_production_dataset_proof(
        dataset_proof,
        result.prepared_dataset,
    )
    backend = result.training_result.backend
    if backend not in {STUB_BACKEND, PRIMARY_BACKEND}:
        raise QlibWorkflowError(f"Unsupported Qlib activation backend: {backend}")

    candidate_packet = copy.deepcopy(result.candidate_packet)
    candidate_packet["production_dataset_proof"] = normalized_proof
    candidate_packet["gate_state"] = "candidate_handoff_ready"
    candidate_packet["artifact_state_review_scope"] = "draft_or_candidate_only"

    return {
        "packet_id": f"qlib-production-activation-{result.prepared_dataset.strategy_id}",
        "task_id": task_id,
        "created_at": utc_now(),
        "artifact_state": result.registry_entry["artifact_state"],
        "requested_artifact_state": result.candidate_packet["requested_artifact_state"],
        "deployment_stage": result.registry_entry["deployment_summary"]["current_stage"],
        "registry_write_authority": "registry_service_only",
        "order_route": "none",
        "backend_smoke": {
            "backend": backend,
            "real_backend_selected": backend == PRIMARY_BACKEND,
            "stub_backend_selected": backend == STUB_BACKEND,
            "run_id": result.training_result.run_id,
            "metrics": copy.deepcopy(result.training_result.metrics),
        },
        "dataset_floor_summary": {
            "num_instruments": result.prepared_dataset.num_instruments,
            "history_years": result.prepared_dataset.history_years,
            "min_instrument_history_years": result.prepared_dataset.min_instrument_history_years,
            "min_periods_per_instrument": result.prepared_dataset.min_periods_per_instrument,
            "data_frequency": result.prepared_dataset.data_frequency,
            "required_min_instruments": ACTIVATION_READY_MIN_INSTRUMENTS,
            "required_min_history_years": ACTIVATION_READY_MIN_HISTORY_YEARS,
            "required_min_daily_periods": ACTIVATION_READY_MIN_DAILY_PERIODS,
        },
        "production_dataset_proof": normalized_proof,
        "candidate_handoff": candidate_packet,
        "safety_assertions": {
            "no_registry_write": True,
            "no_order_route": True,
            "no_broker_session": True,
            "no_capital_binding": True,
            "deployment_stage_remains_none": (
                result.registry_entry["deployment_summary"]["current_stage"] == "none"
            ),
        },
    }


def persist_production_activation_packet(packet: Mapping[str, Any], output_dir: str | Path) -> str:
    """Persist the production activation packet as a review artifact."""
    base = Path(output_dir)
    base.mkdir(parents=True, exist_ok=True)
    packet_path = base / "production_activation_packet.json"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(packet_path)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strings(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value > 0:
        return int(value)
    return 0
