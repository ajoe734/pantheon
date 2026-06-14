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
import json
from pathlib import Path
from typing import Any

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
from services.incidents.consumer import ThresholdTelemetryIncidentConsumer  # noqa: E402

client = TestClient(app)
_SWEEP_FIXTURE_PATH = Path(__file__).with_name("fixtures") / "threshold_breach_daily_sweep.json"


def uid() -> str:
    return f"ev-t-{uuid.uuid4().hex[:8]}"


def sweep_fixture(extra: dict | None = None) -> dict:
    payload = json.loads(_SWEEP_FIXTURE_PATH.read_text(encoding="utf-8"))
    if extra:
        payload.update(extra)
    return payload


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


def test_medium_risk_operator_cannot_approve_without_risk_owner():
    """
    Regression: EVOLUTION_REVIEW_AND_THRESHOLDS.md §6.2 — medium-risk approved
    owner is Risk Owner only. Operator must not be accepted as a standalone
    approver (would bypass Risk Owner sign-off).
    """
    did = uid()
    body = {**MEDIUM_RISK_BODY, "decision_id": did, "target_id": f"artifact-op-{did}"}
    client.post("/api/evolution/proposals", json=body).raise_for_status()

    # Review with a valid reviewer role
    client.post(f"/api/evolution/proposals/{did}/review", json={
        "actor_role": "reviewer",
        "actor_id": "rev-medium",
        "approval_decision_id": "apv-medium-op",
    }).raise_for_status()

    # Attempt to approve as operator — must be rejected
    r = client.post(f"/api/evolution/proposals/{did}/approve", json={
        "actor_role": "operator",
        "actor_id": "op-medium",
    })
    assert r.status_code == 422, (
        f"operator must not be able to approve a medium-risk decision alone; "
        f"got {r.status_code}: {r.text}"
    )


def test_boundary_medium_risk_does_not_surface_operator_as_approved_owner():
    """
    The governance read path (boundary_for) must not expose operator as an
    approved_owner_role for medium-risk decisions.
    """
    did = uid()
    body = {**MEDIUM_RISK_BODY, "decision_id": did, "target_id": f"artifact-bnd-{did}"}
    client.post("/api/evolution/proposals", json=body).raise_for_status()

    r = client.get(f"/api/evolution/proposals/{did}/boundary")
    assert r.status_code == 200
    b = r.json()
    assert "operator" not in b["approved_owner_roles"], (
        f"operator must not appear as approved_owner_role for medium-risk; "
        f"got: {b['approved_owner_roles']}"
    )
    assert "risk_owner" in b["approved_owner_roles"]


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

