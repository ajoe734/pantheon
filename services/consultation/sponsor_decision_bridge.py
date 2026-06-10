from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Mapping


class SponsorDecisionBridgeError(ValueError):
    """Raised when a sponsor decision cannot be mapped to governance proposal payloads."""


APPROVAL_DECISION_TYPES = {"approval", "approval_decision"}
EVOLUTION_DECISION_TYPES = {"evolution", "evolution_decision"}

APPROVAL_TARGET_TYPES = {
    "registry_entry",
    "strategy_spec",
    "model_artifact",
    "allocation_policy",
    "persona_capital_binding",
    "evolution_proposal",
}
APPROVAL_RISK_LEVELS = {"low", "medium", "high", "critical"}

EVOLUTION_TARGET_TYPES = {
    "strategy_spec",
    "alpha_template",
    "candidate_artifact",
    "allocation_policy_artifact",
    "persona",
    "persona_capital_binding",
    "capital_pool",
}
EVOLUTION_RISK_LEVELS = {"low", "medium", "high"}
EVOLUTION_TARGET_STAGES = {"paper", "canary", "live", "frozen"}

LOW_RISK_EVOLUTION_ACTIONS = {
    "observe",
    "revalidate",
    "retrain",
    "require_more_data",
    "flag_for_review",
}
MEDIUM_RISK_EVOLUTION_ACTIONS = {
    "reduce_budget",
    "tighten_risk_policy",
    "mutate_persona_route_policy",
    "mutate_consult_policy",
}
HIGH_RISK_EVOLUTION_ACTIONS = {
    "retire",
    "split_persona",
    "merge_persona",
    "remove_live_owner_role",
    "restrict_pool_eligibility",
    "force_risk_off",
    "revive",
}

GOVERNANCE_EVIDENCE_REF_TYPES = {
    "evaluator_result",
    "critic_finding",
    "drift_report",
    "telemetry_summary",
    "audit_log_entry",
    "manual_review_ticket",
    "committee_memo",
    "service_handoff",
}

CONSULTATION_GATE_TARGET_TYPES = {"allocation_policy"}
CONSULTATION_GATE_RISK_LEVELS = {"high", "critical"}


@dataclass(frozen=True)
class SponsorDecision:
    decision_id: str
    type: str
    sponsor_persona_id: str
    target_type: str
    target_id: str
    target_version: str
    rationale: str
    sponsor_decision: str | None = None
    risk_level: str | None = None
    evidence_refs: list[Any] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    committee_id: str | None = None
    rationale_ref: str | None = None
    trace_id: str | None = None
    handoff_id: str | None = None
    capital_pool_id: str | None = None
    persona_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    action_type: str | None = None
    target_stage: str | None = None
    threshold_snapshots: list[Mapping[str, Any]] = field(default_factory=list)
    linked_incident_id: str | None = None
    linked_postmortem_id: str | None = None


@dataclass(frozen=True)
class ApprovalDecisionProposal:
    proposal_type: str
    decision_id: str
    target_type: str
    target_id: str
    target_version: str
    decision_state: str
    risk_level: str
    rationale: str | None
    evidence_refs: list[dict[str, Any]]
    capital_pool_id: str | None
    persona_id: str | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvolutionDecisionProposal:
    proposal_type: str
    decision_id: str
    target_type: str
    target_id: str
    target_version: str
    action_type: str
    decision_state: str
    risk_level: str
    created_by_role: str
    created_by_id: str
    rationale: str
    evidence_refs: list[dict[str, Any]]
    threshold_snapshots: list[dict[str, Any]]
    target_stage: str | None
    persona_id: str | None
    capital_pool_id: str | None
    linked_incident_id: str | None
    linked_postmortem_id: str | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


GovernanceProposal = ApprovalDecisionProposal | EvolutionDecisionProposal


