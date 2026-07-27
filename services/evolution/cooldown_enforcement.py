"""Cooldown enforcement for EvolutionDecisionProposal emission.

This module sits before governance admission.  Proposal emitters can use it to
avoid producing repeated EvolutionDecisionProposal payloads for the same
artifact while a prior proposal's cooldown is still open.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from services.foundation import (
    ActorRef,
    ActorType,
    AuditAction,
    EnvironmentName,
    EnvironmentScope,
    TraceContext,
)
from services.governance.human_gate.decision_model import (
    HumanGateDecision,
    HumanGateDecisionError,
    validate_decision,
)


COOLDOWN_ENFORCEMENT_SCHEMA_VERSION = "EvolutionProposalCooldownEnforcement.v1"

# Mirrors ``evolution_decision.DEFAULT_TENANT_ID``.  Duplicated as a plain
# constant rather than imported because this module is loaded as an ordinary
# ``services.evolution`` package member and must not depend on the
# ``services/control-plane/governance`` sys.path bootstrap.  The two constants
# are pinned equal by
# ``test_l12_evo_001_tenant_authority.py::test_cooldown_tenant_default_matches_governance``.
DEFAULT_TENANT_ID = "pantheon-default"
COOLDOWN_REJECTION_ACTION_TYPE = "evolution_proposal_cooldown_rejected"
COOLDOWN_OVERRIDE_SCOPE = "evolution_proposal_cooldown_override"
POLICY_REFS = (
    "EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md section 5.2-5.4",
    "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 12.3",
    "MGMT-EVO-007 evolution OODA loop closure cooldown policy",
)

DEFAULT_ACTION_COOLDOWN_HOURS = {
    "observe": 72,
    "require_more_data": 72,
    "flag_for_review": 72,
    "retrain": 72,
    "revalidate": 72,
    "rollback": 72,
    "reduce_budget": 168,
    "tighten_risk_policy": 168,
    "mutate_persona_route_policy": 168,
    "mutate_consult_policy": 168,
    "freeze": 168,
    "retire": 336,
    "split_persona": 336,
    "merge_persona": 336,
    "remove_live_owner_role": 336,
    "restrict_pool_eligibility": 336,
    "force_risk_off": 336,
    "revive": 336,
}

DEFAULT_ACTOR = ActorRef(
    actor_type=ActorType.SYSTEM,
    actor_id="evolution-cooldown-enforcement",
    roles=("evolution_controller", "cooldown_enforcement"),
)


class CooldownEnforcementError(ValueError):
    """Raised when cooldown enforcement input is malformed."""


def normalize_tenant_id(value: Any) -> str:
    """Resolve any tenant input to the authoritative tenant identifier."""

    text = "" if value is None else str(value).strip()
    return text or DEFAULT_TENANT_ID


@dataclass(frozen=True)
class ProposalArtifactKey:
    """The artifact identity used for cooldown matching.

    Cooldown is tenant-scoped: one tenant's open cooldown on an artifact must
    not suppress another tenant's proposal for an artifact that happens to
    share an id, and conversely a proposal must never escape its own tenant's
    cooldown by omitting the tenant.  ``tenant_id`` is therefore part of the
    match key and of the emitted ``target_ref`` audit string.
    """

    artifact_id: str
    artifact_version: str | None = None
    target_type: str = "candidate_artifact"
    tenant_id: str = DEFAULT_TENANT_ID

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", normalize_tenant_id(self.tenant_id))

    @property
    def target_ref(self) -> str:
        if self.artifact_version:
            return f"{self.tenant_id}/{self.target_type}:{self.artifact_id}@{self.artifact_version}"
        return f"{self.tenant_id}/{self.target_type}:{self.artifact_id}"

    def matches(self, other: "ProposalArtifactKey") -> bool:
        if self.tenant_id != other.tenant_id:
            return False
        if self.artifact_id != other.artifact_id:
            return False
        if self.artifact_version and other.artifact_version:
            return self.artifact_version == other.artifact_version
        return True

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "tenant_id": self.tenant_id,
            "target_type": self.target_type,
            "artifact_id": self.artifact_id,
            "target_ref": self.target_ref,
        }
        if self.artifact_version:
            payload["artifact_version"] = self.artifact_version
        return payload


@dataclass(frozen=True)
class EmittedProposalRecord:
    """Audit-friendly cooldown record for an emitted proposal."""

    proposal_id: str
    artifact: ProposalArtifactKey
    proposed_action: str
    emitted_at: str
    cooldown_window_hours: int
    cooldown_ends_at: str
    source_ref: str | None = None
    override_decision_ref: str | None = None
    policy_refs: tuple[str, ...] = POLICY_REFS

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "EmittedProposalRecord":
        artifact_payload = _mapping(data.get("artifact") or {}, "record.artifact")
        artifact = ProposalArtifactKey(
            artifact_id=_required_text(
                artifact_payload.get("artifact_id")
                or artifact_payload.get("target_artifact_id")
                or data.get("artifact_id")
                or data.get("target_artifact_id")
                or data.get("target_id"),
                "record.artifact_id",
            ),
            artifact_version=_optional_text(
                artifact_payload.get("artifact_version")
                or artifact_payload.get("target_artifact_version")
                or data.get("artifact_version")
                or data.get("target_artifact_version")
                or data.get("target_version")
            ),
            target_type=_optional_text(artifact_payload.get("target_type") or data.get("target_type"))
            or "candidate_artifact",
            tenant_id=normalize_tenant_id(
                artifact_payload.get("tenant_id") or data.get("tenant_id")
            ),
        )
        return cls(
            proposal_id=_required_text(data.get("proposal_id") or data.get("decision_id"), "record.proposal_id"),
            artifact=artifact,
            proposed_action=_required_text(
                data.get("proposed_action") or data.get("action_type"),
                "record.proposed_action",
            ),
            emitted_at=_format_rfc3339(_parse_rfc3339(data.get("emitted_at"), "record.emitted_at")),
            cooldown_window_hours=_positive_int(
                data.get("cooldown_window_hours"),
                "record.cooldown_window_hours",
            ),
            cooldown_ends_at=_format_rfc3339(
                _parse_rfc3339(data.get("cooldown_ends_at"), "record.cooldown_ends_at")
            ),
            source_ref=_optional_text(data.get("source_ref")),
            override_decision_ref=_optional_text(data.get("override_decision_ref")),
            policy_refs=tuple(str(item) for item in data.get("policy_refs") or POLICY_REFS),
        )

    def blocks(self, artifact: ProposalArtifactKey, as_of: datetime) -> bool:
        return self.artifact.matches(artifact) and as_of < _parse_rfc3339(
            self.cooldown_ends_at,
            "record.cooldown_ends_at",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "artifact": self.artifact.to_dict(),
            "proposed_action": self.proposed_action,
            "emitted_at": self.emitted_at,
            "cooldown_window_hours": self.cooldown_window_hours,
            "cooldown_ends_at": self.cooldown_ends_at,
            "source_ref": self.source_ref,
            "override_decision_ref": self.override_decision_ref,
            "policy_refs": list(self.policy_refs),
        }


@dataclass(frozen=True)
class CooldownRejection:
    """Fail-closed cooldown rejection plus immutable audit evidence."""

    proposal_id: str
    artifact: ProposalArtifactKey
    blocking_record: EmittedProposalRecord
    rejected_at: str
    reason: str
    audit_action: AuditAction
    audit_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "artifact": self.artifact.to_dict(),
            "blocking_record": self.blocking_record.to_dict(),
            "rejected_at": self.rejected_at,
            "reason": self.reason,
            "audit_action": self.audit_action.to_dict(),
            "audit_payload": dict(self.audit_payload),
        }


@dataclass(frozen=True)
class CooldownAdmission:
    """Result of evaluating one proposal against cooldown history."""

    accepted: bool
    record: EmittedProposalRecord | None = None
    rejection: CooldownRejection | None = None
    override_decision: HumanGateDecision | None = None

    @property
    def audit_actions(self) -> tuple[AuditAction, ...]:
        if self.rejection is None:
            return ()
        return (self.rejection.audit_action,)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "record": self.record.to_dict() if self.record else None,
            "rejection": self.rejection.to_dict() if self.rejection else None,
            "override_decision_ref": _human_gate_ref(self.override_decision)
            if self.override_decision
            else None,
        }


class EvolutionProposalCooldownEnforcer:
    """In-memory cooldown enforcer for proposal emitters."""

    def __init__(self, records: Iterable[EmittedProposalRecord | Mapping[str, Any]] = ()) -> None:
        self._records = tuple(_coerce_record(record) for record in records)

    @property
    def records(self) -> tuple[EmittedProposalRecord, ...]:
        return self._records

    def admit(
        self,
        proposal: Mapping[str, Any] | Any,
        *,
        emitted_at: str | datetime | None = None,
        override_decision: HumanGateDecision | Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
        actor_ref: ActorRef | Mapping[str, Any] | None = None,
    ) -> CooldownAdmission:
        admission = enforce_proposal_cooldown(
            proposal,
            self._records,
            emitted_at=emitted_at,
            override_decision=override_decision,
            trace_id=trace_id,
            correlation_id=correlation_id,
            actor_ref=actor_ref,
        )
        if admission.accepted and admission.record is not None:
            self._records = (*self._records, admission.record)
        return admission


def enforce_proposal_cooldown(
    proposal: Mapping[str, Any] | Any,
    history: Iterable[EmittedProposalRecord | Mapping[str, Any]],
    *,
    emitted_at: str | datetime | None = None,
    override_decision: HumanGateDecision | Mapping[str, Any] | None = None,
    trace_id: str | None = None,
    correlation_id: str | None = None,
    actor_ref: ActorRef | Mapping[str, Any] | None = None,
) -> CooldownAdmission:
    """Evaluate whether a proposal may be emitted under cooldown policy.

    If another proposal for the same artifact is still cooling down, this
    function rejects by default and returns an AuditAction evidence object.  An
    override is accepted only when it is a valid, approved HumanGateDecision
    whose target_id matches the artifact being re-proposed.
    """

    payload = _payload(proposal)
    now = _coerce_time(emitted_at)
    artifact = proposal_artifact_key(payload)
    blocking_record = _blocking_record(artifact, now, history)
    approved_override: HumanGateDecision | None = None

    if blocking_record is not None:
        if override_decision is not None:
            approved_override = validate_cooldown_override(override_decision, artifact)
        else:
            rejection = _cooldown_rejection(
                payload,
                artifact,
                blocking_record,
                rejected_at=now,
                trace_id=trace_id,
                correlation_id=correlation_id,
                actor_ref=actor_ref,
            )
            return CooldownAdmission(accepted=False, rejection=rejection)

    record = record_emitted_proposal(
        payload,
        emitted_at=now,
        override_decision=approved_override,
    )
    return CooldownAdmission(
        accepted=True,
        record=record,
        override_decision=approved_override,
    )


def record_emitted_proposal(
    proposal: Mapping[str, Any] | Any,
    *,
    emitted_at: str | datetime | None = None,
    override_decision: HumanGateDecision | Mapping[str, Any] | None = None,
) -> EmittedProposalRecord:
    """Build a durable cooldown record for an already accepted proposal."""

    payload = _payload(proposal)
    now = _coerce_time(emitted_at)
    cooldown_window_hours = _cooldown_window_hours(payload)
    cooldown_ends_at = now + timedelta(hours=cooldown_window_hours)
    override = validate_cooldown_override(override_decision, proposal_artifact_key(payload)) if override_decision else None
    return EmittedProposalRecord(
        proposal_id=_proposal_id(payload),
        artifact=proposal_artifact_key(payload),
        proposed_action=_proposed_action(payload),
        emitted_at=_format_rfc3339(now),
        cooldown_window_hours=cooldown_window_hours,
        cooldown_ends_at=_format_rfc3339(cooldown_ends_at),
        source_ref=_source_ref(payload),
        override_decision_ref=_human_gate_ref(override),
    )


def validate_cooldown_override(
    decision: HumanGateDecision | Mapping[str, Any],
    artifact: ProposalArtifactKey,
) -> HumanGateDecision:
    """Validate the HumanGateDecision required to override cooldown."""

    try:
        normalized = validate_decision(decision)
    except HumanGateDecisionError as exc:
        raise CooldownEnforcementError(f"invalid HumanGateDecision cooldown override: {exc}") from exc

    if normalized.status != "approved" or not normalized.can_proceed:
        raise CooldownEnforcementError(
            "cooldown override HumanGateDecision must be approved and can_proceed=true"
        )
    if normalized.target_id != artifact.artifact_id:
        raise CooldownEnforcementError(
            "cooldown override HumanGateDecision target_id must match proposal artifact_id"
        )

    override_scope = str(normalized.metadata.get("override_scope") or "").strip()
    if override_scope != COOLDOWN_OVERRIDE_SCOPE:
        raise CooldownEnforcementError(
            f"cooldown override HumanGateDecision override_scope must be {COOLDOWN_OVERRIDE_SCOPE!r}"
        )

    # A cooldown override is tenant-scoped authority.  An override raised in
    # one tenant must not unlock another tenant's cooldown on an artifact that
    # happens to share an id, so a non-default tenant must be named explicitly
    # and any declared tenant must match the artifact being re-proposed.
    override_tenant = str(normalized.metadata.get("override_tenant_id") or "").strip()
    if not override_tenant:
        if artifact.tenant_id != DEFAULT_TENANT_ID:
            raise CooldownEnforcementError(
                "cooldown override HumanGateDecision must declare metadata.override_tenant_id "
                f"for tenant-scoped artifact tenant '{artifact.tenant_id}'"
            )
    elif normalize_tenant_id(override_tenant) != artifact.tenant_id:
        raise CooldownEnforcementError(
            f"cooldown override HumanGateDecision override_tenant_id '{override_tenant}' does not "
            f"match proposal tenant '{artifact.tenant_id}'"
        )
    return normalized


def proposal_artifact_key(proposal: Mapping[str, Any] | Any) -> ProposalArtifactKey:
    """Extract the same-artifact cooldown key from a proposal payload."""

    payload = _payload(proposal)
    artifact_id = _required_text(
        payload.get("target_artifact_id")
        or payload.get("artifact_id")
        or payload.get("target_id"),
        "proposal target_artifact_id/artifact_id/target_id",
    )
    artifact_version = _optional_text(
        payload.get("target_artifact_version")
        or payload.get("artifact_version")
        or payload.get("target_version")
    )
    target_type = _optional_text(payload.get("target_type")) or "candidate_artifact"
    return ProposalArtifactKey(
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        target_type=target_type,
        tenant_id=normalize_tenant_id(payload.get("tenant_id")),
    )


def _blocking_record(
    artifact: ProposalArtifactKey,
    as_of: datetime,
    history: Iterable[EmittedProposalRecord | Mapping[str, Any]],
) -> EmittedProposalRecord | None:
    blockers = [
        record
        for record in (_coerce_record(item) for item in history)
        if record.blocks(artifact, as_of)
    ]
    if not blockers:
        return None
    return max(blockers, key=lambda record: _parse_rfc3339(record.cooldown_ends_at, "record.cooldown_ends_at"))


def _cooldown_rejection(
    proposal: Mapping[str, Any],
    artifact: ProposalArtifactKey,
    blocking_record: EmittedProposalRecord,
    *,
    rejected_at: datetime,
    trace_id: str | None,
    correlation_id: str | None,
    actor_ref: ActorRef | Mapping[str, Any] | None,
) -> CooldownRejection:
    proposal_id = _proposal_id(proposal)
    rejected_at_text = _format_rfc3339(rejected_at)
    reason = (
        "EvolutionDecisionProposal rejected: artifact is still inside cooldown "
        f"from proposal {blocking_record.proposal_id} until {blocking_record.cooldown_ends_at}. "
        "A cooldown override requires an approved HumanGateDecision."
    )
    payload = {
        "schema_version": COOLDOWN_ENFORCEMENT_SCHEMA_VERSION,
        "event": COOLDOWN_REJECTION_ACTION_TYPE,
        "proposal_id": proposal_id,
        "artifact": artifact.to_dict(),
        "proposed_action": _proposed_action(proposal),
        "rejected_at": rejected_at_text,
        "blocking_record": blocking_record.to_dict(),
        "override_required_schema": "HumanGateDecision.v1",
        "policy_refs": list(POLICY_REFS),
    }
    actor = _actor_ref(actor_ref)
    trace = _trace_context(
        proposal,
        artifact,
        actor,
        trace_id=trace_id,
        correlation_id=correlation_id,
    )
    audit_action = AuditAction.record(
        actor_ref=actor,
        action_type=COOLDOWN_REJECTION_ACTION_TYPE,
        target_ref=artifact.target_ref,
        environment=_environment_scope(proposal),
        reason=reason,
        trace=trace,
        payload=payload,
        before_state_ref=f"EvolutionDecisionProposal:{blocking_record.proposal_id}",
        after_state_ref=f"EvolutionDecisionProposal:{proposal_id}:rejected",
        metadata={
            "projection_schema_version": COOLDOWN_ENFORCEMENT_SCHEMA_VERSION,
            "source": "evolution_cooldown_enforcement",
            "policy_refs": list(POLICY_REFS),
            "proposal_id": proposal_id,
            "blocking_proposal_id": blocking_record.proposal_id,
            "artifact_id": artifact.artifact_id,
            "artifact_version": artifact.artifact_version,
            "cooldown_ends_at": blocking_record.cooldown_ends_at,
            "override_required_schema": "HumanGateDecision.v1",
        },
    )
    return CooldownRejection(
        proposal_id=proposal_id,
        artifact=artifact,
        blocking_record=blocking_record,
        rejected_at=rejected_at_text,
        reason=reason,
        audit_action=audit_action,
        audit_payload=payload,
    )


def _payload(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
    elif hasattr(value, "model_dump"):
        payload = value.model_dump()
    elif hasattr(value, "dict"):
        payload = value.dict()
    else:
        raise CooldownEnforcementError("proposal must be a mapping or expose to_dict/model_dump/dict")
    if not isinstance(payload, Mapping):
        raise CooldownEnforcementError("proposal conversion must return a mapping")
    return dict(payload)


def _coerce_record(value: EmittedProposalRecord | Mapping[str, Any]) -> EmittedProposalRecord:
    if isinstance(value, EmittedProposalRecord):
        return value
    if isinstance(value, Mapping):
        return EmittedProposalRecord.from_mapping(value)
    raise CooldownEnforcementError("history entries must be EmittedProposalRecord or mapping")


def _cooldown_window_hours(proposal: Mapping[str, Any]) -> int:
    raw = proposal.get("cooldown_window_hours")
    if raw is not None:
        return _positive_int(raw, "proposal.cooldown_window_hours")
    action = _proposed_action(proposal)
    if action in DEFAULT_ACTION_COOLDOWN_HOURS:
        return DEFAULT_ACTION_COOLDOWN_HOURS[action]
    raise CooldownEnforcementError(
        "proposal.cooldown_window_hours is required for unsupported action_type "
        f"{action!r}"
    )


def _proposal_id(proposal: Mapping[str, Any]) -> str:
    source_postmortem_id = _optional_text(proposal.get("source_postmortem_id"))
    action = _optional_text(proposal.get("proposed_action") or proposal.get("action_type"))
    generated = f"evolution-proposal://{source_postmortem_id}/{action}" if source_postmortem_id and action else None
    return _required_text(
        proposal.get("proposal_id")
        or proposal.get("decision_id")
        or proposal.get("id")
        or generated,
        "proposal_id/decision_id/id",
    )


def _proposed_action(proposal: Mapping[str, Any]) -> str:
    return _required_text(
        proposal.get("proposed_action") or proposal.get("action_type"),
        "proposal.proposed_action/action_type",
    ).lower()


def _source_ref(proposal: Mapping[str, Any]) -> str | None:
    if proposal.get("source_ref"):
        return _optional_text(proposal["source_ref"])
    if proposal.get("source_postmortem_id"):
        return f"Postmortem:{proposal['source_postmortem_id']}"
    if proposal.get("linked_postmortem_id"):
        return f"Postmortem:{proposal['linked_postmortem_id']}"
    if proposal.get("source_incident_id"):
        return f"IncidentCase:{proposal['source_incident_id']}"
    if proposal.get("linked_incident_id"):
        return f"IncidentCase:{proposal['linked_incident_id']}"
    return None


def _trace_context(
    proposal: Mapping[str, Any],
    artifact: ProposalArtifactKey,
    actor: ActorRef,
    *,
    trace_id: str | None,
    correlation_id: str | None,
) -> TraceContext:
    metadata = proposal.get("metadata") if isinstance(proposal.get("metadata"), Mapping) else {}
    resolved_trace_id = trace_id or _optional_text(proposal.get("trace_id")) or _optional_text(metadata.get("trace_id"))
    resolved_correlation_id = (
        correlation_id
        or _optional_text(proposal.get("correlation_id"))
        or _optional_text(metadata.get("correlation_id"))
        or resolved_trace_id
    )
    if resolved_trace_id:
        return TraceContext(
            trace_id=resolved_trace_id,
            correlation_id=resolved_correlation_id or resolved_trace_id,
            environment=_environment_scope(proposal),
            actor_ref=actor,
            source_system="pantheon.evolution.cooldown_enforcement",
        )
    return TraceContext.new(
        environment=_environment_scope(proposal),
        actor_ref=actor,
        source_system="pantheon.evolution.cooldown_enforcement",
        correlation_id=resolved_correlation_id or artifact.target_ref,
    )


def _environment_scope(proposal: Mapping[str, Any]) -> EnvironmentScope:
    raw = (
        proposal.get("target_deployment_stage")
        or proposal.get("deployment_stage")
        or proposal.get("target_stage")
        or "paper"
    )
    value = str(raw).strip().lower()
    if value not in {item.value for item in EnvironmentName}:
        value = EnvironmentName.PAPER.value
    return EnvironmentScope(name=value)


def _actor_ref(value: ActorRef | Mapping[str, Any] | None) -> ActorRef:
    if value is None:
        return DEFAULT_ACTOR
    if isinstance(value, ActorRef):
        return value
    if not isinstance(value, Mapping):
        raise CooldownEnforcementError("actor_ref must be an ActorRef or mapping")
    return ActorRef(
        actor_type=value.get("actor_type") or ActorType.SYSTEM,
        actor_id=_required_text(value.get("actor_id"), "actor_ref.actor_id"),
        display_name=_optional_text(value.get("display_name")),
        roles=tuple(str(role) for role in value.get("roles") or ()),
        workspace_id=_optional_text(value.get("workspace_id")),
        persona_id=_optional_text(value.get("persona_id")),
        session_id=_optional_text(value.get("session_id")),
    )


def _human_gate_ref(decision: HumanGateDecision | None) -> str | None:
    if decision is None:
        return None
    return f"HumanGateDecision:{decision.decision_id}@{decision.evidence_hash}"


def _coerce_time(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(microsecond=0)
    return _parse_rfc3339(value, "timestamp")


def _parse_rfc3339(value: Any, field_name: str) -> datetime:
    text = _required_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CooldownEnforcementError(f"{field_name} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _format_rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _required_text(value: Any, field_name: str) -> str:
    if value is None:
        raise CooldownEnforcementError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise CooldownEnforcementError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CooldownEnforcementError(f"{field_name} must be an object")
    return dict(value)


def _positive_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CooldownEnforcementError(f"{field_name} must be a positive integer") from exc
    if parsed <= 0:
        raise CooldownEnforcementError(f"{field_name} must be a positive integer")
    return parsed