def _seed_incident_and_postmortem(
    postmortem_id: str,
    *,
    incident_id: str | None = None,
    incident_overrides: dict[str, Any] | None = None,
    postmortem_overrides: dict[str, Any] | None = None,
) -> str:
    """Seed a minimal IncidentCase + Postmortem into the incident store."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    inc_id = incident_id or f"inc-seed-{postmortem_id}"
    incident_payload = {
        "incident_id": inc_id,
        "title": "Seed incident for evolution link test",
        "status": "open",
        "severity": "medium",
        "created_at": now,
        "binding_id": "bind-seed-001",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-seed-001",
        "capital_pool_id": "pool-seed-001",
        "persona_capital_binding_id": "pcb-seed-001",
        "artifact_id": "artifact-seed-001",
        "artifact_version": "v1",
        "runtime_id": "runtime-seed-001",
        "trace_id": "trace-seed-001",
    }
    incident_payload.update(incident_overrides or {})
    inc = IncidentCase(
        **incident_payload,
    )
    evo_main.incident_store.create_incident(inc)
    postmortem_payload = dict(
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
    postmortem_payload.update(postmortem_overrides or {})
    pm = Postmortem(**postmortem_payload)
    evo_main.incident_store.create_postmortem(pm)
    return inc_id


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


# ---------------------------------------------------------------------------
# MGMT-EVO-002: proposal creation from incident / postmortem
# ---------------------------------------------------------------------------

def test_propose_from_incident_postmortem_derives_review_gated_freeze():
    """
    MGMT-EVO-002: critical IncidentCase + Postmortem evidence creates only a
    proposed EvolutionDecision with incident/postmortem links and no execution.
    """
    pm_id = f"pm-derive-{uuid.uuid4().hex[:8]}"
    inc_id = _seed_incident_and_postmortem(
        pm_id,
        incident_overrides={
            "severity": "critical",
            "deployment_stage": "live",
            "artifact_id": "artifact-live-critical",
            "artifact_version": "v9",
            "runtime_id": "runtime-live-critical",
            "trace_id": "trace-live-critical",
        },
        postmortem_overrides={
            "root_cause": "Live runtime accepted a stale artifact binding.",
        },
    )

    did = uid()
    r = client.post("/api/evolution/proposals/from-incident", json={
        "decision_id": did,
        "incident_id": inc_id,
        "postmortem_id": pm_id,
        "has_active_runtime": True,
    })

    assert r.status_code == 201, r.text
    d = r.json()
    assert d["decision_id"] == did
    assert d["decision_state"] == "proposed"
    assert d["action_type"] == "freeze"
    assert d["risk_level"] == "high"
    assert d["target_type"] == "candidate_artifact"
    assert d["target_id"] == "artifact-live-critical"
    assert d["target_version"] == "v9"
    assert d["target_stage"] == "live"
    assert d["linked_incident_id"] == inc_id
    assert d["linked_postmortem_id"] == pm_id
    assert d["capital_pool_id"] == "pool-seed-001"
    assert d["review_chain"] == []
    assert d["execution_result"] is None
    assert d["cooldown_ends_at"] is None
    assert {ref["ref_id"] for ref in d["evidence_refs"]} == {inc_id, pm_id}
    assert d["threshold_snapshots"][0]["metric_name"] == "severity1_incident_count"
    assert d["metadata"]["task_id"] == "MGMT-EVO-002"
    assert d["metadata"]["source"] == "incident_postmortem"
    assert d["metadata"]["source_trace_id"] == "trace-live-critical"
    assert d["metadata"]["proposal_only"] is True
    assert d["metadata"]["runtime_binding_mutation_allowed"] is False
    assert d["metadata"]["broker_order_allowed"] is False
    assert d["metadata"]["capital_binding_mutation_allowed"] is False

    pm = evo_main.incident_store.get_postmortem(pm_id)
    assert pm is not None
    assert pm.linked_evolution_decision_id == did


def test_propose_from_incident_uses_existing_postmortem_when_not_explicit():
    """When no postmortem_id is supplied, the incident's postmortem is linked if present."""
    pm_id = f"pm-auto-{uuid.uuid4().hex[:8]}"
    inc_id = _seed_incident_and_postmortem(pm_id)

    r = client.post("/api/evolution/proposals/from-incident", json={
        "decision_id": uid(),
        "incident_id": inc_id,
        "action_type": "revalidate",
    })

    assert r.status_code == 201, r.text
    d = r.json()
    assert d["action_type"] == "revalidate"
    assert d["risk_level"] == "low"
    assert d["linked_incident_id"] == inc_id
    assert d["linked_postmortem_id"] == pm_id
    assert d["metadata"]["proposal_only"] is True


def test_propose_from_incident_missing_incident_returns_404():
    r = client.post("/api/evolution/proposals/from-incident", json={
        "decision_id": uid(),
        "incident_id": "inc-does-not-exist",
    })

    assert r.status_code == 404
    assert "incidentcase" in r.json()["detail"].lower()


