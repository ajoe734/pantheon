"""Lesson-derived rule proposals are pending decision records that never act on their own."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("GOVERNANCE_DATA_DIR", tempfile.mkdtemp(prefix="lrt_gov_"))

from services.governance import main  # noqa: E402
from services.governance.models import ProposeApprovalRequest, TargetType  # noqa: E402

_path = Path(__file__).resolve().parents[1] / "control-plane" / "governance" / "approval_decision.py"
_spec = importlib.util.spec_from_file_location("approval_decision_lesson_targets_under_test", _path)
ad = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ad
_spec.loader.exec_module(ad)

TYPES = {"lesson_gate_check_proposal": "gate_check", "pool_policy_parameter_proposal": "pool_policy"}


def body(target_type, **over):
    data = {"expected_version": 0, "tenant_id": "t1", "owner_user_id": "persona-svc", "target_type": target_type,
            "target_id": "target-1", "target_version": "v1", "risk_level": "high", "persona_id": "p1",
            "subject": {"proposal_kind": TYPES[target_type], "lesson_ids": "l1,l2", "case_file_id": "casefile-x",
                        "proposal_digest": "d" * 64}}
    data.update(over)
    return ProposeApprovalRequest.model_validate(data)


@pytest.mark.parametrize("target_type", TYPES)
def test_target_types_exist_on_wire_and_platform_models(target_type):
    assert TargetType(target_type).value == target_type == ad.TargetType(target_type).value


@pytest.mark.parametrize("target_type", TYPES)
def test_valid_proposal_is_pending_and_not_decided_by_an_automated_gate(target_type):
    request = body(target_type)
    assert main.lesson_rule_proposal_errors(request) == []
    fields = request.model_dump(mode="json", exclude={"expected_version", "decision_id"})
    decision = ad.ApprovalDecision.create_proposed(decision_id="d1", **fields)
    assert not decision.validate()
    assert decision.decision_state.value == "proposed" and decision.decision is None
    with pytest.raises(ValueError):
        decision.accept_review("automated_gate", "bot")
    decision.accept_review("risk_owner", "operator-1")
    decision.decide("approved", "reviewed", actor_role="risk_owner", actor_id="operator-1")
    # a decision record only: nothing in it executes, sizes or limits anything
    assert set(decision.metadata) <= {"subject"}


@pytest.mark.parametrize("target_type", TYPES)
@pytest.mark.parametrize("missing", ["proposal_kind", "lesson_ids", "case_file_id", "proposal_digest"])
def test_missing_citation_fails_closed(target_type, missing):
    subject = dict(body(target_type).subject)
    subject.pop(missing)
    errors = main.lesson_rule_proposal_errors(body(target_type, subject=subject))
    assert any(missing in e for e in errors)


@pytest.mark.parametrize("target_type", TYPES)
def test_low_or_medium_risk_and_wrong_kind_are_refused(target_type):
    for risk in ("low", "medium"):
        assert any("risk_level" in e for e in main.lesson_rule_proposal_errors(body(target_type, risk_level=risk)))
    other = next(k for k in TYPES.values() if k != TYPES[target_type])
    subject = {**body(target_type).subject, "proposal_kind": other}
    assert any("proposal_kind" in e for e in main.lesson_rule_proposal_errors(body(target_type, subject=subject)))


def test_other_targets_are_unaffected():
    request = ProposeApprovalRequest.model_validate({
        "expected_version": 0, "tenant_id": "t1", "owner_user_id": "u", "target_type": "registry_entry",
        "target_id": "a", "target_version": "v1", "risk_level": "low"})
    assert main.lesson_rule_proposal_errors(request) == []
