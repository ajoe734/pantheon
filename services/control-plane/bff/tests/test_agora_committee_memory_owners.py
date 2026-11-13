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
        if (u.hostname, u.path) in control["response"]:
            content = json.dumps(control["response"][(u.hostname, u.path)]).encode()
        if response.status_code >= 400:
            raise HTTPError(req.full_url, response.status_code, "owner rejected", {}, io.BytesIO(content))
        result = io.BytesIO(content)
        result.status = response.status_code
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
    calls.clear()
    yield TestClient(app), calls, control, candidate, clients, store


def test_all_restored_routes_reach_real_owners(stack):
    client, calls, _, candidate, owners, store = stack
    panels = client.get(PANEL, headers=headers())
    assert panels.status_code == 200, panels.text
    assert [p["request_id"] for p in panels.json()["data"]] == ["panel-a"]
    assert client.get(PANEL + "/panel-a", headers=headers()).json()["data"]["consultation_type"] == "persona_panel"
    assert client.get(PANEL + "/panel-a/memos", headers=headers()).json()["data"][0]["memo_id"] == "memo-panel-a"
    path = PANEL + "/panel-a/memos/memo-panel-a/publish"
    published = client.post(path, headers=headers())
    assert published.status_code == 200, published.text
    assert published.json()["data"]["status"] == "published"
    assert store.get_memo("memo-panel-a").status.value == "published"
    assert client.post(path, headers=headers()).json()["data"] == published.json()["data"]
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
    control["response"][("consultation.test", "/api/consult/requests")] = []
    assert client.get(PANEL, headers=headers()).json()["reason"] == "no_persona_panels"
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
