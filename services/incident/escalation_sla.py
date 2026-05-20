"""Incident escalation SLA gate for high-severity incident closure.

The SLA gate is intentionally pure: it reads IncidentCase, Postmortem, and an
EvolutionDecisionProposal-shaped mapping, then returns a closure evaluation. It
does not write to the incident store, governance store, or postmortem bridge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from .incident import IncidentCase, Postmortem, PostmortemStatus


DEFAULT_TRIGGERING_SEVERITIES = ("high", "critical")
DEFAULT_ALLOWED_PROPOSAL_ACTIONS = {
    "high": ("rollback",),
    "critical": ("freeze", "rollback"),
}


class EscalationSlaError(ValueError):
    """Raised when a caller tries to close an incident with unresolved SLA gaps."""


@dataclass(frozen=True)
class EscalationSlaConfig:
    """Configurable SLA thresholds and severity/action policy."""

    postmortem_sla_hours: int = 24
    proposal_sla_hours: int = 24
    triggering_severities: tuple[str, ...] = DEFAULT_TRIGGERING_SEVERITIES
    allowed_proposal_actions: Mapping[str, Sequence[str]] = field(
        default_factory=lambda: dict(DEFAULT_ALLOWED_PROPOSAL_ACTIONS)
    )

    def __post_init__(self) -> None:
        if self.postmortem_sla_hours <= 0:
            raise ValueError("postmortem_sla_hours must be positive")
        if self.proposal_sla_hours <= 0:
            raise ValueError("proposal_sla_hours must be positive")

    def normalized_triggering_severities(self) -> tuple[str, ...]:
        return tuple(severity.lower() for severity in self.triggering_severities)

    def allowed_actions_for(self, severity: str) -> tuple[str, ...]:
        actions = self.allowed_proposal_actions.get(severity.lower(), ())
        return tuple(str(action).lower() for action in actions)


@dataclass(frozen=True)
class EscalationSlaBreach:
    """One fail-closed reason produced by the escalation SLA gate."""

    code: str
    message: str
    due_at: str | None = None
    actual_at: str | None = None


@dataclass(frozen=True)
class EscalationSlaEvaluation:
    """Closure readiness result for a single incident escalation chain."""

    incident_id: str
    severity: str
    requires_escalation: bool
    postmortem_due_at: str | None
    proposal_due_at: str | None
    postmortem_id: str | None
    proposal_action: str | None
    breaches: tuple[EscalationSlaBreach, ...] = ()

    @property
    def closure_allowed(self) -> bool:
        return not self.breaches

    @property
    def closure_blockers(self) -> tuple[str, ...]:
        return tuple(breach.message for breach in self.breaches)


def evaluate_escalation_sla(
    incident: IncidentCase,
    *,
    postmortem: Postmortem | None = None,
    proposal: Mapping[str, Any] | None = None,
    config: EscalationSlaConfig | None = None,
) -> EscalationSlaEvaluation:
    """Evaluate whether an incident can close under the escalation SLA.

    High and critical incidents require a published postmortem within the
    incident-to-postmortem SLA, then an EvolutionDecisionProposal within the
    postmortem-to-proposal SLA. Missing or late artifacts block closure.
    """

    effective_config = config or EscalationSlaConfig()
    severity = incident.severity.lower()
    requires_escalation = severity in effective_config.normalized_triggering_severities()
    if not requires_escalation:
        return EscalationSlaEvaluation(
            incident_id=incident.incident_id,
            severity=severity,
            requires_escalation=False,
            postmortem_due_at=None,
            proposal_due_at=None,
            postmortem_id=postmortem.postmortem_id if postmortem else None,
            proposal_action=_proposal_action(proposal),
        )

    incident_created_at = _parse_utc(incident.created_at, field_name="incident.created_at")
    postmortem_due = incident_created_at + timedelta(hours=effective_config.postmortem_sla_hours)
    breaches: list[EscalationSlaBreach] = []

    if postmortem is None:
        breaches.append(
            EscalationSlaBreach(
                code="postmortem_missing",
                message="high-severity incident closure requires a published postmortem",
                due_at=_format_utc(postmortem_due),
            )
        )
        return _evaluation(
            incident=incident,
            requires_escalation=True,
            postmortem_due=postmortem_due,
            proposal_due=None,
            postmortem=None,
            proposal=proposal,
            breaches=breaches,
        )

    if postmortem.incident_id != incident.incident_id:
        breaches.append(
            EscalationSlaBreach(
                code="postmortem_incident_mismatch",
                message="postmortem incident_id must match the incident being closed",
                due_at=_format_utc(postmortem_due),
            )
        )

    postmortem_published_at = _published_at(postmortem, breaches, postmortem_due)
    if postmortem_published_at and postmortem_published_at > postmortem_due:
        breaches.append(
            EscalationSlaBreach(
                code="postmortem_sla_breached",
                message="postmortem was published after the incident escalation SLA",
                due_at=_format_utc(postmortem_due),
                actual_at=_format_utc(postmortem_published_at),
            )
        )

    proposal_due = (
        postmortem_published_at + timedelta(hours=effective_config.proposal_sla_hours)
        if postmortem_published_at
        else None
    )
    if postmortem_published_at:
        breaches.extend(
            _proposal_breaches(
                incident=incident,
                postmortem=postmortem,
                proposal=proposal,
                proposal_due=proposal_due,
                config=effective_config,
            )
        )

    return _evaluation(
        incident=incident,
        requires_escalation=True,
        postmortem_due=postmortem_due,
        proposal_due=proposal_due,
        postmortem=postmortem,
        proposal=proposal,
        breaches=breaches,
    )


def assert_incident_closure_allowed(
    incident: IncidentCase,
    *,
    postmortem: Postmortem | None = None,
    proposal: Mapping[str, Any] | None = None,
    config: EscalationSlaConfig | None = None,
) -> EscalationSlaEvaluation:
    """Return the SLA evaluation or raise when closure must fail closed."""

    evaluation = evaluate_escalation_sla(
        incident,
        postmortem=postmortem,
        proposal=proposal,
        config=config,
    )
    if not evaluation.closure_allowed:
        blockers = "; ".join(evaluation.closure_blockers)
        raise EscalationSlaError(f"incident closure blocked by escalation SLA: {blockers}")
    return evaluation


def _proposal_breaches(
    *,
    incident: IncidentCase,
    postmortem: Postmortem,
    proposal: Mapping[str, Any] | None,
    proposal_due: datetime | None,
    config: EscalationSlaConfig,
) -> list[EscalationSlaBreach]:
    if proposal_due is None:
        return []

    if proposal is None:
        return [
            EscalationSlaBreach(
                code="proposal_missing",
                message="postmortem closure chain requires an EvolutionDecisionProposal",
                due_at=_format_utc(proposal_due),
            )
        ]

    breaches: list[EscalationSlaBreach] = []
    if proposal.get("source_postmortem_id") != postmortem.postmortem_id:
        breaches.append(
            EscalationSlaBreach(
                code="proposal_postmortem_mismatch",
                message="proposal source_postmortem_id must match the published postmortem",
                due_at=_format_utc(proposal_due),
            )
        )
    if proposal.get("source_incident_id") != incident.incident_id:
        breaches.append(
            EscalationSlaBreach(
                code="proposal_incident_mismatch",
                message="proposal source_incident_id must match the incident being closed",
                due_at=_format_utc(proposal_due),
            )
        )

    allowed_actions = config.allowed_actions_for(incident.severity)
    action = _proposal_action(proposal)
    if action is None:
        breaches.append(
            EscalationSlaBreach(
                code="proposal_action_missing",
                message="proposal proposed_action is required for escalation closure",
                due_at=_format_utc(proposal_due),
            )
        )
    elif action not in allowed_actions:
        breaches.append(
            EscalationSlaBreach(
                code="proposal_action_not_allowed",
                message=(
                    "proposal proposed_action must be one of "
                    f"{sorted(allowed_actions)} for severity {incident.severity!r}"
                ),
                due_at=_format_utc(proposal_due),
            )
        )

    created_at = proposal.get("created_at") or proposal.get("proposed_at")
    if not created_at:
        breaches.append(
            EscalationSlaBreach(
                code="proposal_created_at_missing",
                message="proposal created_at is required for postmortem-to-proposal SLA",
                due_at=_format_utc(proposal_due),
            )
        )
        return breaches

    proposal_created_at = _parse_utc(str(created_at), field_name="proposal.created_at")
    if proposal_created_at > proposal_due:
        breaches.append(
            EscalationSlaBreach(
                code="proposal_sla_breached",
                message="EvolutionDecisionProposal was created after the postmortem escalation SLA",
                due_at=_format_utc(proposal_due),
                actual_at=_format_utc(proposal_created_at),
            )
        )
    return breaches


def _published_at(
    postmortem: Postmortem,
    breaches: list[EscalationSlaBreach],
    postmortem_due: datetime,
) -> datetime | None:
    if postmortem.status != PostmortemStatus.PUBLISHED.value:
        breaches.append(
            EscalationSlaBreach(
                code="postmortem_not_published",
                message="postmortem must be published before incident closure",
                due_at=_format_utc(postmortem_due),
            )
        )
        return None
    if not postmortem.published_at:
        breaches.append(
            EscalationSlaBreach(
                code="postmortem_published_at_missing",
                message="published postmortem must carry published_at for SLA evaluation",
                due_at=_format_utc(postmortem_due),
            )
        )
        return None
    return _parse_utc(postmortem.published_at, field_name="postmortem.published_at")


def _evaluation(
    *,
    incident: IncidentCase,
    requires_escalation: bool,
    postmortem_due: datetime | None,
    proposal_due: datetime | None,
    postmortem: Postmortem | None,
    proposal: Mapping[str, Any] | None,
    breaches: Sequence[EscalationSlaBreach],
) -> EscalationSlaEvaluation:
    return EscalationSlaEvaluation(
        incident_id=incident.incident_id,
        severity=incident.severity.lower(),
        requires_escalation=requires_escalation,
        postmortem_due_at=_format_utc(postmortem_due) if postmortem_due else None,
        proposal_due_at=_format_utc(proposal_due) if proposal_due else None,
        postmortem_id=postmortem.postmortem_id if postmortem else None,
        proposal_action=_proposal_action(proposal),
        breaches=tuple(breaches),
    )


def _proposal_action(proposal: Mapping[str, Any] | None) -> str | None:
    if proposal is None:
        return None
    action = proposal.get("proposed_action") or proposal.get("action_type")
    return str(action).lower() if action else None


def _parse_utc(value: str, *, field_name: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EscalationSlaError(f"{field_name} must be ISO-8601 UTC: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
