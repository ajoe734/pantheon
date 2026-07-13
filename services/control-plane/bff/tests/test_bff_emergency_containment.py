import pytest

from command_executor import (
    _execute_bff_action_adapter,
    _execute_emergency_containment_authority,
)
from emergency_containment_policy import ALLOWED_TRIGGERS, validate_emergency_containment
from rebalance_authority_test_support import (
    HEADERS,
    CapitalBffAuthorityHarness,
    rebalance_payload,
)


def _command(**overrides):
    params = {
        "action": "freeze",
        "trigger": "hard_risk_breach",
        "evidence_refs": ["risk-event:42"],
    }
    params.update(overrides)
    return params


@pytest.mark.parametrize("trigger", sorted(ALLOWED_TRIGGERS))
def test_all_emergency_triggers_admit_risk_decreasing_containment(trigger):
    validate_emergency_containment(_command(trigger=trigger))


@pytest.mark.parametrize(
    "action",
    ["promote", "promote_to_canary", "promote_to_live", "increase_allocation", "create_canary", "create_live"],
)
def test_emergency_command_rejects_promotion_and_increase_actions(action):
    with pytest.raises(ValueError, match="cannot promote or increase"):
        validate_emergency_containment(_command(action=action))


def test_emergency_capital_reduction_must_actually_reduce_weight():
    with pytest.raises(ValueError, match="must lower"):
        validate_emergency_containment(_command(action="reduce_capital", current_weight=.10, target_weight=.11))
    validate_emergency_containment(_command(action="reduce_capital", current_weight=.10, target_weight=.04))


def test_emergency_command_requires_evidence_and_rollback_reference():
    with pytest.raises(ValueError, match="evidence_refs"):
        validate_emergency_containment(_command(evidence_refs=[]))
    with pytest.raises(ValueError, match="rollback_ref"):
        validate_emergency_containment(_command(action="rollback_allocation"))


def test_containment_adapter_receipt_is_auditable_and_never_claims_live_mutation():
    params = _command(action="rollback_allocation", rollback_ref="allocation:snapshot-before-breach")
    params["action_id"] = "EmergencyContainment"
    receipt = _execute_bff_action_adapter("cmd-42", params)
    assert receipt["containment"] is True
    assert receipt["risk_direction"] == "decrease_only"
    assert receipt["evidence_refs"] == ["risk-event:42"]
    assert receipt["rollback_ref"] == "allocation:snapshot-before-breach"
    assert receipt["live_capital_side_effects"] is False


def test_bff_command_admission_keeps_risk_increasing_containment_at_422(tmp_path):
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        assert harness.client is not None
        response = harness.client.post(
            "/bff/v1/commands",
            headers={**HEADERS, "Idempotency-Key": "containment-increase-denied"},
            json={
                "command": "EmergencyContainment",
                "target": {"type": "Persona", "id": "p-live"},
                "params": {
                    **_command(
                        action="reduce_capital",
                        persona_id="p-live",
                        current_weight=0.10,
                        target_weight=0.11,
                    ),
                    "capital_pool_id": "pool-real",
                },
                "audit_context": {"reason": "risk increase must never pass containment admission"},
            },
        )
        assert response.status_code == 422, response.text
        assert "must lower" in response.text
        assert harness.capital_client is not None
        assert harness.capital_client.get("/api/containments").json() == []


def test_authority_dispatch_projects_explicit_frozen_containment_after_restart(tmp_path):
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        harness.create_persona("p-live")
        assert harness.client is not None
        proposal = harness.client.post(
            "/bff/rebalances",
            headers={**HEADERS, "Idempotency-Key": "containment-baseline-proposal"},
            json=rebalance_payload(),
        )
        assert proposal.status_code == 202, proposal.text

        receipt = _execute_emergency_containment_authority(
            "cmd-containment-freeze",
            {
                **_command(),
                "persona_id": "p-live",
                "capital_pool_id": "pool-real",
                "current_weight": 0.10,
                "target_weight": 0.10,
                "entity_type": "Persona",
                "entity_id": "p-live",
                "actor_id": "op-2",
                "actor_role": "operator",
                "idempotency_key": "containment-freeze-owner",
                "request_hash": "containment-freeze-owner-request",
            },
        )
        assert receipt["status"] == "executed"
        assert receipt["containment_state"] == "frozen"
        assert receipt["entity_type"] == "Persona"
        assert receipt["entity_id"] == "p-live"
        assert receipt["receipt_ref"].startswith("capital-containment-receipt:")
        assert receipt["audit_ref"].startswith("capital-audit:")
        assert receipt["authoritative_containment_readback"] is True
        assert receipt["authoritative_capital_readback"] is True
        assert receipt["authoritative_capital_state_applied"] is True
        assert receipt["live_capital_side_effects"] is False

        harness.restart()
        assert harness.client is not None
        detail = harness.client.get("/bff/personas/p-live", headers=HEADERS)
        assert detail.status_code == 200, detail.text
        data = detail.json()["data"]
        assert data["containment_state"] == "frozen"
        assert data["containmentState"] == "frozen"
        assert data["frozen"] is True
        assert data["containment"]["state"] == "frozen"
        assert data["containment"]["containment_state"] == "frozen"
        assert data["containment"]["command_id"] == "cmd-containment-freeze"
        assert data["containment"]["authoritative_containment_readback"] is True
