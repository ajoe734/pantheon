"""
Tests for the Evolution Service API.

Exercises the full EvolutionDecision lifecycle via FastAPI TestClient:
  proposed -> reviewed -> approved -> executed

Also verifies:
  - cooldown and observation-window metadata is set on execute
  - actor-role enforcement (wrong role rejected)
  - single-active-rule enforcement
  - evidence linkage requirement
  - threshold evaluator endpoint
  - list/filter query path
  - boundary query endpoint

Run:
    python3 -m pytest services/evolution/test_evolution_service.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

# ---- Isolate storage BEFORE importing main ----
_tmp = tempfile.mkdtemp(prefix="evo_test_")
os.environ["EVOLUTION_DATA_DIR"] = _tmp
os.environ["INCIDENT_DATA_DIR"] = _tmp

# ---- Make platform objects importable ----
_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from fastapi.testclient import TestClient  # noqa: E402

from services.evolution import main as evo_main  # noqa: E402
from services.evolution.main import app  # noqa: E402

# ---- Incident domain objects (for seeding postmortems in tests) ----
_INC_SVC = Path(__file__).resolve().parent.parent / "incident"
if str(_INC_SVC) not in sys.path:
    sys.path.insert(0, str(_INC_SVC))

from incident import IncidentCase, Postmortem  # noqa: E402

client = TestClient(app)


def uid() -> str:
    return f"ev-t-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def reset_store():
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
    evo_main.incident_store._incidents.clear()
    evo_main.incident_store._postmortems.clear()
    if evo_main.incident_store._path and evo_main.incident_store._path.exists():
        evo_main.incident_store._path.unlink()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LOW_RISK_BODY = dict(
    target_type="strategy_spec",
    target_id="strat-001",
    target_version="v1",
    action_type="retrain",
    rationale="Sharpe < 50% of baseline over last 20 days.",
    created_by_id="evolution-controller",
    threshold_snapshots=[
        {
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.1",
            "signal_type": "performance_degradation",
            "metric_name": "sharpe_pct_of_baseline",
            "comparator": "lt",
            "observed_value": 0.42,
            "threshold_value": 0.50,
            "window": "20d",
        }
    ],
)

MEDIUM_RISK_BODY = dict(
    target_type="candidate_artifact",
    target_id="artifact-002",
    target_version="v1",
    action_type="freeze",
    rationale="PSI exceeded 0.30 for paper artifact.",
    created_by_id="evolution-controller",
    target_stage="paper",
    threshold_snapshots=[
        {
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.3",
            "signal_type": "feature_drift",
            "metric_name": "population_stability_index",
            "comparator": "gt",
            "observed_value": 0.31,
            "threshold_value": 0.30,
        }
    ],
)

HIGH_RISK_BODY = dict(
    target_type="persona",
    target_id="persona-live-003",
    target_version="v2",
    action_type="freeze",
    rationale="Severity-1 incident.",
    created_by_id="evolution-controller",
    target_stage="live",
    linked_incident_id="inc-sev1-2026-001",
    threshold_snapshots=[
        {
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.5",
            "signal_type": "governance_incident",
            "metric_name": "severity1_incident_count",
            "comparator": "gte",
            "observed_value": 1,
            "threshold_value": 1,
        }
    ],
)


def propose(extra: dict | None = None) -> dict:
    body = {**LOW_RISK_BODY, "decision_id": uid()}
    if extra:
        body.update(extra)
    r = client.post("/api/evolution/proposals", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def advance_to_reviewed(decision_id: str, apv_id: str = "apv-001") -> dict:
    r = client.post(f"/api/evolution/proposals/{decision_id}/review", json={
        "actor_role": "reviewer_on_duty",
        "actor_id": "reviewer-001",
        "approval_decision_id": apv_id,
    })
    assert r.status_code == 200, r.text
    return r.json()


def advance_to_approved(decision_id: str, actor_role: str = "reviewer_on_duty") -> dict:
    r = client.post(f"/api/evolution/proposals/{decision_id}/approve", json={
        "actor_role": actor_role,
        "actor_id": "approver-001",
    })
    assert r.status_code == 200, r.text
    return r.json()


def advance_to_executed(decision_id: str) -> dict:
    r = client.post(f"/api/evolution/proposals/{decision_id}/execute", json={
        "actor_role": "evolution_controller",
        "actor_id": "evo-ctrl",
    })
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Propose — success paths
# ---------------------------------------------------------------------------

def test_propose_low_risk_returns_proposed():
    body = {**LOW_RISK_BODY, "decision_id": uid()}
    r = client.post("/api/evolution/proposals", json=body)
    assert r.status_code == 201
    d = r.json()
    assert d["decision_state"] == "proposed"
    assert d["risk_level"] == "low"
    assert d["action_type"] == "retrain"
    assert d["is_active"] is True
    assert d["cooldown_ends_at"] is None  # not yet executed


def test_propose_medium_risk_freeze_paper():
    body = {**MEDIUM_RISK_BODY, "decision_id": uid()}
    r = client.post("/api/evolution/proposals", json=body)
    assert r.status_code == 201
    d = r.json()
    assert d["decision_state"] == "proposed"
    assert d["risk_level"] == "medium"
    assert d["target_stage"] == "paper"


def test_propose_high_risk_freeze_live():
    body = {**HIGH_RISK_BODY, "decision_id": uid()}
    r = client.post("/api/evolution/proposals", json=body)
    assert r.status_code == 201
    d = r.json()
    assert d["decision_state"] == "proposed"
    assert d["risk_level"] == "high"
    assert d["linked_incident_id"] == "inc-sev1-2026-001"


# ---------------------------------------------------------------------------
# Propose — error paths
# ---------------------------------------------------------------------------

def test_propose_missing_evidence_link_rejected():
    body = {
        "decision_id": uid(),
        "target_type": "strategy_spec",
        "target_id": "strat-no-evidence",
        "target_version": "v1",
        "action_type": "retrain",
        "rationale": "Missing evidence.",
        "created_by_id": "evolution-controller",
        # no evidence_refs, threshold_snapshots, linked_incident_id, linked_postmortem_id
    }
    r = client.post("/api/evolution/proposals", json=body)
    assert r.status_code == 422
    assert "evidence" in r.json()["detail"].lower()


def test_propose_invalid_proposer_role_rejected():
    body = {
        **LOW_RISK_BODY,
        "decision_id": uid(),
        "created_by_role": "reviewer_on_duty",  # not a valid proposer role
    }
    r = client.post("/api/evolution/proposals", json=body)
    assert r.status_code == 422


def test_propose_freeze_without_target_stage_rejected():
    body = {
        "decision_id": uid(),
        "target_type": "strategy_spec",
        "target_id": "strat-no-stage",
        "target_version": "v1",
        "action_type": "freeze",
        "rationale": "Missing target_stage.",
        "created_by_id": "evolution-controller",
        "linked_incident_id": "inc-001",
    }
    r = client.post("/api/evolution/proposals", json=body)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Full lifecycle: proposed -> reviewed -> approved -> executed
# ---------------------------------------------------------------------------

def test_full_lifecycle_low_risk():
    # Step 1: propose
    did = uid()
    body = {**LOW_RISK_BODY, "decision_id": did}
    r = client.post("/api/evolution/proposals", json=body)
    assert r.status_code == 201
    assert r.json()["decision_state"] == "proposed"

    # Step 2: review
    r = client.post(f"/api/evolution/proposals/{did}/review", json={
        "actor_role": "reviewer_on_duty",
        "actor_id": "rev-001",
        "approval_decision_id": "apv-low-001",
    })
    assert r.status_code == 200
    assert r.json()["decision_state"] == "reviewed"
    chain = r.json()["review_chain"]
    assert any(s["step_type"] == "reviewed" for s in chain)

    # Step 3: approve
    r = client.post(f"/api/evolution/proposals/{did}/approve", json={
        "actor_role": "reviewer_on_duty",
        "actor_id": "approver-001",
    })
    assert r.status_code == 200
    assert r.json()["decision_state"] == "approved"

    # Step 4: execute
    r = client.post(f"/api/evolution/proposals/{did}/execute", json={
        "actor_role": "evolution_controller",
        "actor_id": "evo-ctrl",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["decision_state"] == "executed"

    # Cooldown and observation windows must be present after execution
    assert d["cooldown_started_at"] is not None
    assert d["cooldown_ends_at"] is not None
    assert d["observation_window_started_at"] is not None
    assert d["observation_window_ends_at"] is not None

    # Low-risk retrain: 3-day cooldown, 7-day observation
    from datetime import datetime, timezone
    cd_start = datetime.fromisoformat(d["cooldown_started_at"].replace("Z", "+00:00"))
    cd_end = datetime.fromisoformat(d["cooldown_ends_at"].replace("Z", "+00:00"))
    assert (cd_end - cd_start).days == 3

    obs_start = datetime.fromisoformat(d["observation_window_started_at"].replace("Z", "+00:00"))
    obs_end = datetime.fromisoformat(d["observation_window_ends_at"].replace("Z", "+00:00"))
    assert (obs_end - obs_start).days == 7

    # Review chain must contain reviewed, approved, executed steps
    step_types = {s["step_type"] for s in d["review_chain"]}
    assert {"reviewed", "approved", "executed"}.issubset(step_types)

    # is_active is True while inside cooldown / observation window
    assert d["is_active"] is True


def test_full_lifecycle_medium_risk_freeze_paper():
    did = uid()
    body = {**MEDIUM_RISK_BODY, "decision_id": did}
    client.post("/api/evolution/proposals", json=body).raise_for_status()

    client.post(f"/api/evolution/proposals/{did}/review", json={
        "actor_role": "reviewer",
        "actor_id": "rev-002",
        "approval_decision_id": "apv-med-002",
    }).raise_for_status()

    client.post(f"/api/evolution/proposals/{did}/approve", json={
        "actor_role": "risk_owner",
        "actor_id": "risk-002",
    }).raise_for_status()

    r = client.post(f"/api/evolution/proposals/{did}/execute", json={
        "actor_role": "evolution_controller",
        "actor_id": "evo-ctrl",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["decision_state"] == "executed"

    # Medium-risk freeze paper: 7-day cooldown
    from datetime import datetime
    cd_start = datetime.fromisoformat(d["cooldown_started_at"].replace("Z", "+00:00"))
    cd_end = datetime.fromisoformat(d["cooldown_ends_at"].replace("Z", "+00:00"))
    assert (cd_end - cd_start).days == 7


def test_full_lifecycle_high_risk_freeze_live():
    did = uid()
    body = {**HIGH_RISK_BODY, "decision_id": did}
    client.post("/api/evolution/proposals", json=body).raise_for_status()

    client.post(f"/api/evolution/proposals/{did}/review", json={
        "actor_role": "governance_committee",
        "actor_id": "committee-003",
        "approval_decision_id": "apv-high-003",
    }).raise_for_status()

    client.post(f"/api/evolution/proposals/{did}/approve", json={
        "actor_role": "governance_committee",
        "actor_id": "committee-003",
    }).raise_for_status()

    r = client.post(f"/api/evolution/proposals/{did}/execute", json={
        "actor_role": "evolution_controller",
        "actor_id": "evo-ctrl",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["decision_state"] == "executed"

    # High-risk freeze live: 14-day cooldown
    from datetime import datetime
    cd_start = datetime.fromisoformat(d["cooldown_started_at"].replace("Z", "+00:00"))
    cd_end = datetime.fromisoformat(d["cooldown_ends_at"].replace("Z", "+00:00"))
    assert (cd_end - cd_start).days == 14


# ---------------------------------------------------------------------------
# Actor-role enforcement
# ---------------------------------------------------------------------------

def test_review_wrong_role_rejected():
    """Low-risk decision requires reviewer_on_duty or automated_gate; reviewer is not allowed."""
    d = propose()
    r = client.post(f"/api/evolution/proposals/{d['decision_id']}/review", json={
        "actor_role": "governance_committee",  # too high for low-risk path
        "actor_id": "actor-001",
        "approval_decision_id": "apv-xxx",
    })
    assert r.status_code == 422


def test_approve_wrong_role_rejected():
    d = propose()
    advance_to_reviewed(d["decision_id"])
    r = client.post(f"/api/evolution/proposals/{d['decision_id']}/approve", json={
        "actor_role": "governance_committee",  # not in low-risk approval matrix
        "actor_id": "actor-002",
    })
    assert r.status_code == 422


def test_execute_wrong_role_rejected():
    d = propose()
    advance_to_reviewed(d["decision_id"])
    advance_to_approved(d["decision_id"])
    r = client.post(f"/api/evolution/proposals/{d['decision_id']}/execute", json={
        "actor_role": "reviewer_on_duty",  # not a valid execution role
        "actor_id": "actor-003",
    })
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# State-machine transitions
# ---------------------------------------------------------------------------

def test_cannot_review_from_approved():
    d = propose()
    advance_to_reviewed(d["decision_id"])
    advance_to_approved(d["decision_id"])
    r = client.post(f"/api/evolution/proposals/{d['decision_id']}/review", json={
        "actor_role": "reviewer_on_duty",
        "actor_id": "rev-late",
        "approval_decision_id": "apv-late",
    })
    assert r.status_code == 422


def test_cannot_approve_from_proposed():
    d = propose()
    r = client.post(f"/api/evolution/proposals/{d['decision_id']}/approve", json={
        "actor_role": "reviewer_on_duty",
        "actor_id": "approver-too-early",
    })
    assert r.status_code == 422


def test_cannot_execute_from_proposed():
    d = propose()
    r = client.post(f"/api/evolution/proposals/{d['decision_id']}/execute", json={
        "actor_role": "evolution_controller",
        "actor_id": "evo-ctrl",
    })
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Reject and cancel paths
# ---------------------------------------------------------------------------

def test_reject_from_reviewed():
    d = propose()
    advance_to_reviewed(d["decision_id"])
    r = client.post(f"/api/evolution/proposals/{d['decision_id']}/reject", json={
        "actor_role": "reviewer_on_duty",
        "actor_id": "rev-reject",
        "note": "Not actionable at this time.",
    })
    assert r.status_code == 200
    assert r.json()["decision_state"] == "rejected"


def test_cancel_from_proposed():
    d = propose()
    r = client.post(f"/api/evolution/proposals/{d['decision_id']}/cancel", json={
        "actor_role": "operator",
        "actor_id": "op-cancel",
        "note": "Stale proposal — manually cancelled.",
    })
    assert r.status_code == 200
    assert r.json()["decision_state"] == "canceled"


def test_cancel_from_approved():
    d = propose()
    advance_to_reviewed(d["decision_id"])
    advance_to_approved(d["decision_id"])
    r = client.post(f"/api/evolution/proposals/{d['decision_id']}/cancel", json={
        "actor_role": "risk_owner",
        "actor_id": "risk-cancel",
        "note": "Situation changed; decision no longer needed.",
    })
    assert r.status_code == 200
    assert r.json()["decision_state"] == "canceled"


# ---------------------------------------------------------------------------
# Single-active-rule
# ---------------------------------------------------------------------------

def test_single_active_rule_blocks_duplicate_target():
    target_id = f"strat-single-{uuid.uuid4().hex[:6]}"
    # First proposal — should succeed
    body1 = {**LOW_RISK_BODY, "decision_id": uid(), "target_id": target_id}
    r1 = client.post("/api/evolution/proposals", json=body1)
    assert r1.status_code == 201

    # Second proposal for the same target — should be blocked
    body2 = {**LOW_RISK_BODY, "decision_id": uid(), "target_id": target_id}
    r2 = client.post("/api/evolution/proposals", json=body2)
    assert r2.status_code == 422
    assert "single-active-rule" in r2.json()["detail"]


# ---------------------------------------------------------------------------
# List / filter
# ---------------------------------------------------------------------------

def test_list_returns_all():
    r = client.get("/api/evolution/proposals")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_list_filter_by_state():
    # Create a fresh proposed decision
    did = uid()
    body = {**LOW_RISK_BODY, "decision_id": did, "target_id": f"strat-filter-{did}"}
    client.post("/api/evolution/proposals", json=body).raise_for_status()

    r = client.get("/api/evolution/proposals", params={"decision_state": "proposed"})
    assert r.status_code == 200
    states = {d["decision_state"] for d in r.json()}
    assert states == {"proposed"}


def test_list_filter_by_target_id():
    target_id = f"strat-list-{uuid.uuid4().hex[:6]}"
    did = uid()
    body = {**LOW_RISK_BODY, "decision_id": did, "target_id": target_id}
    client.post("/api/evolution/proposals", json=body).raise_for_status()

    r = client.get("/api/evolution/proposals", params={"target_id": target_id})
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["target_id"] == target_id


def test_list_active_only():
    r = client.get("/api/evolution/proposals", params={"active_only": "true"})
    assert r.status_code == 200
    for item in r.json():
        assert item["is_active"] is True


# ---------------------------------------------------------------------------
# Get single
# ---------------------------------------------------------------------------

def test_get_single():
    d = propose()
    r = client.get(f"/api/evolution/proposals/{d['decision_id']}")
    assert r.status_code == 200
    assert r.json()["decision_id"] == d["decision_id"]


def test_get_missing_returns_404():
    r = client.get("/api/evolution/proposals/does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Boundary query
# ---------------------------------------------------------------------------

def test_boundary_low_risk():
    d = propose()
    r = client.get(f"/api/evolution/proposals/{d['decision_id']}/boundary")
    assert r.status_code == 200
    b = r.json()
    assert b["default_cooldown_days"] == 3
    assert b["default_observation_days"] == 7
    assert "reviewer_on_duty" in b["reviewed_owner_roles"]


def test_boundary_high_risk_live_with_active_runtime():
    did = uid()
    body = {**HIGH_RISK_BODY, "decision_id": did, "target_id": f"persona-br-{did}"}
    client.post("/api/evolution/proposals", json=body).raise_for_status()

    r = client.get(
        f"/api/evolution/proposals/{did}/boundary",
        params={"has_active_runtime": "true"},
    )
    assert r.status_code == 200
    b = r.json()
    assert b["boundary_key"] == "freeze_live_active_runtime"
    assert b["default_cooldown_days"] == 14
    assert "deployment.freeze_stage" in b["followthrough"]


# ---------------------------------------------------------------------------
# Threshold evaluator
# ---------------------------------------------------------------------------

def test_threshold_eval_retrain_signal():
    r = client.post("/api/evolution/threshold-evaluate", json={
        "snapshot": {
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.1",
            "signal_type": "performance_degradation",
            "metric_name": "sharpe_pct_of_baseline",
            "comparator": "lt",
            "observed_value": 0.40,
            "threshold_value": 0.50,
        }
    })
    assert r.status_code == 200
    assert r.json()["proposed_action"] == "retrain"


def test_threshold_eval_freeze_governance_incident():
    r = client.post("/api/evolution/threshold-evaluate", json={
        "snapshot": {
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.5",
            "signal_type": "governance_incident",
            "metric_name": "severity1_incident_count",
            "comparator": "gte",
            "observed_value": 1,
            "threshold_value": 1,
        },
        "context": {"has_active_runtime": True},
    })
    assert r.status_code == 200
    ev = r.json()
    assert ev["proposed_action"] == "freeze"
    assert ev["requires_runtime_followthrough"] is True


def test_threshold_eval_observe_psi_warning():
    r = client.post("/api/evolution/threshold-evaluate", json={
        "snapshot": {
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.3",
            "signal_type": "feature_drift",
            "metric_name": "population_stability_index",
            "comparator": "gt",
            "observed_value": 0.22,
            "threshold_value": 0.20,
        }
    })
    assert r.status_code == 200
    assert r.json()["proposed_action"] == "observe"


def test_threshold_eval_unknown_metric_rejected():
    r = client.post("/api/evolution/threshold-evaluate", json={
        "snapshot": {
            "policy_source": "somewhere",
            "signal_type": "feature_drift",
            "metric_name": "unknown_metric_xyz",
            "comparator": "gt",
            "observed_value": 99,
            "threshold_value": 1,
        }
    })
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Evidence linkage in list response
# ---------------------------------------------------------------------------

def test_propose_with_evidence_refs_round_trips():
    did = uid()
    body = {
        "decision_id": did,
        "target_type": "strategy_spec",
        "target_id": f"strat-ev-{did}",
        "target_version": "v1",
        "action_type": "retrain",
        "rationale": "Test evidence ref round-trip.",
        "created_by_id": "evolution-controller",
        "evidence_refs": [
            {
                "ref_type": "telemetry_summary",
                "ref_id": "tel-sum-998",
                "storage_ref": {"backend": "object_store", "path": "/telemetry/sum/998"},
                "note": "Sharpe collapse summary bundle.",
            }
        ],
    }
    r = client.post("/api/evolution/proposals", json=body)
    assert r.status_code == 201
    d = r.json()
    assert len(d["evidence_refs"]) == 1
    assert d["evidence_refs"][0]["ref_id"] == "tel-sum-998"
    assert d["evidence_refs"][0]["note"] == "Sharpe collapse summary bundle."

    # Also verify via GET
    r2 = client.get(f"/api/evolution/proposals/{did}")
    assert r2.status_code == 200
    assert r2.json()["evidence_refs"][0]["ref_id"] == "tel-sum-998"
    assert r2.json()["evidence_refs"][0]["note"] == "Sharpe collapse summary bundle."


# ---------------------------------------------------------------------------
# HTTP boundary validation — invalid enum inputs must return 4xx not 500
# ---------------------------------------------------------------------------

def test_propose_invalid_evidence_ref_type_returns_400():
    """Invalid EvidenceRefType value must return 400, not an uncaught ValueError/500."""
    did = uid()
    body = {
        "decision_id": did,
        "target_type": "strategy_spec",
        "target_id": f"strat-bad-ref-{did}",
        "target_version": "v1",
        "action_type": "retrain",
        "rationale": "Testing invalid ref_type.",
        "created_by_id": "evolution-controller",
        "evidence_refs": [
            {
                "ref_type": "not_a_valid_ref_type",
                "ref_id": "ref-999",
            }
        ],
    }
    r = client.post("/api/evolution/proposals", json=body)
    assert r.status_code == 400
    assert "ref_type" in r.json()["detail"].lower()


def test_execute_invalid_freeze_mode_returns_400():
    """Invalid FreezeFollowthroughMode value must return 400, not an uncaught ValueError/500."""
    d = propose()
    advance_to_reviewed(d["decision_id"])
    advance_to_approved(d["decision_id"])
    r = client.post(f"/api/evolution/proposals/{d['decision_id']}/execute", json={
        "actor_role": "evolution_controller",
        "actor_id": "evo-ctrl",
        "freeze_mode": "not_a_valid_freeze_mode",
    })
    assert r.status_code == 400
    assert "freeze_mode" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Evolution → incident store back-link
# ---------------------------------------------------------------------------

def _seed_incident_and_postmortem(postmortem_id: str) -> None:
    """Seed a minimal IncidentCase + Postmortem into the incident store."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    inc_id = f"inc-seed-{postmortem_id}"
    inc = IncidentCase(
        incident_id=inc_id,
        title="Seed incident for evolution link test",
        status="open",
        severity="medium",
        created_at=now,
        binding_id="bind-seed-001",
        deployment_stage="paper",
        deployment_plan_id="plan-seed-001",
        capital_pool_id="pool-seed-001",
        persona_capital_binding_id="pcb-seed-001",
        artifact_id="artifact-seed-001",
        artifact_version="v1",
        runtime_id="runtime-seed-001",
        trace_id="trace-seed-001",
    )
    evo_main.incident_store.create_incident(inc)
    pm = Postmortem(
        postmortem_id=postmortem_id,
        title="Seed postmortem for evolution link test",
        status="draft",
        created_at=now,
        incident_id=inc_id,
        binding_id=inc.binding_id,
        deployment_stage=inc.deployment_stage,
        deployment_plan_id=inc.deployment_plan_id,
        capital_pool_id=inc.capital_pool_id,
        persona_capital_binding_id=inc.persona_capital_binding_id,
        artifact_id=inc.artifact_id,
        artifact_version=inc.artifact_version,
        runtime_id=inc.runtime_id,
        trace_id=inc.trace_id,
        root_cause="Seed root cause for test.",
    )
    evo_main.incident_store.create_postmortem(pm)


