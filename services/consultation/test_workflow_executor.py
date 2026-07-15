"""
Unit tests for the consultation workflow executor.

Acceptance (LOOP-AUTO-KNOW-006):
- Committee and red-team workflow executes from ConsultRequest.
- Memo generation and governance handoff are durable (idempotent).
- Handoffs are consumed exactly once or reported blocked.
"""
from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from services.consultation import workflow_executor as wex
from services.consultation.main import app, store
from services.consultation.models import (
    ConsultRequestStatus,
    GateHandoffStatus,
    MemoStatus,
)
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request_payload(
    request_type: str = "strategy_review",
    target_id: str = "strat-wf-001",
) -> dict[str, Any]:
    return {
        "request_type": request_type,
        "requested_by": {"actor_type": "persona", "actor_id": "p-test"},
        "target_type": "strategy",
        "target_id": target_id,
        "trace_id": f"trace-wf-{request_type}",
    }


# ---------------------------------------------------------------------------
# execute_workflow tests (via FastAPI TestClient → real store)
# ---------------------------------------------------------------------------


class TestExecuteWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        store.__init__(self.tmp.name)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _base_url(self) -> str:
        return "http://testserver"

    def _create_submitted_request(self, request_type: str = "strategy_review") -> str:
        resp = self.client.post(
            "/api/consult/requests", json=_make_request_payload(request_type)
        )
        assert resp.status_code == 201, resp.text
        request_id = resp.json()["request_id"]
        submit = self.client.post(f"/api/consult/requests/{request_id}/submit")
        assert submit.status_code == 200, submit.text
        return request_id

    def _get_request(self, request_id: str) -> dict[str, Any]:
        resp = self.client.get(f"/api/consult/requests/{request_id}")
        assert resp.status_code == 200, resp.text
        return resp.json()

    def _patched_wex(self) -> Any:
        """Return workflow_executor with HTTP calls wired to TestClient."""

        class _PatchedWex:
            def __init__(self, client: TestClient, base_url: str) -> None:
                self._client = client
                self._base_url = base_url

            def _get(self, path: str) -> Any:
                resp = self._client.get(path)
                assert resp.status_code == 200, resp.text
                return resp.json()

            def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
                resp = self._client.post(path, json=payload)
                assert resp.status_code in (200, 201), resp.text
                return resp.json()

        return _PatchedWex(self.client, self._base_url())

    # We patch the HTTP-level functions in workflow_executor to use TestClient.

    def _http_mock(self) -> dict[str, Any]:
        client = self.client

        def _mock_get(url: str, *, timeout_seconds: float) -> Any:
            path = url.replace("http://testserver", "").replace("http://127.0.0.1:8098", "")
            resp = client.get(path)
            if resp.status_code == 200:
                return resp.json()
            raise urllib.error.HTTPError(url, resp.status_code, resp.text, {}, None)  # type: ignore[arg-type]

        def _mock_post(url: str, payload: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
            path = url.replace("http://testserver", "").replace("http://127.0.0.1:8098", "")
            resp = client.post(path, json=payload)
            if resp.status_code in (200, 201):
                return resp.json()
            raise urllib.error.HTTPError(url, resp.status_code, resp.text, {}, None)  # type: ignore[arg-type]

        return {"_json_get": _mock_get, "_json_post": _mock_post}

    def _run_workflow(self, request_dict: dict[str, Any]) -> dict[str, str]:
        mocks = self._http_mock()
        with (
            patch.object(wex, "_json_get", mocks["_json_get"]),
            patch.object(wex, "_json_post", mocks["_json_post"]),
        ):
            return wex.execute_workflow(
                api_url="http://testserver",
                request=request_dict,
                timeout_seconds=10.0,
            )

    def _run_tick(self) -> dict[str, Any]:
        mocks = self._http_mock()
        with (
            patch.object(wex, "_json_get", mocks["_json_get"]),
            patch.object(wex, "_json_post", mocks["_json_post"]),
        ):
            return wex.run_tick(api_url="http://testserver", timeout_seconds=10.0)

    def _simulate_participant_activity(self, request_id: str, participant_ref: str, participant_type: str = "committee") -> str:
        # 1. Post a transcript event from the participant
        self.client.post(
            f"/api/consult/requests/{request_id}/events",
            json={
                "request_id": request_id,
                "event_type": "discussion",
                "actor": {"actor_type": participant_type, "actor_id": participant_ref},
                "content": {"message": "Discussing strategy review"},
                "evidence_refs": []
            }
        )
        
        # 2. Attach evidence from the participant
        self.client.post(
            f"/api/consult/requests/{request_id}/evidence",
            json={
                "evidence_ref": {
                    "id": f"ev-{request_id}",
                    "evidence_type": "artifact_ref",
                    "link": "http://evidence-link"
                },
                "attached_by": {"actor_type": participant_type, "actor_id": participant_ref},
                "trace_id": f"trace-{request_id}"
            }
        )
        
        # 3. Submit memo from the participant
        memo_type = "committee_summary" if participant_type == "committee" else "redteam_report"
        resp = self.client.post(
            "/api/consult/memos",
            json={
                "request_id": request_id,
                "memo_type": memo_type,
                "author_type": participant_type,
                "author_ref": participant_ref,
                "summary": "Qualified memo summary",
                "findings": [],
                "recommendation": "approve",
                "trace_id": f"trace-{request_id}"
            }
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["memo_id"]

    # --- Tests ---

    def test_committee_workflow_runs_from_submitted_request(self) -> None:
        """Committee workflow: submitted → assigned → memo submitted + published → handoff created."""
        request_id = self._create_submitted_request("strategy_review")
        req = self._get_request(request_id)
        assert req["status"] == "submitted"

        # Tick 1: Assign participant (returns advanced)
        result1 = self._run_workflow(req)
        assert result1["outcome"] == "advanced"
        assert "assigned participant" in result1["detail"]

        # Tick 2: Post workflow_started event (returns advanced)
        req = self._get_request(request_id)
        result2 = self._run_workflow(req)
        assert result2["outcome"] == "advanced"
        assert "posted workflow_started event" in result2["detail"]

        # Now simulate provider activity (memo, transcript event, evidence)
        self._simulate_participant_activity(request_id, "committee-strategy_review")

        # Tick 3: Publish memo and create handoff (returns completed)
        req = self._get_request(request_id)
        result3 = self._run_workflow(req)
        assert result3["outcome"] == "completed"
        assert "gate_handoff" in result3["detail"]

        # Verify memo was published
        memos_resp = self.client.get(f"/api/consult/memos?request_id={request_id}")
        assert memos_resp.status_code == 200
        memos = memos_resp.json()
        assert len(memos) == 1
        assert memos[0]["status"] == "published"

        # Verify gate handoff was created
        handoffs_resp = self.client.get(f"/api/consult/handoffs?request_id={request_id}")
        assert handoffs_resp.status_code == 200
        handoffs = handoffs_resp.json()
        assert len(handoffs) == 1
        assert handoffs[0]["target_gate"].startswith("consultation.committee.")

    def test_redteam_workflow_runs_from_submitted_request(self) -> None:
        """Red-team workflow: submitted → assigned → memo submitted + published → handoff created."""
        request_id = self._create_submitted_request("redteam")
        req = self._get_request(request_id)

        # Tick 1: assign
        self._run_workflow(req)
        # Tick 2: workflow_started
        req = self._get_request(request_id)
        self._run_workflow(req)

        # Simulate provider activity
        self._simulate_participant_activity(request_id, "redteam-redteam")

        # Tick 3: publish and create handoff
        req = self._get_request(request_id)
        result = self._run_workflow(req)

        assert result["outcome"] == "completed", result

        memos_resp = self.client.get(f"/api/consult/memos?request_id={request_id}")
        memos = memos_resp.json()
        published = [m for m in memos if m["status"] == "published"]
        assert len(published) == 1

        handoffs_resp = self.client.get(f"/api/consult/handoffs?request_id={request_id}")
        handoffs = handoffs_resp.json()
        assert len(handoffs) == 1
        assert handoffs[0]["target_gate"].startswith("consultation.redteam.")

    def test_handoff_idempotent_on_second_tick(self) -> None:
        """Running the workflow twice produces exactly one handoff (idempotent)."""
        request_id = self._create_submitted_request("execution_risk")
        req = self._get_request(request_id)

        # Tick 1: assign
        self._run_workflow(req)
        # Tick 2: workflow_started
        req = self._get_request(request_id)
        self._run_workflow(req)

        # Simulate provider activity
        self._simulate_participant_activity(request_id, "committee-execution_risk")

        # Tick 3: publish and create handoff
        req = self._get_request(request_id)
        self._run_workflow(req)

        # Tick 4: idempotent handoff check
        req = self._get_request(request_id)
        result = self._run_workflow(req)
        assert result["outcome"] in {"completed", "skipped"}

        handoffs_resp = self.client.get(f"/api/consult/handoffs?request_id={request_id}")
        handoffs = handoffs_resp.json()
        assert len(handoffs) == 1, f"Expected exactly 1 handoff, got {len(handoffs)}"

    def test_unknown_request_type_is_blocked(self) -> None:
        """Requests with unknown type are reported as blocked, not silently skipped."""
        request_id = self._create_submitted_request("strategy_review")
        req = self._get_request(request_id)
        req["request_type"] = "unknown_type_xyz"

        result = self._run_workflow(req)

        assert result["outcome"] == "blocked"
        assert "unknown request_type" in result["detail"]

    def test_run_tick_processes_all_pending_requests(self) -> None:
        """run_tick picks up all submitted requests in one cycle."""
        ids = [
            self._create_submitted_request("strategy_review"),
            self._create_submitted_request("redteam"),
            self._create_submitted_request("capital_pool"),
        ]
        # Tick 1: assigns all
        result1 = self._run_tick()
        assert result1["requests_found"] >= len(ids)
        assert result1["advanced"] >= len(ids)

        # Tick 2: posts workflow_started for all
        result2 = self._run_tick()
        assert result2["advanced"] >= len(ids)

        # Simulate provider activity for all requests
        self._simulate_participant_activity(ids[0], "committee-strategy_review")
        self._simulate_participant_activity(ids[1], "redteam-redteam")
        self._simulate_participant_activity(ids[2], "committee-capital_pool")

        # Tick 3: publishes memos and creates handoffs for all
        result3 = self._run_tick()
        assert result3["completed"] >= len(ids)

    def test_run_tick_with_no_pending_requests(self) -> None:
        """run_tick returns zero counts when no requests are in actionable states."""
        result = self._run_tick()

        assert result["requests_found"] == 0
        assert result["completed"] == 0
        assert result["errors"] == []

    def test_memo_published_before_handoff_created(self) -> None:
        """Gate handoff requires a published memo: executor publishes before creating handoff."""
        request_id = self._create_submitted_request("incident")
        req = self._get_request(request_id)

        # Tick 1: assign
        self._run_workflow(req)
        # Tick 2: workflow_started
        req = self._get_request(request_id)
        self._run_workflow(req)

        # Simulate provider activity
        self._simulate_participant_activity(request_id, "committee-incident")

        # Tick 3: publish and create handoff
        req = self._get_request(request_id)
        result = self._run_workflow(req)
        assert result["outcome"] == "completed"

        handoffs_resp = self.client.get(f"/api/consult/handoffs?request_id={request_id}")
        handoffs = handoffs_resp.json()
        assert len(handoffs) == 1

        # Memo must be published before handoff was created
        memos_resp = self.client.get(f"/api/consult/memos?request_id={request_id}")
        memos = memos_resp.json()
        assert all(m["status"] == "published" for m in memos)

    def test_second_tick_result_is_completed_not_advanced(self) -> None:
        """After workflow completes, subsequent ticks report completed (not re-advancing)."""
        request_id = self._create_submitted_request("data_leakage")
        req = self._get_request(request_id)

        # Tick 1: assign
        self._run_workflow(req)
        # Tick 2: workflow_started
        req = self._get_request(request_id)
        self._run_workflow(req)

        # Simulate provider activity
        self._simulate_participant_activity(request_id, "redteam-data_leakage")

        # Tick 3: publish and create handoff (drives to completion)
        req = self._get_request(request_id)
        result = self._run_workflow(req)
        assert result["outcome"] == "completed"

        # Tick 4: subsequent tick (reports completed)
        req = self._get_request(request_id)
        result2 = self._run_workflow(req)
        assert result2["outcome"] in {"completed", "skipped"}, result2

    def test_already_published_request_is_skipped(self) -> None:
        """Requests already in PUBLISHED state are not re-processed."""
        request_id = self._create_submitted_request("persona_policy")
        req = self._get_request(request_id)

        # Tick 1: assign
        self._run_workflow(req)
        # Tick 2: workflow_started
        req = self._get_request(request_id)
        self._run_workflow(req)

        # Simulate provider activity
        self._simulate_participant_activity(request_id, "committee-persona_policy")

        # Tick 3: publish and create handoff
        req = self._get_request(request_id)
        self._run_workflow(req)

        # Manually read the now-published request
        req = self._get_request(request_id)
        if req["status"] == "published":
            result = self._run_workflow(req)
            assert result["outcome"] == "skipped"

    def test_system_memo_rejected_and_workflow_blocked(self) -> None:
        """System-authored memos must be rejected/blocked from publishing."""
        request_id = self._create_submitted_request("strategy_review")
        req = self._get_request(request_id)
        
        # Tick 1: Assign participant
        self._run_workflow(req)
        # Tick 2: Start transcript event
        req = self._get_request(request_id)
        self._run_workflow(req)
        
        # Post a real transcript event and attach qualified evidence
        self.client.post(
            f"/api/consult/requests/{request_id}/events",
            json={
                "request_id": request_id,
                "event_type": "discussion",
                "actor": {"actor_type": "committee", "actor_id": "committee-strategy_review"},
                "content": {"message": "Discussing strategy review"},
                "evidence_refs": []
            }
        )
        self.client.post(
            f"/api/consult/requests/{request_id}/evidence",
            json={
                "evidence_ref": {
                    "id": f"ev-{request_id}",
                    "evidence_type": "artifact_ref",
                    "link": "http://evidence-link"
                },
                "attached_by": {"actor_type": "committee", "actor_id": "committee-strategy_review"},
                "trace_id": f"trace-{request_id}"
            }
        )

        # Submit a SYSTEM-authored memo
        resp = self.client.post(
            "/api/consult/memos",
            json={
                "request_id": request_id,
                "memo_type": "committee_summary",
                "author_type": "system",
                "author_ref": "consultation-workflow-executor",
                "summary": "System memo summary",
                "findings": [],
                "recommendation": "approve",
                "trace_id": f"trace-{request_id}"
            }
        )
        assert resp.status_code == 201
        
        # Try to publish or run workflow (which tries to publish) -> should be blocked
        req = self._get_request(request_id)
        result = self._run_workflow(req)
        assert result["outcome"] == "blocked"
        assert "no qualified real participant/provider-authored memo" in result["detail"]

    def test_unassigned_participant_memo_rejected(self) -> None:
        """Memo authored by an unassigned participant must be blocked."""
        request_id = self._create_submitted_request("strategy_review")
        req = self._get_request(request_id)
        
        # Tick 1: Assign participant (assigns committee-strategy_review)
        self._run_workflow(req)
        # Tick 2: Start transcript event
        req = self._get_request(request_id)
        self._run_workflow(req)
        
        # Post a real transcript event and attach qualified evidence
        self.client.post(
            f"/api/consult/requests/{request_id}/events",
            json={
                "request_id": request_id,
                "event_type": "discussion",
                "actor": {"actor_type": "committee", "actor_id": "committee-strategy_review"},
                "content": {"message": "Discussing strategy review"},
                "evidence_refs": []
            }
        )
        self.client.post(
            f"/api/consult/requests/{request_id}/evidence",
            json={
                "evidence_ref": {
                    "id": f"ev-{request_id}",
                    "evidence_type": "artifact_ref",
                    "link": "http://evidence-link"
                },
                "attached_by": {"actor_type": "committee", "actor_id": "committee-strategy_review"},
                "trace_id": f"trace-{request_id}"
            }
        )

        # Submit a memo authored by an unassigned participant
        resp = self.client.post(
            "/api/consult/memos",
            json={
                "request_id": request_id,
                "memo_type": "committee_summary",
                "author_type": "committee",
                "author_ref": "some-other-committee", # Not assigned!
                "summary": "Unassigned memo summary",
                "findings": [],
                "recommendation": "approve",
                "trace_id": f"trace-{request_id}"
            }
        )
        assert resp.status_code == 201
        
        # Should be blocked
        req = self._get_request(request_id)
        result = self._run_workflow(req)
        assert result["outcome"] == "blocked"
        assert "no qualified real participant/provider-authored memo" in result["detail"]

    def test_transcript_without_real_events_blocked(self) -> None:
        """Workflow is blocked if transcript contains no events from real assigned participant."""
        request_id = self._create_submitted_request("strategy_review")
        req = self._get_request(request_id)
        
        # Tick 1: Assign participant
        self._run_workflow(req)
        # Tick 2: Start transcript event (this event is authored by system/service)
        req = self._get_request(request_id)
        self._run_workflow(req)
        
        # Submit a qualified memo and attach evidence, but NO real transcript event
        self.client.post(
            f"/api/consult/requests/{request_id}/evidence",
            json={
                "evidence_ref": {
                    "id": f"ev-{request_id}",
                    "evidence_type": "artifact_ref",
                    "link": "http://evidence-link"
                },
                "attached_by": {"actor_type": "committee", "actor_id": "committee-strategy_review"},
                "trace_id": f"trace-{request_id}"
            }
        )
        self.client.post(
            "/api/consult/memos",
            json={
                "request_id": request_id,
                "memo_type": "committee_summary",
                "author_type": "committee",
                "author_ref": "committee-strategy_review",
                "summary": "Qualified memo summary",
                "findings": [],
                "recommendation": "approve",
                "trace_id": f"trace-{request_id}"
            }
        )
        
        # Should be blocked because no real transcript event exists
        req = self._get_request(request_id)
        result = self._run_workflow(req)
        assert result["outcome"] == "blocked"
        assert "transcript is not qualified" in result["detail"]


# ---------------------------------------------------------------------------
# Unit tests for pure helper functions (no HTTP)
# ---------------------------------------------------------------------------


class TestHelpers(unittest.TestCase):
    def test_stable_id_is_deterministic(self) -> None:
        a = wex._stable_id("trace", "cr-abc123")
        b = wex._stable_id("trace", "cr-abc123")
        assert a == b
        assert a.startswith("trace-")

    def test_stable_id_differs_per_request(self) -> None:
        a = wex._stable_id("trace", "cr-aaa")
        b = wex._stable_id("trace", "cr-bbb")
        assert a != b

    def test_committee_types_route_to_primary_reviewer(self) -> None:
        for rt in ["strategy_review", "capital_pool", "execution_risk", "incident", "persona_policy"]:
            spec = wex._request_type_to_participant(rt)
            assert spec is not None, rt
            assert spec[2] == "primary_reviewer", rt

    def test_redteam_types_route_to_red_team(self) -> None:
        for rt in ["redteam", "data_leakage"]:
            spec = wex._request_type_to_participant(rt)
            assert spec is not None, rt
            assert spec[2] == "red_team", rt

    def test_unknown_type_returns_none(self) -> None:
        assert wex._request_type_to_participant("not_a_type") is None

    def test_memo_type_routing(self) -> None:
        assert wex._request_type_to_memo_type("redteam") == "redteam_report"
        assert wex._request_type_to_memo_type("data_leakage") == "redteam_report"
        assert wex._request_type_to_memo_type("strategy_review") == "committee_summary"
        assert wex._request_type_to_memo_type("capital_pool") == "committee_summary"

    def test_gate_routing(self) -> None:
        assert wex._request_type_to_gate("redteam").startswith("consultation.redteam.")
        assert wex._request_type_to_gate("strategy_review").startswith("consultation.committee.")

    def test_non_actionable_status_skipped(self) -> None:
        req = {
            "request_id": "cr-test",
            "request_type": "strategy_review",
            "status": "published",
            "evidence_refs": [],
        }

        def _mock_get(url: str, *, timeout_seconds: float) -> Any:
            return []

        def _mock_post(url: str, payload: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
            raise AssertionError("Should not call POST for published request")

        with (
            patch.object(wex, "_json_get", _mock_get),
            patch.object(wex, "_json_post", _mock_post),
        ):
            result = wex.execute_workflow(api_url="http://localhost", request=req, timeout_seconds=5.0)

        assert result["outcome"] == "skipped"


if __name__ == "__main__":
    unittest.main()
