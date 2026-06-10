"""Candidate artifact admission gate for research activation.

The gate is intentionally side-effect free: it validates a candidate admission
packet before registry write authority may be exercised by the registry service.
It does not write to the registry, mutate deployment stage, or open any broker,
runtime, or capital route.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from .production_data_proof import (
    ALLOWED_ADAPTER_OUTPUT_TYPES,
    PRODUCTION_DATA_TIER,
    SCHEMA_VERSION as PRODUCTION_DATA_PROOF_SCHEMA_VERSION,
    ORDER_CAPABLE_TARGETS,
    ProductionDataProof,
    ValidationIssue,
)


SCHEMA_VERSION = "ResearchAdmissionGate.v1"
REQUIRED_SAFETY_ASSERTIONS = (
    "no_order_route",
    "no_broker_session",
    "no_capital_binding",
    "deployment_stage_remains_none",
    "artifact_state_request_limited_to_candidate",
)
REQUIRED_SCOPE_FALSE_FLAGS = (
    "registry_write_performed",
    "broker_session_opened",
)
REQUIRED_SCOPE_NONE_FIELDS = (
    "deployment_stage",
    "order_route",
    "capital_binding",
)
PASSED_GATE_STATUSES = frozenset({"passed", "present"})


class AdmissionGateError(ValueError):
    """Raised when a candidate admission packet fails the gate."""


@dataclass(frozen=True)
class AdmissionGateIssue:
    code: str
    path: str
    message: str

    @classmethod
    def from_validation_issue(cls, issue: ValidationIssue) -> "AdmissionGateIssue":
        return cls(code=issue.code, path=f"production_data_proof.{issue.path}", message=issue.message)

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class AdmissionGateResult:
    passed: bool
    target_id: str | None = None
    artifact_type: str | None = None
    proof_id: str | None = None
    production_data_tier: str | None = None
    errors: tuple[AdmissionGateIssue, ...] = ()
    warnings: tuple[AdmissionGateIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "passed": self.passed,
            "target_id": self.target_id,
            "artifact_type": self.artifact_type,
            "proof_id": self.proof_id,
            "production_data_tier": self.production_data_tier,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


def evaluate_candidate_admission(packet: Mapping[str, Any]) -> AdmissionGateResult:
    """Evaluate whether a research artifact packet may enter candidate review."""

    errors: list[AdmissionGateIssue] = []
    warnings: list[AdmissionGateIssue] = []
    add = errors.append

    proof = _load_production_data_proof(packet, add)
    if proof is not None:
        proof_result = proof.validate()
        errors.extend(AdmissionGateIssue.from_validation_issue(issue) for issue in proof_result.errors)
        warnings.extend(AdmissionGateIssue.from_validation_issue(issue) for issue in proof_result.warnings)

    target_type = _normalized_text(packet.get("target_type"))
    if target_type != "artifact":
        add(_issue("target_type_not_artifact", "target_type", "candidate admission target_type must be artifact"))

    target_id = _optional_text(packet.get("target_id"))
    if not target_id:
        add(_issue("missing_target_id", "target_id", "target_id is required"))

    environment = _normalized_text(packet.get("environment"))
    if environment != "paper":
        add(_issue("environment_not_paper", "environment", "candidate review admission must remain paper-scoped"))

    if packet.get("missing_evidence") not in (None, []):
        add(_issue("missing_evidence_not_empty", "missing_evidence", "missing_evidence must be empty"))
    if packet.get("can_proceed") is not True:
        add(_issue("can_proceed_not_true", "can_proceed", "can_proceed must be true before candidate admission"))

    _validate_gate_results(packet, add)

    registry_request = _mapping(packet.get("registry_request"))
    candidate = _mapping(packet.get("candidate_artifact"))
    safety = _mapping(packet.get("safety_assertions"))
    scope = _mapping(packet.get("downstream_scope"))

    _validate_registry_request(registry_request, target_id, add)
    _validate_candidate_artifact(candidate, registry_request, target_id, proof, add)
    _validate_safety_assertions(safety, scope, add)

    proof_id = proof.proof_id if proof is not None else None
    artifact_type = _normalized_text(
        candidate.get("artifact_type") or registry_request.get("artifact_type")
    ) or None
    return AdmissionGateResult(
        passed=not errors,
        target_id=target_id,
        artifact_type=artifact_type,
        proof_id=proof_id,
        production_data_tier=proof.activation_tier if proof is not None else None,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def require_candidate_admission(packet: Mapping[str, Any]) -> AdmissionGateResult:
    """Return a passed result or raise AdmissionGateError with issue codes."""

    result = evaluate_candidate_admission(packet)
    if not result.passed:
        codes = ", ".join(issue.code for issue in result.errors)
        raise AdmissionGateError(f"Research admission gate failed: {codes}")
    return result


def _load_production_data_proof(
    packet: Mapping[str, Any],
    add: Any,
) -> ProductionDataProof | None:
    proof_payload = _mapping(packet.get("production_data_proof"))
    if not proof_payload:
        candidate = _mapping(packet.get("candidate_artifact"))
        metadata = _mapping(candidate.get("metadata"))
        proof_payload = _mapping(metadata.get("production_data_proof"))
    if not proof_payload:
        add(_issue("missing_production_data_proof", "production_data_proof", "ProductionDataProof is required"))
        return None

    proof = ProductionDataProof.from_mapping(proof_payload)
    if proof.schema_version != PRODUCTION_DATA_PROOF_SCHEMA_VERSION:
        add(_issue("production_data_proof_schema_mismatch", "production_data_proof.schema_version", f"expected {PRODUCTION_DATA_PROOF_SCHEMA_VERSION}"))
    if proof.activation_tier != PRODUCTION_DATA_TIER:
        add(_issue("production_data_tier_not_r3", "production_data_proof.activation_tier", "production data proof must claim R3"))
    return proof


def _validate_gate_results(packet: Mapping[str, Any], add: Any) -> None:
    gate_results = _sequence_of_mappings(packet.get("gate_results"))
    failed = [
        _optional_text(gate.get("gate")) or f"gate_results[{index}]"
        for index, gate in enumerate(gate_results)
        if _normalized_text(gate.get("status")) not in PASSED_GATE_STATUSES
    ]
    if failed:
        add(_issue("packet_gate_results_failed", "gate_results", "packet gate_results must all pass: " + ", ".join(failed)))


def _validate_registry_request(
    request: Mapping[str, Any],
    target_id: str | None,
    add: Any,
) -> None:
    if not request:
        add(_issue("missing_registry_request", "registry_request", "registry_request is required"))
        return

    request_target_id = _optional_text(request.get("registry_id") or request.get("target_id"))
    if target_id and request_target_id and request_target_id != target_id:
        add(_issue("registry_request_target_mismatch", "registry_request.registry_id", "registry_request target must match packet target_id"))

    if _normalized_text(request.get("current_artifact_state")) not in {"", "draft"}:
        add(_issue("current_state_not_draft", "registry_request.current_artifact_state", "current artifact state must be draft"))
    if _normalized_text(request.get("requested_artifact_state")) != "candidate":
        add(_issue("requested_state_not_candidate", "registry_request.requested_artifact_state", "requested artifact state must be candidate"))
    if _normalized_text(request.get("requested_transition")) not in {"", "draft_to_candidate"}:
        add(_issue("transition_not_draft_to_candidate", "registry_request.requested_transition", "requested transition must be draft_to_candidate"))
    if _normalized_text(request.get("deployment_stage")) != "none":
        add(_issue("deployment_stage_not_none", "registry_request.deployment_stage", "candidate admission cannot set deployment stage"))
    if request.get("registry_write_performed") is not False:
        add(_issue("registry_write_already_performed", "registry_request.registry_write_performed", "admission packet must be validated before registry write"))
    if _normalized_text(request.get("registry_write_authority")) not in {"", "registry_service_only"}:
        add(_issue("registry_write_authority_not_registry_service", "registry_request.registry_write_authority", "registry writes must remain registry_service_only"))


def _validate_candidate_artifact(
    candidate: Mapping[str, Any],
    request: Mapping[str, Any],
    target_id: str | None,
    proof: ProductionDataProof | None,
    add: Any,
) -> None:
    if not candidate:
        add(_issue("missing_candidate_artifact", "candidate_artifact", "candidate_artifact is required"))
        return

    registry_id = _optional_text(candidate.get("registry_id") or candidate.get("target_id"))
    if target_id and registry_id and registry_id != target_id:
        add(_issue("candidate_target_mismatch", "candidate_artifact.registry_id", "candidate artifact must match packet target_id"))

    artifact_type = _normalized_text(candidate.get("artifact_type"))
    request_artifact_type = _normalized_text(request.get("artifact_type"))
    if not artifact_type:
        add(_issue("missing_candidate_artifact_type", "candidate_artifact.artifact_type", "candidate artifact_type is required"))
    elif artifact_type not in ALLOWED_ADAPTER_OUTPUT_TYPES:
        add(_issue("candidate_artifact_type_not_allowed", "candidate_artifact.artifact_type", f"unsupported candidate artifact type: {artifact_type}"))
    if request_artifact_type and artifact_type and request_artifact_type != artifact_type:
        add(_issue("request_candidate_artifact_type_mismatch", "registry_request.artifact_type", "registry_request artifact_type must match candidate artifact"))

    if _normalized_text(candidate.get("artifact_state")) != "draft":
        add(_issue("candidate_state_not_draft", "candidate_artifact.artifact_state", "candidate artifact must remain draft before admission"))
    if not str(candidate.get("checksum") or "").startswith("sha256:"):
        add(_issue("candidate_checksum_not_sha256", "candidate_artifact.checksum", "candidate artifact checksum must be sha256-prefixed"))

    lineage = _mapping(candidate.get("lineage"))
    _validate_lineage(lineage, proof, add)
    if proof is not None and artifact_type and artifact_type not in set(proof.no_order_route.produced_artifact_types):
        add(_issue("candidate_type_not_in_proof_outputs", "candidate_artifact.artifact_type", "candidate artifact_type must be covered by ProductionDataProof no_order_route output types"))


def _validate_lineage(
    lineage: Mapping[str, Any],
    proof: ProductionDataProof | None,
    add: Any,
) -> None:
    if not lineage:
        add(_issue("missing_candidate_lineage", "candidate_artifact.lineage", "candidate artifact lineage is required"))
        return
    run_ids = _strings(lineage.get("source_run_ids"))
    dataset_refs = _strings(lineage.get("source_dataset_refs"))
    strategy_spec_id = _optional_text(lineage.get("source_strategy_spec_id"))
    if not run_ids:
        add(_issue("missing_lineage_source_run", "candidate_artifact.lineage.source_run_ids", "lineage must include source run ids"))
    if not dataset_refs:
        add(_issue("missing_lineage_dataset_refs", "candidate_artifact.lineage.source_dataset_refs", "lineage must include source dataset refs"))
    if not strategy_spec_id:
        add(_issue("missing_lineage_strategy_spec", "candidate_artifact.lineage.source_strategy_spec_id", "lineage must include source StrategySpec id"))
    if proof is not None and dataset_refs:
        proof_refs = set(proof.source_dataset_refs)
        if proof.storage.dataset_ref:
            proof_refs.add(proof.storage.dataset_ref)
        if not set(dataset_refs) & proof_refs:
            add(_issue("lineage_dataset_not_in_proof", "candidate_artifact.lineage.source_dataset_refs", "lineage dataset refs must match ProductionDataProof source refs"))


def _validate_safety_assertions(
    safety: Mapping[str, Any],
    scope: Mapping[str, Any],
    add: Any,
) -> None:
    for key in REQUIRED_SAFETY_ASSERTIONS:
        if safety.get(key) is not True:
            add(_issue(f"{key}_not_asserted", f"safety_assertions.{key}", f"safety_assertions.{key} must be true"))
    for key in REQUIRED_SCOPE_FALSE_FLAGS:
        if scope.get(key) is not False:
            add(_issue(f"{key}_not_false", f"downstream_scope.{key}", f"downstream_scope.{key} must be false"))
    for key in REQUIRED_SCOPE_NONE_FIELDS:
        if _normalized_text(scope.get(key)) != "none":
            add(_issue(f"{key}_not_none", f"downstream_scope.{key}", f"downstream_scope.{key} must be none"))

    execution_targets = set(_normalized_tokens(scope.get("execution_targets")))
    forbidden = sorted(execution_targets & ORDER_CAPABLE_TARGETS)
    if forbidden:
        add(_issue("order_capable_downstream_target", "downstream_scope.execution_targets", "order-capable downstream target is forbidden: " + ", ".join(forbidden)))


def _issue(code: str, path: str, message: str) -> AdmissionGateIssue:
    return AdmissionGateIssue(code=code, path=path, message=message)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _text(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return value.strip() if isinstance(value, str) else ""


def _normalized_text(value: Any) -> str:
    return _normalize_token(_text(value))


def _strings(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _normalized_tokens(value: Any) -> list[str]:
    return [_normalize_token(item) for item in _strings(value)]


def _normalize_token(value: str) -> str:
    text = value.strip().lower()
    compact = text.replace("-", "").replace("_", "").replace(" ", "")
    if compact in {"w&b", "wandb", "weights&biases", "weightsandbiases"}:
        return "wandb"
    return text.replace("-", "_").replace(" ", "_")


def _sequence_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


__all__ = [
    "AdmissionGateError",
    "AdmissionGateIssue",
    "AdmissionGateResult",
    "REQUIRED_SAFETY_ASSERTIONS",
    "SCHEMA_VERSION",
    "evaluate_candidate_admission",
    "require_candidate_admission",
]
