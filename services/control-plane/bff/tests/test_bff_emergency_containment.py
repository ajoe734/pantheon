import pytest

from command_executor import _execute_bff_action_adapter
from emergency_containment_policy import ALLOWED_TRIGGERS, validate_emergency_containment


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
