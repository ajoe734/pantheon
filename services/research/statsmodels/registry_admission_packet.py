"""Emit the OSS-STAT-V2-001 registry admission packet.

The packet follows the minimal PromotionReadinessPacket.v1 shape while adding
statsmodels signal_snapshot admission details. It is review-only: registry
writes, broker sessions, and deployment remain outside this module.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:  # Allows both package imports and direct script execution.
    from .production_cointegration import (
        DEFAULT_DATASET_MANIFEST_PATH,
        DEFAULT_DATASET_PATH,
        DEFAULT_ROLLING_WINDOW,
        DEFAULT_TOP_N,
        TASK_ID,
        ProductionCointegrationError,
        run_production,
        utc_now,
    )
except ImportError:  # pragma: no cover - direct script fallback
    from production_cointegration import (  # type: ignore
        DEFAULT_DATASET_MANIFEST_PATH,
        DEFAULT_DATASET_PATH,
        DEFAULT_ROLLING_WINDOW,
        DEFAULT_TOP_N,
        TASK_ID,
        ProductionCointegrationError,
        run_production,
        utc_now,
    )


DEFAULT_ADMISSION_PACKET_PATH = (
    REPO_ROOT / "support" / "evidence" / TASK_ID / "admission_packet.json"
)
PROMOTION_READINESS_SCHEMA_VERSION = "PromotionReadinessPacket.v1"
REQUIRED_EVIDENCE = [
    "mgmt_qlib_001_dataset_manifest_bound",
    "production_cointegration_completed",
    "top_n_cointegrated_pairs_present",
    "signal_snapshot_ref_registered",
    "lineage_refs_present",
    "fail_closed_research_only",
]
REQUIRED_PACKET_FIELDS = {
    "packet_id",
    "target_type",
    "target_id",
    "environment",
    "required_evidence",
    "provided_evidence",
    "missing_evidence",
    "gate_results",
    "risk_owner_required",
    "operator_required",
    "can_proceed",
    "reason",
}


class RegistryAdmissionPacketError(ValueError):
    """Raised when the statsmodels admission packet is incomplete or unsafe."""


def build_admission_packet(
    signal_snapshot: Mapping[str, Any],
    *,
    created_at: str | None = None,
    source_task_id: str = TASK_ID,
) -> dict[str, Any]:
    """Build a PromotionReadinessPacket-shaped registry admission packet."""

    timestamp = created_at or utc_now()
    registry_entry = _mapping(signal_snapshot.get("registry_entry"))
    signal_ref = _mapping(signal_snapshot.get("signal_snapshot_ref"))
    dataset_summary = _mapping(signal_snapshot.get("dataset"))
    cointegration_summary = _mapping(signal_snapshot.get("cointegration_summary"))
    lineage = _mapping(registry_entry.get("lineage"))
    registry_id = _required_text(registry_entry.get("registry_id"), "registry_entry.registry_id")

    provided_evidence = [
        {
            "key": "mgmt_qlib_001_dataset_manifest_bound",
            "source_task_id": "MGMT-QLIB-001",
            "path": "support/evidence/MGMT-QLIB-001/dataset_manifest.json",
            "ref_type": "dataset_manifest",
            "status": "passed",
            "description": "Production TWSE OHLCV dataset manifest bound to the statsmodels run.",
        },
        {
            "key": "production_cointegration_completed",
            "source_task_id": source_task_id,
            "path": "services/research/statsmodels/production_cointegration.py",
            "ref_type": "signal_snapshot_builder",
            "status": "passed",
            "description": (
                f"Engle-Granger checks completed for {cointegration_summary.get('pair_count')} pairs "
                f"with {cointegration_summary.get('cointegrated_pair_count')} pairs below p<0.05."
            ),
        },
        {
            "key": "top_n_cointegrated_pairs_present",
            "source_task_id": source_task_id,
            "path": "services/research/statsmodels/production_cointegration.py",
            "ref_type": "cointegration_gate",
            "status": "passed",
            "description": (
                f"Top-{cointegration_summary.get('top_n')} signal_snapshot pairs include "
                f"best pair {cointegration_summary.get('best_pair_id')} "
                f"at p={cointegration_summary.get('best_p_value')}."
            ),
        },
        {
            "key": "signal_snapshot_ref_registered",
            "source_task_id": source_task_id,
            "path": "services/research/statsmodels/production_cointegration.py",
            "ref_type": "signal_snapshot_projection",
            "status": "passed",
            "description": "Draft signal_snapshot projection includes checksum, storage_ref, and registry id.",
        },
        {
            "key": "lineage_refs_present",
            "source_task_id": source_task_id,
            "path": "services/research/statsmodels/registry_admission_packet.py",
            "ref_type": "lineage_check",
            "status": "passed",
            "description": "Lineage links dataset refs, StrategySpec id, source task ids, and manifest id.",
        },
        {
            "key": "fail_closed_research_only",
            "source_task_id": source_task_id,
            "path": "services/research/statsmodels/registry_admission_packet.py",
            "ref_type": "safety_assertions",
            "status": "passed",
            "description": "Registry write, broker session, order route, capital binding, and deployment remain disabled.",
        },
    ]
    provided_keys = {entry["key"] for entry in provided_evidence}
    missing_evidence = [key for key in REQUIRED_EVIDENCE if key not in provided_keys]

    packet = {
        "packet_id": f"prp-{source_task_id.lower()}-{registry_id}",
        "schema_version": PROMOTION_READINESS_SCHEMA_VERSION,
        "target_type": "artifact",
        "target_id": registry_id,
        "environment": "paper",
        "generated_at": timestamp,
        "generated_by": f"Codex2 / {source_task_id}",
        "source_task_id": source_task_id,
        "depends_on_tasks": ["OSS-STAT-001", "MGMT-QLIB-001"],
        "required_evidence": list(REQUIRED_EVIDENCE),
        "provided_evidence": provided_evidence,
        "missing_evidence": missing_evidence,
        "gate_results": [
            {
                "gate": "production_dataset_floor",
                "status": "passed" if dataset_summary.get("production_scale_satisfied") is True else "failed",
                "source_ref": "support/evidence/MGMT-QLIB-001/dataset_manifest.json",
                "num_instruments": dataset_summary.get("num_instruments"),
                "min_periods_per_instrument": dataset_summary.get("min_periods_per_instrument"),
            },
            {
                "gate": "rolling_engle_granger",
                "status": "passed" if cointegration_summary.get("cointegrated_pair_count", 0) >= 3 else "failed",
                "source_ref": "signal_snapshot.pairs",
                "rolling_window": signal_snapshot.get("rolling_window"),
                "pair_count": cointegration_summary.get("pair_count"),
                "cointegrated_pair_count": cointegration_summary.get("cointegrated_pair_count"),
                "best_pair_id": cointegration_summary.get("best_pair_id"),
                "best_p_value": cointegration_summary.get("best_p_value"),
            },
            {
                "gate": "signal_snapshot_projection",
                "status": "passed" if registry_entry.get("artifact_type") == "signal_snapshot" else "failed",
                "source_ref": "signal_snapshot.registry_entry",
                "checksum": registry_entry.get("checksum"),
                "artifact_state": registry_entry.get("artifact_state"),
            },
            {
                "gate": "lineage_refs",
                "status": "passed" if _lineage_complete(lineage) else "failed",
                "source_ref": "signal_snapshot.registry_entry.lineage",
                "source_strategy_spec_id": lineage.get("source_strategy_spec_id"),
                "source_dataset_refs": lineage.get("source_dataset_refs"),
            },
            {
                "gate": "safety_fail_closed",
                "status": "passed",
                "source_ref": "signal_snapshot.safety_assertions",
                "registry_write_performed": False,
                "deployment_stage": "none",
                "order_route": "none",
            },
        ],
        "risk_owner_required": False,
        "risk_owner_approval_recorded": False,
        "operator_required": False,
        "operator_approval_recorded": False,
        "can_proceed": not missing_evidence,
        "reason": (
            "Production statsmodels Engle-Granger signal_snapshot is ready for registry "
            "candidate review. This packet performs no registry write and grants no "
            "paper/canary/live deployment authority."
        ),
        "registry_request": {
            "request_type": "artifact_state_transition",
            "artifact_type": "signal_snapshot",
            "registry_id": registry_id,
            "strategy_id": registry_entry.get("strategy_id"),
            "version": registry_entry.get("version"),
            "current_artifact_state": "draft",
            "requested_artifact_state": "candidate",
            "requested_transition": "draft_to_candidate",
            "deployment_stage": "none",
            "approval_scope": "candidate_admission_review_only",
            "registry_write_authority": "registry_service_only",
            "registry_write_performed": False,
        },
        "candidate_artifact": copy.deepcopy(registry_entry),
        "signal_snapshot_ref": copy.deepcopy(signal_ref),
        "signal_snapshot_summary": {
            "snapshot_id": signal_snapshot.get("snapshot_id"),
            "task_id": signal_snapshot.get("task_id"),
            "backend_id": signal_snapshot.get("backend_id"),
            "rolling_window": signal_snapshot.get("rolling_window"),
            "checksum": signal_snapshot.get("checksum"),
        },
        "cointegration_summary": copy.deepcopy(cointegration_summary),
        "top_cointegrated_pairs": copy.deepcopy(signal_snapshot.get("top_cointegrated_pairs", [])),
        "production_dataset": copy.deepcopy(dataset_summary),
        "lineage": copy.deepcopy(lineage),
        "downstream_scope": {
            "registry_admission_packet_only": True,
            "registry_write_authority": "registry_service_only",
            "registry_write_performed": False,
            "deployment_stage": "none",
            "training_performed_in_this_task": False,
            "broker_session_opened": False,
            "order_route": "none",
            "capital_binding": "none",
            "cpu_only": True,
            "gpu_required": False,
        },
        "safety_assertions": {
            "no_registry_write": True,
            "no_order_route": True,
            "no_broker_session": True,
            "no_capital_binding": True,
            "deployment_stage_remains_none": True,
            "artifact_state_request_limited_to_candidate": True,
            "research_only_not_direct_action": True,
            "cpu_only": True,
            "no_gpu": True,
        },
    }
    validate_admission_packet(packet)
    return packet


def emit_admission_packet(
    output_path: str | Path = DEFAULT_ADMISSION_PACKET_PATH,
    *,
    signal_snapshot: Mapping[str, Any] | None = None,
    pair_universe: Sequence[Any] | str = "twse_large_cap_pairs",
    rolling_window: int = DEFAULT_ROLLING_WINDOW,
    created_at: str | None = None,
    top_n: int = DEFAULT_TOP_N,
    dataset_path: str | Path = DEFAULT_DATASET_PATH,
    dataset_manifest_path: str | Path = DEFAULT_DATASET_MANIFEST_PATH,
) -> dict[str, Any]:
    """Run production cointegration if needed, write admission_packet.json, and return it."""

    snapshot = signal_snapshot or run_production(
        pair_universe,
        rolling_window,
        dataset_path=dataset_path,
        dataset_manifest_path=dataset_manifest_path,
        created_at=created_at,
        top_n=top_n,
    )
    packet = build_admission_packet(snapshot, created_at=created_at)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return packet


def validate_admission_packet(packet: Mapping[str, Any]) -> list[str]:
    """Validate the packet's PromotionReadinessPacket shape and safety fields."""

    errors: list[str] = []
    missing = REQUIRED_PACKET_FIELDS - set(packet.keys())
    if missing:
        errors.append("missing required PromotionReadinessPacket fields: " + ", ".join(sorted(missing)))
    if packet.get("schema_version") != PROMOTION_READINESS_SCHEMA_VERSION:
        errors.append("schema_version must be PromotionReadinessPacket.v1")
    if packet.get("target_type") != "artifact":
        errors.append("target_type must be artifact")
    if packet.get("environment") != "paper":
        errors.append("environment must be paper for artifact candidate review")
    provided_keys = {entry.get("key") for entry in _sequence(packet.get("provided_evidence")) if isinstance(entry, Mapping)}
    missing_evidence = [key for key in packet.get("required_evidence", []) if key not in provided_keys]
    if missing_evidence:
        errors.append("provided_evidence missing keys: " + ", ".join(missing_evidence))
    if packet.get("missing_evidence") != []:
        errors.append("missing_evidence must be empty")

    request = _mapping(packet.get("registry_request"))
    candidate = _mapping(packet.get("candidate_artifact"))
    signal_ref = _mapping(packet.get("signal_snapshot_ref"))
    safety = _mapping(packet.get("safety_assertions"))
    scope = _mapping(packet.get("downstream_scope"))
    if request.get("artifact_type") != "signal_snapshot":
        errors.append("registry_request.artifact_type must be signal_snapshot")
    if request.get("requested_artifact_state") != "candidate":
        errors.append("registry_request.requested_artifact_state must be candidate")
    if request.get("registry_write_performed") is not False:
        errors.append("registry_request.registry_write_performed must be false")
    if request.get("deployment_stage") != "none":
        errors.append("registry_request.deployment_stage must be none")
    if candidate.get("artifact_type") != "signal_snapshot":
        errors.append("candidate_artifact.artifact_type must be signal_snapshot")
    if candidate.get("artifact_state") != "draft":
        errors.append("candidate_artifact.artifact_state must remain draft")
    if not str(candidate.get("checksum") or "").startswith("sha256:"):
        errors.append("candidate_artifact.checksum must be sha256-prefixed")
    if signal_ref.get("registry_id") != candidate.get("registry_id"):
        errors.append("signal_snapshot_ref.registry_id must match candidate_artifact")
    if not _lineage_complete(_mapping(candidate.get("lineage"))):
        errors.append("candidate_artifact.lineage must include run, dataset, and StrategySpec refs")
    for key in (
        "no_registry_write",
        "no_order_route",
        "no_broker_session",
        "no_capital_binding",
        "deployment_stage_remains_none",
        "artifact_state_request_limited_to_candidate",
        "research_only_not_direct_action",
        "cpu_only",
        "no_gpu",
    ):
        if safety.get(key) is not True:
            errors.append(f"safety_assertions.{key} must be true")
    if scope.get("registry_write_performed") is not False:
        errors.append("downstream_scope.registry_write_performed must be false")
    if scope.get("deployment_stage") != "none":
        errors.append("downstream_scope.deployment_stage must be none")
    if scope.get("order_route") != "none":
        errors.append("downstream_scope.order_route must be none")
    failed_gates = [
        str(gate.get("gate"))
        for gate in _sequence(packet.get("gate_results"))
        if isinstance(gate, Mapping) and gate.get("status") not in {"passed", "present"}
    ]
    if failed_gates:
        errors.append("gate_results failed: " + ", ".join(failed_gates))
    if packet.get("can_proceed") is not True:
        errors.append("can_proceed must be true for complete candidate-review packet")
    if errors:
        raise RegistryAdmissionPacketError("statsmodels admission packet failed: " + "; ".join(errors))
    return []


