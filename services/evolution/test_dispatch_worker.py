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

import json
import os
import sys
import tempfile
import time
import urllib.error
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
    _decode_json_response,
    fetch_approved_decisions,
    fetch_boundary,
    dispatch_decision,
    run_poll,
    healthcheck,
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
    metadata: dict[str, object] | None = None,
) -> str:
    """Create a proposed decision, advance through review and approval, return decision_id."""
    decision_id = uid()
    review_role = _REVIEW_ROLE_FOR_RISK[risk_level]
    approve_role = _APPROVE_ROLE_FOR_RISK[risk_level]

    proposal: dict[str, object] = {
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
        }
    if metadata is not None:
        proposal["metadata"] = metadata
    resp = client.post("/api/evolution/proposals", json=proposal)
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


class TestHttpPayloadValidation:
    @pytest.mark.parametrize("body", ["", "  ", "{"])
    def test_empty_or_malformed_2xx_body_is_rejected(self, body):
        with pytest.raises(RuntimeError):
            _decode_json_response(body, context="test response")


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

        post_payloads: list[dict[str, object]] = []

        def fake_get(url, timeout_seconds=10.0):
            path = url.split("127.0.0.1:8093")[-1]
            resp = client.get(path)
            return resp.json()

        def fake_post(url, payload, timeout_seconds=30.0):
            post_payloads.append(payload)
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
        assert item["execution_plane"] == "research"
        assert item["execution_result"]["status"] == "submitted"
        assert item["execution_result"]["plane"] == "research"
        assert item["execution_result"]["execution_ref_id"] == (
            f"dispatch-{decision_id}"
        )
        assert item["execution_result"]["executed_at"] is not None
        assert item["cooldown_ends_at"] is not None
        assert item["observation_window_ends_at"] is not None
        assert post_payloads == [
            {
                "actor_role": "evolution_controller",
                "actor_id": "test-worker",
            }
        ]

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

    def test_run_poll_idempotent_after_store_restart(self):
        """A fresh worker/service store does not redispatch an executed decision."""
        decision_id = _propose_and_approve(action_type="retrain", risk_level="low")

        import services.evolution.dispatch_worker as dw

        post_calls: list[str] = []

        def fake_get(url, timeout_seconds=10.0):
            path = url.split("127.0.0.1:8093")[-1]
            return client.get(path).json()

        def fake_post(url, payload, timeout_seconds=30.0):
            post_calls.append(url)
            path = url.split("127.0.0.1:8093")[-1]
            resp = client.post(path, json=payload)
            if resp.status_code >= 400:
                import urllib.error
                raise urllib.error.HTTPError(url, resp.status_code, resp.text, {}, None)
            return resp.json()

        orig_get = dw._http_get
        orig_post = dw._http_post
        orig_store = evo_main.store
        dw._http_get = fake_get  # type: ignore[attr-defined]
        dw._http_post = fake_post  # type: ignore[attr-defined]
        try:
            first = run_poll(api_url="http://127.0.0.1:8093", actor_id="test-worker")
            assert orig_store._storage_path is not None
            evo_main.store = evo_main.EvolutionDecisionStore(
                storage_path=str(orig_store._storage_path)
            )
            second = run_poll(api_url="http://127.0.0.1:8093", actor_id="test-worker")
            restarted_decision = evo_main.store.get(decision_id)
            assert restarted_decision is not None
            persisted = restarted_decision.to_dict()
        finally:
            evo_main.store = orig_store
            dw._http_get = orig_get  # type: ignore[attr-defined]
            dw._http_post = orig_post  # type: ignore[attr-defined]

        assert first["dispatched"] == 1
        # Second poll finds nothing in approved state
        assert second["decisions_found"] == 0
        assert second["dispatched"] == 0
        assert second["errors"] == []
        assert len(post_calls) == 1
        assert persisted["execution_result"]["execution_ref_id"] == (
            f"dispatch-{decision_id}"
        )
        executed_steps = [
            step for step in persisted["review_chain"] if step["step_type"] == "executed"
        ]
        assert len(executed_steps) == 1
        assert executed_steps[0]["actor_id"] == "test-worker"

    def test_run_poll_fails_closed_when_boundary_is_unreachable(self):
        """A boundary read failure must never fall through to /execute."""
        decision_id = _propose_and_approve(action_type="retrain", risk_level="low")

        import services.evolution.dispatch_worker as dw

        post_calls: list[str] = []

        def fake_get(url, timeout_seconds=10.0):
            if "/boundary" in url:
                raise urllib.error.URLError("boundary unavailable")
            path = url.split("127.0.0.1:8093")[-1]
            return client.get(path).json()

        def fake_post(url, payload, timeout_seconds=30.0):
            post_calls.append(url)
            raise AssertionError("fail-closed poll must not call /execute")

        orig_get = dw._http_get
        orig_post = dw._http_post
        dw._http_get = fake_get  # type: ignore[attr-defined]
        dw._http_post = fake_post  # type: ignore[attr-defined]
        try:
            result = run_poll(api_url="http://127.0.0.1:8093", actor_id="test-worker")
        finally:
            dw._http_get = orig_get  # type: ignore[attr-defined]
            dw._http_post = orig_post  # type: ignore[attr-defined]

        assert result["decisions_found"] == 1
        assert result["dispatched"] == 0
        assert post_calls == []
        assert result["errors"] == [
            f"decision_id={decision_id} boundary_fetch_error=<urlopen error boundary unavailable>"
        ]
        assert evo_main.store.get(decision_id).decision_state.value == "approved"

    def test_active_live_freeze_with_binding_is_not_silently_consumed(
        self, monkeypatch
    ):
        """A daily-sweep-shaped live freeze stays approved for an explicit owner."""
        decision_id = _propose_and_approve(
            action_type="freeze",
            target_stage="live",
            risk_level="high",
            metadata={
                "source": "evolution_daily_sweep",
                "runtime_binding_id": "rb-live-1",
                "deployment_stage_snapshot": "live",
                "threshold_evaluation": {
                    "requires_runtime_followthrough": True,
                },
            },
        )

        import services.evolution.dispatch_worker as dw

        get_calls: list[str] = []
        post_calls: list[str] = []

        def fake_get(url, timeout_seconds=10.0):
            get_calls.append(url)
            if "/boundary" in url:
                raise AssertionError("unsupported freeze must be skipped before boundary")
            path = url.split("127.0.0.1:8093")[-1]
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        def fake_post(url, payload, timeout_seconds=30.0):
            post_calls.append(url)
            raise AssertionError("unsupported freeze must never call /execute")

        monkeypatch.setattr(dw, "_http_get", fake_get)
        monkeypatch.setattr(dw, "_http_post", fake_post)

        result = run_poll(
            api_url="http://127.0.0.1:8093", actor_id="test-worker"
        )
        decision = evo_main.store.get(decision_id)
        assert decision is not None
        assert decision.metadata["runtime_binding_id"] == "rb-live-1"
        assert "has_active_runtime" not in decision.metadata
        assert (
            decision.metadata["threshold_evaluation"][
                "requires_runtime_followthrough"
            ]
            is True
        )

        assert len(get_calls) == 1
        assert post_calls == []
        assert result["decisions_found"] == 1
        assert result["dispatched"] == 0
        assert result["skipped_unsupported"] == 1
        assert result["errors"] == []
        assert result["skip_items"] == [
            {
                "decision_id": decision_id,
                "action_type": "freeze",
                "target_stage": "live",
                "reported_runtime_binding_id": "rb-live-1",
                "reason": (
                    "automatic dispatch supports research-plane actions only; "
                    "governance/deployment/runtime actions require an explicit "
                    "authoritative owner and approved follow-through mode"
                ),
            }
        ]
        assert decision.decision_state.value == "approved"
        assert decision.execution_result is None
        assert not any(
            step.step_type.value == "executed" for step in decision.review_chain
        )

    @pytest.mark.parametrize(
        "boundary_payload",
        [
            {},
            [],
            {"boundary_key": "research_retrain"},
        ],
        ids=["empty-object", "non-object", "missing-fields"],
    )
    def test_run_poll_rejects_empty_or_malformed_boundary_2xx(
        self, monkeypatch, boundary_payload
    ):
        decision_id = _propose_and_approve(action_type="retrain", risk_level="low")

        import services.evolution.dispatch_worker as dw

        post_calls: list[str] = []

        def fake_get(url, timeout_seconds=10.0):
            if "/boundary" in url:
                return boundary_payload
            path = url.split("127.0.0.1:8093")[-1]
            return client.get(path).json()

        def fake_post(url, payload, timeout_seconds=30.0):
            post_calls.append(url)
            raise AssertionError("malformed boundary must fail before /execute")

        monkeypatch.setattr(dw, "_http_get", fake_get)
        monkeypatch.setattr(dw, "_http_post", fake_post)

        result = run_poll(
            api_url="http://127.0.0.1:8093", actor_id="test-worker"
        )

        assert result["dispatched"] == 0
        assert post_calls == []
        assert len(result["errors"]) == 1
        assert "boundary_fetch_error" in result["errors"][0]
        assert evo_main.store.get(decision_id).decision_state.value == "approved"

    @pytest.mark.parametrize(
        "boundary_override",
        [
            {"boundary_key": "governance_retrain"},
            {"execution_plane": "governance"},
            {"followthrough": ["runtime.rollback"]},
        ],
        ids=["wrong-key", "wrong-plane", "unexpected-followthrough"],
    )
    def test_run_poll_rejects_boundary_outside_research_only_contract(
        self, monkeypatch, boundary_override
    ):
        decision_id = _propose_and_approve(action_type="retrain", risk_level="low")

        import services.evolution.dispatch_worker as dw

        post_calls: list[str] = []

        def fake_get(url, timeout_seconds=10.0):
            path = url.split("127.0.0.1:8093")[-1]
            response = client.get(path)
            response.raise_for_status()
            payload = response.json()
            if "/boundary" in url:
                payload.update(boundary_override)
            return payload

        def fake_post(url, payload, timeout_seconds=30.0):
            post_calls.append(url)
            raise AssertionError("boundary mismatch must fail before /execute")

        monkeypatch.setattr(dw, "_http_get", fake_get)
        monkeypatch.setattr(dw, "_http_post", fake_post)

        result = run_poll(
            api_url="http://127.0.0.1:8093", actor_id="test-worker"
        )

        assert result["dispatched"] == 0
        assert post_calls == []
        assert len(result["errors"]) == 1
        assert "research auto-dispatch boundary mismatch" in result["errors"][0]
        assert evo_main.store.get(decision_id).decision_state.value == "approved"

    @pytest.mark.parametrize(
        "payload_kind",
        [
            "empty-object",
            "non-object",
            "missing-execution-result",
            "wrong-id",
            "wrong-state",
            "wrong-plane",
            "missing-ref",
            "wrong-ref",
        ],
    )
    def test_run_poll_rejects_empty_or_malformed_execute_2xx(
        self, monkeypatch, payload_kind
    ):
        decision_id = _propose_and_approve(action_type="retrain", risk_level="low")

        import services.evolution.dispatch_worker as dw

        def fake_get(url, timeout_seconds=10.0):
            path = url.split("127.0.0.1:8093")[-1]
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        def fake_post(url, payload, timeout_seconds=30.0):
            if payload_kind == "empty-object":
                return {}
            if payload_kind == "non-object":
                return []
            response = {
                "decision_id": decision_id,
                "action_type": "retrain",
                "decision_state": "executed",
                "cooldown_ends_at": "2026-07-17T00:00:00Z",
                "observation_window_ends_at": "2026-07-21T00:00:00Z",
                "execution_result": {
                    "status": "submitted",
                    "plane": "research",
                    "execution_ref_id": f"dispatch-{decision_id}",
                    "executed_at": "2026-07-14T00:00:00Z",
                },
            }
            if payload_kind == "missing-execution-result":
                response.pop("execution_result")
            elif payload_kind == "wrong-id":
                response["decision_id"] = "different-decision"
            elif payload_kind == "wrong-state":
                response["decision_state"] = "approved"
            elif payload_kind == "wrong-plane":
                response["execution_result"]["plane"] = "governance"
            elif payload_kind == "missing-ref":
                response["execution_result"]["execution_ref_id"] = ""
            elif payload_kind == "wrong-ref":
                response["execution_result"]["execution_ref_id"] = "dispatch-other"
            return response

        monkeypatch.setattr(dw, "_http_get", fake_get)
        monkeypatch.setattr(dw, "_http_post", fake_post)

        result = run_poll(
            api_url="http://127.0.0.1:8093", actor_id="test-worker"
        )

        assert result["dispatched"] == 0
        assert len(result["errors"]) == 1
        assert "decision_id=" + decision_id in result["errors"][0]
        assert evo_main.store.get(decision_id).decision_state.value == "approved"

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


