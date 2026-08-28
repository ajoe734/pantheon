"""OODA-E2E-005: DeploymentPlan(paper) → RuntimeBinding → paper run end-to-end test.

Proves the OODA Act stage: a governed paper DeploymentPlan can be bound by the
Runtime Manager, the artifact loader can materialize the artifact, and the LEAN
smoke algorithm runs 5 deterministic trading days without touching a live broker.

Dependencies: RT-001/RT-002 (RuntimeBinding/RuntimeManager) +
              EX-002-RB (ArtifactLoader) + LEAN-ALGO-001 (smoke algorithm).
"""

from __future__ import annotations

import importlib
import importlib.util as _ilu
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "deployment_plan_for_runtime.json"


def _lean_submodule_available() -> bool:
    lean_algo_path = REPO_ROOT / "lean" / "Algorithm.Python"
    if str(lean_algo_path) not in sys.path:
        sys.path.insert(0, str(lean_algo_path))
    try:
        importlib.import_module("pantheon_algo.smoke_loader_test")
        return True
    except (ImportError, ModuleNotFoundError):
        return False


_LEAN_AVAILABLE = _lean_submodule_available()

from services.runtime_manager import (
    RuntimeBindingStatus,
    RuntimeBindingStore,
    RuntimeManagerService,
)
from services.execution.lean_runtime.smoke_algorithm import (  # noqa: E402
    SMOKE_CAPITAL_POOL_ID,
    SMOKE_PERSONA_CAPITAL_BINDING_ID,
    SMOKE_PLAN_ID,
    SMOKE_STRATEGY_ID,
    SMOKE_VERSION,
    run_algorithm_smoke,
    run_algorithm_smoke_from_binding,
)


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_is_valid_paper_deployment_plan() -> None:
    """Fixture JSON represents a valid paper DeploymentPlan."""
    plan = _load_fixture()
    assert plan["target_stage"] == "paper"
    assert plan["current_stage"] == "none"
    assert plan["transition_type"] == "activate"
    assert plan["runtime_action"] == "deploy_new_binding"
    assert plan["status"] == "approved"
    assert plan["scale"]["capital_scale_pct"] == 0.0
    assert isinstance(plan["rollback"], dict)
    assert plan["rollback"]["target_artifact_id"] != plan["artifact_id"]
    assert plan["rollback"]["target_version"] != plan["artifact_version"]


def test_runtime_manager_binds_deployment_plan_paper() -> None:
    """Runtime Manager creates an active paper RuntimeBinding from the fixture DeploymentPlan."""
    plan = _load_fixture()

    service = RuntimeManagerService()
    binding = service.deploy({
        "plan_id": plan["plan_id"],
        "plan_status": plan["status"],
        "target_stage": plan["target_stage"],
        "artifact_id": plan["artifact_id"],
        "artifact_version": plan["artifact_version"],
        "capital_pool_id": plan["capital_pool_id"],
        "persona_capital_binding_id": SMOKE_PERSONA_CAPITAL_BINDING_ID,
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "paper",
        "loader_checks_passed": True,
        "runtime_id": "rt-ooda-e2e-005-paper-001",
        "strategy_id": plan.get("strategy_id"),
    })

    assert binding.deployment_mode == "paper", f"expected paper, got {binding.deployment_mode}"
    assert binding.artifact_id == plan["artifact_id"]
    assert binding.artifact_version == plan["artifact_version"]
    assert binding.plan_id == plan["plan_id"]
    assert binding.capital_pool_id == plan["capital_pool_id"]
    assert binding.persona_capital_binding_id == SMOKE_PERSONA_CAPITAL_BINDING_ID
    assert binding.status == RuntimeBindingStatus.ACTIVE.value

    retrieved = service.store.get(binding.binding_id)
    assert retrieved is not None
    assert retrieved.deployment_mode == "paper"


