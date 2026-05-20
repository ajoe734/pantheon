"""Link CanaryOodaPacket rollback refs to EP5 rollback drill output."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from services.ooda.canary_packet_model import CanaryOodaPacket


DEFAULT_EP5_ROLLBACK_DRILL_OUTPUT_REF = "support/evidence/EP5-007-V2/rollback-drill.json"
EP5_ROLLBACK_DRILL_REF_PREFIX = "rollback-drill://ep5-007-v2/"


class CanaryRollbackDrillLinkageError(ValueError):
    """Raised when EP5 rollback drill output cannot back a canary packet ref."""


@dataclass(frozen=True)
class CanaryRollbackDrillLinkage:
    rollback_drill_ref: str
    source_ref: str
    evidence_id: str
    harness_id: str
    proof_packet_id: str
    runtime_binding_ref: str
    canary_runtime_ref: str
    deployment_plan_ref: str


def load_ep5_rollback_drill_output(
    path: str | Path = DEFAULT_EP5_ROLLBACK_DRILL_OUTPUT_REF,
) -> dict[str, Any]:
    output_path = Path(path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CanaryRollbackDrillLinkageError("EP5 rollback drill output must be a JSON object")
    return payload


def build_canary_rollback_drill_linkage(
    rollback_drill_output: Mapping[str, Any],
    *,
    source_ref: str = DEFAULT_EP5_ROLLBACK_DRILL_OUTPUT_REF,
) -> CanaryRollbackDrillLinkage:
    errors = validate_ep5_rollback_drill_output(rollback_drill_output)
    if errors:
        raise CanaryRollbackDrillLinkageError("; ".join(errors))

    evidence = _mapping(rollback_drill_output.get("rollback_drill_evidence"))
    proof_packet = _mapping(rollback_drill_output.get("proof_packet"))
    runtime = _mapping(proof_packet.get("runtime"))

    evidence_id = _text(evidence.get("evidence_id"))
    return CanaryRollbackDrillLinkage(
        rollback_drill_ref=f"{EP5_ROLLBACK_DRILL_REF_PREFIX}{evidence_id}",
        source_ref=source_ref,
        evidence_id=evidence_id,
        harness_id=_text(rollback_drill_output.get("harness_id")),
        proof_packet_id=_text(proof_packet.get("packet_id")),
        runtime_binding_ref=f"runtime-binding://{_text(runtime.get('runtime_binding_id'))}",
        canary_runtime_ref=f"runtime://{_text(runtime.get('runtime_id'))}",
        deployment_plan_ref=f"deployment-plan://{_text(runtime.get('deployment_plan_id'))}",
    )


def link_canary_packet_to_ep5_rollback_drill(
    packet: CanaryOodaPacket,
    rollback_drill_output: Mapping[str, Any],
    *,
    source_ref: str = DEFAULT_EP5_ROLLBACK_DRILL_OUTPUT_REF,
) -> CanaryRollbackDrillLinkage:
    linkage = build_canary_rollback_drill_linkage(
        rollback_drill_output,
        source_ref=source_ref,
    )
    packet.stages.act.rollback_drill_ref = linkage.rollback_drill_ref
    packet.assertions.rollback_drill_completed = True
    return linkage


def validate_canary_rollback_drill_linkage(
    packet: CanaryOodaPacket | Mapping[str, Any],
    rollback_drill_output: Mapping[str, Any],
    *,
    source_ref: str = DEFAULT_EP5_ROLLBACK_DRILL_OUTPUT_REF,
) -> list[str]:
    packet_obj = (
        packet
        if isinstance(packet, CanaryOodaPacket)
        else CanaryOodaPacket.from_dict(packet)
    )
    errors: list[str] = []

    output_errors = validate_ep5_rollback_drill_output(rollback_drill_output)
    if output_errors:
        errors.extend(f"ep5 rollback drill output: {error}" for error in output_errors)
    else:
        linkage = build_canary_rollback_drill_linkage(
            rollback_drill_output,
            source_ref=source_ref,
        )
        if packet_obj.act.rollback_drill_ref != linkage.rollback_drill_ref:
            errors.append(
                "stages.act.rollback_drill_ref must equal "
                f"{linkage.rollback_drill_ref}"
            )
        if packet_obj.act.runtime_binding_ref != linkage.runtime_binding_ref:
            errors.append(
                "stages.act.runtime_binding_ref must equal "
                f"{linkage.runtime_binding_ref}"
            )
        if packet_obj.act.canary_runtime_ref != linkage.canary_runtime_ref:
            errors.append(
                "stages.act.canary_runtime_ref must equal "
                f"{linkage.canary_runtime_ref}"
            )
        if packet_obj.decide.deployment_plan_ref != linkage.deployment_plan_ref:
            errors.append(
                "stages.decide.deployment_plan_ref must equal "
                f"{linkage.deployment_plan_ref}"
            )
        if packet_obj.assertions.rollback_drill_completed is not True:
            errors.append(
                "assertions.rollback_drill_completed must be true after EP5 rollback drill linkage"
            )

    errors.extend(f"canary packet: {error}" for error in packet_obj.validate())
    return errors


def validate_ep5_rollback_drill_output(
    rollback_drill_output: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    if not isinstance(rollback_drill_output, Mapping):
        return ["EP5 rollback drill output must be an object"]

    evidence = _mapping(rollback_drill_output.get("rollback_drill_evidence"))
    proof_packet = _mapping(rollback_drill_output.get("proof_packet"))
    runtime = _mapping(proof_packet.get("runtime"))
    proof = _mapping(proof_packet.get("proof"))
    result = _mapping(proof_packet.get("result"))
    runtime_response = _mapping(rollback_drill_output.get("runtime_manager_response"))
    old_binding = _mapping(runtime_response.get("old_binding"))
    new_binding = _mapping(runtime_response.get("new_binding"))
    position_lineage = _mapping(runtime_response.get("position_lineage"))
    drill_packet = _mapping(rollback_drill_output.get("drill_packet"))

    _require_text(rollback_drill_output, "harness_id", errors)
    if rollback_drill_output.get("status") != "passed":
        errors.append("status must be passed")
    if rollback_drill_output.get("rollback_drill_completed") is not True:
        errors.append("rollback_drill_completed must be true")
    if rollback_drill_output.get("live_capital_side_effects") is not False:
        errors.append("live_capital_side_effects must be false")

    if not evidence:
        errors.append("rollback_drill_evidence is required")
    evidence_id = _require_text(evidence, "rollback_drill_evidence.evidence_id", errors)
    current_binding_id = _require_text(
        evidence,
        "rollback_drill_evidence.current_binding_id",
        errors,
    )
    evidence_action_type = _require_text(
        evidence,
        "rollback_drill_evidence.action_type",
        errors,
    )
    if evidence.get("passed") is not True:
        errors.append("rollback_drill_evidence.passed must be true")
    if evidence.get("promotion_eligible") is not True:
        errors.append("rollback_drill_evidence.promotion_eligible must be true")
    if evidence.get("dry_run") is not True:
        errors.append("rollback_drill_evidence.dry_run must be true")
    if evidence.get("drill_stage") != "canary":
        errors.append("rollback_drill_evidence.drill_stage must be canary")
    _require_empty_list(
        evidence,
        "rollback_drill_evidence.blocking_reasons",
        errors,
    )
    _require_empty_list(evidence, "rollback_drill_evidence.errors", errors)

    expected_outcome = _mapping(evidence.get("expected_outcome"))
    if expected_outcome.get("side_effects_executed") != []:
        errors.append(
            "rollback_drill_evidence.expected_outcome.side_effects_executed must be empty"
        )
    safety_guards = evidence.get("safety_guards")
    if not isinstance(safety_guards, list) or "no_broker_api_call" not in safety_guards:
        errors.append("rollback_drill_evidence.safety_guards must include no_broker_api_call")
    if not isinstance(safety_guards, list) or "dry_run_only" not in safety_guards:
        errors.append("rollback_drill_evidence.safety_guards must include dry_run_only")

    if drill_packet.get("dry_run") is not True:
        errors.append("drill_packet.dry_run must be true")
    for field_name in ("side_effects_allowed", "dispatch_live", "call_broker_api"):
        if drill_packet.get(field_name) is not False:
            errors.append(f"drill_packet.{field_name} must be false")
    if drill_packet.get("drill_stage") != "canary":
        errors.append("drill_packet.drill_stage must be canary")

    proof_packet_id = _require_text(proof_packet, "proof_packet.packet_id", errors)
    if proof_packet.get("status") != "passed":
        errors.append("proof_packet.status must be passed")
    if result.get("pass") is not True:
        errors.append("proof_packet.result.pass must be true")
    if proof.get("rollback_drill_completed") is not True:
        errors.append("proof_packet.proof.rollback_drill_completed must be true")
    if proof.get("live_capital_side_effects") is not False:
        errors.append("proof_packet.proof.live_capital_side_effects must be false")
    runtime_binding_id = _require_text(
        runtime,
        "proof_packet.runtime.runtime_binding_id",
        errors,
    )
    _require_text(runtime, "proof_packet.runtime.runtime_id", errors)
    _require_text(runtime, "proof_packet.runtime.deployment_plan_id", errors)

    evidence_refs = result.get("evidence_refs")
    if not isinstance(evidence_refs, list):
        errors.append("proof_packet.result.evidence_refs must be a list")
    elif evidence_id and f"rollback-drill:{evidence_id}" not in evidence_refs:
        errors.append(
            "proof_packet.result.evidence_refs must include "
            f"rollback-drill:{evidence_id}"
        )

    if runtime_response.get("action_type") != evidence_action_type:
        errors.append("runtime_manager_response.action_type must match rollback drill evidence")
    old_binding_id = _require_text(
        old_binding,
        "runtime_manager_response.old_binding.binding_id",
        errors,
    )
    if old_binding.get("status") != "retired":
        errors.append("runtime_manager_response.old_binding.status must be retired")
    if new_binding.get("rollback_parent") != old_binding_id:
        errors.append("runtime_manager_response.new_binding.rollback_parent must match old binding")
    if new_binding.get("rollback_action_type") != evidence_action_type:
        errors.append(
            "runtime_manager_response.new_binding.rollback_action_type must match rollback drill evidence"
        )
    if current_binding_id and old_binding_id and current_binding_id != old_binding_id:
        errors.append("rollback drill current_binding_id must match retired old binding")
    if runtime_binding_id and current_binding_id and runtime_binding_id != current_binding_id:
        errors.append("proof runtime_binding_id must match rollback drill current_binding_id")

    opened_by_artifact_id = _text(position_lineage.get("opened_by_artifact_id"))
    if opened_by_artifact_id and opened_by_artifact_id != _text(old_binding.get("artifact_id")):
        errors.append("position_lineage.opened_by_artifact_id must match old binding artifact_id")
    if not proof_packet_id:
        errors.append("EP5 proof packet id is required for rollback drill linkage")

    return errors


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _require_text(mapping: Mapping[str, Any], path: str, errors: list[str]) -> str:
    key = path.rsplit(".", 1)[-1]
    value = _text(mapping.get(key))
    if not value:
        errors.append(f"{path} must be a non-empty string")
    return value


def _require_empty_list(mapping: Mapping[str, Any], path: str, errors: list[str]) -> None:
    key = path.rsplit(".", 1)[-1]
    value = mapping.get(key)
    if value != []:
        errors.append(f"{path} must be empty")


__all__ = [
    "DEFAULT_EP5_ROLLBACK_DRILL_OUTPUT_REF",
    "EP5_ROLLBACK_DRILL_REF_PREFIX",
    "CanaryRollbackDrillLinkage",
    "CanaryRollbackDrillLinkageError",
    "build_canary_rollback_drill_linkage",
    "link_canary_packet_to_ep5_rollback_drill",
    "load_ep5_rollback_drill_output",
    "validate_canary_rollback_drill_linkage",
    "validate_ep5_rollback_drill_output",
]
