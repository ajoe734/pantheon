"""Persona cycle memory panel: asserted identity/roles, named refusal, persona and tenant isolation."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from services.control_plane.bff.agora.memory_review.client import MemoryReviewClient
from services.control_plane.bff.personas.cycle_views import PERSONA_MEMORY_PANEL_ROLES
from services.control_plane.bff.tests.test_persona_cycle_views import (  # noqa: F401
    PATHS, PID, TENANT, cycle_stack, headers,
)
from services.control_plane.bff.tests.test_agora_committee_memory_owners import stack  # noqa: F401
from services.persona.test_harness import system  # noqa: F401

PANEL = "persona_memory_summary"


@pytest.fixture
def queries(monkeypatch):
    """Record the retrieve query the BFF actually sends, then delegate to the real client."""
    seen = []
    real = MemoryReviewClient.call

    def spy(self, method, path, *a, **k):
        if "/api/memory/retrieve" in path:
            seen.append(parse_qs(urlparse(path).query))
        return real(self, method, path, *a, **k)

    monkeypatch.setattr(MemoryReviewClient, "call", spy)
    return seen


# auditor is allowed by memory policy but cannot open the BFF page (no viewer-level role): not reachable here.
@pytest.mark.parametrize("role", ["operator", "reviewer"])
def test_allowed_role_gets_entries_with_verified_roles(cycle_stack, queries, role):
    client, *_ = cycle_stack
    response = client.get(PATHS[0], headers=headers(roles=[role]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["surfaces"][PANEL]["status"] == "ok", body["meta"]["surfaces"][PANEL]
    assert body["data"]["personaCycle"]["memorySummary"]["returned"] >= 1
    queries = queries
    assert queries
    for q in queries:
        assert q["actor_roles"] == [role]  # explicit, verifiable subset of the token roles
        assert q["actor_id"] == ["alice"]


def test_extra_token_roles_are_not_asserted(cycle_stack, queries):
    client, *_ = cycle_stack
    response = client.get(PATHS[0], headers=headers(roles=["viewer", "operator"]))
    assert response.status_code == 200, response.text
    assert all(q["actor_roles"] == ["operator"] for q in queries)


@pytest.mark.parametrize("roles", [["viewer"]])
def test_disallowed_role_gets_named_state_and_page_still_loads(cycle_stack, queries, roles):
    client, *_ = cycle_stack
    response = client.get(PATHS[0], headers=headers(roles=roles))
    assert response.status_code == 200, response.text
    body = response.json()
    surface = body["meta"]["surfaces"][PANEL]
    assert surface["status"] == "unavailable"
    assert surface["reason"] == "memory_read_role_not_allowed"
    assert body["data"]["personaCycle"]["memorySummary"] is None
    assert body["data"]["personaCycle"]["cycle"]  # rest of the page loads
    assert not queries  # no grant widened, no refused call to memory-svc


def test_no_broad_grant_for_viewer_role():
    assert "viewer" not in PERSONA_MEMORY_PANEL_ROLES
    assert set(PERSONA_MEMORY_PANEL_ROLES) == {"operator", "admin", "reviewer", "auditor"}


def test_other_persona_or_tenant_entries_are_never_shown(cycle_stack):
    client, _, control, *_ = cycle_stack
    for entry in ({"memory_id": "x", "tenant_id": "tenant-other", "persona_id": PID, "content": {}},
                  {"memory_id": "y", "tenant_id": TENANT, "persona_id": "persona-b", "content": {}}):
        control["response"][("memory.test", "/api/memory/retrieve")] = {
            "authz": {"allowed": True}, "hits": [{"type": "persona", "entry": entry}]}
        body = client.get(PATHS[0], headers=headers()).json()
        assert body["data"]["personaCycle"]["memorySummary"] is None
        assert body["meta"]["surfaces"][PANEL]["reason"] == "memory_scope_or_identity_mismatch"
