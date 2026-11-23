"""Authenticated normal BFF routes against owner HTTP contract doubles.

These prove adapter behavior, not a deployed/integrated Research transition.
Research owns independent transition/concurrency/evidence-owner tests.
"""
from __future__ import annotations

import copy
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort
from services.control_plane.bff.research.client import ResearchServiceClient
from services.control_plane.bff.tests.knowledge_read_port_fixtures import create_research_test_app
from services.control_plane.bff.tests.test_meetings_human_inbox import (
    inbox_meetings_stack, _auth_headers, SECRET as INBOX_SECRET,
)
from services.control_plane.bff.tests.test_meetings_views import meetings_stack, make_headers
from services.runtime_auth_inbound import encode_jwt_hs256


def ticket():
    return {"ticket_id": "rt-action-" + "a" * 32, "action_id": "rt-action-" + "a" * 32,
            "tenant_id": "tenant-a", "title": "Supply scoped research result",
            "owner": "persona-alpha", "assignee": "persona-alpha", "assignee_kind": "persona",
            "status": "in_progress", "action_state": "waiting_for_input", "revision": 3,
            "required_actor": "op-inbox", "input_request": {"reason": "Need licensed observations",
                "human_actor_id": "op-inbox", "requested_at": "2026-11-23T01:00:00Z"},
            "meeting_action": {"meeting_id": "mtg-001", "meeting_type": "MT-1",
                "subject_ref": "pool-alpha", "assignee_kind": "persona", "deliverable": "Licensed observations",
                "acceptance_criterion": "Readable owner artifact", "due_at": "2026-11-22T00:00:00Z",
                "next_review": "W48-MT1"}, "evidence_refs": [], "lifecycle_history": [],
            "created_at": "2026-11-21T01:00:00Z", "updated_at": "2026-11-23T01:00:00Z"}


