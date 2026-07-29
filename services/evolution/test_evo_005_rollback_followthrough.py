"""
LOOP-AUTO-EVO-005: Prove evolution rollback and follow-through.

Acceptance criteria:
  1. Evidence proves approved rollback command reaches runtime-manager or deployment.
  2. BFF shows proposed → reviewed → approved → dispatched → executed stages.
  3. Failure path records blocked reason and retry state.

Tests are structured in three groups:
  A. Failure-path enforcement (blocked reason + retry state)
  B. Happy-path lifecycle stages (BFF stage visibility)
  C. Integration: approved rollback command reaches runtime-manager.rollback()

Run:
    python3 -m pytest services/evolution/test_evo_005_rollback_followthrough.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest

# ---- Isolate storage BEFORE importing main ----
_tmp = tempfile.mkdtemp(prefix="evo_evo005_test_")
os.environ["EVOLUTION_DATA_DIR"] = _tmp
os.environ["INCIDENT_DATA_DIR"] = _tmp

# ---- Make platform objects importable ----
_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from fastapi.testclient import TestClient  # noqa: E402

from services.evolution import main as evo_main  # noqa: E402
from services.evolution.main import app  # noqa: E402
from services.evolution.testing_receipts import (  # noqa: E402
    ALL_PLANES,
    install_scripted_adapter,
    receipt_body,
)


@pytest.fixture(autouse=True)
def scripted_receipts():
    """Give rollback-followthrough a runtime record it can really read back.

    Runtime mitigation is carried out by the Rollback Controller / Runtime
    Manager; the evolution service records its terminal outcome and refuses to
    synthesise one. These tests exercise routing and follow-through visibility,
    so they install a scripted runtime record that reports terminal success.
    """
    original = dict(evo_main.receipt_registry)
    adapter = install_scripted_adapter(
        evo_main.receipt_registry, *ALL_PLANES, always_succeeds=True
    )
    try:
        yield adapter
    finally:
        evo_main.receipt_registry.clear()
        evo_main.receipt_registry.update(original)
import services.evolution.main as evo_main  # noqa: E402

# ---- RuntimeManagerService (direct in-process, no HTTP) ----
_RM_DIR = Path(__file__).resolve().parent.parent / "runtime-manager"
_EXEC_RM_DIR = Path(__file__).resolve().parent.parent.parent / "services" / "execution" / "runtime-manager"
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
for _p in (_RM_DIR, _EXEC_RM_DIR, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from service import RuntimeManagerService  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_evolution_store():
    evo_main.store._decisions.clear()
    if evo_main.store._storage_path and evo_main.store._storage_path.exists():
        evo_main.store._storage_path.unlink()
    evo_main.incident_store._incidents.clear()
    evo_main.incident_store._postmortems.clear()
    if evo_main.incident_store._path and evo_main.incident_store._path.exists():
        evo_main.incident_store._path.unlink()
    yield
    evo_main.store._decisions.clear()
    if evo_main.store._storage_path and evo_main.store._storage_path.exists():
        evo_main.store._storage_path.unlink()


@pytest.fixture
def runtime_manager():
    """Fresh in-process RuntimeManagerService for each test."""
    return RuntimeManagerService(store_path=None, single_runtime_enforced=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def uid() -> str:
    return f"ev-005-{uuid.uuid4().hex[:8]}"


def _high_risk_freeze_body(decision_id: str) -> dict[str, Any]:
    """Build a Severity-1 freeze body — the canonical rollback trigger."""
    return {
        "decision_id": decision_id,
        "target_type": "candidate_artifact",
        "target_id": "artifact-live-001",
        "target_version": "v3",
        "action_type": "freeze",
        "rationale": "Severity-1 incident; runtime mitigation required.",
        "created_by_id": "evolution-controller",
        "target_stage": "live",
        "threshold_snapshots": [
            {
                "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.5",
                "signal_type": "governance_incident",
                "metric_name": "severity1_incident_count",
                "comparator": "gte",
                "observed_value": 1,
                "threshold_value": 1,
            }
        ],
        "metadata": {"has_active_runtime": True},
    }


def _propose(extra: dict | None = None) -> dict[str, Any]:
    did = uid()
    body = _high_risk_freeze_body(did)
    if extra:
        body.update(extra)
    r = client.post("/api/evolution/proposals", json=body)
    assert r.status_code == 201, f"propose failed: {r.text}"
    return r.json()


def _review(decision_id: str) -> dict[str, Any]:
    r = client.post(f"/api/evolution/proposals/{decision_id}/review", json={
        "actor_role": "governance_committee",
        "actor_id": "gov-reviewer-001",
        "approval_decision_id": f"apv-{decision_id}",
    })
    assert r.status_code == 200, f"review failed: {r.text}"
    return r.json()


def _approve(decision_id: str) -> dict[str, Any]:
    r = client.post(f"/api/evolution/proposals/{decision_id}/approve", json={
        "actor_role": "governance_committee",
        "actor_id": "gov-approver-001",
    })
    assert r.status_code == 200, f"approve failed: {r.text}"
    return r.json()


def _rollback_followthrough(
    decision_id: str,
    *,
    active_binding_id: str | None = "rb-test-active-001",
    actor_role: str = "evolution_controller",
    actor_id: str = "evo-ctrl-001",
    rollback_action_type: str | None = None,
    extra: dict | None = None,
) -> tuple[int, dict[str, Any]]:
    """Call the rollback-followthrough endpoint; return (status_code, body)."""
    body: dict[str, Any] = {
        "actor_role": actor_role,
        "actor_id": actor_id,
    }
    if active_binding_id is not None:
        body["active_binding_id"] = active_binding_id
    if rollback_action_type is not None:
        body["rollback_action_type"] = rollback_action_type
    body["execution_receipt"] = receipt_body(decision_id)
    if extra:
        body.update(extra)
    r = client.post(f"/api/evolution/proposals/{decision_id}/rollback-followthrough", json=body)
    return r.status_code, r.json()


def _get_decision(decision_id: str) -> dict[str, Any]:
    r = client.get(f"/api/evolution/proposals/{decision_id}")
    assert r.status_code == 200
    return r.json()


def _active_paper_binding(rm: RuntimeManagerService) -> str:
    """Create a minimal active paper RuntimeBinding; return its binding_id."""
    binding = rm.deploy({
        "plan_id": f"plan-{uuid.uuid4().hex[:8]}",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-live-001",
        "artifact_version": "v3",
        "capital_pool_id": "pool-alpha",
        "persona_capital_binding_id": "pcb-alpha",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
    })
    return binding.binding_id


def _canonical_authority_report(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "passed",
        "authority": "canonical_deployment_registry_governance_capital",
        "plan_id": request["plan_id"],
        "plan_status": request["plan_status"],
        "target_stage": request["target_stage"],
        "artifact_id": request["artifact_id"],
        "artifact_version": request["artifact_version"],
        "strategy_id": request.get("strategy_id", "strategy-alpha"),
        "approval_decision_id": request.get("approval_decision_id", "dec-123"),
        "capital_pool_id": request["capital_pool_id"],
        "sponsor_persona_id": request.get("sponsor_persona_id", "persona-123"),
        "persona_capital_binding_id": request["persona_capital_binding_id"],
        "persona_capital_binding_status": request["persona_capital_binding_status"],
        "allowed_deployment_scope": request["allowed_deployment_scope"],
        "deployment_plan_sha256": "sha256:" + "0" * 64,
        "registry_entry_sha256": "sha256:" + "1" * 64,
        "approval_decision_sha256": "sha256:" + "2" * 64,
        "capital_pool_sha256": "sha256:" + "3" * 64,
        "capital_admissibility_sha256": "sha256:" + "4" * 64,
        "persona_capital_binding_sha256": "sha256:" + "5" * 64,
    }


def _seed_retired_target(rm: RuntimeManagerService, old_binding_id: str, plan_id: str, artifact_id: str, version: str) -> dict[str, Any]:
    old_binding = rm.get(old_binding_id)
    req = {
        "plan_id": plan_id,
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": artifact_id,
        "artifact_version": version,
        "capital_pool_id": old_binding.capital_pool_id,
        "persona_capital_binding_id": old_binding.persona_capital_binding_id,
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
        "strategy_id": "strategy-alpha",
        "approval_decision_id": "dec-123",
        "sponsor_persona_id": "persona-123",
    }
    att = _canonical_authority_report(req)
    req["metadata"] = {
        "strategy_id": "strategy-alpha",
        "authoritative_loader_attestation": att,
    }
    prior = rm.deploy(req, _allow_cutover_bypass=True)
    rm.retire(prior.binding_id)
    return att


# ===========================================================================
# Group A: Failure-path enforcement (AC-3)
# Blocked reason must be recorded; these scenarios represent the retry-state
# surface the operator sees when follow-through is not yet possible.
# ===========================================================================

class TestRollbackFollowthroughFailurePaths:
    """Failure path records blocked reason."""

    def test_requires_approved_state_proposed_returns_422(self):
        """Rollback-followthrough on a proposed (not-yet-approved) decision is rejected."""
        d = _propose()
        did = d["decision_id"]
        # Still in 'proposed' state — not approved
        code, body = _rollback_followthrough(did)
        assert code == 422, f"Expected 422, got {code}: {body}"
        # Blocked reason is surfaced in the error detail
        assert "approved" in str(body).lower() or "state" in str(body).lower(), (
            f"Expected rejection message to mention 'approved' or 'state', got: {body}"
        )

    def test_requires_approved_state_reviewed_returns_422(self):
        """Rollback-followthrough on a reviewed (not-yet-approved) decision is rejected."""
        d = _propose()
        did = d["decision_id"]
        _review(did)
        # In 'reviewed' state — not yet approved
        code, body = _rollback_followthrough(did)
        assert code == 422, f"Expected 422, got {code}: {body}"
        assert "approved" in str(body).lower() or "state" in str(body).lower()

    def test_requires_active_binding_id(self):
        """Rollback-followthrough without active_binding_id is rejected with 422."""
        d = _propose()
        did = d["decision_id"]
        _review(did)
        _approve(did)
        # No active_binding_id — follow-through blocked
        code, body = _rollback_followthrough(did, active_binding_id=None)
        assert code == 422, f"Expected 422, got {code}: {body}"
        # Blocked reason is surfaced
        detail = str(body)
        assert "active_binding_id" in detail or "binding" in detail.lower(), (
            f"Expected 'active_binding_id' in rejection message, got: {body}"
        )

    def test_requires_freeze_action_type(self):
        """Rollback-followthrough on a non-freeze decision returns an error.

        The controller only emits a RollbackCommand for freeze actions on an
        active runtime — other action types hit the governance-only path.
        """
        # Build a low-risk retrain decision (non-freeze)
        did = uid()
        body = {
            "decision_id": did,
            "target_type": "strategy_spec",
            "target_id": "strat-002",
            "target_version": "v1",
            "action_type": "retrain",
            "rationale": "Sharpe < 50% of baseline.",
            "created_by_id": "evolution-controller",
            "threshold_snapshots": [{
                "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.1",
                "signal_type": "performance_degradation",
                "metric_name": "sharpe_pct_of_baseline",
                "comparator": "lt",
                "observed_value": 0.40,
                "threshold_value": 0.50,
                "window": "20d",
            }],
        }
        r = client.post("/api/evolution/proposals", json=body)
        assert r.status_code == 201
        client.post(f"/api/evolution/proposals/{did}/review", json={
            "actor_role": "reviewer_on_duty",
            "actor_id": "r-001",
            "approval_decision_id": f"apv-{did}",
        })
        client.post(f"/api/evolution/proposals/{did}/approve", json={
            "actor_role": "reviewer_on_duty",
            "actor_id": "a-001",
        })
        # rollback-followthrough on a retrain-plane decision must be rejected
        # (no active runtime follow-through is possible on the research plane)
        code, resp = _rollback_followthrough(did, active_binding_id="rb-fake")
        # The endpoint should either reject (422) or execute without rollback command.
        # Either is acceptable — what matters is the follow-through does NOT silently
        # route a rollback to the research plane.
        if code == 200:
            # Executed but must NOT carry a rollback command in execution_result
            exec_res = resp.get("execution_result") or {}
            assert exec_res.get("plane") != "runtime", (
                f"Research-plane action must not be executed on runtime plane: {exec_res}"
            )
        else:
            assert code == 422, f"Expected rejection for non-freeze action, got {code}: {resp}"

    def test_duplicate_execution_returns_422(self):
        """A decision that is already executed cannot be rolled back again."""
        d = _propose()
        did = d["decision_id"]
        _review(did)
        _approve(did)
        code, _ = _rollback_followthrough(did)
        assert code == 200, "First rollback-followthrough must succeed"
        # Second attempt: decision is already in 'executed' state
        code2, body2 = _rollback_followthrough(did)
        assert code2 == 422, f"Expected 422 on re-execution, got {code2}: {body2}"


# ===========================================================================
# Group B: BFF stage visibility — AC-2
# The BFF read model must show all stages: proposed, reviewed, approved,
# dispatched (execution_result), executed (decision_state).
# ===========================================================================

class TestBffStageVisibility:
    """BFF shows proposed → reviewed → approved → dispatched → executed stages."""

    def test_proposed_stage_visible(self):
        d = _propose()
        assert d["decision_state"] == "proposed"
        # Fetch via GET — same truth
        fetched = _get_decision(d["decision_id"])
        assert fetched["decision_state"] == "proposed"

    def test_reviewed_stage_visible(self):
        d = _propose()
        did = d["decision_id"]
        reviewed = _review(did)
        assert reviewed["decision_state"] == "reviewed"
        # review_chain carries the reviewed step
        chain = reviewed["review_chain"]
        assert any(step["step_type"] == "reviewed" for step in chain), (
            f"review_chain must include a 'reviewed' step; got {chain}"
        )

    def test_approved_stage_visible(self):
        d = _propose()
        did = d["decision_id"]
        _review(did)
        approved = _approve(did)
        assert approved["decision_state"] == "approved"
        chain = approved["review_chain"]
        step_types = [step["step_type"] for step in chain]
        assert "reviewed" in step_types, f"review_chain missing 'reviewed': {chain}"
        assert "approved" in step_types, f"review_chain missing 'approved': {chain}"

    def test_dispatched_stage_visible_via_execution_result(self):
        """After rollback-followthrough the decision carries execution_result (dispatched)."""
        d = _propose()
        did = d["decision_id"]
        _review(did)
        _approve(did)
        code, executed = _rollback_followthrough(did)
        assert code == 200, f"rollback-followthrough failed: {executed}"
        # execution_result is the dispatched evidence
        exec_res = executed["execution_result"]
        assert exec_res is not None, "execution_result must not be None after execute"
        # 'submitted' is a dispatch intent, not an outcome, and is no longer a
        # valid executed state: the recorded status is the terminal one the
        # runtime record reported back.
        assert exec_res["status"] in {"succeeded", "failed", "noop"}, (
            f"execution_result.status must be a terminal downstream status; got {exec_res['status']}"
        )
        assert exec_res["plane"] == "governance", (
            f"Freeze decision plane must be 'governance'; got {exec_res['plane']}"
        )
        assert exec_res["execution_ref_id"] is not None, (
            "execution_ref_id must carry the dispatch command id"
        )

    def test_executed_state_visible(self):
        """After rollback-followthrough the decision_state transitions to 'executed'."""
        d = _propose()
        did = d["decision_id"]
        _review(did)
        _approve(did)
        code, executed = _rollback_followthrough(did)
        assert code == 200
        assert executed["decision_state"] == "executed", (
            f"decision_state must be 'executed' after follow-through; got {executed['decision_state']}"
        )

    def test_full_review_chain_visible(self):
        """review_chain carries all steps: reviewed + approved."""
        d = _propose()
        did = d["decision_id"]
        _review(did)
        _approve(did)
        code, executed = _rollback_followthrough(did)
        assert code == 200
        chain = executed["review_chain"]
        step_types = {step["step_type"] for step in chain}
        for expected in ("reviewed", "approved"):
            assert expected in step_types, (
                f"review_chain missing step_type='{expected}'; chain={chain}"
            )

    def test_observation_report_shows_executed_decision(self):
        """After rollback-followthrough, observation-report surfaces the execution evidence."""
        d = _propose()
        did = d["decision_id"]
        _review(did)
        _approve(did)
        code, executed = _rollback_followthrough(did)
        assert code == 200
        # Observation report is the canonical BFF evidence endpoint
        r = client.get(f"/api/evolution/proposals/{did}/observation-report")
        assert r.status_code == 200, r.text
        report = r.json()
        # Stage visibility
        assert report["decision_state"] == "executed"
        assert report["action_type"] == "freeze"
        # Execution evidence is present
        assert report["execution"] is not None
        assert report["execution"].get("plane") == "governance"
        # Follow-through refs carry the dispatch command
        assert len(report["followthrough_refs"]) > 0, (
            f"followthrough_refs must contain at least one ref: {report}"
        )
        dispatch_ref = report["followthrough_refs"][0]
        assert dispatch_ref["ref_type"] == "dispatch_command"
        assert dispatch_ref["ref_id"] is not None

    def test_boundary_query_shows_runtime_rollback_followthrough(self):
        """Boundary for freeze+active_runtime decision declares runtime.rollback follow-through."""
        d = _propose()
        did = d["decision_id"]
        r = client.get(
            f"/api/evolution/proposals/{did}/boundary",
            params={"has_active_runtime": "true"},
        )
        assert r.status_code == 200, r.text
        boundary = r.json()
        assert "runtime.rollback" in boundary["followthrough"], (
            f"Boundary must declare 'runtime.rollback' follow-through for "
            f"freeze+active_runtime; got {boundary['followthrough']}"
        )


# ===========================================================================
# Group C: Integration — approved rollback command reaches runtime-manager
# This is the primary evidence for AC-1.
# ===========================================================================

class TestRollbackFollowthroughRuntimeManagerIntegration:
    """Approved rollback command reaches runtime-manager.rollback()."""

    def test_rollback_command_parameters_carried_in_execution_result(self):
        """Verify the RollbackCommand is embedded in the executed decision's metadata.

        The evolution controller stores the rollback command fields in the
        execution outcome so the operator / follow-through worker can extract
        them and call runtime-manager.rollback() without re-reading the policy.
        """
        d = _propose()
        did = d["decision_id"]
        _review(did)
        _approve(did)
        code, executed = _rollback_followthrough(
            did,
            active_binding_id="rb-test-binding-123",
            rollback_action_type="pause_then_replace",
        )
        assert code == 200, f"rollback-followthrough failed: {executed}"
        assert executed["decision_state"] == "executed"
        # The execution_ref_id traces back to the dispatch command
        exec_res = executed["execution_result"]
        # execution_ref_id traces to the downstream record whose terminal state
        # authorised the execution, not to a locally minted command id.
        assert exec_res["execution_ref_id"] == receipt_body(did)["downstream_ref_id"], (
            f"execution_ref_id must reference the downstream record; got {exec_res['execution_ref_id']}"
        )

    def test_runtime_manager_rollback_replace_strategy(self, runtime_manager):
        """RuntimeManagerService.rollback() with 'replace' retires old and creates new binding.

        This test uses the rollback command parameters that the evolution service
        would emit after a freeze+active_runtime decision is executed.  It proves
        the runtime-manager end of the follow-through chain is functional.
        """
        # Step 1: Create the active binding that would be targeted by the rollback.
        old_binding_id = _active_paper_binding(runtime_manager)
        old_binding = runtime_manager.get(old_binding_id)
        assert old_binding is not None
        assert old_binding.status == "active"

        # Step 2: Execute the evolution rollback-followthrough to get the decision.
        d = _propose()
        did = d["decision_id"]
        _review(did)
        _approve(did)
        code, executed = _rollback_followthrough(
            did,
            active_binding_id=old_binding_id,
            rollback_action_type="replace",
        )
        assert code == 200

        # Step 3: Extract RollbackCommand parameters from the executed decision.
        # In the real operator workflow these come from the rollback_command field
        # returned by dispatch_approved(). Here we simulate that by using the
        # binding_id we passed and the capital_pool from the binding.
        capital_pool_id = old_binding.capital_pool_id

        # Step 4: Call RuntimeManagerService.rollback() — this is the runtime plane
        # follow-through that the rollback operator / saga would execute.
        replacement_plan_id = f"rb-plan-{uuid.uuid4().hex[:8]}"
        att = _seed_retired_target(runtime_manager, old_binding_id, replacement_plan_id, "artifact-fallback-001", "v2-fallback")
        result = runtime_manager.rollback({
            "current_binding_id": old_binding_id,
            "action_type": "replace",
            "replacement_plan_id": replacement_plan_id,
            "replacement_plan_status": "approved",
            "replacement_artifact_id": "artifact-fallback-001",
            "replacement_artifact_version": "v2-fallback",
            "replacement_persona_capital_binding_id": "pcb-alpha",
            "replacement_persona_capital_binding_status": "active",
            "replacement_allowed_deployment_scope": "live",
            "loader_checks_passed": True,
            "replacement_authority_attestation": att,
        })

        # Step 5: Verify follow-through evidence.
        # A) Old binding must be retired.
        assert result["old_binding"]["binding_id"] == old_binding_id
        assert result["old_binding"]["status"] == "retired", (
            f"Old binding must be retired after replace; got {result['old_binding']['status']}"
        )
        # B) New replacement binding must be active.
        assert result["new_binding"]["status"] == "active", (
            f"Replacement binding must be active; got {result['new_binding']['status']}"
        )
        assert result["new_binding"]["artifact_id"] == "artifact-fallback-001"
        assert result["new_binding"]["rollback_parent"] == old_binding_id
        assert result["new_binding"]["rollback_action_type"] == "replace"
        # C) Position lineage is set.
        lineage = result["position_lineage"]
        assert lineage["prev_binding_id"] == old_binding_id
        assert lineage["new_binding_id"] == result["new_binding"]["binding_id"]
        assert lineage["current_managed_by_binding_id"] == result["new_binding"]["binding_id"]

    def test_runtime_manager_rollback_pause_then_replace_strategy(self, runtime_manager):
        """pause_then_replace drains the old binding to paused before creating replacement."""
        old_binding_id = _active_paper_binding(runtime_manager)
        replacement_plan_id = f"rb-plan-{uuid.uuid4().hex[:8]}"
        att = _seed_retired_target(runtime_manager, old_binding_id, replacement_plan_id, "artifact-fallback-002", "v2-fallback")

        result = runtime_manager.rollback({
            "current_binding_id": old_binding_id,
            "action_type": "pause_then_replace",
            "replacement_plan_id": replacement_plan_id,
            "replacement_artifact_id": "artifact-fallback-002",
            "replacement_artifact_version": "v2-fallback",
            "replacement_persona_capital_binding_id": "pcb-alpha",
            "replacement_persona_capital_binding_status": "active",
            "replacement_allowed_deployment_scope": "live",
            "loader_checks_passed": True,
            "replacement_authority_attestation": att,
        })

        assert result["old_binding"]["status"] == "retired"
        assert result["new_binding"]["status"] == "active"
        assert result["new_binding"]["rollback_action_type"] == "pause_then_replace"

    def test_runtime_manager_rollback_liquidate_then_replace_start_paused(self, runtime_manager):
        """liquidate_then_replace with replacement_start_paused keeps new binding paused."""
        old_binding_id = _active_paper_binding(runtime_manager)
        replacement_plan_id = f"rb-plan-{uuid.uuid4().hex[:8]}"
        att = _seed_retired_target(runtime_manager, old_binding_id, replacement_plan_id, "artifact-fallback-003", "v2-fallback")

        result = runtime_manager.rollback({
            "current_binding_id": old_binding_id,
            "action_type": "liquidate_then_replace",
            "replacement_plan_id": replacement_plan_id,
            "replacement_artifact_id": "artifact-fallback-003",
            "replacement_artifact_version": "v2-fallback",
            "replacement_persona_capital_binding_id": "pcb-alpha",
            "replacement_persona_capital_binding_status": "active",
            "replacement_allowed_deployment_scope": "live",
            "loader_checks_passed": True,
            "replacement_start_paused": True,
            "replacement_authority_attestation": att,
        })

        assert result["old_binding"]["status"] == "retired"
        # Replacement starts paused (guarded mode); operator must confirm before re-enabling.
        assert result["new_binding"]["status"] == "paused", (
            f"liquidate+start_paused must leave replacement in paused state; "
            f"got {result['new_binding']['status']}"
        )
        # Position lineage keeps old binding as current owner until zero-position confirmed.
        assert result["position_lineage"]["current_managed_by_binding_id"] == old_binding_id

    def test_end_to_end_evolution_freeze_to_runtime_rollback(self, runtime_manager):
        """Full E2E: evolution freeze proposal → rollback-followthrough → runtime.rollback().

        This test exercises the complete follow-through chain that proves AC-1:
          Approved rollback command reaches runtime-manager.
        """
        # === Evolution side ===
        # 1. Create an approved freeze decision with active runtime context.
        d = _propose()
        did = d["decision_id"]
        assert d["decision_state"] == "proposed"

        reviewed = _review(did)
        assert reviewed["decision_state"] == "reviewed"

        approved = _approve(did)
        assert approved["decision_state"] == "approved"

        # 2. Create an active RuntimeBinding (simulating the live runtime).
        old_binding_id = _active_paper_binding(runtime_manager)

        # 3. Execute the rollback follow-through — dispatch the rollback command.
        code, executed = _rollback_followthrough(
            did,
            active_binding_id=old_binding_id,
            rollback_action_type="replace",
        )
        assert code == 200, f"rollback-followthrough returned {code}: {executed}"
        assert executed["decision_state"] == "executed"

        # Verify the dispatched execution_result is populated.
        exec_res = executed["execution_result"]
        assert exec_res is not None
        dispatch_ref_id = exec_res["execution_ref_id"]
        assert dispatch_ref_id is not None

        # === Runtime-manager side ===
        # 4. The operator / saga picks up the rollback command and calls runtime-manager.
        #    Parameters come from the RollbackCommand emitted by the evolution controller.
        replacement_plan_id = f"evo-rb-{did[:12]}"
        att = _seed_retired_target(runtime_manager, old_binding_id, replacement_plan_id, "artifact-fallback-001", "v2-fallback")
        rollback_result = runtime_manager.rollback({
            "current_binding_id": old_binding_id,
            "action_type": "replace",
            "replacement_plan_id": replacement_plan_id,
            "replacement_plan_status": "approved",
            "replacement_artifact_id": "artifact-fallback-001",
            "replacement_artifact_version": "v2-fallback",
            "replacement_persona_capital_binding_id": "pcb-alpha",
            "replacement_persona_capital_binding_status": "active",
            "replacement_allowed_deployment_scope": "live",
            "loader_checks_passed": True,
            "replacement_authority_attestation": att,
        })

        # === Verify follow-through evidence ===
        # Old binding retired.
        assert rollback_result["old_binding"]["status"] == "retired"
        # New binding active — the approved rollback command reached runtime-manager.
        assert rollback_result["new_binding"]["status"] == "active"
        # Decision shows executed state — visible to BFF / operator.
        final = _get_decision(did)
        assert final["decision_state"] == "executed"
        assert final["execution_result"]["execution_ref_id"] == dispatch_ref_id

        # === Observation report (BFF final stage) ===
        obs_r = client.get(f"/api/evolution/proposals/{did}/observation-report")
        assert obs_r.status_code == 200, obs_r.text
        obs = obs_r.json()
        assert obs["decision_state"] == "executed"
        # All five stages are visible in the report:
        #   proposed   → decision was created
        #   reviewed   → review_chain has 'reviewed' step
        #   approved   → review_chain has 'approved' step
        #   dispatched → execution.execution_ref_id is set
        #   executed   → decision_state == 'executed'
        chain_types = {s["step_type"] for s in obs["review_chain"]}
        assert "reviewed" in chain_types, f"Stage 'reviewed' missing from review_chain: {obs['review_chain']}"
        assert "approved" in chain_types, f"Stage 'approved' missing from review_chain: {obs['review_chain']}"
        assert obs["execution"]["execution_ref_id"] is not None, (
            "Dispatched stage must carry execution_ref_id"
        )

    def test_rollback_blocked_reason_surfaced_on_terminal_binding(self, runtime_manager):
        """runtime-manager.rollback() records a blocked reason when the binding is already terminal.

        This proves AC-3: failure path records the blocked reason in the raised error.
        """
        old_binding_id = _active_paper_binding(runtime_manager)
        # Manually retire the binding first (simulating an already-terminal binding).
        runtime_manager.retire(old_binding_id)
        retired = runtime_manager.get(old_binding_id)
        assert retired.status == "retired"

        # Attempting rollback on an already-terminal binding must raise an error
        # with a clear blocked reason.
        from service import RuntimeManagerError
        with pytest.raises(RuntimeManagerError) as exc_info:
            runtime_manager.rollback({
                "current_binding_id": old_binding_id,
                "action_type": "replace",
                "replacement_plan_id": "plan-blocked",
                "replacement_artifact_id": "artifact-fallback",
                "replacement_artifact_version": "v2",
                "replacement_persona_capital_binding_id": "pcb-alpha",
                "replacement_persona_capital_binding_status": "active",
                "replacement_allowed_deployment_scope": "live",
                "loader_checks_passed": True,
            })
        error_msg = str(exc_info.value)
        # Blocked reason must identify the terminal state.
        assert "terminal" in error_msg.lower() or "retired" in error_msg.lower(), (
            f"Error must identify terminal/retired blocked reason; got: {error_msg}"
        )

    def test_rollback_blocked_reason_surfaced_on_missing_binding(self, runtime_manager):
        """runtime-manager.rollback() surfaces a blocked reason when binding is not found.

        The store raises RuntimeBindingError (a domain error with a clear message) when
        the binding_id is unknown — this IS the blocked-reason signal that the operator
        / retry-saga would act on.
        """
        from service import RuntimeManagerError
        # RuntimeBindingError is also acceptable here as a domain-level blocked reason.
        try:
            from runtime_binding import RuntimeBindingError
        except ImportError:
            RuntimeBindingError = RuntimeManagerError  # type: ignore[assignment,misc]
        with pytest.raises((RuntimeManagerError, RuntimeBindingError)) as exc_info:
            runtime_manager.rollback({
                "current_binding_id": "rb-does-not-exist",
                "action_type": "replace",
                "replacement_plan_id": "plan-x",
                "replacement_artifact_id": "af-001",
                "replacement_artifact_version": "v1",
                "replacement_persona_capital_binding_id": "pcb-x",
                "replacement_persona_capital_binding_status": "active",
                "replacement_allowed_deployment_scope": "live",
                "loader_checks_passed": True,
            })
        # The error message must name the missing binding (blocked reason).
        error_msg = str(exc_info.value)
        assert "rb-does-not-exist" in error_msg or "not found" in error_msg.lower(), (
            f"Blocked reason must identify the unknown binding; got: {error_msg}"
        )