def bridge(sponsor_decision: SponsorDecision | Mapping[str, Any] | Any) -> GovernanceProposal:
    """Map a committee sponsor decision to a proposed governance action payload.

    This function is deliberately side-effect free: it only validates and returns
    a proposal object for the appropriate downstream governance service.
    """
    payload = _to_payload(sponsor_decision)
    decision_type = _required_lower(payload, "type", "decision_type")

    if decision_type in APPROVAL_DECISION_TYPES:
        return _approval_proposal(payload, decision_type)
    if decision_type in EVOLUTION_DECISION_TYPES:
        return _evolution_proposal(payload, decision_type)

    supported = sorted(APPROVAL_DECISION_TYPES | EVOLUTION_DECISION_TYPES)
    raise SponsorDecisionBridgeError(
        f"sponsor decision type must be one of {supported}, got {decision_type!r}"
    )


def _approval_proposal(payload: Mapping[str, Any], decision_type: str) -> ApprovalDecisionProposal:
    sponsor_persona_id = _required_str(payload, "sponsor_persona_id", "sponsorPersonaId")
    target_type = _required_lower(payload, "target_type", "targetType")
    _ensure_member("target_type", target_type, APPROVAL_TARGET_TYPES)

    risk_level = _optional_lower(payload, "risk_level", "riskLevel") or "medium"
    _ensure_member("risk_level", risk_level, APPROVAL_RISK_LEVELS)

    source_decision_id = _required_str(payload, "decision_id", "id", "source_decision_id")
    metadata = _base_metadata(payload, decision_type, source_decision_id, sponsor_persona_id)
    metadata["recommended_decision"] = _recommended_decision(payload)

    conditions = _string_list(_value(payload, "conditions") or [])
    if conditions:
        metadata["conditions"] = conditions

    evidence = _evidence_refs(payload)

    if target_type in CONSULTATION_GATE_TARGET_TYPES and risk_level in CONSULTATION_GATE_RISK_LEVELS:
        committee_id = _optional_str(payload, "committee_id", "committeeId")
        handoff_id = _optional_str(payload, "handoff_id", "handoffId")
        if not committee_id:
            raise SponsorDecisionBridgeError(
                "committee_id is required for high-risk allocation_policy approval proposal"
            )
        if not handoff_id:
            raise SponsorDecisionBridgeError(
                "handoff_id is required for high-risk allocation_policy approval proposal"
            )
        existing_types = {r.get("ref_type") for r in evidence if isinstance(r, dict)}
        if "committee_memo" not in existing_types:
            evidence = [{"ref_type": "committee_memo", "ref_id": committee_id}] + evidence
        if "service_handoff" not in existing_types:
            evidence = evidence + [{"ref_type": "service_handoff", "ref_id": handoff_id}]

    return ApprovalDecisionProposal(
        proposal_type="approval_decision",
        decision_id=_proposal_id(payload, "approval", source_decision_id),
        target_type=target_type,
        target_id=_required_str(payload, "target_id", "targetId"),
        target_version=_required_str(payload, "target_version", "targetVersion"),
        decision_state="proposed",
        risk_level=risk_level,
        rationale=_rationale(payload, source_decision_id, sponsor_persona_id),
        evidence_refs=evidence,
        capital_pool_id=_optional_str(payload, "capital_pool_id", "capitalPoolId"),
        persona_id=(
            _optional_str(payload, "persona_id", "personaId", "target_persona_id")
            or sponsor_persona_id
        ),
        metadata=metadata,
    )


