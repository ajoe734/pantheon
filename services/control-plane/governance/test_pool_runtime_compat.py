"""Tests for DEP-004 pool/runtime compatibility guard."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_GOV_DIR = Path(__file__).resolve().parent
if str(_GOV_DIR) not in sys.path:
    sys.path.insert(0, str(_GOV_DIR))

from pool_runtime_compat import check_compatibility, enforce_compatibility


def active_pool(**overrides):
    pool = {
        "pool_id": "pool-001",
        "status": "active",
        "risk_budget": 100_000,
        "metadata": {"jurisdiction": "US"},
    }
    pool.update(overrides)
    return pool


def deployment_plan(**overrides):
    plan = {
        "plan_id": "plan-001",
        "capital_pool_id": "pool-001",
        "target_stage": "canary",
        "metadata": {
            "target_size": 25_000,
            "persona_capital_binding_id": "pcb-001",
        },
    }
    plan.update(overrides)
    return plan


def runtime_requirements(**overrides):
    runtime = {
        "runtime_mode": "canary",
        "broker_jurisdiction": "US",
        "persona_capital_binding_id": "pcb-001",
    }
    runtime.update(overrides)
    return runtime


def active_binding(**overrides):
    binding = {
        "binding_id": "pcb-001",
        "persona_id": "persona-ops",
        "capital_pool_id": "pool-001",
        "status": "active",
    }
    binding.update(overrides)
    return binding


def compat(**overrides):
    params = {
        "capital_pool": active_pool(),
        "deployment_plan": deployment_plan(),
        "runtime_requirements": runtime_requirements(),
        "persona_capital_binding": active_binding(),
    }
    params.update(overrides)
    return check_compatibility("pool-001", "plan-001", **params)


def test_check_compatibility_passes_with_active_pool_matching_runtime_and_binding():
    result = compat()

    assert result["passed"] is True
    assert result["rejection_reasons"] == []
    assert result["details"]["deployment_stage"] == "canary"
    assert result["details"]["pool_risk_budget"] == 100_000


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"capital_pool": active_pool(status="suspended")}, "pool_admissibility_status_not_active"),
        (
            {"deployment_plan": deployment_plan(metadata={"target_size": 125_000, "persona_capital_binding_id": "pcb-001"})},
            "pool_risk_budget_insufficient",
        ),
        ({"runtime_requirements": runtime_requirements(broker_jurisdiction="TW")}, "pool_runtime_jurisdiction_mismatch"),
        ({"runtime_requirements": runtime_requirements(runtime_mode="live")}, "runtime_mode_stage_mismatch"),
        ({"persona_capital_binding": active_binding(status="revoked")}, "persona_capital_binding_not_active"),
    ],
)
def test_check_compatibility_rejects_one_failed_guard_per_scenario(overrides, reason):
    result = compat(**overrides)

    assert result["passed"] is False
    assert result["rejection_reasons"] == [reason]


def test_enforce_compatibility_raises_with_rejection_reason():
    with pytest.raises(ValueError, match="pool_admissibility_status_not_active"):
        enforce_compatibility(
            "pool-001",
            "plan-001",
            capital_pool=active_pool(status="archived"),
            deployment_plan=deployment_plan(),
            runtime_requirements=runtime_requirements(),
            persona_capital_binding=active_binding(),
        )
