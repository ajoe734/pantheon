#!/usr/bin/env python3
"""Deployment-review composition through typed deployment/governance ports."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ports import create_in_memory_read_surface_ports


PLAN = {
    "id": "plan-F-042",
    "plan_id": "plan-F-042",
    "approval_decision_id": "approval-042",
    "current_stage": "none",
    "target_stage": "paper",
    "status": "approved",
}
DECISION = {
    "id": "approval-042",
    "decision_id": "approval-042",
    "decision": "approved",
    "outcome": "approved",
    "decision_state": "decided",
    "actor_id": "governance",
    "reviewer": "governance",
    "risk_level": "medium",
}


def _ports(*, include_decision: bool = True):
    decisions = [DECISION] if include_decision else []
    return create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"deployment_plans": [PLAN]},
        ooda_management_kwargs={
            "deployment_plans": [PLAN],
            "approval_decisions": decisions,
        },
    )


def test_seeded_review_summary() -> None:
    ports = _ports()

    plan = ports.get_deployment_plan("plan-F-042")
    assert plan == PLAN
    review = ports.get_review_summary("plan-F-042")
    assert review["governanceOutcome"] == "approved"
    assert review["decisionState"] == "decided"
    assert review["reviewer"] == "governance"
    assert ports.get_allowed_actions("plan-F-042")["canPromoteToPaper"] is True


def test_review_composition_reflects_missing_decision() -> None:
    ports = _ports(include_decision=False)

    review = ports.get_review_summary("plan-F-042")
    assert review == {"riskSummary": "Risk summary unavailable."}
    assert ports.get_allowed_actions("plan-F-042")["canPromoteToPaper"] is False


def test_unknown_plan_is_fail_closed() -> None:
    ports = _ports()

    assert ports.get_deployment_plan("plan-missing") is None
    assert ports.get_review_summary("plan-missing") is None
    assert ports.get_allowed_actions("plan-missing") == {
        "canApprove": False,
        "canReject": False,
        "canPromoteToPaper": False,
    }
