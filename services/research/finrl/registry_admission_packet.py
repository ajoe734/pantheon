"""Registry admission packet generator for FinRL offline review artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


DEFAULT_OUTPUT_PATH = Path("support/evidence/OSS-FINRL-V2-001/admission_packet.json")


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _packet_from_result(
    result: Any,
    *,
    evidence_refs: Mapping[str, str] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    registry_entry = result.registry_entry
    candidate_packet = result.candidate_packet
    training_result = result.training_result
    packet = {
        "packet_id": f"finrl-admission-{training_result.run_id}",
        "target_type": "model_artifact",
        "environment": "offline_review",
        "timestamp": created_at or _iso_now(),
        "source_run_id": training_result.run_id,
        "evaluation_summary": dict(training_result.metrics),
        "artifact_ref": registry_entry["registry_id"],
        "current_artifact_state": registry_entry["artifact_state"],
        "requested_artifact_state": candidate_packet["requested_artifact_state"],
        "deployment_stage": registry_entry["deployment_summary"]["current_stage"],
        "gate_state": candidate_packet["gate_state"],
        "required_evidence": [
            "training_metrics_artifact",
            "model_checksum_verified",
            "rl_gate_closed",
            "no_live_execution",
        ],
        "evidence_refs": dict(evidence_refs or {}),
        "allowed_next_action": "offline_registry_review_only",
        "can_proceed": False,
        "governance_notes": [
            "Admission packet is review-only and cannot promote to paper/canary/live.",
            "FinRL RL gate remains closed; produced artifact_state=draft with deployment_stage=none.",
        ],
    }
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
        packet = {
            "packet_id": f"finrl-admission-{run_id}",
            "target_type": "model_artifact",
            "environment": "offline_review",
            "timestamp": created_at or _iso_now(),
            "source_run_id": run_id,
            "evaluation_summary": dict(evaluation_summary),
            "artifact_ref": artifact_ref,
            "current_artifact_state": "draft",
            "requested_artifact_state": "candidate",
            "deployment_stage": "none",
            "gate_state": "closed",
            "required_evidence": [
                "training_metrics_artifact",
                "model_checksum_verified",
                "rl_gate_closed",
                "no_live_execution",
            ],
            "evidence_refs": dict(evidence_refs or {}),
            "allowed_next_action": "offline_registry_review_only",
            "can_proceed": False,
            "governance_notes": [
                "Admission packet is review-only and cannot promote to paper/canary/live.",
                "FinRL RL gate remains closed; produced artifact_state=draft with deployment_stage=none.",
            ],
        }

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")
    return packet


if __name__ == "__main__":
    run_id = "simulated-run-id"
    summary = {"sharpe": 1.5, "annual_return": 0.2, "max_drawdown": 0.1}
    artifact_ref = "finrl-policy-twse-ppo-strategy-001-1.0.0"
    generate_admission_packet(run_id, summary, artifact_ref)
    print("Admission packet generated.")
