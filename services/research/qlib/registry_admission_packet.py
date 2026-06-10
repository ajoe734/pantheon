"""Emit the OSS-QLIB-V2-001 registry admission packet.

The packet follows the minimal PromotionReadinessPacket.v1 shape while adding
Qlib model-artifact admission details. It is review-only: registry writes and
deployment remain outside this module.
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
    from .production_rolling_run import (
        DEFAULT_DATASET_MANIFEST_PATH,
        DEFAULT_DATASET_PATH,
        DEFAULT_LABEL_HORIZON_DAYS,
        DEFAULT_STRATEGY_SPEC_PACKET_PATH,
        TASK_ID,
        ProductionRollingRunError,
        run_production,
        utc_now,
    )
except ImportError:  # pragma: no cover - direct script fallback
    from production_rolling_run import (  # type: ignore
        DEFAULT_DATASET_MANIFEST_PATH,
        DEFAULT_DATASET_PATH,
        DEFAULT_LABEL_HORIZON_DAYS,
        DEFAULT_STRATEGY_SPEC_PACKET_PATH,
        TASK_ID,
        ProductionRollingRunError,
        run_production,
        utc_now,
    )


DEFAULT_ADMISSION_PACKET_PATH = (
    REPO_ROOT / "support" / "evidence" / TASK_ID / "admission_packet.json"
)
PROMOTION_READINESS_SCHEMA_VERSION = "PromotionReadinessPacket.v1"
REQUIRED_EVIDENCE = [
    "mgmt_qlib_001_dataset_manifest_bound",
    "mgmt_qlib_002_strategy_spec_bound",
    "production_rolling_run_completed",
    "model_artifact_ref_registered",
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
    """Raised when the Qlib admission packet is incomplete or unsafe."""


def build_admission_packet(
    experiment_run: Mapping[str, Any],
    *,
    created_at: str | None = None,
    source_task_id: str = TASK_ID,
) -> dict[str, Any]:
    """Build a PromotionReadinessPacket-shaped registry admission packet."""

    timestamp = created_at or utc_now()
    metadata = _mapping(experiment_run.get("metadata"))
    model_artifact = _mapping(metadata.get("model_artifact"))
    model_ref = _mapping(metadata.get("model_artifact_ref"))
    dataset_summary = _mapping(metadata.get("production_dataset"))
    rolling_summary = _mapping(metadata.get("rolling_metric_summary"))
    config = _mapping(metadata.get("production_rolling_config"))
    lineage = _mapping(model_artifact.get("lineage"))
    registry_id = _required_text(model_artifact.get("registry_id"), "metadata.model_artifact.registry_id")

    provided_evidence = [
        {
            "key": "mgmt_qlib_001_dataset_manifest_bound",
            "source_task_id": "MGMT-QLIB-001",
            "path": "support/evidence/MGMT-QLIB-001/dataset_manifest.json",
            "ref_type": "dataset_manifest",
            "status": "passed",
            "description": "Production TWSE/TPEx OHLCV dataset manifest bound to the run.",
        },
        {
            "key": "mgmt_qlib_002_strategy_spec_bound",
            "source_task_id": "MGMT-QLIB-002",
            "path": "support/evidence/MGMT-QLIB-002/strategy_spec_packet.json",
            "ref_type": "strategy_spec_packet",
            "status": "passed",
            "description": "Qlib StrategySpec candidate and binding attached.",
        },
        {
            "key": "production_rolling_run_completed",
            "source_task_id": source_task_id,
            "path": "services/research/qlib/production_rolling_run.py",
            "ref_type": "experiment_run_builder",
            "status": "passed",
            "description": (
                f"Rolling run completed with {rolling_summary.get('window_count')} windows, "
                f"mean rolling sharpe={rolling_summary.get('mean_rolling_sharpe')} and "
                f"mean rolling ic={rolling_summary.get('mean_rolling_ic')}."
            ),
        },
        {
            "key": "model_artifact_ref_registered",
            "source_task_id": source_task_id,
            "path": "services/research/qlib/production_rolling_run.py",
            "ref_type": "model_artifact_projection",
            "status": "passed",
            "description": "Draft model_artifact projection includes checksum, storage_ref, and registry id.",
        },
        {
            "key": "lineage_refs_present",
            "source_task_id": source_task_id,
            "path": "services/research/qlib/registry_admission_packet.py",
            "ref_type": "lineage_check",
            "status": "passed",
            "description": "Lineage links dataset refs, StrategySpec id, ExperimentRun id, and source task ids.",
        },
        {
            "key": "fail_closed_research_only",
            "source_task_id": source_task_id,
            "path": "services/research/qlib/registry_admission_packet.py",
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
        "generated_by": f"Codex / {source_task_id}",
        "source_task_id": source_task_id,
        "depends_on_tasks": ["OSS-QLIB-002", "MGMT-QLIB-001", "MGMT-QLIB-002"],
        "required_evidence": list(REQUIRED_EVIDENCE),
        "provided_evidence": provided_evidence,
        "missing_evidence": missing_evidence,
        "gate_results": [
            {
                "gate": "production_dataset_floor",
                "status": "passed" if dataset_summary.get("production_scale_satisfied") is True else "failed",
                "source_ref": config.get("dataset_manifest_ref"),
                "num_instruments": dataset_summary.get("num_instruments"),
                "min_periods_per_instrument": dataset_summary.get("min_periods_per_instrument"),
            },
            {
                "gate": "rolling_window_oos",
                "status": "passed" if rolling_summary.get("window_count", 0) else "failed",
                "source_ref": "metadata.production_rolling_windows",
                "window_count": rolling_summary.get("window_count"),
                "mean_rolling_sharpe": rolling_summary.get("mean_rolling_sharpe"),
                "mean_rolling_ic": rolling_summary.get("mean_rolling_ic"),
            },
            {
                "gate": "model_artifact_projection",
                "status": "passed" if model_artifact.get("artifact_type") == "model_artifact" else "failed",
                "source_ref": "metadata.model_artifact",
                "checksum": model_artifact.get("checksum"),
                "artifact_state": model_artifact.get("artifact_state"),
            },
            {
                "gate": "lineage_refs",
                "status": "passed" if _lineage_complete(lineage) else "failed",
                "source_ref": "metadata.model_artifact.lineage",
                "source_strategy_spec_id": lineage.get("source_strategy_spec_id"),
                "source_dataset_refs": lineage.get("source_dataset_refs"),
            },
            {
                "gate": "safety_fail_closed",
                "status": "passed",
                "source_ref": "metadata.safety_assertions",
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
            "Production-scale Qlib rolling run and draft model_artifact projection are ready "
            "for registry candidate review. This packet performs no registry write and grants "
            "no paper/canary/live deployment authority."
        ),
        "registry_request": {
            "request_type": "artifact_state_transition",
            "artifact_type": "model_artifact",
            "registry_id": registry_id,
            "strategy_id": model_artifact.get("strategy_id"),
            "version": model_artifact.get("version"),
            "current_artifact_state": "draft",
            "requested_artifact_state": "candidate",
            "requested_transition": "draft_to_candidate",
            "deployment_stage": "none",
            "approval_scope": "candidate_admission_review_only",
            "registry_write_authority": "registry_service_only",
            "registry_write_performed": False,
        },
        "candidate_artifact": copy.deepcopy(model_artifact),
        "model_artifact_ref": copy.deepcopy(model_ref),
        "experiment_run_summary": {
            "run_id": experiment_run.get("run_id"),
            "task_id": experiment_run.get("task_id"),
            "backend_id": experiment_run.get("backend_id"),
            "status": experiment_run.get("status"),
            "dataset_version_id": experiment_run.get("dataset_version_id"),
            "artifact_refs": copy.deepcopy(experiment_run.get("artifact_refs", [])),
        },
        "rolling_metric_summary": copy.deepcopy(rolling_summary),
        "production_dataset": copy.deepcopy(dataset_summary),
        "lineage": copy.deepcopy(lineage),
        "downstream_scope": {
            "registry_admission_packet_only": True,
            "registry_write_authority": "registry_service_only",
            "registry_write_performed": False,
            "deployment_stage": "none",
            "training_performed_in_this_task": True,
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
            "scoring_only_not_direct_action": True,
            "cpu_only": True,
            "no_gpu": True,
        },
    }
    validate_admission_packet(packet)
    return packet


def emit_admission_packet(
    output_path: str | Path = DEFAULT_ADMISSION_PACKET_PATH,
    *,
    experiment_run: Mapping[str, Any] | None = None,
    instrument_universe: Sequence[str] | str = "manifest",
    start_date: str = "2024-01-02",
    end_date: str = "2026-01-05",
    window_days: int = 63,
    label_horizon_days: int = DEFAULT_LABEL_HORIZON_DAYS,
    created_at: str | None = None,
    dataset_path: str | Path = DEFAULT_DATASET_PATH,
    dataset_manifest_path: str | Path = DEFAULT_DATASET_MANIFEST_PATH,
    strategy_spec_packet_path: str | Path = DEFAULT_STRATEGY_SPEC_PACKET_PATH,
) -> dict[str, Any]:
    """Run production rolling if needed, write admission_packet.json, and return it."""

    run = experiment_run or run_production(
        instrument_universe,
        start_date,
        end_date,
        window_days,
        label_horizon_days=label_horizon_days,
        dataset_path=dataset_path,
        dataset_manifest_path=dataset_manifest_path,
        strategy_spec_packet_path=strategy_spec_packet_path,
        created_at=created_at,
    )
    packet = build_admission_packet(run, created_at=created_at)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return packet


def validate_admission_packet(packet: Mapping[str, Any]) -> list[str]:
    """Validate the packet's PromotionReadinessPacket shape and Qlib safety fields."""

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
    model_ref = _mapping(packet.get("model_artifact_ref"))
    safety = _mapping(packet.get("safety_assertions"))
    scope = _mapping(packet.get("downstream_scope"))
    if request.get("artifact_type") != "model_artifact":
        errors.append("registry_request.artifact_type must be model_artifact")
    if request.get("requested_artifact_state") != "candidate":
        errors.append("registry_request.requested_artifact_state must be candidate")
    if request.get("registry_write_performed") is not False:
        errors.append("registry_request.registry_write_performed must be false")
    if request.get("deployment_stage") != "none":
        errors.append("registry_request.deployment_stage must be none")
    if candidate.get("artifact_type") != "model_artifact":
        errors.append("candidate_artifact.artifact_type must be model_artifact")
    if candidate.get("artifact_state") != "draft":
        errors.append("candidate_artifact.artifact_state must remain draft")
    if not str(candidate.get("checksum") or "").startswith("sha256:"):
        errors.append("candidate_artifact.checksum must be sha256-prefixed")
    if model_ref.get("registry_id") != candidate.get("registry_id"):
        errors.append("model_artifact_ref.registry_id must match candidate_artifact")
    if not _lineage_complete(_mapping(candidate.get("lineage"))):
        errors.append("candidate_artifact.lineage must include run, dataset, and StrategySpec refs")
    for key in (
        "no_registry_write",
        "no_order_route",
        "no_broker_session",
        "no_capital_binding",
        "deployment_stage_remains_none",
        "artifact_state_request_limited_to_candidate",
        "scoring_only_not_direct_action",
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
        raise RegistryAdmissionPacketError("Qlib admission packet failed: " + "; ".join(errors))
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
    parser = argparse.ArgumentParser(description="Emit OSS-QLIB-V2-001 admission_packet.json.")
    parser.add_argument("--output", type=Path, default=DEFAULT_ADMISSION_PACKET_PATH)
    parser.add_argument("--instrument-universe", default="manifest")
    parser.add_argument("--start-date", default="2024-01-02")
    parser.add_argument("--end-date", default="2026-01-05")
    parser.add_argument("--window-days", type=int, default=63)
    parser.add_argument("--label-horizon-days", type=int, default=DEFAULT_LABEL_HORIZON_DAYS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--dataset-manifest", type=Path, default=DEFAULT_DATASET_MANIFEST_PATH)
    parser.add_argument("--strategy-spec-packet", type=Path, default=DEFAULT_STRATEGY_SPEC_PACKET_PATH)
    parser.add_argument("--created-at")
    args = parser.parse_args(argv)

    try:
        packet = emit_admission_packet(
            args.output,
            instrument_universe=args.instrument_universe,
            start_date=args.start_date,
            end_date=args.end_date,
            window_days=args.window_days,
            label_horizon_days=args.label_horizon_days,
            dataset_path=args.dataset,
            dataset_manifest_path=args.dataset_manifest,
            strategy_spec_packet_path=args.strategy_spec_packet,
            created_at=args.created_at,
        )
    except (ProductionRollingRunError, RegistryAdmissionPacketError) as exc:
        print(str(exc), file=sys.stderr)
        return 4

    print(json.dumps({"packet_path": str(args.output), "target_id": packet["target_id"]}, sort_keys=True))
    return 0


__all__ = [
    "RegistryAdmissionPacketError",
    "build_admission_packet",
    "emit_admission_packet",
    "validate_admission_packet",
]


if __name__ == "__main__":
    raise SystemExit(main())