def test_propose_from_incident_rejects_postmortem_for_different_incident():
    pm_a = f"pm-mismatch-a-{uuid.uuid4().hex[:8]}"
    pm_b = f"pm-mismatch-b-{uuid.uuid4().hex[:8]}"
    inc_a = _seed_incident_and_postmortem(pm_a, incident_id=f"inc-a-{uuid.uuid4().hex[:8]}")
    _seed_incident_and_postmortem(pm_b, incident_id=f"inc-b-{uuid.uuid4().hex[:8]}")

    r = client.post("/api/evolution/proposals/from-incident", json={
        "decision_id": uid(),
        "incident_id": inc_a,
        "postmortem_id": pm_b,
    })

    assert r.status_code == 422
    assert "belongs to incident" in r.json()["detail"]


# ---------------------------------------------------------------------------
# EVO-004: Cooldown enforcement — repeated triggers blocked
# ---------------------------------------------------------------------------

def test_cooldown_blocks_new_proposal_after_execute():
    """
    EVO-004 acceptance criterion: cooldown enforcement has tests (repeated trigger rejected).

    After an EvolutionDecision is executed, the target enters cooldown.
    single-active-rule: a new proposal for the same target must be rejected
    while the executed decision is_active() (i.e. within cooldown / observation window).
    """
    target_id = f"strat-cooldown-{uuid.uuid4().hex[:6]}"

    # Step 1: full lifecycle for first decision
    did = uid()
    body = {**LOW_RISK_BODY, "decision_id": did, "target_id": target_id}
    client.post("/api/evolution/proposals", json=body).raise_for_status()
    advance_to_reviewed(did)
    advance_to_approved(did)
    r_exec = advance_to_executed(did)
    assert r_exec["decision_state"] == "executed"
    assert r_exec["cooldown_ends_at"] is not None

    # Step 2: try to propose a second decision on the same target
    # The executed decision has a 3-day cooldown (low-risk retrain), so is_active() is True
    body2 = {**LOW_RISK_BODY, "decision_id": uid(), "target_id": target_id}
    r2 = client.post("/api/evolution/proposals", json=body2)
    assert r2.status_code == 422, (
        f"Expected 422 because first decision is still within cooldown; got {r2.status_code}: {r2.text}"
    )
    assert "single-active-rule" in r2.json()["detail"]


def test_cooldown_blocks_high_risk_freeze_live_repeated_trigger():
    """
    EVO-004: High-risk freeze on live target has a 14-day cooldown.
    A second freeze proposal on the same persona during that window is rejected.
    """
    target_id = f"persona-cooldown-{uuid.uuid4().hex[:6]}"

    did = uid()
    body = {**HIGH_RISK_BODY, "decision_id": did, "target_id": target_id}
    client.post("/api/evolution/proposals", json=body).raise_for_status()

    client.post(f"/api/evolution/proposals/{did}/review", json={
        "actor_role": "governance_committee",
        "actor_id": "committee-cd",
        "approval_decision_id": "apv-cd-001",
    }).raise_for_status()

    client.post(f"/api/evolution/proposals/{did}/approve", json={
        "actor_role": "governance_committee",
        "actor_id": "committee-cd",
    }).raise_for_status()

    r_exec = advance_to_executed(did)
    assert r_exec["decision_state"] == "executed"
    assert r_exec["cooldown_ends_at"] is not None

    # Second freeze proposal on the same persona — must be blocked
    body2 = {**HIGH_RISK_BODY, "decision_id": uid(), "target_id": target_id}
    r2 = client.post("/api/evolution/proposals", json=body2)
    assert r2.status_code == 422
    assert "single-active-rule" in r2.json()["detail"]


# ---------------------------------------------------------------------------
# DEVLOOP-EVOLUTION: daily sweep + threshold-trigger fixture
# ---------------------------------------------------------------------------

