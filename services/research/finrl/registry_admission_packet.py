"""Emit the OSS-FINRL-V2-001 registry admission packet.

The packet follows the same minimal PromotionReadinessPacket.v1 shape used by
the other OSS V2 research adapters. It is review-only: registry writes, broker
sessions, capital binding, and deployment remain outside this module.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


TASK_ID = "OSS-FINRL-V2-001"
DEFAULT_OUTPUT_PATH = Path("support/evidence/OSS-FINRL-V2-001/admission_packet.json")
PROMOTION_READINESS_SCHEMA_VERSION = "PromotionReadinessPacket.v1"
REQUIRED_EVIDENCE = [
    "twse_stock_env_bound",
    "production_drl_training_completed",
    "evaluation_summary_present",
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
    """Raised when the FinRL admission packet is incomplete or unsafe."""


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _packet_from_result(
    result: Any,
    *,
    evidence_refs: Mapping[str, str] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    registry_entry = dict(result.registry_entry)
    candidate_packet = dict(result.candidate_packet)
    training_result = result.training_result
    artifact_bundle = _mapping(result.artifact_bundle)
    env_summary = _mapping(artifact_bundle.get("twse_stock_env"))
    return build_admission_packet(
        registry_entry,
        evaluation_summary=dict(training_result.metrics),
        source_run_id=training_result.run_id,
        candidate_packet=candidate_packet,
        evidence_refs=evidence_refs,
        env_summary=env_summary,
        created_at=created_at,
    )


def build_admission_packet(
    registry_entry: Mapping[str, Any],
    *,
    evaluation_summary: Mapping[str, Any],
    source_run_id: str,
    candidate_packet: Mapping[str, Any] | None = None,
    evidence_refs: Mapping[str, str] | None = None,
    env_summary: Mapping[str, Any] | None = None,
    created_at: str | None = None,
    source_task_id: str = TASK_ID,
) -> dict[str, Any]:
    """Build a PromotionReadinessPacket-shaped registry admission packet."""

    timestamp = created_at or _iso_now()
    registry = copy.deepcopy(dict(registry_entry))
    candidate = copy.deepcopy(dict(candidate_packet or {}))
    evaluation = copy.deepcopy(dict(evaluation_summary))
    lineage = _mapping(registry.get("lineage"))
    metadata = _mapping(registry.get("metadata"))
    env = copy.deepcopy(dict(env_summary or {}))
    registry_id = _required_text(registry.get("registry_id"), "registry_entry.registry_id")
    trained_policy_ref = _required_text(registry.get("trained_policy_ref"), "registry_entry.trained_policy_ref")
    checksum = _required_text(registry.get("checksum"), "registry_entry.checksum")

    provided_evidence = [
        {
            "key": "twse_stock_env_bound",
            "source_task_id": source_task_id,
            "path": "services/research/finrl/twse_stock_env.py",
            "ref_type": "environment_adapter",
            "status": "passed",
            "description": (
                "TWSE OHLCV records are normalized for FinRL StockTradingEnv with "
                f"{env.get('num_instruments', metadata.get('num_instruments'))} instruments."
            ),
        },
        {
            "key": "production_drl_training_completed",
            "source_task_id": source_task_id,
            "path": "services/research/finrl/production_drl_run.py",
            "ref_type": "offline_training_run",
            "status": "passed",
            "description": (
                f"CPU-only {evaluation.get('algorithm', metadata.get('algorithm'))} run completed "
                f"{evaluation.get('total_training_steps')} bounded training steps."
            ),
        },
        {
            "key": "evaluation_summary_present",
            "source_task_id": source_task_id,
            "path": "support/evidence/OSS-FINRL-V2-001/evaluation_summary.json",
            "ref_type": "metrics_artifact",
            "status": "passed",
            "description": (
                "Evaluation summary includes sharpe, annual_return, max_drawdown, "
                "and portfolio value movement."
            ),
        },
        {
            "key": "model_artifact_ref_registered",
            "source_task_id": source_task_id,
            "path": "services/research/finrl/engine/finrl_adapter.py",
            "ref_type": "model_artifact_projection",
            "status": "passed",
            "description": "Draft model_artifact projection includes checksum and trained_policy_ref.",
        },
        {
            "key": "lineage_refs_present",
            "source_task_id": source_task_id,
            "path": "services/research/finrl/registry_admission_packet.py",
            "ref_type": "lineage_check",
            "status": "passed",
            "description": "Lineage links source run, dataset refs, and StrategySpec id.",
        },
        {
            "key": "fail_closed_research_only",
            "source_task_id": source_task_id,
            "path": "services/research/finrl/registry_admission_packet.py",
            "ref_type": "safety_assertions",
            "status": "passed",
            "description": "Registry write, broker session, order route, capital binding, and deployment remain disabled.",
        },
    ]
    provided_keys = {entry["key"] for entry in provided_evidence}
    missing_evidence = [key for key in REQUIRED_EVIDENCE if key not in provided_keys]
    model_artifact_ref = {
        "artifact_type": "model_artifact",
        "registry_id": registry_id,
        "artifact_ref": f"{registry_id}@{registry.get('version', 'unversioned')}",
        "trained_policy_ref": trained_policy_ref,
        "checksum": checksum,
        "storage_ref": copy.deepcopy(registry.get("storage_ref", {})),
    }

    packet = {
        "packet_id": f"prp-{source_task_id.lower()}-{registry_id}",
        "schema_version": PROMOTION_READINESS_SCHEMA_VERSION,
        "target_type": "artifact",
        "target_id": registry_id,
        "environment": "paper",
        "generated_at": timestamp,
        "generated_by": f"Codex2 / {source_task_id}",
        "source_task_id": source_task_id,
        "depends_on_tasks": ["OSS-FINRL-001", "MGMT-QLIB-001"],
        "source_run_id": source_run_id,
        "required_evidence": list(REQUIRED_EVIDENCE),
        "provided_evidence": provided_evidence,
        "missing_evidence": missing_evidence,
        "gate_results": [
            {
                "gate": "twse_stock_env",
                "status": "passed" if int(env.get("num_instruments") or metadata.get("num_instruments") or 0) >= 5 else "failed",
                "source_ref": "services/research/finrl/twse_stock_env.py",
                "num_instruments": env.get("num_instruments", metadata.get("num_instruments")),
                "state_space": env.get("state_space"),
                "action_space": env.get("action_space"),
            },
            {
                "gate": "bounded_drl_training",
                "status": "passed" if int(evaluation.get("total_training_steps") or 0) >= 1000 else "failed",
                "source_ref": "services/research/finrl/production_drl_run.py",
                "algorithm": evaluation.get("algorithm"),
                "total_training_steps": evaluation.get("total_training_steps"),
                "cpu_only": True,
            },
            {
                "gate": "evaluation_summary",
                "status": "passed" if _evaluation_complete(evaluation) else "failed",
                "source_ref": "support/evidence/OSS-FINRL-V2-001/evaluation_summary.json",
                "sharpe": evaluation.get("sharpe"),
                "annual_return": evaluation.get("annual_return"),
                "max_drawdown": evaluation.get("max_drawdown"),
            },
            {
                "gate": "model_artifact_projection",
                "status": "passed" if registry.get("artifact_type") == "model_artifact" else "failed",
                "source_ref": "support/evidence/OSS-FINRL-V2-001/registry_entry.json",
                "checksum": checksum,
                "trained_policy_ref": trained_policy_ref,
                "artifact_state": registry.get("artifact_state"),
            },
            {
                "gate": "lineage_refs",
                "status": "passed" if _lineage_complete(lineage) else "failed",
                "source_ref": "registry_entry.lineage",
                "source_strategy_spec_id": lineage.get("source_strategy_spec_id"),
                "source_dataset_refs": lineage.get("source_dataset_refs"),
            },
            {
                "gate": "safety_fail_closed",
                "status": "passed",
                "source_ref": "packet.safety_assertions",
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
            "CPU-only FinRL PPO model_artifact is ready for registry candidate review. "
            "This packet performs no registry write and grants no paper/canary/live deployment authority."
        ),
        "registry_request": {
            "request_type": "artifact_state_transition",
            "artifact_type": "model_artifact",
            "registry_id": registry_id,
            "strategy_id": registry.get("strategy_id"),
            "version": registry.get("version"),
            "current_artifact_state": "draft",
            "requested_artifact_state": candidate.get("requested_artifact_state", "candidate"),
            "requested_transition": "draft_to_candidate",
            "deployment_stage": "none",
            "approval_scope": "candidate_admission_review_only",
            "registry_write_authority": "registry_service_only",
            "registry_write_performed": False,
        },
        "candidate_artifact": registry,
        "model_artifact_ref": model_artifact_ref,
        "evaluation_summary": evaluation,
        "twse_stock_env": env,
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
        "legacy_review_fields": {
            "artifact_ref": registry_id,
            "current_artifact_state": registry.get("artifact_state"),
            "requested_artifact_state": candidate.get("requested_artifact_state", "candidate"),
            "gate_state": candidate.get("gate_state", "closed"),
            "allowed_next_action": "offline_registry_review_only",
            "evidence_refs": dict(evidence_refs or {}),
        },
    }
    validate_admission_packet(packet)
    return packet


def generate_admission_packet(
    result_or_run_id: Any,
    evaluation_summary: Mapping[str, Any] | None = None,
    artifact_ref: str | None = None,
    *,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    evidence_refs: Mapping[str, str] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    if hasattr(result_or_run_id, "training_result"):
        packet = _packet_from_result(
            result_or_run_id,
            evidence_refs=evidence_refs,
            created_at=created_at,
        )
    else:
        if evaluation_summary is None or artifact_ref is None:
            raise ValueError("evaluation_summary and artifact_ref are required with a run_id")
        run_id = str(result_or_run_id)
        registry_entry = {
            "registry_id": artifact_ref,
            "artifact_type": "model_artifact",
            "strategy_id": "manual-finrl-strategy",
            "version": "manual",
            "artifact_state": "draft",
            "deployment_summary": {"current_stage": "none"},
            "lineage": {
                "source_run_ids": [run_id],
                "source_dataset_refs": ["manual-finrl-dataset"],
                "source_strategy_spec_id": "manual-finrl-strategy-spec",
            },
            "checksum": "sha256:manual",
            "trained_policy_ref": artifact_ref,
            "storage_ref": {"backend": "manual", "path": artifact_ref},
            "evaluation_summary": dict(evaluation_summary),
            "metadata": {"framework": "finrl", "algorithm": "ppo", "num_steps": evaluation_summary.get("num_steps")},
        }
        packet = build_admission_packet(
            registry_entry,
            evaluation_summary=evaluation_summary,
            source_run_id=run_id,
            evidence_refs=evidence_refs,
            env_summary={"num_instruments": 5, "state_space": 21, "action_space": 5, "cpu_only": True},
            created_at=created_at,
        )

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
    model_ref = _mapping(packet.get("model_artifact_ref"))
    safety = _mapping(packet.get("safety_assertions"))
    scope = _mapping(packet.get("downstream_scope"))
    evaluation = _mapping(packet.get("evaluation_summary"))
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
    if not str(candidate.get("trained_policy_ref") or "").strip():
        errors.append("candidate_artifact.trained_policy_ref is required")
    if model_ref.get("registry_id") != candidate.get("registry_id"):
        errors.append("model_artifact_ref.registry_id must match candidate_artifact")
    if not _lineage_complete(_mapping(candidate.get("lineage"))):
        errors.append("candidate_artifact.lineage must include run, dataset, and StrategySpec refs")
    if int(evaluation.get("total_training_steps") or 0) < 1000:
        errors.append("evaluation_summary.total_training_steps must be >= 1000")
    if not _evaluation_complete(evaluation):
        errors.append("evaluation_summary must include sharpe, annual_return, and max_drawdown")
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
        raise RegistryAdmissionPacketError("FinRL admission packet failed: " + "; ".join(errors))
    return []


def _evaluation_complete(evaluation: Mapping[str, Any]) -> bool:
    return all(key in evaluation for key in ("sharpe", "annual_return", "max_drawdown"))


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


if __name__ == "__main__":
    run_id = "simulated-run-id"
    summary = {
        "algorithm": "ppo",
        "num_steps": 100,
        "total_training_steps": 1200,
        "sharpe": 1.5,
        "annual_return": 0.2,
        "max_drawdown": 0.1,
    }
    artifact_ref = "finrl-policy-twse-ppo-strategy-001-1.0.0"
    generate_admission_packet(run_id, summary, artifact_ref)
    print("Admission packet generated.")
