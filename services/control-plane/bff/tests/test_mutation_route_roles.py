"""BFF-MUTATION-ROUTE-ROLES-001: state-changing routes require the operator role."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# (method, path, expected operator status, expected error code, payload)
MUTATION_ROUTES = [
    ("POST", "/bff/jobs/j1/actions/retry", 404, "RESOURCE_NOT_FOUND", {"reason": "operator retry"}),
    ("POST", "/bff/rankings/r1/actions/publish", 404, "RESOURCE_NOT_FOUND", {}),
    ("POST", "/bff/agora/messages/m1/actions/ack", 202, None, {}),
    ("POST", "/bff/agora/ask/sessions", 201, None, {"question": "What is the current risk?"}),
    ("POST", "/bff/agora/ask/sessions/s1/close", 404, "RESOURCE_NOT_FOUND", {}),
    ("POST", "/bff/agora/ask", 202, None, {"question": "Summarize current risk."}),
    ("POST", "/bff/agora/insights/i1/actions/ack", 404, "RESOURCE_NOT_FOUND", {}),
    ("POST", "/bff/agora/memory/m1/actions/ack", 404, "RESOURCE_NOT_FOUND", {}),
    ("POST", "/bff/memory/m1/actions/quarantine", 404, "RESOURCE_NOT_FOUND", {"reason": "operator review"}),
    ("POST", "/bff/insights/i1/actions/attach-strategy", 404, "RESOURCE_NOT_FOUND", {"strategy_id": "strategy-fixture"}),
    ("POST", "/api/v1/personas/p1/strategy-discovery", 404, "RESOURCE_NOT_FOUND", {"query": "momentum", "lookback_days": 30}),
    ("POST", "/bff/personas/p1/strategy-discovery", 404, "RESOURCE_NOT_FOUND", {"query": "momentum", "lookback_days": 30}),
    ("POST", "/api/v1/personas/p1/strategy-matches/m1/actions", 422, "VALIDATION_FAILED", {"action": "create_research_ticket", "notes": "operator approved"}),
    ("POST", "/bff/personas/p1/strategy-matches/m1/actions", 422, "VALIDATION_FAILED", {"action": "create_research_ticket", "notes": "operator approved"}),
    ("POST", "/bff/personas/p1/test-prompt", 404, "RESOURCE_NOT_FOUND", {"prompt": "What is current portfolio exposure?"}),
]


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    mp.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    from services.control_plane.bff.main import app

    yield TestClient(app, raise_server_exceptions=False)
    mp.undo()


@pytest.mark.parametrize("method,path,status,code,payload", MUTATION_ROUTES)
def test_viewer_token_is_forbidden(client, method, path, status, code, payload):
    r = client.request(method, path, json=payload, headers={
        "Authorization": "Bearer viewer-1:viewer", "Idempotency-Key": f"k-viewer-{path}"})
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("method,path,status,code,payload", MUTATION_ROUTES)
def test_operator_token_keeps_handler_behavior(client, method, path, status, code, payload):
    r = client.request(method, path, json=payload, headers={
        "Authorization": "Bearer op-1:operator", "Idempotency-Key": f"k-op-{path}"})
    assert r.status_code == status, r.text
    body = r.json()
    if code:
        assert body["error"]["code"] == code
        assert body["error"]["message"]
    else:
        assert "data" in body
        assert isinstance(body["data"], dict)
