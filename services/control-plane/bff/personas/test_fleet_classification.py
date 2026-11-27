"""Advisory purpose is the owner enrollment rule even with paper capital."""
import pytest

from services.control_plane.bff.personas.fleet_classification import (
    classify_persona_purpose, is_non_trading_persona,
)
from services.control_plane.persona.persona_policy_resolver import (
    evaluate_participant_action_capability, non_trading_identity_source,
)


@pytest.mark.parametrize("identity,source", [
    ({"persona_class": "consult_panel"}, "persona_class"),
    ({"persona_class": "agora_servant"}, "persona_class"),
    ({"persona_class": "agora_advisor"}, "persona_class"),
    ({"origin": "private_servant"}, "origin"),
    ({"origin": "agora_advisor"}, "origin"),
    ({"owner_scope": "user_private"}, "owner_scope"),
    ({"consult_policy_id": "consult-panel.non-trading"}, "consult_policy_id"),
    ({"agora_user_id": "u", "execution_authority": "none"}, "agora_scope"),
])
@pytest.mark.parametrize("nested", [True, False])
@pytest.mark.parametrize("lifecycle", ["paper_owner", "paper_running"])
def test_projection_and_capability_reuse_owner_exclusion(identity, source, nested, lifecycle):
    record = {"persona_id": "assistant", "status": "active", "lifecycle_state": lifecycle,
              "deployment_stage": "paper", "provisioning_idempotency_key": "existing-paper-book"}
    record.update({"metadata": identity} if nested else identity)
    assert non_trading_identity_source(record) == source
    assert classify_persona_purpose(record) == ("advisory_assistant", source)
    assert is_non_trading_persona(record)
    capability = evaluate_participant_action_capability(record, None)
    assert capability["blocked_reason"] == "persona_not_enrolled_for_daily_execution"


def test_trading_unknowns_and_advisory_precedence():
    assert classify_persona_purpose({}) == ("unknown", "unclassified")
    assert not is_non_trading_persona({})
    assert classify_persona_purpose({"persona_class": "trading"}) == ("trading", "persona_class")
    assert classify_persona_purpose({"deployment_stage": "paper"}) == ("trading", "execution_metadata")
    assert is_non_trading_persona({"metadata": {"persona_class": "trading"}, "owner_scope": "user_private"})
    assert is_non_trading_persona({"personaClass": " AGORA_ADVISOR "})
