"""Tests for Human Inbox meeting-derived items (BFF-MEETINGS-VIEWS-R2-20261116 AC4 & AC5).

Verifies that ManagementService.get_human_inbox includes:
1. Meetings ending in REVIEW (`outcome == "REVIEW"` or `status == "REVIEW"`).
2. Due meetings refused for too few participants (`reason == "insufficient_participants"`).
3. Proposals waiting for an operator (`downstream.status` in pending/operator-waiting states).
"""
from __future__ import annotations

import io
import threading
from copy import deepcopy
import json
import statistics
import time
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import bff_error, extract_identity, require_read_role
from services.control_plane.bff.core.app_factory import build_bff_app
from services.control_plane.bff.core.owner_reads import (
    OwnerReadContextMiddleware, management_read_deadline, remaining_management_read_budget,
)
from services.control_plane.bff.governance.service import human_inbox_surface_timeout_seconds
from services.control_plane.bff.management_read_models import create_management_router
from services.control_plane.bff.management_read_models.service import ManagementService, _snapshot_meta
from services.control_plane.bff.models import utc_now
from services.control_plane.bff.meetings import client as meetings_client
from services.control_plane.bff.personas.routes.common import run_management_read
from services.runtime_auth_inbound import encode_jwt_hs256

SECRET = "meetings-human-inbox-r2-test-secret"
SERVICE_TOKEN = "test-only-consultation-inbox-token"

