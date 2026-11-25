"""DISCOVERY-OWNER-SUMMARY-READ-20261125: tenant-scoped, bounded run page."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.research.tests.test_persona_demand_routes_auth import AUDIENCE, ISSUER, SECRET, TENANT, _headers
from services.research.tests.test_research_orchestrator_http_service import _load_service_module

PAGE = "/api/research-orchestrator/run-page"


@pytest.fixture
def research(tmp_path, monkeypatch):
    for key, value in {"AUTH_MODE": "strict", "JWT_SECRET": SECRET, "JWT_ISSUER": ISSUER,
                       "JWT_AUDIENCE": AUDIENCE, "MFA_REQUIRED": "false"}.items():
        monkeypatch.setenv("PANTHEON_RESEARCH_" + key, value)
    module = _load_service_module(data_dir=str(tmp_path))
    for i in range(260):
        tenant = TENANT if i % 3 else "tenant-other"
        module.store.put_run({"run_id": f"r{i:03d}", "tenant_id": tenant, "status": "completed",
                              "created_at": f"2026-11-{1 + i % 28:02d}T00:00:{i % 60:02d}Z"})
    module.store.put_run({"run_id": "r-unstamped", "status": "completed", "created_at": "2026-12-31T00:00:00Z"})
    return module, TestClient(module.app)


def test_page_is_tenant_scoped_newest_first_and_bounded(research):
    module, client = research
    mine = sorted(
        (r for r in module.store.list_runs() if r.get("tenant_id") == TENANT),
        key=lambda r: (r["created_at"], r["run_id"]), reverse=True,
    )
    seen, cursor = [], None
    while True:
        resp = client.get(PAGE, params={"limit": 50, **({"cursor": cursor} if cursor else {})}, headers=_headers())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tenant_id"] == TENANT and body["count"] <= 50
        assert all(r["tenant_id"] == TENANT for r in body["items"])
        seen.extend(r["run_id"] for r in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == [r["run_id"] for r in mine]
    assert "r-unstamped" not in seen


def test_page_limit_is_capped_and_cursor_validated(research):
    _, client = research
    assert client.get(PAGE, params={"limit": 201}, headers=_headers()).status_code == 422
    assert client.get(PAGE, params={"cursor": "not-base64!"}, headers=_headers()).status_code == 422
    body = client.get(PAGE, params={"limit": 200}, headers=_headers()).json()
    assert body["count"] == 173 and body["next_cursor"] is None


def test_page_requires_auth_and_rejects_cross_tenant(research):
    _, client = research
    assert client.get(PAGE).status_code in (401, 403)
    other = _headers(tenant="tenant-other")  # token for another tenant, header for TENANT
    assert client.get(PAGE, headers=other).status_code == 403


def test_old_unscoped_list_is_unchanged(research):
    _, client = research
    rows = client.get("/api/research-orchestrator/runs").json()
    assert len(rows) == 261
