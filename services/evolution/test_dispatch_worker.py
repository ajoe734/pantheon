"""
Tests for the evolution dispatch worker — LOOP-AUTO-EVO-004.

Verifies:
- Approved decisions are dispatched through the correct gated execution path.
- Already-executed decisions are skipped (idempotent poll).
- Production-affecting mutation requires correct approval gate.
- Dispatch result is visible in EvolutionDecision follow-through.

Run:
    python3 -m pytest services/evolution/test_dispatch_worker.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

# ---- Isolate storage BEFORE importing main ----
_tmp = tempfile.mkdtemp(prefix="evo_dispatch_test_")
os.environ["EVOLUTION_DATA_DIR"] = _tmp
os.environ["INCIDENT_DATA_DIR"] = _tmp

# ---- Make platform objects importable ----
_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from fastapi.testclient import TestClient  # noqa: E402
from services.evolution import main as evo_main  # noqa: E402
from services.evolution.dispatch_worker import (  # noqa: E402
    fetch_approved_decisions,
    fetch_boundary,
    dispatch_decision,
    run_poll,
)
from services.evolution.main import app  # noqa: E402

client = TestClient(app)


def uid() -> str:
    return f"evo-d-{uuid.uuid4().hex[:8]}"


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


_REVIEW_ROLE_FOR_RISK = {
    "low": "reviewer_on_duty",
    "medium": "reviewer",
    "high": "governance_committee",
}
_APPROVE_ROLE_FOR_RISK = {
    "low": "reviewer_on_duty",
    "medium": "risk_owner",
    "high": "governance_committee",
}


def _propose_and_approve(
    action_type: str = "retrain",
    target_stage: str = "paper",
    risk_level: str = "low",
) -> str:
    """Create a proposed decision, advance through review and approval, return decision_id."""
    decision_id = uid()
    review_role = _REVIEW_ROLE_FOR_RISK[risk_level]
    approve_role = _APPROVE_ROLE_FOR_RISK[risk_level]

    resp = client.post(
        "/api/evolution/proposals",
        json={
            "decision_id": decision_id,
            "target_type": "candidate_artifact",
            "target_id": "test-strategy-001",
            "target_version": "v1.0",
            "action_type": action_type,
            "rationale": f"Test {action_type} proposal",
            "created_by_id": "test-controller",
            "created_by_role": "evolution_controller",
            "risk_level": risk_level,
            "target_stage": target_stage,
            "threshold_snapshots": [
                {
                    "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.1",
                    "signal_type": "performance_degradation",
                    "metric_name": "sharpe_ratio",
                    "comparator": "lt",
                    "observed_value": 0.3,
                    "threshold_value": 0.5,
                    "window": "30d",
                    "breached": True,
                }
            ],
        },
    )
    assert resp.status_code == 201, f"propose failed: {resp.json()}"

    resp = client.post(
        f"/api/evolution/proposals/{decision_id}/review",
        json={
            "actor_role": review_role,
            "actor_id": "reviewer-01",
            "approval_decision_id": f"appr-{decision_id}",
        },
    )
    assert resp.status_code == 200, f"review failed: {resp.json()}"

    resp = client.post(
        f"/api/evolution/proposals/{decision_id}/approve",
        json={
            "actor_role": approve_role,
            "actor_id": "reviewer-01",
            "approval_decision_id": f"appr-{decision_id}",
        },
    )
    assert resp.status_code == 200, f"approve failed: {resp.json()}"
    assert resp.json()["decision_state"] == "approved"
    return decision_id


class TestFetchApprovedDecisions:
    def test_no_approved_decisions_returns_empty(self):
        with client:
            resp = client.get("/api/evolution/proposals?decision_state=approved")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_approved_decision_appears_in_list(self):
        decision_id = _propose_and_approve()
        with client:
            resp = client.get("/api/evolution/proposals?decision_state=approved")
        assert resp.status_code == 200
        ids = [d["decision_id"] for d in resp.json()]
        assert decision_id in ids


class TestFetchBoundary:
    def test_boundary_for_retrain_is_research_plane(self):
        decision_id = _propose_and_approve(action_type="retrain", risk_level="low")
        with client:
            resp = client.get(f"/api/evolution/proposals/{decision_id}/boundary")
        assert resp.status_code == 200
        boundary = resp.json()
        assert boundary["execution_plane"] == "research"

    def test_boundary_for_freeze_non_live_is_governance_plane(self):
        decision_id = _propose_and_approve(
            action_type="freeze",
            target_stage="paper",
            risk_level="medium",
        )
        with client:
            resp = client.get(f"/api/evolution/proposals/{decision_id}/boundary")
        assert resp.status_code == 200
        boundary = resp.json()
        assert boundary["execution_plane"] == "governance"


class TestDispatchGate:
    def test_approved_retrain_dispatches_through_research_gate(self):
        """Approved retrain must execute through the research execution plane."""
        decision_id = _propose_and_approve(action_type="retrain", risk_level="low")

        with client:
            boundary_resp = client.get(f"/api/evolution/proposals/{decision_id}/boundary")
            assert boundary_resp.json()["execution_plane"] == "research"

            exec_resp = client.post(
                f"/api/evolution/proposals/{decision_id}/execute",
                json={
                    "actor_role": "evolution_controller",
                    "actor_id": "evolution-dispatch-worker",
                    "has_active_runtime": False,
                    "freeze_mode": "governance_only",
                },
            )
        assert exec_resp.status_code == 200
        result = exec_resp.json()
        assert result["decision_state"] == "executed"
        assert result["execution_result"] is not None
        assert result["execution_result"]["plane"] == "research"

    def test_dispatch_result_visible_in_follow_through(self):
        """execution_result and observation windows are visible after dispatch."""
        decision_id = _propose_and_approve(action_type="retrain", risk_level="low")
        with client:
            exec_resp = client.post(
                f"/api/evolution/proposals/{decision_id}/execute",
                json={
                    "actor_role": "evolution_controller",
                    "actor_id": "evolution-dispatch-worker",
                    "has_active_runtime": False,
                    "freeze_mode": "governance_only",
                },
            )
        assert exec_resp.status_code == 200
        result = exec_resp.json()
        assert result["execution_result"] is not None
        assert result["cooldown_ends_at"] is not None
        assert result["observation_window_ends_at"] is not None

    def test_wrong_actor_role_rejected(self):
        """Execution with a valid-but-unauthorised role is rejected by the gate."""
        decision_id = _propose_and_approve(action_type="retrain", risk_level="low")
        with client:
            exec_resp = client.post(
                f"/api/evolution/proposals/{decision_id}/execute",
                json={
                    "actor_role": "reviewer",
                    "actor_id": "reviewer-not-allowed-to-execute",
                    "has_active_runtime": False,
                    "freeze_mode": "governance_only",
                },
            )
        assert exec_resp.status_code == 422

    def test_non_approved_decision_cannot_be_executed(self):
        """Only approved decisions can be dispatched; proposed is rejected."""
        decision_id = uid()
        resp = client.post(
            "/api/evolution/proposals",
            json={
                "decision_id": decision_id,
                "target_type": "candidate_artifact",
                "target_id": "ts-x",
                "target_version": "v1",
                "action_type": "retrain",
                "rationale": "test",
                "created_by_id": "ctrl",
                "created_by_role": "evolution_controller",
                "threshold_snapshots": [
                    {
                        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.1",
                        "signal_type": "performance_degradation",
                        "metric_name": "sharpe_ratio",
                        "comparator": "lt",
                        "observed_value": 0.2,
                        "threshold_value": 0.5,
                        "window": "30d",
                        "breached": True,
                    }
                ],
            },
        )
        assert resp.status_code == 201

        exec_resp = client.post(
            f"/api/evolution/proposals/{decision_id}/execute",
            json={
                "actor_role": "evolution_controller",
                "actor_id": "dispatch-worker",
                "has_active_runtime": False,
                "freeze_mode": "governance_only",
            },
        )
        assert exec_resp.status_code == 422


class TestRunPoll:
    def test_run_poll_no_approved_decisions(self):
        """When no decisions are approved, poll returns zeroes."""
        with client:
            calls: list[str] = []

            def fake_get(url, timeout_seconds=10.0):
                calls.append(url)
                resp = client.get(url.split("127.0.0.1:8093")[-1])
                return resp.json()

            def fake_post(url, payload, timeout_seconds=30.0):
                resp = client.post(url.split("127.0.0.1:8093")[-1], json=payload)
                if resp.status_code >= 400:
                    raise Exception(f"HTTP {resp.status_code}: {resp.text}")
                return resp.json()

            import services.evolution.dispatch_worker as dw
            orig_get = dw._http_get
            orig_post = dw._http_post
            dw._http_get = fake_get  # type: ignore[attr-defined]
            dw._http_post = fake_post  # type: ignore[attr-defined]
            try:
                result = run_poll(api_url="http://127.0.0.1:8093", actor_id="test-worker")
            finally:
                dw._http_get = orig_get  # type: ignore[attr-defined]
                dw._http_post = orig_post  # type: ignore[attr-defined]

        assert result["decisions_found"] == 0
        assert result["dispatched"] == 0
        assert result["errors"] == []

    def test_run_poll_dispatches_approved_decision(self):
        """run_poll dispatches an approved decision and records execution result."""
        decision_id = _propose_and_approve(action_type="retrain", risk_level="low")

        import services.evolution.dispatch_worker as dw

        def fake_get(url, timeout_seconds=10.0):
            path = url.split("127.0.0.1:8093")[-1]
            resp = client.get(path)
            return resp.json()

        def fake_post(url, payload, timeout_seconds=30.0):
            path = url.split("127.0.0.1:8093")[-1]
            resp = client.post(path, json=payload)
            if resp.status_code >= 400:
                import urllib.error
                raise urllib.error.HTTPError(url, resp.status_code, resp.text, {}, None)
            return resp.json()

        orig_get = dw._http_get
        orig_post = dw._http_post
        dw._http_get = fake_get  # type: ignore[attr-defined]
        dw._http_post = fake_post  # type: ignore[attr-defined]
        try:
            result = run_poll(api_url="http://127.0.0.1:8093", actor_id="test-worker")
        finally:
            dw._http_get = orig_get  # type: ignore[attr-defined]
            dw._http_post = orig_post  # type: ignore[attr-defined]

        assert result["dispatched"] == 1
        assert result["errors"] == []
        assert len(result["dispatch_items"]) == 1
        item = result["dispatch_items"][0]
        assert item["decision_id"] == decision_id
        assert item["resulting_state"] == "executed"
        assert item["execution_result"] is not None

        # Confirm the decision state is now 'executed' in the store.
        decision = evo_main.store.get(decision_id)
        assert decision is not None
        assert str(decision.decision_state.value if hasattr(decision.decision_state, "value") else decision.decision_state) == "executed"

    def test_run_poll_skips_non_approved_decisions(self):
        """Decisions in proposed/reviewed state are not dispatched by the worker."""
        decision_id = uid()
        resp = client.post(
            "/api/evolution/proposals",
            json={
                "decision_id": decision_id,
                "target_type": "candidate_artifact",
                "target_id": "ts-skip",
                "target_version": "v1",
                "action_type": "retrain",
                "rationale": "test skip",
                "created_by_id": "ctrl",
                "created_by_role": "evolution_controller",
                "threshold_snapshots": [
                    {
                        "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.1",
                        "signal_type": "performance_degradation",
                        "metric_name": "sharpe_ratio",
                        "comparator": "lt",
                        "observed_value": 0.1,
                        "threshold_value": 0.5,
                        "window": "30d",
                        "breached": True,
                    }
                ],
            },
        )
        assert resp.status_code == 201

        import services.evolution.dispatch_worker as dw

        def fake_get(url, timeout_seconds=10.0):
            path = url.split("127.0.0.1:8093")[-1]
            return client.get(path).json()

        def fake_post(url, payload, timeout_seconds=30.0):
            path = url.split("127.0.0.1:8093")[-1]
            resp = client.post(path, json=payload)
            if resp.status_code >= 400:
                import urllib.error
                raise urllib.error.HTTPError(url, resp.status_code, resp.text, {}, None)
            return resp.json()

        orig_get = dw._http_get
        orig_post = dw._http_post
        dw._http_get = fake_get  # type: ignore[attr-defined]
        dw._http_post = fake_post  # type: ignore[attr-defined]
        try:
            result = run_poll(api_url="http://127.0.0.1:8093", actor_id="test-worker")
        finally:
            dw._http_get = orig_get  # type: ignore[attr-defined]
            dw._http_post = orig_post  # type: ignore[attr-defined]

        assert result["dispatched"] == 0
        assert result["decisions_found"] == 0  # proposed decisions not in approved list

    def test_run_poll_idempotent_on_second_call(self):
        """A second poll after dispatch finds no approved decisions (already executed)."""
        decision_id = _propose_and_approve(action_type="retrain", risk_level="low")

        import services.evolution.dispatch_worker as dw

        def fake_get(url, timeout_seconds=10.0):
            path = url.split("127.0.0.1:8093")[-1]
            return client.get(path).json()

        def fake_post(url, payload, timeout_seconds=30.0):
            path = url.split("127.0.0.1:8093")[-1]
            resp = client.post(path, json=payload)
            if resp.status_code >= 400:
                import urllib.error
                raise urllib.error.HTTPError(url, resp.status_code, resp.text, {}, None)
            return resp.json()

        orig_get = dw._http_get
        orig_post = dw._http_post
        dw._http_get = fake_get  # type: ignore[attr-defined]
        dw._http_post = fake_post  # type: ignore[attr-defined]
        try:
            first = run_poll(api_url="http://127.0.0.1:8093", actor_id="test-worker")
            second = run_poll(api_url="http://127.0.0.1:8093", actor_id="test-worker")
        finally:
            dw._http_get = orig_get  # type: ignore[attr-defined]
            dw._http_post = orig_post  # type: ignore[attr-defined]

        assert first["dispatched"] == 1
        # Second poll finds nothing in approved state
        assert second["decisions_found"] == 0
        assert second["dispatched"] == 0
        assert second["errors"] == []

    def test_followthrough_visible_in_observation_report(self):
        """After dispatch, observation report confirms execution_result and windows."""
        decision_id = _propose_and_approve(action_type="retrain", risk_level="low")

        exec_resp = client.post(
            f"/api/evolution/proposals/{decision_id}/execute",
            json={
                "actor_role": "evolution_controller",
                "actor_id": "evolution-dispatch-worker",
                "has_active_runtime": False,
                "freeze_mode": "governance_only",
            },
        )
        assert exec_resp.status_code == 200

        obs_resp = client.get(
            f"/api/evolution/proposals/{decision_id}/observation-report"
        )
        assert obs_resp.status_code == 200
        report = obs_resp.json()
        assert report["decision_state"] == "executed"
        assert report["execution"] != {}
        assert report["execution"]["plane"] == "research"
        assert report["convergence_status"] in {
            "collecting_observation_evidence",
            "observation_elapsed_cooldown_active",
            "eligible_for_next_decision",
            "pending_observation",
        }