def test_propose_with_linked_postmortem_populates_back_link():
    """Proposing with linked_postmortem_id must write linked_evolution_decision_id to the Postmortem."""
    pm_id = f"pm-link-{uuid.uuid4().hex[:8]}"
    _seed_incident_and_postmortem(pm_id)

    did = uid()
    body = {
        "decision_id": did,
        "target_type": "strategy_spec",
        "target_id": f"strat-pm-link-{did}",
        "target_version": "v1",
        "action_type": "retrain",
        "rationale": "Post-incident retrain triggered by postmortem findings.",
        "created_by_id": "evolution-controller",
        "linked_postmortem_id": pm_id,
        "threshold_snapshots": [
            {
                "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.1",
                "signal_type": "performance_degradation",
                "metric_name": "sharpe_pct_of_baseline",
                "comparator": "lt",
                "observed_value": 0.45,
                "threshold_value": 0.50,
            }
        ],
    }
    r = client.post("/api/evolution/proposals", json=body)
    assert r.status_code == 201
    d = r.json()
    assert d["linked_postmortem_id"] == pm_id

    # Verify the back-link was written to the incident store
    pm = evo_main.incident_store.get_postmortem(pm_id)
    assert pm is not None
    assert pm.linked_evolution_decision_id == did


def test_propose_with_unknown_postmortem_returns_422():
    """Proposing with a linked_postmortem_id that does not exist must return 422."""
    body = {
        "decision_id": uid(),
        "target_type": "strategy_spec",
        "target_id": f"strat-bad-pm-{uuid.uuid4().hex[:6]}",
        "target_version": "v1",
        "action_type": "retrain",
        "rationale": "References a non-existent postmortem.",
        "created_by_id": "evolution-controller",
        "linked_postmortem_id": "pm-does-not-exist",
        "threshold_snapshots": [
            {
                "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.1",
                "signal_type": "performance_degradation",
                "metric_name": "sharpe_pct_of_baseline",
                "comparator": "lt",
                "observed_value": 0.45,
                "threshold_value": 0.50,
            }
        ],
    }
    r = client.post("/api/evolution/proposals", json=body)
    assert r.status_code == 422
    assert "postmortem" in r.json()["detail"].lower()