def _evolution_proposal(payload: Mapping[str, Any], decision_type: str) -> EvolutionDecisionProposal:
    sponsor_persona_id = _required_str(payload, "sponsor_persona_id", "sponsorPersonaId")
    target_type = _required_lower(payload, "target_type", "targetType")
    _ensure_member("target_type", target_type, EVOLUTION_TARGET_TYPES)

    action_type = _required_lower(payload, "action_type", "actionType")
    target_stage = _optional_lower(payload, "target_stage", "targetStage")
    if target_stage:
        _ensure_member("target_stage", target_stage, EVOLUTION_TARGET_STAGES)

    inferred_risk = _infer_evolution_risk(action_type, target_stage)
    requested_risk = _optional_lower(payload, "risk_level", "riskLevel")
    if requested_risk:
        _ensure_member("risk_level", requested_risk, EVOLUTION_RISK_LEVELS)
        if requested_risk != inferred_risk:
            raise SponsorDecisionBridgeError(
                f"risk_level mismatch for evolution action {action_type!r}: "
                f"expected {inferred_risk!r}, got {requested_risk!r}"
            )
    risk_level = requested_risk or inferred_risk

    source_decision_id = _required_str(payload, "decision_id", "id", "source_decision_id")
    metadata = _base_metadata(payload, decision_type, source_decision_id, sponsor_persona_id)
    metadata["recommended_decision"] = _recommended_decision(payload)

    conditions = _string_list(_value(payload, "conditions") or [])
    if conditions:
        metadata["conditions"] = conditions

    return EvolutionDecisionProposal(
        proposal_type="evolution_decision",
        decision_id=_proposal_id(payload, "evolution", source_decision_id),
        target_type=target_type,
        target_id=_required_str(payload, "target_id", "targetId"),
        target_version=_required_str(payload, "target_version", "targetVersion"),
        action_type=action_type,
        decision_state="proposed",
        risk_level=risk_level,
        created_by_role=_optional_lower(payload, "created_by_role", "createdByRole") or "operator",
        created_by_id=sponsor_persona_id,
        rationale=_rationale(payload, source_decision_id, sponsor_persona_id),
        evidence_refs=_evidence_refs(payload),
        threshold_snapshots=_threshold_snapshots(payload),
        target_stage=target_stage,
        persona_id=_optional_str(payload, "persona_id", "personaId", "target_persona_id"),
        capital_pool_id=_optional_str(payload, "capital_pool_id", "capitalPoolId"),
        linked_incident_id=_optional_str(payload, "linked_incident_id", "linkedIncidentId"),
        linked_postmortem_id=_optional_str(payload, "linked_postmortem_id", "linkedPostmortemId"),
        metadata=metadata,
    )


