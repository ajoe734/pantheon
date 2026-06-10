from __future__ import annotations

from services.capital.binding_live.conflict_resolution_log import OPEN_CONFLICT_BLOCKER
from services.capital.binding_live.dashboard import (
    DASHBOARD_SOURCE,
    build_capital_binding_go_no_go_dashboard,
)


NOW = "2026-05-20T12:00:00Z"


def test_dashboard_matches_2026_05_19_capital_binding_shape() -> None:
    dashboard = build_capital_binding_go_no_go_dashboard(
        _readiness_packet(),
        _responsibility_packet(),
        _conflict_log_packet(),
        _lifecycle_packet(),
        at=NOW,
    )

    assert dashboard.version == "1.0"
    assert dashboard.source == DASHBOARD_SOURCE
    assert dashboard.readiness_state == "go"
    assert dashboard.can_bind_live is True
    assert [gate.id for gate in dashboard.gates] == [
        "readiness_packet",
        "sponsor_mandate",
        "conflict_log",
        "ttl",
    ]
    assert dashboard.progress.ready_gates == 4
    assert dashboard.progress.total_gates == 4
    assert dashboard.progress.ready_items == 4
    assert dashboard.progress.total_items == 4

    payload = dashboard.to_dict()
    assert payload["passed"] is True
    assert payload["readiness"]["can_bind_live"] is True
    assert payload["sponsor_mandate"]["status"] == "active"
    assert payload["conflict_log_status"]["passed"] is True
    assert payload["ttl_status"]["expires_at"] == "2026-05-21T00:00:00Z"


def test_dashboard_happy_path_is_read_only() -> None:
    readiness = _readiness_packet()
    dashboard = build_capital_binding_go_no_go_dashboard(
        readiness,
        _responsibility_packet(),
        _conflict_log_packet(),
        _lifecycle_packet(),
        at=NOW,
    )

    assert dashboard.can_bind_live is True
    assert dashboard.blocking_reasons == ()
    assert {gate.status for gate in dashboard.gates} == {"ready"}
    assert readiness["approval"]["operator"] == "approved"


def test_dashboard_fails_closed_without_operator_approval() -> None:
    readiness = _readiness_packet(
        approval={"risk_owner": "approved", "operator": "pending"},
        result={
            "can_bind_live": False,
            "blocking_reasons": ["operator_approval_pending"],
        },
    )

    dashboard = build_capital_binding_go_no_go_dashboard(
        readiness,
        _responsibility_packet(),
        _conflict_log_packet(),
        _lifecycle_packet(),
        at=NOW,
    )

    assert dashboard.can_bind_live is False
    assert dashboard.readiness_state == "no_go"
    assert dashboard.gates[0].id == "readiness_packet"
    assert dashboard.gates[0].status == "blocked"
    assert "operator_approval_pending" in dashboard.gates[0].blocking_reasons
    assert "operator_approval_pending" in dashboard.blocking_reasons


def test_dashboard_surfaces_open_conflict_and_blocks_live_binding() -> None:
    conflict_log = _conflict_log_packet(
        open_conflicts=[
            {
                "conflict_id": "conflict-committee-001",
                "summary": "Committee has not resolved sponsor dispute.",
                "owner": "committee-chair",
                "evidence_ref": "support/evidence/CBL/conflict-committee-001.json",
            }
        ]
    )

    dashboard = build_capital_binding_go_no_go_dashboard(
        _readiness_packet(),
        _responsibility_packet(),
        conflict_log,
        _lifecycle_packet(),
        at=NOW,
    )

    conflict_gate = dashboard.gates[2]
    assert dashboard.can_bind_live is False
    assert dashboard.readiness_state == "no_go"
    assert conflict_gate.id == "conflict_log"
    assert conflict_gate.status == "blocked"
    assert conflict_gate.blocking_reasons == (OPEN_CONFLICT_BLOCKER,)
    assert dashboard.conflict_log_status.open_conflict_ids == ("conflict-committee-001",)
    assert dashboard.progress.blocked_gates == 1


def test_dashboard_fails_closed_for_expired_ttl() -> None:
    dashboard = build_capital_binding_go_no_go_dashboard(
        _readiness_packet(),
        _responsibility_packet(),
        _conflict_log_packet(),
        _lifecycle_packet(),
        at="2026-05-21T00:00:00Z",
    )

    ttl_gate = dashboard.gates[3]
    assert dashboard.can_bind_live is False
    assert dashboard.readiness_state == "no_go"
    assert ttl_gate.id == "ttl"
    assert ttl_gate.status == "blocked"
    assert "binding_ttl_expired" in ttl_gate.blocking_reasons
    assert dashboard.ttl_status.status == "expired"
    assert dashboard.ttl_status.admissible is False


