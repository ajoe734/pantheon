from __future__ import annotations

from services.broker.live_activation.risk_owner_checklist import (
    CHECKLIST_SOURCE,
    EXPECTED_RISK_OWNER_CHECKLIST_ITEMS,
    generate_risk_owner_checklist,
)
from services.broker.live_activation.validator import EXPECTED_HARD_FAIL_CONDITIONS


def test_checklist_matches_2026_05_19_b3_shape() -> None:
    checklist = generate_risk_owner_checklist(_activation_request())

    assert checklist.version == "1.0"
    assert checklist.source == CHECKLIST_SOURCE
    assert tuple(item.text for item in checklist.items) == EXPECTED_RISK_OWNER_CHECKLIST_ITEMS
    assert [item.id for item in checklist.items] == [
        "risk_owner_b3_01",
        "risk_owner_b3_02",
        "risk_owner_b3_03",
        "risk_owner_b3_04",
        "risk_owner_b3_05",
        "risk_owner_b3_06",
        "risk_owner_b3_07",
        "risk_owner_b3_08",
        "risk_owner_b3_09",
        "risk_owner_b3_10",
    ]


def test_risk_owner_checklist_happy_path_passes_without_live_side_effects() -> None:
    checklist = generate_risk_owner_checklist(_activation_request())

    assert checklist.can_sign_off is True
    assert checklist.blocking_reasons == ()
    assert {item.status for item in checklist.items} == {"ready"}
    assert checklist.to_dict()["passed"] is True


def test_risk_owner_checklist_fails_closed_for_short_canary_period() -> None:
    payload = _activation_request()
    payload["evidence"]["canary_run_days"] = 3

    checklist = generate_risk_owner_checklist(payload)

    assert checklist.can_sign_off is False
    assert checklist.items[2].status == "blocked"
    assert checklist.items[2].blocking_reasons == ("canary_run_days must be >= 7",)
    assert "canary_run_days must be >= 7" in checklist.blocking_reasons


def test_risk_owner_checklist_fails_closed_for_open_conflict() -> None:
    payload = _activation_request()
    payload["risk_owner_review"]["open_conflicts"] = ["persona sponsor disputed"]

    checklist = generate_risk_owner_checklist(payload)

    assert checklist.can_sign_off is False
    conflict_item = checklist.items[9]
    assert conflict_item.status == "blocked"
    assert conflict_item.blocking_reasons == ("conflict resolution log has open conflicts",)


def test_risk_owner_checklist_fails_closed_for_active_hard_fail_condition() -> None:
    payload = _activation_request()
    payload["conditions"]["telemetry_unavailable"] = True

    checklist = generate_risk_owner_checklist(payload)

    assert checklist.can_sign_off is False
    assert all(item.status == "ready" for item in checklist.items)
    assert "hard fail condition active: telemetry_unavailable" in checklist.blocking_reasons


def _activation_request() -> dict:
    return {
        "evidence": {
            "paper_run_days": 14,
            "canary_run_days": 7,
            "ep4_packet_ref": "support/evidence/EP4/proof-packet.json",
            "ep5_packet_ref": "support/evidence/EP5/proof-packet.json",
            "broker_sandbox_smoke_ref": "support/evidence/BROKER/sandbox-smoke.json",
            "broker_credential_scope_verified": True,
            "kill_switch_demo_ref": "support/evidence/KILL/demo.json",
            "rollback_drill_ref": "support/evidence/ROLLBACK/drill.json",
            "bff_ha_readiness_ref": "support/evidence/BFF/ha-readiness.json",
            "telemetry_readiness_ref": "support/evidence/TEL/readiness.json",
            "audit_retention_ref": "support/evidence/AUDIT/retention.json",
            "first_week_observation_window_ref": "support/evidence/BLA/first-week.json",
        },
        "risk_owner_review": {
            "strategy_artifact_lineage_ref": "lineage://candidate-artifact/live-ready-001",
            "risk_policy_matches_capital_pool_charter": True,
            "risk_policy_capital_pool_charter_ref": "policy://capital-pool/main/risk-charter",
            "risk_thresholds_within_policy": True,
            "risk_thresholds_ref": "support/evidence/RISK/thresholds.json",
            "rollback_target_ref": "artifact://rollback/live-ready-rollback-001",
            "postmortem_path_ref": "support/evidence/INCIDENT/postmortem-path.json",
            "sponsor_persona_responsibility_ref": "persona://sponsor/live-owner-001",
            "conflict_resolution_log_ref": "support/evidence/GOV/conflict-resolution.json",
            "open_conflicts": [],
        },
        "approvals": {
            "risk_owner": {"status": "pending"},
            "operator": {"status": "pending"},
        },
        "conditions": {condition: False for condition in EXPECTED_HARD_FAIL_CONDITIONS},
    }
