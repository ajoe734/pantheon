"""BFF-MUTATION-ROUTE-ROLES-001: state-changing routes require the operator role."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

MUTATION_ROUTES = [
    ("POST", "/bff/jobs/j1/actions/retry"),
    ("POST", "/bff/rankings/r1/actions/publish"),
    ("POST", "/bff/agora/messages/m1/actions/ack"),
    ("POST", "/bff/agora/ask/sessions"),
    ("POST", "/bff/agora/ask/sessions/s1/close"),
    ("POST", "/bff/agora/ask"),
    ("POST", "/bff/agora/insights/i1/actions/ack"),
    ("POST", "/bff/agora/memory/m1/actions/ack"),
    ("POST", "/bff/memory/m1/actions/quarantine"),
    ("POST", "/bff/insights/i1/actions/attach-strategy"),
    ("POST", "/api/v1/personas/p1/strategy-discovery"),
    ("POST", "/bff/personas/p1/strategy-discovery"),
    ("POST", "/api/v1/personas/p1/strategy-matches/m1/actions"),
    ("POST", "/bff/personas/p1/strategy-matches/m1/actions"),
    ("POST", "/bff/personas/p1/test-prompt"),
]


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    mp.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    from services.control_plane.bff.main import app

    yield TestClient(app, raise_server_exceptions=False)
    mp.undo()


@pytest.mark.parametrize("method,path", MUTATION_ROUTES)
def test_viewer_token_is_forbidden(client, method, path):
    r = client.request(method, path, json={}, headers={
        "Authorization": "Bearer viewer-1:viewer", "Idempotency-Key": "k-viewer-1"})
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("method,path", MUTATION_ROUTES)
def test_operator_token_passes_role_gate(client, method, path):
    r = client.request(method, path, json={}, headers={
        "Authorization": "Bearer op-1:operator", "Idempotency-Key": "k-op-1"})
    assert r.status_code != 403, r.text
