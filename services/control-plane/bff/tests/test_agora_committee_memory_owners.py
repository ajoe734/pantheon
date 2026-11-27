"""Local production-route/owner integration. Only owner HTTP transport is faked.

The production mounting hook and security stack, Consultation client/API/store,
Memory API/stores and Governance authz API/policy all run. Seed data is test-only;
this suite proves no hosted deployment or PostgreSQL restart durability.
"""
from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import extract_identity, require_operator_role, require_read_role
from services.control_plane.bff.core.app_factory import build_bff_app, mount_agora_review_routers
from services.control_plane.bff.meetings.router import create_meetings_router
from services.control_plane.bff.models import utc_now
from services.consultation import models as cm
from services.consultation.store import ConsultationStore
from services.runtime_auth_inbound import encode_jwt_hs256

SECRET = "agora-owner-integration-fixture-secret-only"
PANEL = "/bff/agora/committee/sessions"
MEMORY = "/bff/agora/memory"


def headers(tenant="tenant-a", roles=None):
    token = encode_jwt_hs256({"sub": "alice", "roles": roles or ["operator"],
                             "tenant_id": tenant, "exp": 4102444800}, secret=SECRET)
    return {"Authorization": "Bearer " + token, "Idempotency-Key": "quarantine-test"}


def _body(response):
    """Error body without the per-request correlation id (non-enumerating comparison)."""
    return {k: v for k, v in response.json().items() if k != "meta"}