@pytest.mark.skipif(
    not _LEAN_AVAILABLE,
    reason="LEAN submodule (lean/Algorithm.Python/pantheon_algo) not initialised",
)
def test_paper_run_fires_5_on_data_callbacks_and_records_at_least_1_fill() -> None:
    """LEAN smoke algorithm processes 5 synthetic trading days and records ≥ 1 fill."""
    result = run_algorithm_smoke()

    assert result.synthetic_bar_count == 5
    assert result.raw_on_data_callbacks == 5
    assert result.executed_on_data_callbacks >= 1
    assert result.fill_count >= 1

    fill = result.fill_events[0]
    assert fill.get("submitted_to_broker") is False

    assert result.loaded_metadata["deployment_stage"] == "paper"
    assert result.loaded_metadata["artifact_state"] == "approved"
    assert result.loaded_metadata["strategy_id"] == SMOKE_STRATEGY_ID


@pytest.mark.skipif(
    not _LEAN_AVAILABLE,
    reason="LEAN submodule (lean/Algorithm.Python/pantheon_algo) not initialised",
)
def test_broker_production_live_flag_stays_false_during_paper_run() -> None:
    """BROKER_PRODUCTION_LIVE_ENABLED must not be set or enabled during a paper run."""
    original_value = os.environ.get("BROKER_PRODUCTION_LIVE_ENABLED")

    result = run_algorithm_smoke()

    after_value = os.environ.get("BROKER_PRODUCTION_LIVE_ENABLED")

    assert result.broker_production_live_enabled == "false"
    assert after_value == original_value


def test_deployment_plan_artifact_matches_smoke_algorithm_registry_id() -> None:
    """Fixture artifact_id is consistent with the smoke algorithm's registry key convention."""
    plan = _load_fixture()
    expected_registry_id = f"reg-{SMOKE_STRATEGY_ID}-{SMOKE_VERSION}"
    assert plan["artifact_id"] == expected_registry_id
    assert plan["strategy_id"] == SMOKE_STRATEGY_ID
    assert plan["artifact_version"] == SMOKE_VERSION


@pytest.mark.skipif(
    not _LEAN_AVAILABLE,
    reason="LEAN submodule (lean/Algorithm.Python/pantheon_algo) not initialised",
)
def test_e2e_fixture_binding_runtime_context_identity() -> None:
    """E2E: fixture DeploymentPlan -> RuntimeManager binding -> ArtifactLoader -> LEAN smoke.

    Verifies that runtime_context returned by the algorithm carries the
    fixture-derived plan_id and RuntimeManager-created binding_id — proving the
    paper run is composed from the fixture, not from SMOKE_* constants.
    """
    plan = _load_fixture()

    service = RuntimeManagerService()
    binding = service.deploy({
        "plan_id": plan["plan_id"],
        "plan_status": plan["status"],
        "target_stage": plan["target_stage"],
        "artifact_id": plan["artifact_id"],
        "artifact_version": plan["artifact_version"],
        "capital_pool_id": plan["capital_pool_id"],
        "persona_capital_binding_id": SMOKE_PERSONA_CAPITAL_BINDING_ID,
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "paper",
        "loader_checks_passed": True,
        "runtime_id": "rt-ooda-e2e-005-identity-001",
        "strategy_id": plan.get("strategy_id"),
    })

    result = run_algorithm_smoke_from_binding(plan, binding)

    ctx = result.runtime_context
    assert ctx["deployment_plan_id"] == plan["plan_id"], (
        f"Expected deployment_plan_id={plan['plan_id']!r}, got {ctx['deployment_plan_id']!r}"
    )
    assert ctx["runtime_binding_id"] == binding.binding_id, (
        f"Expected runtime_binding_id={binding.binding_id!r}, got {ctx['runtime_binding_id']!r}"
    )
    assert ctx["artifact_id"] == plan["artifact_id"], (
        f"Expected artifact_id={plan['artifact_id']!r}, got {ctx['artifact_id']!r}"
    )
    assert ctx["artifact_version"] == plan["artifact_version"]
    assert ctx["deployment_stage"] == plan["target_stage"]
    assert ctx["strategy_id"] == plan["strategy_id"]

    assert result.fill_count >= 1, "E2E fixture binding run must record at least 1 fill"
    assert result.loaded_metadata["deployment_plan_id"] == plan["plan_id"]
    assert result.loaded_metadata["runtime_binding_id"] == binding.binding_id
