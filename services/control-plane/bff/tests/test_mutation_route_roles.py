"""BFF-MUTATION-ROUTE-ROLES-001: state-changing routes require the operator role."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# (method, path, expected operator status, expected error code)
MUTATION_ROUTES = [
    ("POST", "/bff/jobs/j1/actions/retry", 404, "RESOURCE_NOT_FOUND"),
    ("POST", "/bff/rankings/r1/actions/publish", 404, "RESOURCE_NOT_FOUND"),
    ("POST", "/bff/agora/messages/m1/actions/ack", 202, None),
    ("POST", "/bff/agora/ask/sessions", 201, None),
    ("POST", "/bff/agora/ask/sessions/s1/close", 404, "RESOURCE_NOT_FOUND"),
    ("POST", "/bff/agora/ask", 202, None),
    ("POST", "/bff/agora/insights/i1/actions/ack", 404, "RESOURCE_NOT_FOUND"),
    ("POST", "/bff/agora/memory/m1/actions/ack", 404, "RESOURCE_NOT_FOUND"),
    ("POST", "/bff/memory/m1/actions/quarantine", 404, "RESOURCE_NOT_FOUND"),
    ("POST", "/bff/insights/i1/actions/attach-strategy", 404, "RESOURCE_NOT_FOUND"),
    ("POST", "/api/v1/personas/p1/strategy-discovery", 404, "RESOURCE_NOT_FOUND"),
    ("POST", "/bff/personas/p1/strategy-discovery", 404, "RESOURCE_NOT_FOUND"),
    ("POST", "/api/v1/personas/p1/strategy-matches/m1/actions", 422, "VALIDATION_FAILED"),
    ("POST", "/bff/personas/p1/strategy-matches/m1/actions", 422, "VALIDATION_FAILED"),
    ("POST", "/bff/personas/p1/test-prompt", 404, "RESOURCE_NOT_FOUND"),
]


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    mp.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    from services.control_plane.bff.main import app

    yield TestClient(app, raise_server_exceptions=False)
    mp.undo()


@pytest.mark.parametrize("method,path,status,code", MUTATION_ROUTES)
def test_viewer_token_is_forbidden(client, method, path, status, code):
    r = client.request(method, path, json={}, headers={
        "Authorization": "Bearer viewer-1:viewer", "Idempotency-Key": f"k-viewer-{path}"})
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("method,path,status,code", MUTATION_ROUTES)
def test_operator_token_keeps_handler_behavior(client, method, path, status, code):
    r = client.request(method, path, json={}, headers={
        "Authorization": "Bearer op-1:operator", "Idempotency-Key": f"k-op-{path}"})
    assert r.status_code == status, r.text
    if code:
        assert r.json()["error"]["code"] == code