@pytest.fixture
def stack(tmp_path, monkeypatch):
    for prefix in ("PANTHEON_BFF", "PANTHEON_MEMORY", "PANTHEON_GOVERNANCE"):
        monkeypatch.setenv(prefix + "_AUTH_MODE", "strict")
        monkeypatch.setenv(prefix + "_JWT_SECRET", SECRET)
        monkeypatch.setenv(prefix + "_JWT_ISSUER", "")
        monkeypatch.setenv(prefix + "_JWT_AUDIENCE", "")
    monkeypatch.delenv("PANTHEON_BFF_AUTH_STUB", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_TENANT_ID", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_DEFAULT_TENANT_ID", raising=False)
    monkeypatch.delenv("PANTHEON_MEMORY_AUTHZ_MODE", raising=False)
    for name in ("PANTHEON_MEMORY_DATA_DIR", "GOVERNANCE_DATA_DIR", "CONSULTATION_DATA_DIR", "BFF_DATA_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))
    for name, filename in (("PANTHEON_MEMORY_STORE", "institutional.json"),
                           ("PANTHEON_PERSONA_MEMORY_STORE", "persona.json"),
                           ("PANTHEON_TRADE_LESSON_CANDIDATE_STORE", "lessons.json")):
        monkeypatch.setenv(name, str(tmp_path / filename))
    for name in ("PANTHEON_MEMORY_STORE_BACKEND", "PANTHEON_PERSONA_MEMORY_STORE_BACKEND"):
        monkeypatch.setenv(name, "json")
    monkeypatch.setenv("CONSULTATION_AUTH_REQUIRED", "true")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", "test-only-existing-service-token")
    monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", "http://consultation.test")
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory.test")
    monkeypatch.setenv("PANTHEON_GOVERNANCE_AUTHZ_URL", "http://governance.test")
    monkeypatch.setenv("PANTHEON_GOVERNANCE_AUTH_TOKEN", headers()["Authorization"].removeprefix("Bearer "))

    from services.consultation import main as consultation
    from services.memory import main as memory
    from services.governance import main as governance

    store = ConsultationStore(str(tmp_path / "consultation"))
    monkeypatch.setattr(consultation, "store", store)
    for request_id, tenant, kind in (("panel-a", "tenant-a", "persona_panel"),
                                     ("panel-b", "tenant-b", "persona_panel"),
                                     ("ordinary", "tenant-a", None)):
        store.put_request(cm.ConsultRequest(
            request_id=request_id, tenant_id=tenant, request_type="persona_policy",
            requested_by=cm.ActorRef(actor_type="persona", actor_id="persona-a"),
            from_persona_id="persona-a", target_type="pool_admission", target_id="candidate-a",
            consultation_type=kind, trace_id="test-trace", evidence_refs=["artifact://test-review"],
        ))
        store.put_memo(cm.ConsultMemo(
            memo_id="memo-" + request_id, request_id=request_id, memo_type="committee_summary",
            author_type="persona", author_ref="persona-a", target_type="pool_admission",
            target_id="candidate-a", summary="Local test panel evidence", recommendation="approve",
            trace_id="test-trace", status="submitted",
        ))
        store.put_participant(cm.ConsultParticipant(
            participant_id="participant-" + request_id, request_id=request_id,
            participant_type="persona", participant_ref="persona-a", role="primary_reviewer",
        ))
        store.put_transcript(cm.ConsultTranscript(
            transcript_id="transcript-" + request_id, session_id=request_id, request_id=request_id,
            events=[cm.TranscriptEvent(event_id="event-" + request_id, session_id=request_id,
                sequence_no=1, event_type="persona_contribution", actor=cm.ActorRef(actor_type="persona", actor_id="persona-a"),
                content={"claim": "test evidence"})],
        ))

    clients = {"consultation.test": TestClient(consultation.app), "memory.test": TestClient(memory.app),
               "governance.test": TestClient(governance.app)}
    calls = []
    control = {"offline": set(), "response": {}}

    def transport(req, timeout=3):
        u = urlsplit(req.full_url)
        assert u.hostname in clients, "Unexpected downstream call: " + req.full_url
        calls.append((req.method, u.hostname, u.path, dict(req.header_items()), req.data))
        if u.hostname in control["offline"]:
            raise URLError("test-only owner outage")
        response = clients[u.hostname].request(req.method, u.path + ("?" + u.query if u.query else ""),
                                               headers=dict(req.header_items()), content=req.data)
        content = response.content
        status_code = response.status_code
        if (u.hostname, u.path) in control["response"]:
            content = json.dumps(control["response"][(u.hostname, u.path)]).encode()
            status_code = 200
        if status_code >= 400:
            raise HTTPError(req.full_url, status_code, "owner rejected", {}, io.BytesIO(content))
        result = io.BytesIO(content)
        result.status = status_code
        return result

    monkeypatch.setattr("urllib.request.urlopen", transport)
    memory_client = clients["memory.test"]
    common = {"source_event_type": "postmortem_published", "source_event_id": "test-postmortem",
              "written_at": "2026-11-12T00:00:00Z", "write_authority": "incident-svc", "reuse_count": 0}
    private = {**common, "memory_id": "pmem-" + str(uuid4()), "persona_id": "persona-a",
               "memory_type": "strategy_lesson", "content": {"summary": "test private memory", "tags": []},
               "relevance_scope": "persona_private"}
    public = {**common, "entry_id": "mem-" + str(uuid4()), "knowledge_type": "incident_lesson",
              "content": {"headline": "test headline", "body": "test institutional evidence", "tags": []},
              "scope": "system_wide", "contributing_persona_ids": ["persona-a"]}
    for endpoint, payload in (("persona-entries", private), ("entries", public)):
        response = memory_client.post("/api/memory/" + endpoint, headers=headers(), json=payload)
        assert response.status_code == 201, response.text
    candidate = {"lesson_candidate_id": str(uuid4()), "reflection_id": str(uuid4()),
                 "trade_episode_ids": [str(uuid4())], "persona_id": "persona-a", "scope": "persona",
                 "proposed_change": "Review test-only sizing", "confidence": 0.7, "review_state": "proposed",
                 "created_at": utc_now(), "updated_at": utc_now(), "expiry": "2099-01-01T00:00:00Z",
                 "reflection_version": "v1", "target_env": "paper"}
    response = memory_client.post("/api/memory/trade-lessons", headers=headers(), json=candidate)
    assert response.status_code == 201, response.text
    candidate = response.json()
    app = build_bff_app()
    mount_agora_review_routers(app, extract_identity=extract_identity, require_read_role=require_read_role,
                              require_write_role=require_operator_role, utc_now=utc_now)
    # Persona gate panels are served by the Management meetings center (decision 2026-11-26).
    app.include_router(create_meetings_router(extract_identity=extract_identity, require_read_role=require_read_role,
                                              require_operator_role=require_operator_role, utc_now=utc_now))
    calls.clear()
    yield TestClient(app), calls, control, candidate, clients, store


def test_all_restored_routes_reach_real_owners(stack):
    client, calls, _, candidate, owners, store = stack
    # Operator decision 2026-11-26: persona gate panels are never served by Agora.
    sessions = client.get(PANEL, headers=headers())
    assert sessions.status_code == 200, sessions.text
    assert sessions.json()["data"] == []
    assert sessions.json()["reason"] == "no_trader_meetings"
    missing = client.get(PANEL + "/no-such-session", headers=headers())
    assert missing.status_code == 404
    for path in (PANEL + "/panel-a", PANEL + "/panel-a/memos"):
        hidden = client.get(path, headers=headers())
        assert hidden.status_code == 404, hidden.text
        assert _body(hidden) == _body(missing)
    path = PANEL + "/panel-a/memos/memo-panel-a/publish"
    refused = client.post(path, headers=headers())
    assert refused.status_code == 404, refused.text
    assert _body(refused) == _body(missing)
    assert store.get_memo("memo-panel-a").status.value == "submitted"
    review = client.get(MEMORY + "?persona_id=persona-a", headers=headers())
    assert review.status_code == 200, review.text
    data = review.json()["data"]
    assert {h["type"] for h in data["entries"]} == {"persona", "institutional"}
    assert data["lesson_candidates"] == [candidate]
    path = MEMORY + "/" + candidate["lesson_candidate_id"] + "/actions/quarantine"
    body = {"reason": "test quarantine reason", "audit_receipt_id": "test-audit-evidence"}
    quarantined = client.post(path, headers=headers(), json=body)
    assert quarantined.status_code == 200, quarantined.text
    assert quarantined.json()["data"]["review_state"] == "quarantined"
    readback = owners["memory.test"].get("/api/memory/trade-lessons/" + candidate["lesson_candidate_id"], headers=headers())
    assert readback.json()["review_state"] == "quarantined"
    assert client.post(path, headers=headers(), json=body).status_code == 200
    assert any(host == "governance.test" for _, host, *_ in calls)
    for _, host, _, sent_headers, _ in calls:
        if host == "memory.test":
            assert sent_headers["Authorization"] == headers()["Authorization"]
        if host == "consultation.test":
            assert sent_headers["X-pantheon-tenant-id"] == "tenant-a"
            assert sent_headers["Authorization"] == "Bearer test-only-existing-service-token"


def test_tenant_and_panel_binding_precedes_publishing(stack):
    client, calls, _, candidate, _, store = stack
    assert client.get(PANEL + "/panel-b", headers=headers()).status_code == 404
    assert client.get(PANEL + "/ordinary", headers=headers()).status_code == 404
    assert client.post(PANEL + "/panel-a/memos/memo-panel-b/publish", headers=headers()).status_code == 404
    assert store.get_memo("memo-panel-b").status.value == "submitted"
    calls.clear()
    denied = client.get(PANEL, headers={**headers(), "X-Tenant-Id": "tenant-b"})
    assert denied.status_code == 403
    assert calls == []
    denied = client.post(MEMORY + "/" + candidate["lesson_candidate_id"] + "/actions/quarantine",
                         headers=headers("tenant-b"), json={"reason": "deny", "audit_receipt_id": "audit-test"})
    assert denied.status_code == 404
    assert not any(method == "POST" and host == "memory.test" for method, host, *_ in calls)


def test_role_and_payload_checks_fail_closed(stack):
    client, calls, _, candidate, _, _ = stack
    assert client.get(PANEL, headers=headers(roles=["viewer"])).status_code == 200
    calls.clear()
    assert client.post(PANEL + "/panel-a/memos/memo-panel-a/publish", headers=headers(roles=["viewer"])).status_code == 403
    path = MEMORY + "/" + candidate["lesson_candidate_id"] + "/actions/quarantine"
    body = {"reason": "review", "audit_receipt_id": "audit-test"}
    assert client.post(path, headers=headers(roles=["viewer"]), json=body).status_code == 403
    assert calls == []
    assert client.get(MEMORY + "?persona_id=persona-a", headers=headers(roles=["viewer"])).status_code == 403
    # No caller-defined governance roles, actor, environment or mutation type.
    for extra in ({"actor_roles": ["admin"]}, {"operator_id": "someone-else"}, {"target_env": "live"}):
        assert client.post(path, headers=headers(), json={**body, **extra}).status_code == 422
    assert client.post(path, headers=headers(), json={**body, "kind": "persona"}).status_code == 503
    assert client.post(path, headers={"Authorization": headers()["Authorization"]}, json=body).status_code == 400
    assert client.get(MEMORY, headers=headers()).status_code == 422
    assert client.get(PANEL).status_code == 401


def test_institutional_scope_does_not_fetch_private_candidates(stack):
    client, calls, _, _, _, _ = stack
    result = client.get(MEMORY + "?scope=institutional&persona_id=persona-a", headers=headers())
    assert result.status_code == 200, result.text
    assert {h["type"] for h in result.json()["data"]["entries"]} == {"institutional"}
    assert result.json()["data"]["lesson_candidates"] == []
    assert not any(path == "/api/memory/trade-lessons" for _, _, path, *_ in calls)


@pytest.mark.parametrize("host,path", [("consultation.test", PANEL), ("memory.test", MEMORY + "?persona_id=persona-a"),
                                       ("governance.test", MEMORY + "?persona_id=persona-a")])
def test_owner_outage_is_unavailable_without_fallback(stack, host, path):
    client, _, control, *_ = stack
    control["offline"].add(host)
    response = client.get(path, headers=headers())
    assert response.status_code in {403, 503}, response.text
    detail = response.json()
    assert detail["status"] == "unavailable" and detail["reason"]
    assert detail["fallback_used"] is False and "data" not in detail


@pytest.mark.parametrize("body", [None, {}, {"hits": None}, {"hits": [{"type": "persona", "entry": {"persona_id": "other"}}], "authz": {"allowed": True}}])
def test_malformed_memory_response_is_unavailable(stack, body):
    client, _, control, *_ = stack
    control["response"][("memory.test", "/api/memory/retrieve")] = body
    response = client.get(MEMORY + "?persona_id=persona-a", headers=headers())
    assert response.status_code in {403, 503}, response.text
    assert response.json()["status"] == "unavailable"


def test_empty_owner_results_record_reason(stack):
    client, _, control, *_ = stack
    control["response"][("consultation.test", "/api/consult/meetings")] = {"items": [], "next_cursor": None}
    assert client.get(PANEL, headers=headers()).json()["reason"] == "no_trader_meetings"
    control["response"][("memory.test", "/api/memory/retrieve")] = {"hits": [], "authz": {"allowed": True}}
    control["response"][("memory.test", "/api/memory/trade-lessons")] = {"candidates": []}
    assert client.get(MEMORY + "?persona_id=persona-a", headers=headers()).json()["reason"] == "no_memory_records"


def test_cookie_read_forwards_the_same_verified_memory_credential(stack):
    client, calls, *_ = stack
    client.cookies.set("pantheon_session", headers()["Authorization"].removeprefix("Bearer "))
    response = client.get(MEMORY + "?persona_id=persona-a")
    assert response.status_code == 200, response.text
    sent = [h for _, host, _, h, _ in calls if host == "memory.test"]
    assert sent and all(h["Authorization"] == headers()["Authorization"] for h in sent)


@pytest.mark.parametrize("kind", ["persona", "institutional"])
def test_unscoped_or_foreign_entries_never_leave_the_bff(stack, kind):
    client, _, control, *_ = stack
    entry = {"memory_id": "private-foreign", "entry_id": "institutional-foreign",
             "persona_id": "persona-a", "tenant_id": "tenant-b", "content": {"summary": "must not leak"}}
    control["response"][("memory.test", "/api/memory/retrieve")] = {
        "hits": [{"type": kind, "entry": entry}], "authz": {"allowed": True}}
    response = client.get(MEMORY + "?persona_id=persona-a", headers=headers())
    assert response.status_code == 503
    assert response.json()["reason"] == "memory_entry_tenant_or_identity_mismatch"
    assert "must not leak" not in response.text


def test_unconfigured_owners_and_credentials_never_fall_back(stack, monkeypatch):
    client, calls, *_ = stack
    for name in ("PANTHEON_CONSULTATION_API_URL", "PANTHEON_CONSULTATION_SERVICE_URL"):
        monkeypatch.delenv(name, raising=False)
    assert client.get(PANEL, headers=headers()).status_code == 503
    monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", "http://consultation.test")
    for name in ("CONSULTATION_SERVICE_TOKEN", "CONSULTATION_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert client.get(PANEL, headers=headers()).json()["reason"] == "consultation_service_credential_unconfigured"
    for name in ("PANTHEON_MEMORY_API_URL", "PANTHEON_MEMORY_SERVICE_URL", "MEMORY_URL"):
        monkeypatch.delenv(name, raising=False)
    assert client.get(MEMORY + "?persona_id=persona-a", headers=headers()).json()["reason"] == "memory_owner_unconfigured"
    assert calls == []


def test_committee_sessions_list_only_the_callers_meetings_newest_first(stack):
    client, _, control, *_ = stack
    control["response"][("consultation.test", "/api/consult/meetings")] = {
        "items": [
            {
                "meeting_id": "mtg-a",
                "meeting_type": "MT-3",
                "status": "closed",
                "outcome": "completed",
                "chair_persona_id": "persona-a",
                "convened_by": {"kind": "agora_user", "id": "alice"},
                "participants": [
                    {"persona_id": "persona-a", "role": "chair", "origin": "institutional"},
                    {"persona_id": "agora-servant-alice", "role": "servant", "origin": "private_servant", "agora_user_id": "alice"},
                ],
                "round": 2,
                "created_at": "2026-11-16T10:00:00Z",
                "closed_at": "2026-11-16T10:05:00Z",
            },
            {
                "meeting_id": "mtg-bob-private",
                "meeting_type": "MT-1",
                "status": "closed",
                "outcome": "completed",
                "chair_persona_id": "persona-a",
                "convened_by": {"kind": "agora_user", "id": "bob"},
                "participants": [
                    {"persona_id": "agora-servant-bob", "role": "servant", "origin": "private_servant", "agora_user_id": "bob"},
                ],
                "round": 1,
                "created_at": "2026-11-16T11:00:00Z",
                "closed_at": "2026-11-16T11:05:00Z",
            },
            {
                "meeting_id": "mtg-institutional",
                "meeting_type": "MT-1",
                "status": "closed",
                "outcome": "completed",
                "chair_persona_id": "persona-a",
                "convened_by": {"kind": "persona", "id": "persona-a"},
                "participants": [{"persona_id": "persona-a", "role": "chair", "origin": "institutional"}],
                "round": 1,
                "created_at": "2026-11-16T12:00:00Z",
                "closed_at": "2026-11-16T12:05:00Z",
            },
            {
                "meeting_id": "mtg-mt4-alice",
                "meeting_type": "MT-4",
                "status": "convened",
                "chair_persona_id": "persona-a",
                "convened_by": {"kind": "operator", "id": "ops"},
                "subject_ref": "trader:alice",
                "participants": [{"persona_id": "persona-a", "role": "chair", "origin": "institutional"}],
                "round": 0,
                "created_at": "2026-11-16T13:00:00Z",
                "closed_at": None,
            },
        ],
        "next_cursor": None,
    }
    meeting_detail = {
        "meeting_id": "mtg-a",
        "meeting_type": "MT-3",
        "status": "closed",
        "outcome": "completed",
        "chair_persona_id": "persona-a",
        "convened_by": {"kind": "agora_user", "id": "alice"},
        "created_at": "2026-11-16T10:00:00Z",
        "closed_at": "2026-11-16T10:05:00Z",
        "participants": [
            {"persona_id": "persona-a", "role": "chair", "origin": "institutional"},
            {"persona_id": "agora-servant-alice", "role": "servant", "origin": "private_servant", "agora_user_id": "alice"},
        ],
        "agenda": "Evaluate volatility breakout hypothesis",
        "case_file_items": [
            {"item_id": "cf-1", "kind": "hypothesis_card", "excerpt": "Shared fact", "relevance_scope": "committee"},
            {"item_id": "cf-priv", "kind": "private_note", "excerpt": "Must not leak", "relevance_scope": "persona_private"},
        ],
        "pre_read": [
            {"persona_id": "persona-a", "interpretation": "Committee-visible pre-read", "scope": "committee"},
            {"persona_id": "agora-servant-alice", "interpretation": "Private servant note", "memory_scope": "private_user"},
        ],
        "rounds": [
            {
                "round": 1,
                "positions": [
                    {
                        "label": "trading_momentum",
                        "persona_id": "persona-a",
                        "role": "chair",
                        "style": "momentum",
                        "stance": "support",
                        "confidence": 0.82,
                        "citations": ["cf-1"],
                        "changed": False,
                    },
                    {
                        "label": "servant_operator_guided",
                        "persona_id": "agora-servant-alice",
                        "role": "servant",
                        "style": "operator-guided",
                        "stance": "conditional",
                        "confidence": 0.74,
                        "citations": ["cf-1"],
                        "changed": False,
                    },
                ],
            },
            {
                "round": 2,
                "positions": [
                    {
                        "label": "trading_momentum",
                        "persona_id": "persona-a",
                        "role": "chair",
                        "style": "momentum",
                        "stance": "support",
                        "confidence": 0.85,
                        "citations": ["cf-1"],
                        "changed": False,
                    },
                    {
                        "label": "servant_operator_guided",
                        "persona_id": "agora-servant-alice",
                        "role": "servant",
                        "style": "operator-guided",
                        "stance": "support",
                        "confidence": 0.80,
                        "citations": ["cf-1"],
                        "changed": True,
                    },
                ],
            },
        ],
        "synthesis": {
            "proposals": [
                {
                    "proposal_id": "prop-unsponsored-a",
                    "meeting_id": "mtg-a",
                    "kind": "agora_candidate_decision",
                    "sponsor_persona_id": None,
                    "citations": ["cf-1"],
                }
            ],
            "tally": {"support": 1.65, "oppose": 0.0},
            "dissent_memo_id": None,
            "outcome": "completed",
        },
        "writebacks": [{"persona_id": "persona-a", "entry_id": "wb-1"}],
        "usage": {"calls": 5},
    }
    control["response"][("consultation.test", "/api/consult/meetings/mtg-a")] = meeting_detail
    control["response"][("consultation.test", "/api/consult/meetings/mtg-bob-private")] = {
        **meeting_detail,
        "meeting_id": "mtg-bob-private",
        "convened_by": {"kind": "agora_user", "id": "bob"},
        "participants": [
            {"persona_id": "agora-servant-bob", "role": "servant", "origin": "private_servant", "agora_user_id": "bob"},
        ],
    }

    control["response"][("consultation.test", "/api/consult/meetings/mtg-institutional")] = {
        **meeting_detail,
        "meeting_id": "mtg-institutional",
        "meeting_type": "MT-1",
        "convened_by": {"kind": "persona", "id": "persona-a"},
        "participants": [{"persona_id": "persona-a", "role": "chair", "origin": "institutional"}],
    }
    control["response"][("consultation.test", "/api/consult/meetings/mtg-mt4-alice")] = {
        **meeting_detail,
        "meeting_id": "mtg-mt4-alice",
        "meeting_type": "MT-4",
        "status": "convened",
        "convened_by": {"kind": "operator", "id": "ops"},
        "subject_ref": "trader:alice",
        "created_at": "2026-11-16T13:00:00Z",
        "closed_at": None,
    }

    # The tenant holds a gate panel (panel-a), an institutional meeting, another
    # trader's meeting, the caller's own meeting and an MT-4 review of the caller.
    sessions = client.get(PANEL, headers=headers())
    assert sessions.status_code == 200, sessions.text
    items = sessions.json()["data"]
    assert [item["session_id"] for item in items] == ["mtg-mt4-alice", "mtg-a"]
    by_id = {item["session_id"]: item for item in items}
    assert by_id["mtg-a"]["session_class"] == "deliberative_meeting"
    assert by_id["mtg-a"]["created_at"] == "2026-11-16T10:00:00Z"
    assert by_id["mtg-a"]["closed_at"] == "2026-11-16T10:05:00Z"
    assert [r["round"] for r in by_id["mtg-a"]["rounds"]] == [1, 2]
    assert "Must not leak" not in sessions.text
    assert "Private servant note" not in sessions.text
    assert by_id["mtg-a"]["private_items_withheld"] == 2
    assert by_id["mtg-a"]["proposals"][0]["approval_path"] == "agora_candidate_decision"
    servant = next(p for p in by_id["mtg-a"]["participants"] if p["persona_id"] == "agora-servant-alice")
    assert servant["display_name"] == "Your servant"
    assert servant["is_caller_servant"] is True
    assert by_id["mtg-a"]["chair_display_name"]
    assert all(p.get("display_name") for p in by_id["mtg-a"]["participants"])

    for include_panels in ("false", "true"):
        listed = client.get("/bff/agora/committee/meetings?include_panels=" + include_panels, headers=headers())
        assert listed.status_code == 200, listed.text
        assert [m["meeting_id"] for m in listed.json()["data"]] == ["mtg-mt4-alice", "mtg-a"]
    only_panels = client.get(PANEL + "?session_class=gate_panel", headers=headers())
    assert only_panels.json()["data"] == [] and only_panels.json()["reason"] == "no_trader_meetings"

    detail = client.get(PANEL + "/mtg-a", headers=headers())
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["session_class"] == "deliberative_meeting"
    assert len(detail.json()["data"]["rounds"]) == 2

    missing = client.get(PANEL + "/mtg-does-not-exist", headers=headers())
    assert missing.status_code == 404
    for hidden in ("mtg-bob-private", "mtg-institutional", "panel-a"):
        for path in (
            PANEL + "/" + hidden,
            PANEL + "/" + hidden + "/memos",
            "/bff/agora/committee/meetings/" + hidden,
            "/bff/agora/committee/meetings/" + hidden + "/proposals",
        ):
            response = client.get(path, headers=headers())
            assert response.status_code == 404, (path, response.text)
            assert _body(response) == _body(missing), path