def test_daily_sweep_threshold_fixture_creates_evolution_decision():
    payload = sweep_fixture()
    ThresholdTelemetryIncidentConsumer(incident_store=evo_main.incident_store).consume(payload)

    r = client.post(
        "/api/evolution/daily-sweep",
        json={"incident_ids": [payload["incident_id"]], "sweep_id": "fixture-sweep"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scanned_incidents"] == 1
    assert body["created_decisions"] == 1
    assert body["cooldown_blocked"] == 0
    assert body["scheduler_attach"]["worker_module"] == "services.evolution.scheduler_worker"
    item = body["items"][0]
    assert item["status"] == "created"
    assert item["action_type"] == "retrain"

    decision = client.get(f"/api/evolution/proposals/{item['decision_id']}").json()
    assert decision["decision_state"] == "proposed"
    assert decision["linked_incident_id"] == payload["incident_id"]
    assert decision["target_id"] == payload["telemetry_event"]["artifact_id"]
    assert decision["target_stage"] == "paper"
    assert decision["metadata"]["source"] == "evolution_daily_sweep"
    assert decision["metadata"]["proposal_only"] is True
    assert decision["metadata"]["runtime_binding_mutation_allowed"] is False
    assert decision["threshold_snapshots"][0]["metric_name"] == "rolling_drawdown_multiple"
    assert decision["threshold_snapshots"][0]["observed_value"] == 1.42


def test_daily_sweep_respects_cooldown_for_same_target_after_execute():
    first_payload = sweep_fixture()
    ThresholdTelemetryIncidentConsumer(incident_store=evo_main.incident_store).consume(first_payload)

    first = client.post(
        "/api/evolution/daily-sweep",
        json={"incident_ids": [first_payload["incident_id"]], "sweep_id": "cooldown-first"},
    ).json()["items"][0]
    decision_id = first["decision_id"]
    advance_to_reviewed(decision_id, apv_id="apv-daily-sweep-cooldown")
    advance_to_approved(decision_id)
    executed = advance_to_executed(decision_id)
    assert executed["cooldown_ends_at"] is not None

    second_payload = sweep_fixture(
        {
            "incident_id": "inc-evolution-sweep-fixture-002",
            "title": "Paper runtime drawdown threshold breached again",
        }
    )
    second_payload["telemetry_event"]["event_id"] = "tel-evolution-sweep-fixture-002"
    ThresholdTelemetryIncidentConsumer(incident_store=evo_main.incident_store).consume(second_payload)

    r = client.post(
        "/api/evolution/daily-sweep",
        json={"incident_ids": [second_payload["incident_id"]], "sweep_id": "cooldown-second"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scanned_incidents"] == 1
    assert body["created_decisions"] == 0
    assert body["cooldown_blocked"] == 1
    item = body["items"][0]
    assert item["status"] == "cooldown_blocked"
    assert item["active_decision_id"] == decision_id
    assert "cooldown/observation" in item["reason"]


# ---------------------------------------------------------------------------
# MGMT-EVO-006: Observation window report
# ---------------------------------------------------------------------------

def test_observation_window_report_open_window_contains_evidence_refs():
    """
    MGMT-EVO-006: the service exposes a post-execution observation-window
    report with window state, active blocking, execution refs, and evidence refs.
    """
    from datetime import datetime, timedelta

    did = uid()
    body = {**LOW_RISK_BODY, "decision_id": did, "target_id": f"strat-obs-{did}"}
    client.post("/api/evolution/proposals", json=body).raise_for_status()
    advance_to_reviewed(did)
    advance_to_approved(did)
    executed = advance_to_executed(did)

    obs_start = datetime.fromisoformat(executed["observation_window_started_at"].replace("Z", "+00:00"))
    as_of = (obs_start + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    r = client.get(f"/api/evolution/proposals/{did}/observation-report?as_of={as_of}")

    assert r.status_code == 200, r.text
    report = r.json()
    assert report["decision_id"] == did
    assert report["decision_state"] == "executed"
    assert report["observation_state"] == "open"
    assert report["cooldown_state"] == "open"
    assert report["active_blocking"] is True
    assert report["convergence_status"] == "collecting_observation_evidence"
    assert report["active_until"] == executed["observation_window_ends_at"]
    assert report["seconds_since_observation_start"] == 86400
    assert report["seconds_until_observation_end"] > 0
    assert report["execution"]["execution_ref_id"] == f"dispatch-{did}"
    assert report["followthrough_refs"][0]["ref_type"] == "dispatch_command"
    assert report["threshold_snapshots"][0]["metric_name"] == "sharpe_pct_of_baseline"
    assert "EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md §5.2-§5.4" in report["policy_refs"]


def test_observation_window_report_after_active_window_allows_next_decision():
    """Once observation and cooldown windows have elapsed, the report marks the target unblocked."""
    from datetime import datetime, timedelta

    did = uid()
    body = {**LOW_RISK_BODY, "decision_id": did, "target_id": f"strat-obs-elapsed-{did}"}
    client.post("/api/evolution/proposals", json=body).raise_for_status()
    advance_to_reviewed(did)
    advance_to_approved(did)
    executed = advance_to_executed(did)

    obs_end = datetime.fromisoformat(executed["observation_window_ends_at"].replace("Z", "+00:00"))
    as_of = (obs_end + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    r = client.get(f"/api/evolution/proposals/{did}/observation-report?as_of={as_of}")

    assert r.status_code == 200, r.text
    report = r.json()
    assert report["observation_state"] == "elapsed"
    assert report["cooldown_state"] == "elapsed"
    assert report["active_blocking"] is False
    assert report["seconds_until_observation_end"] == 0
    assert report["seconds_until_cooldown_end"] == 0
    assert report["convergence_status"] == "eligible_for_next_decision"


def test_observation_window_report_rejected_before_execute():
    """Observation reports are only valid after the EvolutionDecision is executed."""
    d = propose({"target_id": f"strat-obs-pre-{uuid.uuid4().hex[:6]}"})
    r = client.get(f"/api/evolution/proposals/{d['decision_id']}/observation-report")

    assert r.status_code == 422
    assert "executed" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# EVO-004: Action-paths routing matrix endpoint
# ---------------------------------------------------------------------------

def test_action_paths_returns_all_four_families():
    """
    GET /api/evolution/action-paths must return entries covering all four
    EVO-004 action families: freeze, rollback, retrain, redeploy.
    """
    r = client.get("/api/evolution/action-paths")
    assert r.status_code == 200
    data = r.json()
    assert data["policy_document"] == "EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1"
    families = {p["action_family"] for p in data["paths"]}
    assert {"freeze", "rollback", "retrain", "redeploy"}.issubset(families)


def test_action_paths_each_has_owner_and_cooldown():
    """Every action-path entry must expose reviewed_owner, approved_owner, and cooldown_days."""
    r = client.get("/api/evolution/action-paths")
    assert r.status_code == 200
    for entry in r.json()["paths"]:
        assert entry["reviewed_owner"], f"missing reviewed_owner in {entry['path_key']}"
        assert entry["approved_owner"], f"missing approved_owner in {entry['path_key']}"
        assert "cooldown_days" in entry, f"missing cooldown_days in {entry['path_key']}"
        assert entry["execution_plane"], f"missing execution_plane in {entry['path_key']}"


# ---------------------------------------------------------------------------
# EVO-004: Redeploy follow-through endpoint
# ---------------------------------------------------------------------------

def test_redeploy_followthrough_returns_dispatch_command():
    """
    POST /api/evolution/proposals/{id}/redeploy-followthrough must return a
    DispatchCommand within the parent observation window.
    """
    from datetime import datetime, timezone, timedelta

    # Step 1: full lifecycle to executed
    did = uid()
    target_id = f"strat-redeploy-{uuid.uuid4().hex[:6]}"
    body = {**LOW_RISK_BODY, "decision_id": did, "target_id": target_id}
    client.post("/api/evolution/proposals", json=body).raise_for_status()
    advance_to_reviewed(did)
    advance_to_approved(did)
    r_exec = advance_to_executed(did)
    assert r_exec["decision_state"] == "executed"

    obs_start = r_exec["observation_window_started_at"]
    obs_end = r_exec["observation_window_ends_at"]

    # Pick a requested_at inside the observation window
    start_dt = datetime.fromisoformat(obs_start.replace("Z", "+00:00"))
    requested_at = (start_dt + timedelta(days=1)).isoformat().replace("+00:00", "Z")

    # Step 2: request redeploy followthrough
    r = client.post(f"/api/evolution/proposals/{did}/redeploy-followthrough", json={
        "artifact_id": f"artifact-new-{uuid.uuid4().hex[:6]}",
        "artifact_version": "v2",
        "approval_decision_id": "apv-redeploy-001",
        "target_stage": "paper",
        "requested_at": requested_at,
    })
    assert r.status_code == 200, r.text
    cmd = r.json()
    assert cmd["action_type"] == "redeploy_followthrough"
    assert cmd["execution_plane"] == "deployment"
    assert cmd["cooldown_ends_at"] is not None
    assert "reviewed_owner_roles" in cmd["metadata"]
    assert "requires_new_deployment_plan" in cmd["metadata"]


def test_redeploy_followthrough_rejected_before_execute():
    """Redeploy follow-through on a non-executed decision must return 422."""
    did = uid()
    body = {**LOW_RISK_BODY, "decision_id": did, "target_id": f"strat-rd-pre-{did}"}
    client.post("/api/evolution/proposals", json=body).raise_for_status()
    # Still in proposed state — redeploy followthrough must fail

    r = client.post(f"/api/evolution/proposals/{did}/redeploy-followthrough", json={
        "artifact_id": "artifact-early",
        "artifact_version": "v1",
        "approval_decision_id": "apv-early",
        "target_stage": "paper",
    })
    assert r.status_code == 422


def test_rollback_followthrough_endpoint_requires_approved_freeze():
    """
    POST .../rollback-followthrough must return 422 when called on a decision
    that is not yet in approved state.
    """
    did = uid()
    body = {**HIGH_RISK_BODY, "decision_id": did, "target_id": f"persona-rb-{did}"}
    client.post("/api/evolution/proposals", json=body).raise_for_status()
    # Still proposed — rollback followthrough must be rejected
    r = client.post(f"/api/evolution/proposals/{did}/rollback-followthrough", json={
        "actor_role": "evolution_controller",
        "actor_id": "evo-ctrl",
    })
    assert r.status_code == 422


def test_rollback_followthrough_executes_approved_freeze():
    """
    POST .../rollback-followthrough on an approved high-risk freeze decision
    should move it to executed state with a rollback command emitted.
    """
    did = uid()
    target_id = f"persona-rb-live-{uuid.uuid4().hex[:6]}"
    body = {**HIGH_RISK_BODY, "decision_id": did, "target_id": target_id}
    client.post("/api/evolution/proposals", json=body).raise_for_status()

    client.post(f"/api/evolution/proposals/{did}/review", json={
        "actor_role": "governance_committee",
        "actor_id": "committee-rb",
        "approval_decision_id": "apv-rb-001",
    }).raise_for_status()

    client.post(f"/api/evolution/proposals/{did}/approve", json={
        "actor_role": "governance_committee",
        "actor_id": "committee-rb",
    }).raise_for_status()

    r = client.post(f"/api/evolution/proposals/{did}/rollback-followthrough", json={
        "actor_role": "evolution_controller",
        "actor_id": "evo-ctrl",
        "active_binding_id": "binding-rb-live-001",
        "rollback_action_type": "pause_then_replace",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["decision_state"] == "executed"
    assert d["cooldown_ends_at"] is not None


def test_redeploy_followthrough_rejected_for_executed_freeze():
    """
    Issue 1 regression: redeploy-followthrough must return 422 when the parent
    decision is a freeze action.  Per EVOLUTION_REVIEW_AND_THRESHOLDS.md §11.1
    only retrain/revalidate/revive/observe follow-through can trigger a redeploy.
    """
    did = uid()
    target_id = f"persona-rd-freeze-{uuid.uuid4().hex[:6]}"
    body = {**HIGH_RISK_BODY, "decision_id": did, "target_id": target_id}
    client.post("/api/evolution/proposals", json=body).raise_for_status()
    client.post(f"/api/evolution/proposals/{did}/review", json={
        "actor_role": "governance_committee",
        "actor_id": "committee-rd",
        "approval_decision_id": f"apv-rd-{did}",
    }).raise_for_status()
    client.post(f"/api/evolution/proposals/{did}/approve", json={
        "actor_role": "governance_committee",
        "actor_id": "committee-rd",
    }).raise_for_status()
    client.post(f"/api/evolution/proposals/{did}/execute", json={
        "actor_role": "evolution_controller",
        "actor_id": "evo-ctrl",
    }).raise_for_status()
    r = client.post(f"/api/evolution/proposals/{did}/redeploy-followthrough", json={
        "artifact_id": f"artifact-freeze-{did}",
        "artifact_version": "v2",
        "approval_decision_id": f"apv-rd2-{did}",
        "target_stage": "paper",
    })
    assert r.status_code == 422
    assert "freeze" in r.json()["detail"].lower()


def test_rollback_followthrough_requires_active_binding_id():
    """
    Issue 2 regression: rollback-followthrough must return 422 when called
    without active_binding_id.  Per ROLLBACK_AND_POSITION_SEMANTICS.md §10
    a rollback path requires a binding identity; freeze decisions without an
    active runtime must use the governance-only path instead.
    """
    did = uid()
    target_id = f"persona-rb-nobind-{uuid.uuid4().hex[:6]}"
    body = {**HIGH_RISK_BODY, "decision_id": did, "target_id": target_id}
    client.post("/api/evolution/proposals", json=body).raise_for_status()
    client.post(f"/api/evolution/proposals/{did}/review", json={
        "actor_role": "governance_committee",
        "actor_id": "committee-nb",
        "approval_decision_id": f"apv-nb-{did}",
    }).raise_for_status()
    client.post(f"/api/evolution/proposals/{did}/approve", json={
        "actor_role": "governance_committee",
        "actor_id": "committee-nb",
    }).raise_for_status()
    r = client.post(f"/api/evolution/proposals/{did}/rollback-followthrough", json={
        "actor_role": "evolution_controller",
        "actor_id": "evo-ctrl",
    })
    assert r.status_code == 422
    assert "active_binding_id" in r.json()["detail"]


def test_action_paths_keys_match_boundary_for():
    """
    Issue 3 regression: path_key values in GET /api/evolution/action-paths must
    use the same naming convention as boundary_for() in EvolutionController so
    consumers can map boundary endpoint output back to the routing matrix.
    """
    r = client.get("/api/evolution/action-paths")
    assert r.status_code == 200
    keys = {p["path_key"] for p in r.json()["paths"]}
    # Keys that match boundary_for() output
    assert "freeze_non_live" in keys, "freeze_non_live (was freeze_paper_canary) must be present"
    assert "freeze_live_no_active_runtime" in keys, "freeze_live_no_active_runtime (was freeze_live_no_runtime) must be present"
    assert "research_retrain" in keys, "research_retrain (was retrain_revalidate) must be present"
    assert "research_revalidate" in keys, "research_revalidate must be present as a distinct entry"
    # Old mismatched keys must be absent
    assert "freeze_paper_canary" not in keys
    assert "freeze_live_no_runtime" not in keys
    assert "retrain_revalidate" not in keys
