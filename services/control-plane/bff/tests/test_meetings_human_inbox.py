"""Tests for Human Inbox meeting-derived items (BFF-MEETINGS-VIEWS-R2-20261116 AC4 & AC5).

Verifies that ManagementService.get_human_inbox includes:
1. Meetings ending in REVIEW (`outcome == "REVIEW"` or `status == "REVIEW"`).
2. Due meetings refused for too few participants (`reason == "insufficient_participants"`).
3. Proposals waiting for an operator (`downstream.status` in pending/operator-waiting states).
"""
from __future__ import annotations

import io
import json
import time
from typing import Any, Dict, List
from urllib.error import URLError
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import bff_error, extract_identity, require_read_role
from services.control_plane.bff.core.app_factory import build_bff_app
from services.control_plane.bff.governance.service import human_inbox_surface_timeout_seconds
from services.control_plane.bff.management_read_models import create_management_router
from services.control_plane.bff.management_read_models.service import ManagementService, _snapshot_meta
from services.control_plane.bff.models import utc_now
from services.control_plane.bff.personas.routes.common import run_management_read
from services.runtime_auth_inbound import encode_jwt_hs256

SECRET = "meetings-human-inbox-r2-test-secret"
SERVICE_TOKEN = "test-only-consultation-inbox-token"