class TestHealthcheck:
    def test_healthcheck_accepts_recent_success(self, monkeypatch, tmp_path):
        health_file = tmp_path / "dispatch-health.json"
        health_file.write_text(
            json.dumps({"status": "ok", "ticks": 1}), encoding="utf-8"
        )
        monkeypatch.setenv("EVOLUTION_DISPATCH_HEALTH_FILE", str(health_file))
        monkeypatch.setenv("EVOLUTION_DISPATCH_INTERVAL_SECONDS", "30")

        assert healthcheck() == 0

    @pytest.mark.parametrize("status", ["starting", "degraded"])
    def test_healthcheck_rejects_non_ok_state(
        self, monkeypatch, tmp_path, status
    ):
        health_file = tmp_path / "dispatch-health.json"
        health_file.write_text(
            json.dumps({"status": status, "ticks": 1}), encoding="utf-8"
        )
        monkeypatch.setenv("EVOLUTION_DISPATCH_HEALTH_FILE", str(health_file))

        assert healthcheck() == 1

    def test_healthcheck_rejects_stale_success(self, monkeypatch, tmp_path):
        health_file = tmp_path / "dispatch-health.json"
        health_file.write_text(
            json.dumps({"status": "ok", "ticks": 1}), encoding="utf-8"
        )
        stale_at = time.time() - 91
        os.utime(health_file, (stale_at, stale_at))
        monkeypatch.setenv("EVOLUTION_DISPATCH_HEALTH_FILE", str(health_file))
        monkeypatch.setenv("EVOLUTION_DISPATCH_INTERVAL_SECONDS", "30")

        assert healthcheck() == 1