def test_dashboard_fails_closed_when_sponsor_mandate_mismatches_readiness() -> None:
    responsibility = _responsibility_packet(sponsor_persona_id="persona-other")

    dashboard = build_capital_binding_go_no_go_dashboard(
        _readiness_packet(),
        responsibility,
        _conflict_log_packet(),
        _lifecycle_packet(),
        at=NOW,
    )

    sponsor_gate = dashboard.gates[1]
    assert dashboard.can_bind_live is False
    assert sponsor_gate.id == "sponsor_mandate"
    assert sponsor_gate.status == "blocked"
    assert "sponsor_persona_mismatch" in sponsor_gate.blocking_reasons


def _readiness_packet(**overrides):
    packet = {
        "readiness_id": "cbl-ready-001",
        "binding_id": "binding-live-001",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-main",
        "artifact_id": "artifact-reg-001",
        "runtime_id": "runtime-binding-001",
        "deployment_plan_id": "deployment-plan-canary-001",
        "risk_policy_id": "risk-policy-live-001",
        "roles": {
            "sponsor_persona": "persona-alpha",
            "live_owner": "ops-live-owner",
            "risk_owner": "risk-owner-1",
            "operator": "operator-1",
        },
        "required_evidence": {
            "persona_mandate_ref": "support/evidence/CBL/persona-mandate.json",
            "sponsor_responsibility_ref": "support/evidence/CBL/sponsor-responsibility.json",
            "conflict_resolution_log_ref": "support/evidence/CBL/conflict-resolution-log.json",
            "pool_risk_policy_ref": "support/evidence/CBL/pool-risk-policy.json",
            "runtime_compatibility_ref": "support/evidence/CBL/runtime-compatibility.json",
            "artifact_approval_ref": "support/evidence/CBL/artifact-approval.json",
            "deployment_plan_ref": "support/evidence/CBL/deployment-plan.json",
            "rollback_target_ref": "support/evidence/CBL/rollback-target.json",
            "telemetry_readiness_ref": "support/evidence/CBL/telemetry-readiness.json",
            "ep5_packet_ref": "support/evidence/EP5/proof-packet.json",
        },
        "controls": {
            "max_budget_pct": 5,
            "ttl_hours": 24,
            "revocation_allowed": True,
            "auto_scale_allowed": False,
            "live_order_allowed": False,
        },
        "approval": {
            "risk_owner": "approved",
            "operator": "approved",
        },
        "result": {
            "can_bind_live": True,
            "blocking_reasons": [],
        },
    }
    packet.update(overrides)
    return packet


def _responsibility_packet(**overrides):
    packet = {
        "responsibility_id": "sponsor-resp-001",
        "sponsor_persona_id": "persona-alpha",
        "binding_id": "binding-live-001",
        "capital_pool_id": "pool-main",
        "live_owner": {
            "owner_id": "ops-live-owner",
            "role": "live_owner",
            "binding_id": "binding-live-001",
            "mandate_ref": "support/evidence/CBL/sponsor-mandate.json",
            "contact_ref": "ops://live-owner/on-call",
        },
        "escalation_chain": [
            {
                "level": 1,
                "owner_id": "risk-owner-1",
                "role": "risk_owner",
                "trigger": "risk_limit_breach",
                "action": "pause_and_review",
                "evidence_ref": "support/evidence/CBL/risk-escalation.json",
            },
            {
                "level": 2,
                "owner_id": "operator-1",
                "role": "operator",
                "trigger": "live_owner_unavailable",
                "action": "manual_intervention",
                "evidence_ref": "support/evidence/CBL/operator-escalation.json",
            },
        ],
        "policy_refs": [
            "BINDING_AND_DEPLOYMENT_SEMANTICS.md#3.4",
            "MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md#11-v1-decisions",
        ],
        "status": "active",
    }
    packet.update(overrides)
    return packet


def _conflict_log_packet(**overrides):
    packet = {
        "log_id": "conflict-log-001",
        "capital_pool_id": "pool-main",
        "scope_ref": "strategy:alpha/live",
        "timestamp": "2026-05-19T00:00:00Z",
        "proposal_ids": ["proposal-alpha", "proposal-beta"],
        "vetoed_proposals": [
            {
                "proposal_id": "proposal-beta",
                "persona_id": "persona-beta",
                "reason": "pool_risk_policy_veto",
                "detail": "Proposal exceeds pool risk policy.",
            }
        ],
        "weighting_inputs": {
            "proposal-alpha": 0.8,
            "proposal-beta": 0.0,
        },
        "weighting_outputs": {
            "proposal-alpha": 1.0,
            "proposal-beta": 0.0,
        },
        "open_conflicts": [],
        "committee_ref": None,
        "sponsor_persona_id": "persona-alpha",
        "rejected_reason": None,
        "synthesis_method": "weighted_fusion",
    }
    packet.update(overrides)
    return packet


def _lifecycle_packet(**overrides):
    packet = {
        "binding_id": "binding-live-001",
        "status": "active",
        "ttl": {
            "issued_at": "2026-05-20T00:00:00Z",
            "ttl_hours": 24,
        },
        "revocation_policy": {
            "revocation_allowed": True,
            "allowed_revoker_roles": ["risk_owner", "operator"],
            "requires_reason": True,
        },
    }
    packet.update(overrides)
    return packet
