from __future__ import annotations

import pytest

from services.incident.escalation_sla import (
    EscalationSlaConfig,
    EscalationSlaError,
    assert_incident_closure_allowed,
    evaluate_escalation_sla,
)
from services.incident.incident import (
    IncidentCase,
    IncidentSeverity,
    IncidentStatus,
    Postmortem,
    PostmortemStatus,
)


def _make_incident(**overrides) -> IncidentCase:
    payload = {
        "incident_id": "inc-sla-001",
        "title": "Live runtime drawdown threshold breached",
        "status": IncidentStatus.RESOLVED.value,
        "severity": IncidentSeverity.HIGH.value,
        "created_at": "2026-05-20T00:00:00Z",
        "resolved_at": "2026-05-20T03:00:00Z",
        "binding_id": "binding-sla-001",
        "deployment_stage": "live",
        "deployment_plan_id": "plan-sla-001",
        "capital_pool_id": "pool-sla-001",
        "persona_capital_binding_id": "pcb-sla-001",
        "artifact_id": "artifact-sla-001",
        "artifact_version": "1.2.3",
        "runtime_id": "runtime-sla-001",
        "trace_id": "trace-sla-001",
    }
    payload.update(overrides)
    return IncidentCase(**payload)


def _make_postmortem(incident: IncidentCase, **overrides) -> Postmortem:
    payload = {
        "postmortem_id": "pm-sla-001",
        "title": "Drawdown threshold postmortem",
        "status": PostmortemStatus.PUBLISHED.value,
        "created_at": "2026-05-20T02:00:00Z",
        "published_at": "2026-05-20T04:00:00Z",
        "incident_id": incident.incident_id,
        "binding_id": incident.binding_id,
        "deployment_stage": incident.deployment_stage,
        "deployment_plan_id": incident.deployment_plan_id,
        "capital_pool_id": incident.capital_pool_id,
        "persona_capital_binding_id": incident.persona_capital_binding_id,
        "artifact_id": incident.artifact_id,
        "artifact_version": incident.artifact_version,
        "runtime_id": incident.runtime_id,
        "trace_id": incident.trace_id,
        "root_cause": "Risk limit did not account for intraday volatility.",
    }
    payload.update(overrides)
    return Postmortem(**payload)


def _make_proposal(
    incident: IncidentCase,
    postmortem: Postmortem,
    **overrides,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "proposal_id": "evo-proposal-sla-001",
        "source_postmortem_id": postmortem.postmortem_id,
        "source_incident_id": incident.incident_id,
        "proposed_action": "rollback",
        "created_at": "2026-05-20T08:00:00Z",
        "target_artifact_id": incident.artifact_id,
        "target_artifact_version": incident.artifact_version,
        "created_by_id": "postmortem-bridge",
    }
    payload.update(overrides)
    return payload


def test_high_severity_happy_path_allows_closure() -> None:
    incident = _make_incident()
    postmortem = _make_postmortem(incident)
    proposal = _make_proposal(incident, postmortem)

    evaluation = assert_incident_closure_allowed(
        incident,
        postmortem=postmortem,
        proposal=proposal,
    )

    assert evaluation.requires_escalation is True
    assert evaluation.closure_allowed is True
    assert evaluation.postmortem_due_at == "2026-05-21T00:00:00Z"
    assert evaluation.proposal_due_at == "2026-05-21T04:00:00Z"
    assert evaluation.proposal_action == "rollback"


@pytest.mark.parametrize("action", ["freeze", "rollback"])
def test_critical_incident_allows_freeze_or_rollback_proposal(action: str) -> None:
    incident = _make_incident(severity=IncidentSeverity.CRITICAL.value)
    postmortem = _make_postmortem(incident)
    proposal = _make_proposal(incident, postmortem, proposed_action=action)

    evaluation = evaluate_escalation_sla(
        incident,
        postmortem=postmortem,
        proposal=proposal,
    )

    assert evaluation.closure_allowed is True
    assert evaluation.proposal_action == action


def test_configurable_postmortem_sla_changes_due_at_and_allows_tighter_window() -> None:
    incident = _make_incident()
    postmortem = _make_postmortem(incident, published_at="2026-05-20T02:00:00Z")
    proposal = _make_proposal(incident, postmortem, created_at="2026-05-20T03:00:00Z")
    config = EscalationSlaConfig(postmortem_sla_hours=3, proposal_sla_hours=2)

    evaluation = evaluate_escalation_sla(
        incident,
        postmortem=postmortem,
        proposal=proposal,
        config=config,
    )

    assert evaluation.closure_allowed is True
    assert evaluation.postmortem_due_at == "2026-05-20T03:00:00Z"
    assert evaluation.proposal_due_at == "2026-05-20T04:00:00Z"


def test_postmortem_sla_breach_blocks_closure_fail_closed() -> None:
    incident = _make_incident()
    postmortem = _make_postmortem(incident, published_at="2026-05-21T02:00:00Z")
    proposal = _make_proposal(incident, postmortem, created_at="2026-05-21T03:00:00Z")

    evaluation = evaluate_escalation_sla(
        incident,
        postmortem=postmortem,
        proposal=proposal,
    )

    assert evaluation.closure_allowed is False
    assert [breach.code for breach in evaluation.breaches] == ["postmortem_sla_breached"]
    assert evaluation.breaches[0].due_at == "2026-05-21T00:00:00Z"
    assert evaluation.breaches[0].actual_at == "2026-05-21T02:00:00Z"
    with pytest.raises(EscalationSlaError, match="incident closure blocked"):
        assert_incident_closure_allowed(
            incident,
            postmortem=postmortem,
            proposal=proposal,
        )


def test_missing_proposal_blocks_high_severity_closure_fail_closed() -> None:
    incident = _make_incident()
    postmortem = _make_postmortem(incident)

    evaluation = evaluate_escalation_sla(incident, postmortem=postmortem)

    assert evaluation.closure_allowed is False
    assert [breach.code for breach in evaluation.breaches] == ["proposal_missing"]


def test_low_severity_incident_does_not_require_escalation_sla() -> None:
    incident = _make_incident(severity=IncidentSeverity.LOW.value)

    evaluation = evaluate_escalation_sla(incident)

    assert evaluation.requires_escalation is False
    assert evaluation.closure_allowed is True
    assert evaluation.postmortem_due_at is None
    assert evaluation.proposal_due_at is None