class TestMainLoop:
    def test_boot_resets_old_success_health_before_first_poll(
        self, monkeypatch, tmp_path, capsys
    ):
        import services.evolution.dispatch_worker as dw

        health_file = tmp_path / "dispatch-health.json"
        health_file.write_text(
            json.dumps({"status": "ok", "ticks": 99}), encoding="utf-8"
        )
        observed_before_poll: list[dict[str, object]] = []

        def inspect_starting_health(**kwargs):
            observed_before_poll.append(
                json.loads(health_file.read_text(encoding="utf-8"))
            )
            return {
                "decisions_found": 0,
                "dispatched": 0,
                "skipped_already_executed": 0,
                "skipped_unsupported": 0,
                "errors": [],
                "dispatch_items": [],
                "skip_items": [],
            }

        monkeypatch.setattr(dw, "run_poll", inspect_starting_health)
        monkeypatch.setenv("EVOLUTION_DISPATCH_MAX_TICKS", "1")
        monkeypatch.setenv("EVOLUTION_DISPATCH_HEALTH_FILE", str(health_file))

        assert dw.main() == 0
        capsys.readouterr()

        assert len(observed_before_poll) == 1
        assert observed_before_poll[0]["status"] == "starting"
        assert observed_before_poll[0]["ticks"] == 0
        assert observed_before_poll[0]["total_dispatched"] == 0

    def test_approved_decision_auto_executes_on_single_tick(
        self, monkeypatch, tmp_path, capsys
    ):
        import services.evolution.dispatch_worker as dw

        decision_id = _propose_and_approve(action_type="retrain", risk_level="low")
        health_file = tmp_path / "dispatch-health.json"

        def fake_get(url, timeout_seconds=10.0):
            path = url.split("127.0.0.1:8093")[-1]
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        def fake_post(url, payload, timeout_seconds=30.0):
            path = url.split("127.0.0.1:8093")[-1]
            response = client.post(path, json=payload)
            response.raise_for_status()
            return response.json()

        monkeypatch.setattr(dw, "_http_get", fake_get)
        monkeypatch.setattr(dw, "_http_post", fake_post)
        monkeypatch.setenv("EVOLUTION_API_URL", "http://127.0.0.1:8093")
        monkeypatch.setenv("EVOLUTION_DISPATCH_MAX_TICKS", "1")
        monkeypatch.setenv("EVOLUTION_DISPATCH_HEALTH_FILE", str(health_file))

        assert dw.main() == 0

        tick = json.loads(capsys.readouterr().out)
        decision = evo_main.store.get(decision_id)
        assert decision is not None
        persisted = decision.to_dict()
        assert tick["health"]["status"] == "ok"
        assert tick["result"]["dispatched"] == 1
        assert tick["result"]["dispatch_items"][0]["decision_id"] == decision_id
        assert persisted["decision_state"] == "executed"
        assert persisted["execution_result"]["execution_ref_id"] == (
            f"dispatch-{decision_id}"
        )

    def test_unreachable_api_logs_degraded_tick_without_dispatch(
        self, monkeypatch, tmp_path, capsys
    ):
        import services.evolution.dispatch_worker as dw

        health_file = tmp_path / "dispatch-health.json"
        post_calls: list[str] = []

        def unreachable_get(url, timeout_seconds=10.0):
            raise urllib.error.URLError("evolution api unavailable")

        def fail_if_posted(url, payload, timeout_seconds=30.0):
            post_calls.append(url)
            raise AssertionError("unreachable API tick must not dispatch")

        monkeypatch.setattr(dw, "_http_get", unreachable_get)
        monkeypatch.setattr(dw, "_http_post", fail_if_posted)
        monkeypatch.setenv("EVOLUTION_API_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("EVOLUTION_DISPATCH_MAX_TICKS", "1")
        monkeypatch.setenv("EVOLUTION_DISPATCH_HEALTH_FILE", str(health_file))

        assert dw.main() == 0

        tick = json.loads(capsys.readouterr().out)
        persisted_health = json.loads(health_file.read_text(encoding="utf-8"))
        assert post_calls == []
        assert tick["health"]["status"] == "degraded"
        assert tick["result"]["dispatched"] == 0
        assert tick["result"]["dispatch_items"] == []
        assert "evolution api unavailable" in tick["result"]["errors"][0]
        assert persisted_health["status"] == "degraded"
        assert "evolution api unavailable" in persisted_health["last_failure_reason"]
