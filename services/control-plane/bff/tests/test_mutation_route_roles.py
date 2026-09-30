"""BFF-MUTATION-ROUTE-ROLES-001: state-changing routes require the operator role."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# (method, path, expected operator status, expected error code, payload)
MUTATION_ROUTES = [
    ("POST", "/bff/jobs/j1/actions/retry", 202, None, {"reason": "operator retry"}),
    ("POST", "/bff/rankings/r1/actions/publish", 202, None, {}),
    ("POST", "/bff/agora/messages/m1/actions/ack", 202, None, {}),
    ("POST", "/bff/agora/ask/sessions", 201, None, {"question": "What is the current risk?"}),
    ("POST", "/bff/agora/ask/sessions/{session}/close", 200, None, {}),
    ("POST", "/bff/agora/ask", 202, None, {"question": "Summarize current risk."}),
    ("POST", "/bff/agora/insights/i1/actions/ack", 202, None, {}),
    ("POST", "/bff/agora/memory/m1/actions/ack", 202, None, {}),
    ("POST", "/bff/memory/m1/actions/quarantine", 202, None, {"reason": "operator review"}),
    ("POST", "/bff/insights/i1/actions/attach-strategy", 202, None, {"strategy_id": "strategy-fixture"}),
    ("POST", "/api/v1/personas/p1/strategy-discovery", 202, None, {"query": "momentum", "lookback_days": 30}),
    ("POST", "/bff/personas/p1/strategy-discovery", 202, None, {"query": "momentum", "lookback_days": 30}),
    ("POST", "/api/v1/personas/p1/strategy-matches/m1/actions", 202, None, {"action": "promote_seed_candidate", "notes": "operator approved"}),
    ("POST", "/bff/personas/p1/strategy-matches/m1/actions", 202, None, {"action": "promote_seed_candidate", "notes": "operator approved"}),
    ("POST", "/bff/personas/p1/test-prompt", 202, None, {"prompt": "What is current portfolio exposure?"}),
]


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    mp.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    from services.control_plane.bff import main
    from services.control_plane.bff.agora.service import AgoraService
    from services.control_plane.bff.personas import service as persona_service
    from services.control_plane.bff.personas.routes import lifecycle

    # main.py calls get_catalog_entry without importing it (pre-existing, out of scope here).
    from services.control_plane.bff.action_catalog import get_catalog_entry
    mp.setattr(main, "get_catalog_entry", get_catalog_entry, raising=False)
    # Valid fixture resources for every id the routes look up.
    store = type(main.read_store)
    mp.setattr(store, "get_job_bff", lambda self, job_id: {"job_id": job_id, "status": "failed"}, raising=False)
    mp.setattr(store, "get_ranking", lambda self, rid: {"ranking_id": rid}, raising=False)
    mp.setattr(AgoraService, "get_insight", lambda self, i: {"insightId": i})
    mp.setattr(AgoraService, "get_memory_entry", lambda self, m: {"memoryId": m})
    match = {"match_id": "m1", "matched_object_type": "strategy_spec_seed", "matched_object_id": "seed-1", "metadata": {}}
    mp.setattr(persona_service, "_ensure_persona_exists", lambda *a, **k: None)
    mp.setattr(lifecycle, "_ensure_persona_exists", lambda *a, **k: None)
    mp.setattr(persona_service, "_persona_strategy_discovery_payload", lambda *a, **k: {
        "profile": {}, "matches": [match], "surfaces": {}, "candidate_counts": {}})
    yield TestClient(app := main.app, raise_server_exceptions=False)
    mp.undo()


def _resolve(client, path):
    if "{session}" not in path:
        return path
    r = client.post("/bff/agora/ask/sessions", json={"question": "fixture"}, headers={
        "Authorization": "Bearer op-1:operator", "Idempotency-Key": "k-fixture-session"})
    assert r.status_code == 201, r.text
    return path.replace("{session}", r.json()["data"]["sessionId"])


@pytest.mark.parametrize("method,path,status,code,payload", MUTATION_ROUTES)
def test_viewer_token_is_forbidden(client, method, path, status, code, payload):
    r = client.request(method, _resolve(client, path), json=payload, headers={
        "Authorization": "Bearer viewer-1:viewer", "Idempotency-Key": f"k-viewer-{path}"})
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("method,path,status,code,payload", MUTATION_ROUTES)
def test_operator_token_keeps_handler_behavior(client, method, path, status, code, payload):
    r = client.request(method, _resolve(client, path), json=payload, headers={
        "Authorization": "Bearer op-1:operator", "Idempotency-Key": f"k-op-{path}"})
    assert r.status_code == status, r.text
    body = r.json()
    if code:
        assert body["error"]["code"] == code
        assert body["error"]["message"]
    else:
        assert "data" in body
        assert isinstance(body["data"], (dict, list))