@pytest.fixture
def research_stack(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", INBOX_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "")
    monkeypatch.delenv("PANTHEON_BFF_AUTH_STUB", raising=False)
    state = {"ticket": ticket(), "calls": [], "fail": None, "replays": {}}

    def transport(method, url, payload=None, headers=None):
        state["calls"].append((method, url, copy.deepcopy(payload), headers))
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["X-Tenant-Id"] == "tenant-a"
        if state["fail"]:
            return state["fail"], {"detail": "owner denied" if state["fail"] == 403 else "owner unavailable"}
        if method == "GET" and urlsplit(url).path == "/api/research/tickets":
            query = parse_qs(urlsplit(url).query)
            assert query["meeting_linked"] == ["true"]
            return 200, {"data": [copy.deepcopy(state["ticket"])],
                         "page_info": {"next_page_token": "opaque-owner-next"}}
        if method == "GET":
            return 200, copy.deepcopy(state["ticket"])
        assert method == "PATCH"
        patch = payload["patch"]
        assert payload["actor_id"] == "op-inbox"
        if patch.get("actor_id") or patch.get("tenant_id"):
            return 403, {"detail": "forged actor or tenant"}
        if patch.get("idempotency_key") in state["replays"]:
            return 200, copy.deepcopy(state["ticket"])
        if patch.get("expected_revision") != state["ticket"]["revision"]:
            return 409, {"detail": "Stale expected_revision"}
        if patch.get("action") != "provide_input" or not patch.get("evidence_refs"):
            return 400, {"detail": "Explicit evidence command required"}
        state["replays"][patch["idempotency_key"]] = True
        state["ticket"].update(revision=4, action_state="in_progress", required_actor="persona-alpha",
            human_input={"evidence_refs": patch["evidence_refs"], "provided_by": payload["actor_id"],
                         "provided_at": "2026-11-23T02:00:00Z"})
        state["ticket"]["lifecycle_history"].append({"action": "provide_input", "revision": 4})
        return 200, copy.deepcopy(state["ticket"])

    owner = ResearchServiceClient(base_url="http://research.test", http_request=transport)
    port = DefaultResearchKnowledgeSourcePort(research_write_owner=owner)
    app = create_research_test_app(port)
    app.add_middleware(OwnerReadContextMiddleware)
    return TestClient(app), state, owner


def test_original_ticket_response_preserves_revision_replay_and_durable_readback(research_stack):
    client, state, _ = research_stack
    path = "/api/v1/research/tickets/" + state["ticket"]["ticket_id"]
    command = {"action": "provide_input", "expected_revision": 3,
               "evidence_refs": [{"kind": "research_artifact", "artifact_id": "artifact-licensed"}]}
    headers = {**_auth_headers(), "Idempotency-Key": "same-input"}
    response = client.patch(path, headers=headers, json=command)
    assert response.status_code == 200, response.text
    assert response.json()["required_actor"] == "persona-alpha"
    assert response.json()["human_input"]["provided_by"] == "op-inbox"
    assert response.json()["revision"] == 4
    assert client.patch(path, headers=headers, json=command).json() == response.json()
    assert client.get(path, headers=_auth_headers()).json()["human_input"] == response.json()["human_input"]
    assert len(state["ticket"]["lifecycle_history"]) == 1
    assert all(not p or "human_input" not in p.get("patch", {}) for _, _, p, _ in state["calls"])
    assert client.patch(path, headers={**headers, "Idempotency-Key": "stale-new-key"}, json=command).status_code == 409


def test_viewer_wrong_tenant_actor_forgery_and_owner_denial(research_stack):
    client, state, _ = research_stack
    path = "/api/v1/research/tickets/" + state["ticket"]["ticket_id"]
    viewer = encode_jwt_hs256({"sub": "viewer", "roles": ["viewer"], "tenant_id": "tenant-a", "exp": 4102444800}, secret=INBOX_SECRET)
    assert client.patch(path, headers={"Authorization": "Bearer " + viewer}, json={"action": "provide_input"}).status_code == 403
    assert state["calls"] == []
    assert client.get(path, headers={**_auth_headers(), "X-Tenant-Id": "tenant-b"}).status_code == 403
    assert state["calls"] == []
    assert client.patch(path, headers=_auth_headers(), json={"action": "provide_input", "actor_id": "forged"}).status_code == 403
    state["fail"] = 403
    assert client.get(path, headers=_auth_headers()).status_code == 403
    state["fail"] = 503
    assert client.get(path, headers=_auth_headers()).status_code == 503


def test_meeting_ticket_page_keeps_owner_filters_and_cursor(research_stack):
    client, state, _ = research_stack
    response = client.get("/api/v1/research/tickets?meeting_linked=true&action_state=waiting_for_input&meeting_type=MT-1&subject_ref=pool-alpha&page_size=2&page_token=opaque-scope", headers=_auth_headers())
    assert response.status_code == 200, response.text
    assert response.json()["page_info"]["next_page_token"] == "opaque-owner-next"
    assert response.json()["data"][0]["revision"] == 3
    assert len(state["calls"]) == 1
    query = parse_qs(urlsplit(state["calls"][0][1]).query)
    assert query["page_token"] == ["opaque-scope"]
    assert query["subject_ref"] == ["pool-alpha"]
    assert query["action_state"] == ["waiting_for_input"]


def test_meeting_minutes_preserve_frozen_and_live_actions_without_backfill(meetings_stack):
    client, calls, state = meetings_stack
    detail = state["details"]["mtg-001"]
    detail["tenant_id"] = "tenant-a"
    action = {**ticket(), "live_owner_status": {"status": "ok", "action_state": "submitted"},
              "recorded_disposition": {"decision": "returned", "reason": "Missing observation dates"},
              "citations": ["cf-shared-1", "cf-private-leak-1"],
              "evidence_refs": [{"kind": "research_artifact", "artifact_id": "artifact-real"},
                                {"scope": "persona_private", "artifact_id": "private-ref"},
                                {"tenant_id": "tenant-b", "artifact_id": "foreign-ref"}]}
    detail["actions"] = [action]
    detail["prior_actions"] = [copy.deepcopy(action)]
    detail["action_read"] = {"status": "ok", "source": "research"}
    detail["participant_capabilities"] = [{"persona_id": "persona-alpha", "can_execute": True,
                                                  "private_memory": "never"}]
    detail["synthesis"]["actions"] = [action]
    response = client.get("/bff/management/meetings/mtg-001", headers=make_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["actions"][0]["live_owner_status"]["action_state"] == "submitted"
    assert body["prior_actions"][0]["recorded_disposition"]["decision"] == "returned"
    assert body["actions"][0]["revision"] == 3
    assert "private-ref" not in response.text and "foreign-ref" not in response.text and "never" not in response.text
    assert "cf-private-leak-1" not in response.text
    assert body["actions"][0]["citations"] == ["cf-shared-1"]
    assert body["synthesis"]["actions"] == body["actions"]
    legacy = client.get("/bff/management/meetings/mtg-002", headers=make_headers()).json()
    assert legacy["actions"] == [] and legacy["prior_actions"] == []
    assert legacy["action_read"]["reason"] == "meeting_actions_not_recorded"


@pytest.mark.parametrize("viewer,roles,can_view_trader", [
    ("op-alice", ["operator"], True),
    ("trader-alice", ["viewer"], True),
    ("trader-bob", ["viewer"], False),
])
@pytest.mark.parametrize("top_level", ["present", "absent", "null_actions", "null_prior_actions", "null_both"])
def test_action_citations_withhold_whole_references_for_calling_viewer(
    meetings_stack, viewer, roles, can_view_trader, top_level,
):
    client, _, state = meetings_stack
    detail = state["details"]["mtg-001"]
    detail["tenant_id"] = "tenant-a"
    public = {"item_id": "cf-shared-1", "excerpt": "PUBLIC_ACTION_CITATION"}
    trader = {"ref_id": " cf-agora-lock-1\t", "excerpt": "TRADER_ACTION_CITATION"}
    citations = [public, trader, "cf-shared-1", " cf-agora-lock-1\n",
                 {"item_id": "cf-private-leak-1", "excerpt": "WITHHELD_ACTION_CITATION_CONTENT"},
                 {"item_id": "cf-shared-1", "ref_id": "cf-private-leak-1",
                  "excerpt": "WITHHELD_ACTION_CITATION_CONTENT"}]
    for key in ("item_id", "id", "source_id", "ref_id", "entry_id", "record_id"):
        citations.append({key: " cf-private-leak-1\t",
                          "excerpt": "WITHHELD_ACTION_CITATION_CONTENT",
                          "metadata": {"content": "WITHHELD_ACTION_CITATION_CONTENT"}})
    citations.extend(["cf-private-leak-1", " cf-private-leak-1\t\n"])
    action = {**ticket(), "citations": copy.deepcopy(citations),
              "recorded_disposition": {"citations": copy.deepcopy(citations)}}
    for key in ("actions", "prior_actions"):
        detail["synthesis"][key] = [copy.deepcopy(action)]
        if top_level == "present":
            detail[key] = [copy.deepcopy(action)]
        elif top_level in ("null_both", "null_" + key):
            detail[key] = None
    detail["rounds"][0]["positions"][0]["citations"] = copy.deepcopy(citations)
    original = copy.deepcopy(detail)

    response = client.get("/bff/management/meetings/mtg-001",
                          headers=make_headers(sub=viewer, roles=roles))
    assert response.status_code == 200, response.text
    expected = [public, trader, "cf-shared-1", " cf-agora-lock-1\n"] if can_view_trader else [public, "cf-shared-1"]
    body = response.json()
    for container in (body, body["synthesis"]):
        for key in ("actions", "prior_actions"):
            assert container[key][0]["citations"] == expected
            assert container[key][0]["recorded_disposition"]["citations"] == expected
            assert container[key][0]["ticket_id"] == action["ticket_id"]
    assert body["rounds"][0]["positions"][0]["citations"] == expected
    assert "WITHHELD_ACTION_CITATION_CONTENT" not in response.text
    assert "cf-private-leak-1" not in response.text
    if not can_view_trader:
        assert "TRADER_ACTION_CITATION" not in response.text
        assert "cf-agora-lock-1" not in response.text
    assert detail == original  # Visibility shaping must never mutate owner history.


@pytest.mark.parametrize("viewer,roles,can_view_trader", [
    ("op-alice", ["operator"], True),
    ("trader-alice", ["viewer"], True),
    ("trader-bob", ["viewer"], False),
])
@pytest.mark.parametrize("top_level", ["absent", "null", "empty"])
@pytest.mark.parametrize("key,is_list", [
    ("actions", True), ("prior_actions", True), ("participant_capabilities", True),
    ("prior_action_reviews", True), ("series_subject_ref", False),
    ("action_read", False), ("action_page_info", False),
])
def test_synthesis_action_fields_preserve_viewer_boundary(
    meetings_stack, viewer, roles, can_view_trader, top_level, key, is_list,
):
    client, _, state = meetings_stack
    detail = state["details"]["mtg-001"]
    detail["tenant_id"] = "tenant-a"
    public = {"tenant_id": "tenant-a", "excerpt": "PUBLIC_ACTION_RECORD"}
    trader = {"trader_id": "trader-alice", "excerpt": "SCOPED_TRADER_RECORD"}
    payload = {"records": [public, trader,
        {"tenant_id": "tenant-b", "excerpt": "FOREIGN_ACTION_RECORD"},
        {"memory_layer": "persona_private", "excerpt": "PRIVATE_ACTION_RECORD"}],
        "citations": ["cf-shared-1", " cf-private-leak-1 ",
            {"item_id": "cf-private-leak-1", "excerpt": "PRIVATE_CITATION_RECORD"}]}
    detail["synthesis"][key] = [payload] if is_list else payload
    empty = [] if is_list else {}
    if top_level != "absent":
        detail[key] = None if top_level == "null" else empty
    original = copy.deepcopy(detail)
    response = client.get("/bff/management/meetings/mtg-001",
                          headers=make_headers(sub=viewer, roles=roles))
    assert response.status_code == 200, response.text
    body = response.json()
    expected = {"records": [public, trader] if can_view_trader else [public],
                "citations": ["cf-shared-1"]}
    expected = empty if top_level == "empty" else [expected] if is_list else expected
    assert body[key] == body["synthesis"][key] == expected
    for private in ("FOREIGN_ACTION_RECORD", "PRIVATE_ACTION_RECORD", "PRIVATE_CITATION_RECORD", "cf-private-leak-1"):
        assert private not in response.text
    if not can_view_trader:
        assert "SCOPED_TRADER_RECORD" not in response.text
    assert detail == original


def test_inbox_one_real_ticket_entry_and_partial_permission_failure(inbox_meetings_stack, monkeypatch):
    client, _, state = inbox_meetings_stack
    client.app.add_middleware(OwnerReadContextMiddleware)
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research.test")
    calls = []
    row = ticket()
    fail = [None]

    def transport(method, url, payload=None, headers=None):
        calls.append((method, url, headers))
        if fail[0]:
            return fail[0], {"detail": "owner denied"}
        # Even a duplicate summary must not duplicate a single original ticket.
        return 200, {"data": [copy.deepcopy(row), copy.deepcopy(row)], "page_info": {"next_page_token": "more-human-work"}}

    owner = ResearchServiceClient(base_url="http://research.test", http_request=transport)
    monkeypatch.setattr("services.control_plane.bff.research.client.ResearchServiceClient", lambda: owner)
    for _ in range(2):
        response = client.get("/bff/management/human-inbox", headers=_auth_headers())
        assert response.status_code == 200, response.text
        body = response.json()
        work = [i for i in body["data"]["items"] if i["kind"] == "meeting_action"]
        assert len(work) == 1
        assert work[0]["source_id"] == row["ticket_id"]
        assert work[0]["resumes_with"] == "persona-alpha"
        assert work[0]["summary"] == "Need licensed observations"
        assert work[0]["overdue"] is True
        assert "action=" in work[0]["href"]
        assert body["meta"]["surfaces"]["meetings"]["surfaces"]["research"]["page_info"]["next_page_token"] == "more-human-work"
    assert len(calls) == 2  # Exactly one Research list per reload; no per-ticket reads.
    assert calls[0][2]["Authorization"] == _auth_headers()["Authorization"]
    assert parse_qs(urlsplit(calls[0][1]).query)["action_state"] == ["waiting_for_input"]
    fail[0] = 403
    body = client.get("/bff/management/human-inbox", headers=_auth_headers()).json()
    assert body["meta"]["partial"] is True
    assert body["meta"]["surfaces"]["meetings"]["surfaces"]["research"]["status"] == "denied"
    assert any(i["kind"] == "meeting_review" for i in body["data"]["items"])
    fail[0] = None
    state["offline"] = True
    body = client.get("/bff/management/human-inbox", headers=_auth_headers()).json()
    assert any(i["kind"] == "meeting_action" for i in body["data"]["items"])
    assert body["meta"]["partial"] is True


@pytest.mark.parametrize("kind", ["trader", "agora_user"])
@pytest.mark.parametrize("viewer,roles,allowed", [
    ("op-alice", ["operator"], True),
    ("trader-alice", ["viewer"], True),
    ("trader-bob", ["viewer"], False),
])
@pytest.mark.parametrize("top_level", ["present", "absent", "null"])
def test_typed_action_subject_refs_follow_existing_visibility(
    meetings_stack, kind, viewer, roles, allowed, top_level,
):
    client, _, state = meetings_stack
    detail = state["details"]["mtg-001"]
    ref = {"kind": kind, "id": "trader-alice"}
    detail["subject_ref"] = copy.deepcopy(ref)

    def action_fields(subject):
        record = {"subject_ref": subject, "evidence_refs": [subject],
                  "recorded_disposition": {"subject_ref": subject}}
        return {"series_subject_ref": subject, "actions": [record],
                "prior_actions": [record], "participant_capabilities": [record],
                "prior_action_reviews": [record], "action_read": record,
                "action_page_info": record}

    for key, value in action_fields(ref).items():
        detail["synthesis"][key] = copy.deepcopy(value)
        if top_level == "present":
            detail[key] = copy.deepcopy(value)
        elif top_level == "null":
            detail[key] = None
    original = copy.deepcopy(detail)
    response = client.get("/bff/management/meetings/mtg-001",
                          headers=make_headers(sub=viewer, roles=roles))
    assert response.status_code == 200, response.text
    body = response.json()
    expected_ref = ref if allowed else {"kind": kind, "redacted": True}
    assert body["subject_ref"] == expected_ref
    for container in (body, body["synthesis"]):
        for key, expected in action_fields(expected_ref).items():
            assert container[key] == expected
    assert detail == original


@pytest.mark.parametrize("next_page_token", [None, "more-human-work"])
@pytest.mark.parametrize("store_missing", [False, True])
def test_research_inbox_survives_missing_consultation_and_read_store(
    inbox_meetings_stack, monkeypatch, store_missing, next_page_token,
):
    client, service, _ = inbox_meetings_stack
    client.app.add_middleware(OwnerReadContextMiddleware)
    for name in ("PANTHEON_CONSULTATION_API_URL", "PANTHEON_CONSULTATION_SERVICE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research.test")
    if store_missing:
        monkeypatch.setattr(service, "_get_read_store", lambda: None)
    calls = []

    def transport(method, url, payload=None, headers=None):
        calls.append((method, url))
        assert headers["Authorization"] == _auth_headers()["Authorization"]
        assert headers["X-Tenant-Id"] == "tenant-a"
        assert method == "GET" and urlsplit(url).path == "/api/research/tickets"
        assert parse_qs(urlsplit(url).query)["action_state"] == ["waiting_for_input"]
        assert parse_qs(urlsplit(url).query)["meeting_linked"] == ["true"]
        return 200, {"data": [ticket()], "page_info": {"next_page_token": next_page_token}}

    owner = ResearchServiceClient(base_url="http://research.test", http_request=transport)
    monkeypatch.setattr("services.control_plane.bff.research.client.ResearchServiceClient", lambda: owner)
    for reload_count in (1, 2):
        response = client.get("/bff/management/human-inbox", headers=_auth_headers())
        assert response.status_code == 200, response.text
        body = response.json()
        work = [item for item in body["data"]["items"] if item.get("kind") == "meeting_action"]
        assert len(work) == 1
        assert work[0]["source_id"] == ticket()["ticket_id"]
        assert len(calls) == reload_count
        assert body["meta"]["partial"] is True
        assert body["meta"]["surfaces"]["meetings"]["status"] == "degraded"
        assert body["meta"]["surfaces"]["human_inbox"]["status"] == "degraded"
        sources = body["meta"]["surfaces"]["meetings"]["surfaces"]
        assert sources["research"]["status"] == ("degraded" if next_page_token else "ok")
        assert sources["research"]["page_info"]["next_page_token"] == next_page_token
        assert sources["consultation"]["status"] == "unavailable"


def test_owner_rejection_does_not_retry_an_unfiltered_ticket_list(research_stack):
    client, state, _ = research_stack
    state["fail"] = 403
    response = client.get("/api/v1/research/tickets?meeting_linked=true&action_state=waiting_for_input", headers=_auth_headers())
    assert response.status_code == 403
    assert len(state["calls"]) == 1
    assert "meeting_linked=true" in state["calls"][0][1]
    state["calls"].clear()
    assert client.get("/api/v1/research/tickets?action_state=waiting_for_input", headers=_auth_headers()).status_code == 400
    assert state["calls"] == []


def test_original_ticket_rejects_plain_close_and_conflicting_idempotency(research_stack):
    client, state, _ = research_stack
    path = "/api/v1/research/tickets/" + state["ticket"]["ticket_id"]
    assert client.patch(path, headers=_auth_headers(), json={"status": "closed", "expected_revision": 3}).status_code == 400
    assert state["ticket"]["action_state"] == "waiting_for_input"
    state["calls"].clear()
    assert client.patch(path, headers={**_auth_headers(), "Idempotency-Key": "header-key"},
                        json={"action": "provide_input", "idempotency_key": "other-key"}).status_code == 400
    assert state["calls"] == []


def test_ticket_reply_page_bound_is_enforced(research_stack):
    client, state, owner = research_stack
    def oversized_page(method, url, payload=None, headers=None):
        return 200, {"data": [ticket()] * 3, "page_info": {"next_page_token": None}}
    owner.http_request = oversized_page
    response = client.get("/api/v1/research/tickets?meeting_linked=true&page_size=2", headers=_auth_headers())
    assert response.status_code == 503
    assert "exceeded requested page size" in response.text


def test_slow_consultation_retains_research_items_and_uses_contributor_budget(inbox_meetings_stack, monkeypatch):
    import urllib.request
    from urllib.error import URLError
    client, _, _ = inbox_meetings_stack
    client.app.add_middleware(OwnerReadContextMiddleware)
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research.test")
    monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "1.0")
    timeouts = []
    def slow_owner(request, timeout=0):
        timeouts.append(timeout)
        raise URLError("timed out")
    monkeypatch.setattr(urllib.request, "urlopen", slow_owner)
    owner = ResearchServiceClient(base_url="http://research.test", http_request=lambda *args:
        (200, {"data": [ticket()], "page_info": {"next_page_token": None}}))
    monkeypatch.setattr("services.control_plane.bff.research.client.ResearchServiceClient", lambda: owner)
    body = client.get("/bff/management/human-inbox", headers=_auth_headers()).json()
    assert any(i["kind"] == "meeting_action" for i in body["data"]["items"])
    assert timeouts and all(0 < value <= 0.4 for value in timeouts)
    # Research remains a contributor even if consultation has no configured URL.
    monkeypatch.delenv("PANTHEON_CONSULTATION_API_URL")
    body = client.get("/bff/management/human-inbox", headers=_auth_headers()).json()
    assert any(i["kind"] == "meeting_action" for i in body["data"]["items"])


def test_ticket_http_reply_limit_fails_closed(research_stack, monkeypatch):
    import io
    import urllib.request
    from services.control_plane.bff.research.client import default_http_request
    client, _, owner = research_stack
    def oversized_reply(*args, **kwargs):
        result = io.BytesIO(b" " * (4 * 1024 * 1024 + 100))
        result.status = 200
        return result
    owner.http_request = default_http_request
    monkeypatch.setattr(urllib.request, "urlopen", oversized_reply)
    response = client.get("/api/v1/research/tickets?meeting_linked=true&page_size=2", headers=_auth_headers())
    assert response.status_code == 502
    assert "exceeds 4 MiB" in response.text


def test_meeting_owner_denied_is_distinct_from_unavailable_and_historical(meetings_stack):
    client, _, state = meetings_stack
    state["status_override"]["/api/consult/meetings/mtg-001"] = 403
    response = client.get("/bff/management/meetings/mtg-001", headers=make_headers())
    assert response.status_code == 403
    assert "FORBIDDEN" in response.text
    state["status_override"].clear()
    state["offline"] = True
    response = client.get("/bff/management/meetings/mtg-001", headers=make_headers())
    assert response.status_code == 200 and response.json()["status"] == "unavailable"