def _to_payload(value: SponsorDecision | Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json"))
    if hasattr(value, "dict"):
        return dict(value.dict())
    raise SponsorDecisionBridgeError("sponsor_decision must be a mapping or structured model")


def _value(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _required_str(payload: Mapping[str, Any], *keys: str) -> str:
    value = _value(payload, *keys)
    if value is None:
        raise SponsorDecisionBridgeError(f"{keys[0]} is required")
    text = str(value).strip()
    if not text:
        raise SponsorDecisionBridgeError(f"{keys[0]} is required")
    return text


def _optional_str(payload: Mapping[str, Any], *keys: str) -> str | None:
    value = _value(payload, *keys)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_lower(payload: Mapping[str, Any], *keys: str) -> str:
    return _required_str(payload, *keys).lower()


def _optional_lower(payload: Mapping[str, Any], *keys: str) -> str | None:
    value = _optional_str(payload, *keys)
    return value.lower() if value else None


def _ensure_member(field_name: str, value: str, allowed: set[str]) -> None:
    if value not in allowed:
        raise SponsorDecisionBridgeError(
            f"{field_name} must be one of {sorted(allowed)}, got {value!r}"
        )


def _proposal_id(payload: Mapping[str, Any], prefix: str, source_decision_id: str) -> str:
    explicit = _optional_str(payload, "proposal_id", "proposalId", f"{prefix}_proposal_id")
    if explicit:
        return explicit
    return f"{prefix}-proposal-from-{source_decision_id}"


def _rationale(payload: Mapping[str, Any], source_decision_id: str, sponsor_persona_id: str) -> str:
    rationale = _optional_str(payload, "rationale", "summary")
    if rationale:
        return rationale
    rationale_ref = _optional_str(payload, "rationale_ref", "rationaleRef")
    if rationale_ref:
        return f"Sponsor decision {source_decision_id} is backed by {rationale_ref}."
    return f"Sponsor decision {source_decision_id} was recorded by {sponsor_persona_id}."


def _recommended_decision(payload: Mapping[str, Any]) -> str:
    value = _optional_lower(payload, "sponsor_decision", "outcome", "decision")
    if value in {None, ""}:
        return "approved"
    if value in {"approved", "approve"}:
        return "approved"
    if value in {"conditional", "approved_with_conditions", "approve_with_conditions"}:
        return "approved_with_conditions"
    if value in {"rejected", "reject"}:
        return "rejected"
    raise SponsorDecisionBridgeError(f"unsupported sponsor_decision outcome: {value!r}")


def _base_metadata(
    payload: Mapping[str, Any],
    decision_type: str,
    source_decision_id: str,
    sponsor_persona_id: str,
) -> dict[str, Any]:
    raw_metadata = _value(payload, "metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
    metadata.update(
        {
            "bridge_source": "consultation_sponsor_decision",
            "source_decision_id": source_decision_id,
            "source_decision_type": decision_type,
            "sponsor_persona_id": sponsor_persona_id,
        }
    )
    for source_key, metadata_key in (
        ("committee_id", "committee_id"),
        ("committeeId", "committee_id"),
        ("handoff_id", "handoff_id"),
        ("handoffId", "handoff_id"),
        ("rationale_ref", "rationale_ref"),
        ("rationaleRef", "rationale_ref"),
        ("trace_id", "trace_id"),
        ("traceId", "trace_id"),
    ):
        value = _optional_str(payload, source_key)
        if value:
            metadata[metadata_key] = value
    return metadata


def _evidence_refs(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs = _value(payload, "evidence_refs", "evidenceRefs") or []
    if not isinstance(refs, list):
        raise SponsorDecisionBridgeError("evidence_refs must be a list")
    return [_normalize_evidence_ref(item) for item in refs]


def _normalize_evidence_ref(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        ref_id = item.strip()
        if not ref_id:
            raise SponsorDecisionBridgeError("evidence_refs entries must not be empty")
        return {"ref_type": "manual_review_ticket", "ref_id": ref_id}

    if not isinstance(item, Mapping):
        raise SponsorDecisionBridgeError("evidence_refs entries must be strings or objects")

    ref_type = str(item.get("ref_type") or item.get("type") or "manual_review_ticket").strip()
    ref_id = str(item.get("ref_id") or item.get("id") or "").strip()
    if not ref_id:
        raise SponsorDecisionBridgeError("evidence_refs[].ref_id is required")
    if ref_type not in GOVERNANCE_EVIDENCE_REF_TYPES:
        raise SponsorDecisionBridgeError(
            f"evidence_refs[].ref_type must be one of {sorted(GOVERNANCE_EVIDENCE_REF_TYPES)}, got {ref_type!r}"
        )

    normalized: dict[str, Any] = {"ref_type": ref_type, "ref_id": ref_id}
    if isinstance(item.get("storage_ref"), Mapping):
        normalized["storage_ref"] = dict(item["storage_ref"])
    if item.get("note"):
        normalized["note"] = str(item["note"])
    return normalized


def _threshold_snapshots(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    snapshots = _value(payload, "threshold_snapshots", "thresholdSnapshots") or []
    if not isinstance(snapshots, list):
        raise SponsorDecisionBridgeError("threshold_snapshots must be a list")
    normalized: list[dict[str, Any]] = []
    for item in snapshots:
        if not isinstance(item, Mapping):
            raise SponsorDecisionBridgeError("threshold_snapshots entries must be objects")
        normalized.append(dict(item))
    return normalized


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise SponsorDecisionBridgeError("conditions must be a list")
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _infer_evolution_risk(action_type: str, target_stage: str | None) -> str:
    if action_type in LOW_RISK_EVOLUTION_ACTIONS:
        return "low"
    if action_type in MEDIUM_RISK_EVOLUTION_ACTIONS:
        return "medium"
    if action_type == "freeze":
        if target_stage not in {"paper", "canary", "live"}:
            raise SponsorDecisionBridgeError(
                "freeze action_type requires target_stage to be paper, canary, or live"
            )
        return "high" if target_stage == "live" else "medium"
    if action_type in HIGH_RISK_EVOLUTION_ACTIONS:
        return "high"
    allowed = sorted(
        LOW_RISK_EVOLUTION_ACTIONS
        | MEDIUM_RISK_EVOLUTION_ACTIONS
        | HIGH_RISK_EVOLUTION_ACTIONS
        | {"freeze"}
    )
    raise SponsorDecisionBridgeError(
        f"action_type must be one of {allowed}, got {action_type!r}"
    )