class _StubReadSurface:
    def list_approval_queue_items(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return []

    def list_governance_review_queue_items(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return []

    def list_incidents(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return []

    def dataset_source(self, _name: str) -> str:
        return "read_store"


def _auth_headers(tenant: str = "tenant-a") -> Dict[str, str]:
    token = encode_jwt_hs256(
        {"sub": "op-inbox", "roles": ["operator"], "tenant_id": tenant, "exp": 4102444800},
        secret=SECRET,
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant}


@pytest.fixture
def inbox_meetings_stack(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "")
    monkeypatch.delenv("PANTHEON_BFF_AUTH_STUB", raising=False)
    monkeypatch.setenv("CONSULTATION_AUTH_REQUIRED", "true")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", "http://consultation.test")

    state: Dict[str, Any] = {
        "offline": False,
        "meetings": [
            {
                "meeting_id": "mtg-review-01",
                "meeting_type": "MT-1",
                "status": "closed",
                "outcome": "REVIEW",
                "chair_persona_id": "persona-alpha",
                "participants": [{"persona_id": "persona-alpha", "role": "chair", "origin": "institutional"}],
                "round": 2,
                "created_at": "2026-11-16T08:00:00Z",
                "closed_at": "2026-11-16T08:10:00Z",
            },
            {
                "meeting_id": "mtg-normal-02",
                "meeting_type": "MT-2",
                "status": "closed",
                "outcome": "PROPOSALS_ISSUED",
                "chair_persona_id": "persona-alpha",
                "participants": [{"persona_id": "persona-alpha", "role": "chair", "origin": "institutional"}],
                "round": 2,
                "created_at": "2026-11-16T07:00:00Z",
                "closed_at": "2026-11-16T07:10:00Z",
            },
        ],
        "proposals": [
            {
                "proposal_id": "prop-wait-01",
                "meeting_id": "mtg-normal-02",
                "kind": "governance_approval",
                "sponsor_persona_id": "persona-alpha",
                "downstream": {
                    "target": "governance_approval",
                    "id": "apv-901",
                    "status": "pending_operator",
                },
            },
            {
                "proposal_id": "prop-done-02",
                "meeting_id": "mtg-normal-02",
                "kind": "research_demand",
                "sponsor_persona_id": "persona-alpha",
                "downstream": {
                    "target": "research_demand",
                    "id": "rd-902",
                    "status": "completed",
                },
            },
        ],
        "catalogue": {
            "types": [
                {
                    "meeting_type": "MT-3",
                    "enabled": True,
                    "cadence": "weekly",
                    "roster_rule": "all_enrolled_trading_personas_plus_red_team",
                    "budget": {"max_participants": 5, "max_rounds": 2, "weekly_limit": 3},
                }
            ],
            "schedule": [
                {
                    "persona_id": "persona-alpha",
                    "meeting_type": "MT-3",
                    "last_run_at": "2026-11-09T09:00:00Z",
                    "next_due_at": "2026-11-16T09:00:00Z",
                }
            ],
            "refusals": [
                {
                    "persona_id": "persona-alpha",
                    "meeting_type": "MT-3",
                    "reason": "insufficient_participants",
                    "at": "2026-11-16T09:00:00Z",
                },
                {
                    "persona_id": "persona-alpha",
                    "meeting_type": "MT-2",
                    "reason": "budget_exhausted",
                    "at": "2026-11-16T09:30:00Z",
                },
            ],
        },
    }

    def transport(req, timeout=5):
        u = urlsplit(req.full_url)
        assert u.hostname == "consultation.test", f"Unexpected host: {req.full_url}"
        if state["offline"]:
            raise URLError("simulated consultation outage")
        if u.path == "/api/consult/meetings":
            res = io.BytesIO(json.dumps({"items": state["meetings"], "next_cursor": None}).encode("utf-8"))
            res.status = 200
            return res
        if u.path == "/api/consult/meetings/proposals":
            res = io.BytesIO(json.dumps({"items": state["proposals"]}).encode("utf-8"))
            res.status = 200
            return res
        if u.path == "/api/consult/meetings/catalogue":
            res = io.BytesIO(json.dumps(state["catalogue"]).encode("utf-8"))
            res.status = 200
            return res
        raise AssertionError(f"Unexpected path {u.path}")

    monkeypatch.setattr("urllib.request.urlopen", transport)

    store = _StubReadSurface()
    service = ManagementService(get_read_store=lambda: store)
    app = build_bff_app()
    app.include_router(
        create_management_router(
            read_surface=store,
            get_read_store=lambda: store,
            service=service,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            snapshot_meta=_snapshot_meta,
            utc_now=utc_now,
            bff_error=bff_error,
            run_management_read=run_management_read,
        )
    )
    return TestClient(app), service, state


def test_human_inbox_includes_review_meetings_refused_due_meetings_and_operator_proposals(
    inbox_meetings_stack,
):
    client, _service, _state = inbox_meetings_stack

    resp = client.get("/bff/management/human-inbox", headers=_auth_headers())
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    items = payload["data"]["items"]

    kinds_by_id = {item["id"]: item["kind"] for item in items}
    assert kinds_by_id == {
        "meeting_review:mtg-review-01": "meeting_review",
        "meeting_refusal:refusal-persona-alpha-MT-3-0": "meeting_refusal",
        "meeting_proposal:prop-wait-01": "meeting_proposal",
    }

    review_item = next(i for i in items if i["kind"] == "meeting_review")
    assert review_item["meeting_id"] == "mtg-review-01"
    assert review_item["outcome"] == "REVIEW"
    assert review_item["href"] == "/management/meetings/mtg-review-01"

    refusal_item = next(i for i in items if i["kind"] == "meeting_refusal")
    assert refusal_item["meeting_type"] == "MT-3"
    assert refusal_item["reason"] == "insufficient_participants"
    assert refusal_item["href"] == "/management/meetings?tab=budget"

    proposal_item = next(i for i in items if i["kind"] == "meeting_proposal")
    assert proposal_item["proposal_id"] == "prop-wait-01"
    assert proposal_item["meeting_id"] == "mtg-normal-02"
    assert proposal_item["href"] == "/management/meetings/mtg-normal-02"

    assert payload["data"]["summary"]["meeting_count"] == 3
    assert payload["meta"]["surfaces"]["meetings"]["status"] == "ok"

    # Filtering by source_type=meeting or specific kind works
    only_review = client.get(
        "/bff/management/human-inbox?source_type=meeting_review",
        headers=_auth_headers(),
    )
    assert only_review.status_code == 200
    assert [i["id"] for i in only_review.json()["data"]["items"]] == ["meeting_review:mtg-review-01"]


def test_human_inbox_degrades_only_meetings_surface_when_consultation_owner_fails(
    inbox_meetings_stack,
):
    client, _service, state = inbox_meetings_stack
    state["offline"] = True

    resp = client.get("/bff/management/human-inbox", headers=_auth_headers())
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["meta"]["partial"] is True
    assert "meetings" in payload["meta"]["degradation"]["contributors"]
    assert payload["meta"]["surfaces"]["meetings"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["approval_queue"]["status"] == "ok"


def test_human_inbox_200_meetings_within_budget(inbox_meetings_stack):
    client, _service, state = inbox_meetings_stack
    state["meetings"] = [
        {
            "meeting_id": f"mtg-{i:03d}",
            "meeting_type": "MT-1",
            "status": "closed",
            "outcome": "REVIEW" if i <= 10 else "PROPOSALS_ISSUED",
            "chair_persona_id": "persona-alpha",
            "participants": [{"persona_id": "persona-alpha", "role": "chair", "origin": "institutional"}],
            "round": 2,
            "created_at": "2026-11-16T08:00:00Z",
            "closed_at": "2026-11-16T08:10:00Z",
        }
        for i in range(1, 201)
    ]

    budget = human_inbox_surface_timeout_seconds()
    t0 = time.perf_counter()
    resp = client.get("/bff/management/human-inbox?page_size=50", headers=_auth_headers())
    elapsed = time.perf_counter() - t0
    assert resp.status_code == 200, resp.text
    assert elapsed < budget, f"Human inbox with 200 meetings took {elapsed:.3f}s >= budget {budget:.3f}s"
    assert resp.json()["data"]["summary"]["meeting_count"] == 12


def test_human_inbox_review_meeting_exposes_cause_and_evidence_and_leaves_pending_when_reviewed(
    inbox_meetings_stack,
):
    client, _service, state = inbox_meetings_stack
    state["meetings"] = [
        {
            "meeting_id": "mtg-2c016da5651a0dc4",
            "meeting_type": "MT-1",
            "status": "closed",
            "outcome": "REVIEW",
            "chair_persona_id": "persona-2448c7053e33ea48da47",
            "participants": [{"persona_id": "persona-2448c7053e33ea48da47", "role": "chair", "origin": "institutional"}],
            "case_file_items": [
                {"item_id": "facts:pool:persona-2448c7053e33ea48da47:v1"},
                {"item_id": "secret-private-inbox-item", "scope": "private"},
            ],
            "rounds": [
                {
                    "round": 1,
                    "positions": [
                        {
                            "persona_id": "persona-2448c7053e33ea48da47",
                            "is_private": True,
                            "citations": ["secret-private-inbox-pos-ref"],
                        }
                    ],
                }
            ],
            "synthesis": {
                "proposals": [],
                "outcome": "REVIEW",
                "unreadable_reason": None,
                "summary": "Synthesis empty or unreadable; recorded as REVIEW with no proposal.",
            },
            "operator_review": None,
            "round": 2,
            "created_at": "2026-11-20T17:38:10Z",
            "closed_at": "2026-11-20T17:40:52Z",
        },
        {
            "meeting_id": "mtg-review-specific-02",
            "meeting_type": "MT-2",
            "status": "closed",
            "outcome": "REVIEW",
            "synthesis_unreadable_reason": "model_output_missing",
            "chair_persona_id": "persona-alpha",
            "participants": [{"persona_id": "persona-alpha", "role": "chair", "origin": "institutional"}],
            "evidence_refs": ["facts:lessons:alpha:1"],
            "operator_review": None,
            "round": 2,
            "created_at": "2026-11-21T08:00:00Z",
            "closed_at": "2026-11-21T08:05:00Z",
        },
    ]

    # Before operator review: both REVIEW meetings are pending in Human Inbox with cause & evidence_refs
    before = client.get("/bff/management/human-inbox?source_type=meeting_review", headers=_auth_headers())
    assert before.status_code == 200, before.text
    items_before = {i["meeting_id"]: i for i in before.json()["data"]["items"]}
    assert set(items_before) == {"mtg-2c016da5651a0dc4", "mtg-review-specific-02"}
    assert items_before["mtg-2c016da5651a0dc4"]["unreadable_reason"] == "unknown"
    assert items_before["mtg-2c016da5651a0dc4"]["evidence_refs"] == ["facts:pool:persona-2448c7053e33ea48da47:v1"]
    assert items_before["mtg-review-specific-02"]["unreadable_reason"] == "model_output_missing"
    assert items_before["mtg-review-specific-02"]["evidence_refs"] == ["facts:lessons:alpha:1"]

    # After operator reviews mtg-review-specific-02: it leaves the pending Human Inbox while original REVIEW outcome stays
    state["meetings"][1]["operator_review"] = {
        "status": "reviewed",
        "disposition": "closed_no_proposal",
        "note": "Participants requested more research; closed review without proposal.",
        "reviewed_by": "op-inbox",
        "reviewed_at": "2026-11-21T12:10:00Z",
        "cause": "model_output_missing",
    }

    after = client.get("/bff/management/human-inbox?source_type=meeting_review", headers=_auth_headers())
    assert after.status_code == 200, after.text
    items_after = after.json()["data"]["items"]
    assert [i["meeting_id"] for i in items_after] == ["mtg-2c016da5651a0dc4"]