# Existing incident-shaped regression row; retained unchanged for budget tests.
INCIDENT_MEETING = {
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
}


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
    assert payload["meta"]["surfaces"]["meetings"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["meetings"]["surfaces"]["consultation"]["status"] == "ok"
    assert payload["meta"]["surfaces"]["meetings"]["surfaces"]["research"]["reason"] == "research_owner_unconfigured"

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
    assert payload["meta"]["surfaces"]["meetings"]["status"] == "unavailable"
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
        deepcopy(INCIDENT_MEETING),
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


READ_PATHS = {
    "list_meetings": "/api/consult/meetings",
    "list_proposals": "/api/consult/meetings/proposals",
    "get_catalogue": "/api/consult/meetings/catalogue",
}


@pytest.fixture
def budget_stack(inbox_meetings_stack, monkeypatch):
    client, service, state = inbox_meetings_stack
    client.app.add_middleware(OwnerReadContextMiddleware)
    # Use the existing incident row, including REVIEW synthesis, operator_review
    # and private evidence, rather than a minimal id/status-only fake.
    state["meetings"] = [deepcopy(INCIDENT_MEETING)]
    monkeypatch.delenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", raising=False)
    return client, service, state


def _consultation_surface(payload):
    return payload["meta"]["surfaces"]["meetings"]["surfaces"]["consultation"]


def test_three_owner_reads_share_one_allowance_concurrently(budget_stack, monkeypatch, record_property):
    import urllib.request

    client, _service, state = budget_stack
    # Simulate the full 100-row owner page using the incident's full row shape.
    state["meetings"].extend(
        dict(deepcopy(INCIDENT_MEETING), meeting_id=f"mtg-budget-{index}")
        for index in range(99)
    )
    transport = urllib.request.urlopen
    barrier = threading.Barrier(3)
    calls = []
    latencies = {path: [] for path in READ_PATHS.values()}

    def delayed(req, timeout):
        assert req.get_header("X-pantheon-tenant-id") == "tenant-a"
        assert req.get_header("Authorization") == f"Bearer {SERVICE_TOKEN}"
        assert req.get_header("X-pantheon-service-actor") == "bff:human-inbox"
        assert 0 < timeout <= 0.8
        assert 0 < remaining_management_read_budget() <= 0.8
        calls.append(req.full_url)
        started = time.monotonic()
        barrier.wait(timeout=0.2)
        time.sleep(0.18)  # Barrier proves concurrency with the larger allowance too.
        response = transport(req, timeout=timeout)
        latencies[urlsplit(req.full_url).path].append(time.monotonic() - started)
        return response

    monkeypatch.setattr(urllib.request, "urlopen", delayed)
    elapsed_reads = []
    for _ in range(30):
        started = time.monotonic()
        response = client.get("/bff/management/human-inbox?source_type=meeting&page_size=200", headers=_auth_headers())
        elapsed_reads.append(time.monotonic() - started)
        assert response.status_code == 200, response.text
        assert elapsed_reads[-1] < 0.4
        surface = _consultation_surface(response.json())
        assert surface["status"] == "ok", surface
        items = response.json()["data"]["items"]
        assert any(item["id"] == "meeting_review:mtg-2c016da5651a0dc4" for item in items)
        assert "secret-private-inbox" not in response.text
    assert len(calls) == 90
    assert set(calls) == {
        "http://consultation.test/api/consult/meetings?limit=100",
        "http://consultation.test/api/consult/meetings/proposals",
        "http://consultation.test/api/consult/meetings/catalogue",
    }
    record_property("local_measurements_json", json.dumps({
        "kind": "local simulated transport; not hosted evidence",
        "calls": 30,
        "owner_reads_seconds": {
            name: {"median": statistics.median(latencies[path]), "max": max(latencies[path]),
                   "count": len(latencies[path])}
            for name, path in READ_PATHS.items()
        },
        "inbox_seconds": {"median": statistics.median(elapsed_reads), "max": max(elapsed_reads)},
    }))


@pytest.mark.parametrize("read_name", READ_PATHS)
@pytest.mark.parametrize("failure", ["socket_timeout", "url_timeout", "deadline"])
def test_each_timeout_names_read_and_retains_successful_items(
    budget_stack, monkeypatch, read_name, failure,
):
    import urllib.request

    client, _service, _state = budget_stack
    transport = urllib.request.urlopen
    completed = threading.Event()

    def failing(req, timeout):
        if urlsplit(req.full_url).path == READ_PATHS[read_name]:
            try:
                if failure == "socket_timeout":
                    raise TimeoutError("timed out")
                if failure == "url_timeout":
                    raise URLError(TimeoutError("timed out"))
                # Deliberately ignore the socket timeout to prove collection is
                # bounded independently, even if a transport returns too late.
                time.sleep(0.9)  # Exceeds the new shared 0.8 s owner deadline.
            finally:
                completed.set()
        return transport(req, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", failing)
    started = time.monotonic()
    response = client.get("/bff/management/human-inbox?source_type=meeting", headers=_auth_headers())
    elapsed = time.monotonic() - started
    # Collect the deliberately late mock before monkeypatch teardown.
    assert completed.wait(timeout=1)
    assert response.status_code == 200, response.text
    assert elapsed < 0.88
    surface = _consultation_surface(response.json())
    assert surface["status"] == "degraded", surface
    assert surface["reason"] == f"read_timeout:{read_name}"
    assert surface["reads"][read_name]["reason"] == f"read_timeout:{read_name}"
    ids = {item["id"] for item in response.json()["data"]["items"]}
    if read_name == "list_meetings":
        assert "meeting_review:mtg-2c016da5651a0dc4" not in ids
        assert "meeting_proposal:prop-wait-01" in ids
        assert "meeting_refusal:refusal-persona-alpha-MT-3-0" in ids
    else:
        assert "meeting_review:mtg-2c016da5651a0dc4" in ids
    assert "secret-private-inbox" not in response.text


@pytest.mark.parametrize("read_name", ["list_proposals", "get_catalogue"])
def test_secondary_owner_outage_keeps_incident_review(budget_stack, monkeypatch, read_name):
    import urllib.request

    client, _service, _state = budget_stack
    transport = urllib.request.urlopen

    def failing(req, timeout):
        if urlsplit(req.full_url).path == READ_PATHS[read_name]:
            raise URLError("connection refused")
        return transport(req, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", failing)
    response = client.get("/bff/management/human-inbox?source_type=meeting", headers=_auth_headers())
    assert response.status_code == 200, response.text
    surface = _consultation_surface(response.json())
    assert surface["status"] == "degraded"
    assert surface["reason"] == f"consultation_owner_unavailable:{read_name}"
    assert any(item["id"] == "meeting_review:mtg-2c016da5651a0dc4"
               for item in response.json()["data"]["items"])


def test_exhausted_parent_budget_names_all_three_reads(budget_stack):
    token = management_read_deadline.set(time.monotonic() - 1)
    try:
        with meetings_client._inbox_owner_budget():
            results, surface = meetings_client._read_inbox_consultation("tenant-a")
    finally:
        management_read_deadline.reset(token)
    assert results == {}
    assert surface["status"] == "unavailable"
    assert {row["reason"] for row in surface["reads"].values()} == {
        f"read_timeout:{name}" for name in READ_PATHS
    }


@pytest.mark.parametrize("configured,expected", [("10", 1.0), ("1.1", 1.0), ("0.5", 0.5)])
def test_surface_cap_unchanged_and_parallel_owners_share_former_phases(monkeypatch, configured, expected):
    monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", configured)
    assert human_inbox_surface_timeout_seconds() == expected
    with meetings_client._inbox_owner_budget():
        assert 0 < remaining_management_read_budget() <= expected * 0.8


def test_consultation_capacity_is_bounded_and_reported(budget_stack, monkeypatch):
    client, _service, _state = budget_stack
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(meetings_client, "_INBOX_CONSULTATION_SLOTS", slots)
    response = client.get("/bff/management/human-inbox?source_type=meeting", headers=_auth_headers())
    assert response.status_code == 200, response.text
    surface = _consultation_surface(response.json())
    assert surface["status"] == "unavailable"
    assert {row["reason"] for row in surface["reads"].values()} == {
        f"owner_read_capacity_exhausted:{name}" for name in READ_PATHS
    }


def test_secondary_auth_denial_does_not_publish_partial_consultation_data(budget_stack, monkeypatch):
    import urllib.request

    client, _service, _state = budget_stack
    transport = urllib.request.urlopen

    def denied(req, timeout):
        if urlsplit(req.full_url).path == READ_PATHS["list_proposals"]:
            raise HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b'{}'))
        return transport(req, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", denied)
    response = client.get("/bff/management/human-inbox?source_type=meeting", headers=_auth_headers())
    assert response.status_code == 200, response.text
    surface = _consultation_surface(response.json())
    assert surface["status"] == "denied"
    assert surface["reason"] == "consultation_owner_http_403:list_proposals"
    assert response.json()["data"]["items"] == []


def test_owner_pagination_remains_degraded_and_preserves_available_items(budget_stack, monkeypatch):
    client, _service, _state = budget_stack
    original = meetings_client.MeetingsOwnerClient.list_meetings

    def paged(self, **kwargs):
        return dict(original(self, **kwargs), next_cursor="next-owner-page")

    monkeypatch.setattr(meetings_client.MeetingsOwnerClient, "list_meetings", paged)
    response = client.get("/bff/management/human-inbox?source_type=meeting", headers=_auth_headers())
    assert response.status_code == 200, response.text
    surface = _consultation_surface(response.json())
    assert surface["status"] == "degraded"
    assert surface["reason"] == "owner_page_has_more"
    assert surface["next_cursor"] == "next-owner-page"
    assert any(item["id"] == "meeting_review:mtg-2c016da5651a0dc4"
               for item in response.json()["data"]["items"])


@pytest.fixture
def parallel_owners_stack(budget_stack, monkeypatch):
    import urllib.request
    # Reuse the full owner-contract ticket (meeting_action, input_request,
    # revision and lifecycle fields), alongside the actual incident meeting.
    from services.control_plane.bff.tests.test_meeting_action_human_response import ticket

    client, service, state = budget_stack
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research.test")
    transport = urllib.request.urlopen
    controls = {"consultation_delay": 0.01, "research_delay": 0.01,
                "events": [], "barrier": None, "research_status": 200,
                "research_next_page": None}

    def delayed(req, timeout):
        url = urlsplit(req.full_url)
        owner = "research" if url.hostname == "research.test" else "consultation"
        if owner == "research":
            assert url.path == "/api/research/tickets"
            assert parse_qs(url.query) == {"meeting_linked": ["true"],
                                          "human_actionable": ["true"], "page_size": ["100"]}
            assert req.get_header("Authorization") == _auth_headers()["Authorization"]
            assert req.get_header("X-tenant-id") == "tenant-a"
        else:
            assert req.get_header("Authorization") == f"Bearer {SERVICE_TOKEN}"
            assert req.get_header("X-pantheon-tenant-id") == "tenant-a"
        event = {"owner": owner, "path": url.path, "start": time.monotonic(),
                 "deadline": management_read_deadline.get(), "timeout": timeout,
                 "finished": threading.Event()}
        controls["events"].append(event)
        try:
            if controls["barrier"] is not None:
                controls["barrier"].wait(timeout=0.3)
            time.sleep(controls[f"{owner}_delay"])
            if owner == "consultation":
                return transport(req, timeout=timeout)
            if controls["research_status"] != 200:
                raise HTTPError(req.full_url, controls["research_status"], "Owner error", {},
                                io.BytesIO(b'{"detail":"research owner error"}'))
            response = io.BytesIO(json.dumps({"data": [ticket()], "page_info": {
                "next_page_token": controls["research_next_page"]}}).encode())
            response.status = 200
            return response
        finally:
            event["end"] = time.monotonic()
            event["finished"].set()

    monkeypatch.setattr(urllib.request, "urlopen", delayed)
    yield client, service, state, controls
    # Every deliberately late fake is collected before monkeypatch teardown.
    for event in controls["events"]:
        assert event["finished"].wait(timeout=2)


def _assert_owner_items(response, *, review=True, action=True):
    ids = {item["id"] for item in response.json()["data"]["items"]}
    assert ("meeting_review:mtg-2c016da5651a0dc4" in ids) is review
    assert ("meeting_action:rt-action-" + "a" * 32 in ids) is action
    assert "secret-private-inbox" not in response.text


@pytest.mark.parametrize("slow_owner", ["consultation", "research"])
def test_two_owners_start_together_with_identical_deadline(
    parallel_owners_stack, slow_owner, record_property,
):
    client, _, _, controls = parallel_owners_stack
    controls[f"{slow_owner}_delay"] = 0.18
    # All three consultation GETs and Research must enter before any returns.
    controls["barrier"] = threading.Barrier(4)
    started = time.monotonic()
    response = client.get("/bff/management/human-inbox?source_type=meeting", headers=_auth_headers())
    elapsed = time.monotonic() - started
    assert response.status_code == 200, response.text
    surface = response.json()["meta"]["surfaces"]["meetings"]
    assert surface["status"] == "ok", surface
    _assert_owner_items(response)
    events = controls["events"]
    assert len(events) == 4
    assert len({e["deadline"] for e in events}) == 1
    assert max(e["start"] for e in events) < min(e["end"] for e in events)
    assert max(e["start"] for e in events) - min(e["start"] for e in events) < 0.1
    for event in events:
        assert 0.6 < event["timeout"] <= 0.8
    assert elapsed < 0.4
    record_property("parallel_owner_measurements_json", json.dumps({
        "kind": "local simulated HTTP transport; not hosted evidence", "slow_owner": slow_owner,
        "inbox_seconds": elapsed,
        "events": [{k + "_seconds" if k in {"start", "end", "deadline"} else k:
                    v - started if k in {"start", "end", "deadline"} else v
                    for k, v in e.items() if k != "finished"} for e in events],
    }))


@pytest.mark.parametrize("configured", ["0.3", "0.6", "10"])
def test_both_late_owners_finish_contributor_inside_surface_cap(
    parallel_owners_stack, monkeypatch, configured,
):
    client, _, _, controls = parallel_owners_stack
    monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", configured)
    cap = human_inbox_surface_timeout_seconds()
    controls.update(consultation_delay=cap * 0.8 + 0.06, research_delay=cap * 0.8 + 0.06)
    started = time.monotonic()
    response = client.get("/bff/management/human-inbox?source_type=meeting", headers=_auth_headers())
    elapsed = time.monotonic() - started
    assert response.status_code == 200, response.text
    assert cap * 0.8 <= elapsed < cap
    surface = response.json()["meta"]["surfaces"]["meetings"]
    assert surface["surfaces"]["research"]["reason"] == "read_timeout:list_research_ticket_page"
    assert {r["reason"] for r in surface["surfaces"]["consultation"]["reads"].values()} == {
        f"read_timeout:{name}" for name in READ_PATHS}
    assert len({e["deadline"] for e in controls["events"]}) == 1
    _assert_owner_items(response, review=False, action=False)
    # Late completions must not mutate the response snapshot.
    snapshot = response.json()
    for event in controls["events"]:
        assert event["finished"].wait(timeout=2)
    assert response.json() == snapshot


@pytest.mark.parametrize("slow_owner", ["consultation", "research"])
def test_one_late_owner_retains_other_owner_items(parallel_owners_stack, monkeypatch, slow_owner):
    client, _, _, controls = parallel_owners_stack
    monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "0.4")
    controls[f"{slow_owner}_delay"] = 0.38
    response = client.get("/bff/management/human-inbox?source_type=meeting", headers=_auth_headers())
    assert response.status_code == 200, response.text
    surface = response.json()["meta"]["surfaces"]["meetings"]
    assert surface["status"] == "degraded", surface
    fast_owner = "research" if slow_owner == "consultation" else "consultation"
    assert surface["surfaces"][fast_owner]["status"] == "ok"
    assert surface["surfaces"][slow_owner]["status"] == "unavailable"
    _assert_owner_items(response, review=slow_owner != "consultation", action=slow_owner != "research")


@pytest.mark.parametrize("research_status", [401, 403, 503])
def test_research_error_keeps_consultation_review(parallel_owners_stack, research_status):
    client, _, _, controls = parallel_owners_stack
    controls["research_status"] = research_status
    response = client.get("/bff/management/human-inbox?source_type=meeting", headers=_auth_headers())
    assert response.status_code == 200, response.text
    research = response.json()["meta"]["surfaces"]["meetings"]["surfaces"]["research"]
    assert research["status"] == ("denied" if research_status in {401, 403} else "unavailable")
    assert research["status_code"] == research_status
    assert research["reason"] == "research owner error"
    _assert_owner_items(response, action=False)


def test_research_capacity_is_bounded_without_hiding_consultation(parallel_owners_stack, monkeypatch):
    client, _, _, controls = parallel_owners_stack
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(meetings_client, "_INBOX_RESEARCH_SLOTS", slots)
    response = client.get("/bff/management/human-inbox?source_type=meeting", headers=_auth_headers())
    assert response.status_code == 200, response.text
    research = response.json()["meta"]["surfaces"]["meetings"]["surfaces"]["research"]
    assert research["reason"] == "owner_read_capacity_exhausted:list_research_ticket_page"
    assert all(e["owner"] == "consultation" for e in controls["events"])
    assert meetings_client._INBOX_RESEARCH_EXECUTOR._max_workers == 4
    _assert_owner_items(response, action=False)


def test_research_pagination_is_preserved(parallel_owners_stack):
    client, _, _, controls = parallel_owners_stack
    controls["research_next_page"] = "opaque-owner-next"
    response = client.get("/bff/management/human-inbox?source_type=meeting", headers=_auth_headers())
    assert response.status_code == 200, response.text
    research = response.json()["meta"]["surfaces"]["meetings"]["surfaces"]["research"]
    assert research["status"] == "degraded"
    assert research["reason"] == "owner_page_has_more"
    assert research["page_info"]["next_page_token"] == "opaque-owner-next"
    _assert_owner_items(response)