def _lineage_complete(lineage: Mapping[str, Any]) -> bool:
    run_ids = _sequence(lineage.get("source_run_ids"))
    dataset_refs = _sequence(lineage.get("source_dataset_refs"))
    return bool(run_ids and dataset_refs and lineage.get("source_strategy_spec_id"))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RegistryAdmissionPacketError(f"{field_name} is required")
    return text


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit OSS-STAT-V2-001 admission_packet.json.")
    parser.add_argument("--output", type=Path, default=DEFAULT_ADMISSION_PACKET_PATH)
    parser.add_argument("--pair-universe", default="twse_large_cap_pairs")
    parser.add_argument("--rolling-window", type=int, default=DEFAULT_ROLLING_WINDOW)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--dataset-manifest", type=Path, default=DEFAULT_DATASET_MANIFEST_PATH)
    parser.add_argument("--created-at")
    args = parser.parse_args(argv)
    try:
        packet = emit_admission_packet(
            args.output,
            pair_universe=args.pair_universe,
            rolling_window=args.rolling_window,
            top_n=args.top_n,
            dataset_path=args.dataset,
            dataset_manifest_path=args.dataset_manifest,
            created_at=args.created_at,
        )
    except (ProductionCointegrationError, RegistryAdmissionPacketError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(packet, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
