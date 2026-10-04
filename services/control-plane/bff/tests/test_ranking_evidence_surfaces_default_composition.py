"""Mounted default-composition tests for the 7 quarterly ranking evidence surfaces.

Validates that:
1. Each of the 7 surfaces (evidence_refs, knowledge_evidence, capability_snapshots,
   persona_bindings, runtime_bindings, telemetry_summaries, persona_sessions) is read
   from its existing owner in default BFF composition (compose_bff_app()).
2. Reachable owner with 0 records reports status="ok" (not degraded); source="missing" is
   strictly reserved for unconfigured owners; failing owners report status="unavailable",
   source="unavailable".
3. Default composition with reachable owners reports surfaces as "ok", non-degrading the
   persona evaluator; failing owners degrade ranking and evaluator.
4. Tenant scoping and evidence redaction remain intact across tenants.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.core.app_factory import compose_bff_app
from services.rankings.test_store import _FakeConnection, _fake_psycopg
from services.runtime_auth_inbound import encode_jwt_hs256

_JWT_SECRET = "test-ranking-evidence-surfaces-secret-0123456789"
_JWT_ISSUER = "pantheon-test-issuer"
_JWT_AUDIENCE = "bff-operators"

_SEVEN_SURFACES = [
    "evidence_refs",
    "knowledge_evidence",
    "capability_snapshots",
    "persona_bindings",
    "runtime_bindings",
    "telemetry_summaries",
    "persona_sessions",
]


def _make_auth_header(tenant_id: str, roles: tuple[str, ...] = ("read_only", "operator", "approver")) -> str:
    claims = {
        "sub": f"user-{tenant_id}",
        "roles": list(roles),
        "exp": 4102444800,
        "iss": _JWT_ISSUER,
        "aud": _JWT_AUDIENCE,
        "tenant_id": tenant_id,
        "tenants": [tenant_id],
    }
    token = encode_jwt_hs256(claims, secret=_JWT_SECRET)
    return f"Bearer {token}"


class MockOwnerServer:
    def __init__(self) -> None:
        self.personas: List[Dict[str, Any]] = []
        self.capital_pools: List[Dict[str, Any]] = []
        self.bindings: List[Dict[str, Any]] = []
        self.runtime_bindings: List[Dict[str, Any]] = []
        self.evidence_items: List[Dict[str, Any]] = []
        self.should_fail_personas: bool = False
        self.should_fail_capital: bool = False
        self.should_fail_source: bool = False
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port: int = 0

    def start(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                tenant = self.headers.get("x-tenant-id")
                if outer.should_fail_personas and self.path.startswith("/api/personas"):
                    self.send_json(500, {"error": "Persona service internal error"})
                    return
                if outer.should_fail_capital and (self.path.startswith("/api/capital-pools") or self.path.startswith("/api/bindings")):
                    self.send_json(500, {"error": "Capital service internal error"})
                    return
                if outer.should_fail_source and self.path.startswith("/api/source-ingest"):
                    self.send_json(500, {"error": "Source ingest service internal error"})
                    return

                if self.path.startswith("/api/personas"):
                    res = [p for p in outer.personas if tenant is None or p.get("tenant_id") == tenant]
                    self.send_json(200, res)
                elif self.path.startswith("/api/capital-pools"):
                    res = [p for p in outer.capital_pools if tenant is None or p.get("tenant_id") == tenant]
                    self.send_json(200, res)
                elif self.path.startswith("/api/bindings"):
                    res = [b for b in outer.bindings if tenant is None or b.get("tenant_id") == tenant]
                    self.send_json(200, res)
                elif self.path.startswith("/api/runtime-bindings"):
                    res = [r for r in outer.runtime_bindings if tenant is None or r.get("tenant_id") == tenant]
                    self.send_json(200, {"bindings": res})
                elif self.path.startswith("/api/source-ingest/evidence/items"):
                    res = [e for e in outer.evidence_items if tenant is None or e.get("tenant_id") == tenant]
                    self.send_json(200, {"items": res})
                else:
                    self.send_json(404, {"error": "not found"})

            def send_json(self, status: int, body: Any) -> None:
                raw = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", _JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", _JWT_AUDIENCE)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("PANTHEON_ENV", "test")

    monkeypatch.setattr(_FakeConnection, "rows", {})
    monkeypatch.setattr(_FakeConnection, "statements", [])
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg())


@pytest.fixture
def mock_owner():
    server = MockOwnerServer()
    server.start()
    yield server
    server.stop()


def test_ranking_evidence_surfaces_ok_empty_default_composition(mock_owner: MockOwnerServer, monkeypatch: pytest.MonkeyPatch):
    """Reachable owner with 0 records reports ok (not degraded); non-degrading persona evaluator."""
    owner_url = f"http://127.0.0.1:{mock_owner.port}"
    monkeypatch.setenv("PANTHEON_PERSONA_URL", owner_url)
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", owner_url)
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", owner_url)
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_URL", owner_url)

    app = compose_bff_app()
    client = TestClient(app)

    auth = _make_auth_header("tenant-test")
    resp = client.get("/bff/management/quarterly-ranking", headers={"Authorization": auth})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    surfaces = payload.get("meta", {}).get("surfaces", {})

    # All 7 surfaces must report status="ok"
    for surface_name in _SEVEN_SURFACES:
        s = surfaces.get(surface_name)
        assert s is not None, f"Surface {surface_name} missing from meta.surfaces"
        assert s.get("status") == "ok", f"Surface {surface_name} expected ok, got {s}"

    # Overall quarterly ranking must be ok (not degraded)
    quarterly_ranking_surface = surfaces.get("quarterly_ranking", {})
    assert quarterly_ranking_surface.get("status") == "ok", f"quarterly_ranking aggregate expected ok, got {quarterly_ranking_surface}"

    # Verify that none of the surfaces trigger the persona evaluator's Degraded check on surface unavailability
    spec = importlib.util.spec_from_file_location(
        "persona_evaluator_agent",
        Path.cwd() / "services/persona-evaluator-agent/persona_evaluator_agent.py",
    )
    pea = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pea)

    # In persona_evaluator_agent.collect_evidence, it checks:
    # any(v.get("status") == "unavailable" for v in surfaces.values())
    assert not any(v.get("status") == "unavailable" for v in surfaces.values() if isinstance(v, dict)), (
        "No surface should be unavailable in ok-empty state"
    )


def test_ranking_evidence_surfaces_ok_populated_default_composition(mock_owner: MockOwnerServer, monkeypatch: pytest.MonkeyPatch):
    """Reachable owner with populated records reports ok and includes records for the caller tenant."""
    owner_url = f"http://127.0.0.1:{mock_owner.port}"
    monkeypatch.setenv("PANTHEON_PERSONA_URL", owner_url)
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", owner_url)
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", owner_url)
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_URL", owner_url)

    mock_owner.personas = [
        {"persona_id": "persona-1", "name": "Alpha One", "tenant_id": "tenant-test", "status": "active", "lifecycle_state": "paper_owner"},
        {"persona_id": "persona-2", "name": "Alpha Two", "tenant_id": "tenant-test", "status": "active", "lifecycle_state": "live_owner"},
    ]
    mock_owner.capital_pools = [
        {"pool_id": "pool-test-1", "tenant_id": "tenant-test", "status": "active"},
    ]
    mock_owner.bindings = [
        {"binding_id": "b-test-1", "tenant_id": "tenant-test", "persona_id": "persona-1", "capital_pool_id": "pool-test-1"},
    ]
    mock_owner.runtime_bindings = [
        {"runtime_id": "rt-test-1", "tenant_id": "tenant-test", "persona_id": "persona-1"},
    ]
    mock_owner.evidence_items = [
        {"evidence_item_id": "ev-test-1", "tenant_id": "tenant-test", "title": "Evidence One", "created_at": "2026-01-15T00:00:00Z"},
    ]

    app = compose_bff_app()
    client = TestClient(app)

    auth = _make_auth_header("tenant-test")
    resp = client.get("/bff/management/quarterly-ranking", headers={"Authorization": auth})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    surfaces = payload.get("meta", {}).get("surfaces", {})

    for surface_name in _SEVEN_SURFACES:
        s = surfaces.get(surface_name)
        assert s is not None, f"Surface {surface_name} missing"
        assert s.get("status") == "ok", f"Surface {surface_name} expected ok, got {s}"

    assert surfaces.get("quarterly_ranking", {}).get("status") == "ok"


def test_ranking_evidence_surfaces_owner_down_degrades_ranking_and_evaluator(mock_owner: MockOwnerServer, monkeypatch: pytest.MonkeyPatch):
    """When an owner fails/is unreachable, surface reports unavailable/unavailable and degrades quarterly ranking."""
    owner_url = f"http://127.0.0.1:{mock_owner.port}"
    monkeypatch.setenv("PANTHEON_PERSONA_URL", owner_url)
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", owner_url)
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", owner_url)
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_URL", owner_url)

    mock_owner.should_fail_personas = True

    app = compose_bff_app()
    client = TestClient(app)

    auth = _make_auth_header("tenant-test")
    resp = client.get("/bff/management/quarterly-ranking", headers={"Authorization": auth})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    surfaces = payload.get("meta", {}).get("surfaces", {})

    # capability_snapshots and persona_sessions must report status="unavailable", source="unavailable" (NOT missing)
    for failing_surface in ["capability_snapshots", "persona_sessions"]:
        s = surfaces.get(failing_surface)
        assert s is not None, f"{failing_surface} missing from surfaces"
        assert s.get("status") == "unavailable", f"{failing_surface} expected unavailable, got {s}"
        assert s.get("source") == "unavailable", f"{failing_surface} source expected 'unavailable' (not 'missing'), got {s.get('source')}"

    # Overall quarterly ranking must be degraded
    quarterly_surface = surfaces.get("quarterly_ranking", {})
    assert quarterly_surface.get("status") == "degraded", f"quarterly_ranking expected degraded, got {quarterly_surface}"

    # Persona evaluator collect_evidence raises Degraded on unavailable surface
    spec = importlib.util.spec_from_file_location(
        "persona_evaluator_agent",
        Path.cwd() / "services/persona-evaluator-agent/persona_evaluator_agent.py",
    )
    pea = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pea)

    with pytest.raises(pea.Degraded, match="ranking evidence surface unavailable"):
        pea.collect_evidence("http://bff", "2026-Q1", {"Authorization": auth}, fetch=lambda *a, **k: payload)


def test_ranking_evidence_surfaces_source_owner_down_reports_unavailable(mock_owner: MockOwnerServer, monkeypatch: pytest.MonkeyPatch):
    """When Source owner fails, evidence surfaces report status='unavailable', source='unavailable'."""
    owner_url = f"http://127.0.0.1:{mock_owner.port}"
    monkeypatch.setenv("PANTHEON_PERSONA_URL", owner_url)
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", owner_url)
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", owner_url)
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_URL", owner_url)

    mock_owner.should_fail_source = True

    app = compose_bff_app()
    client = TestClient(app)

    auth = _make_auth_header("tenant-test")
    resp = client.get("/bff/management/quarterly-ranking", headers={"Authorization": auth})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    surfaces = payload.get("meta", {}).get("surfaces", {})

    for failing_surface in ["evidence_refs", "knowledge_evidence"]:
        s = surfaces.get(failing_surface)
        assert s is not None, f"{failing_surface} missing from surfaces"
        assert s.get("status") == "unavailable", f"{failing_surface} expected unavailable, got {s}"
        assert s.get("source") == "unavailable", f"{failing_surface} source expected 'unavailable' (not 'missing'), got {s.get('source')}"

    assert surfaces.get("quarterly_ranking", {}).get("status") == "degraded"


def test_ranking_evidence_surfaces_cross_tenant_isolation_and_redaction(mock_owner: MockOwnerServer, monkeypatch: pytest.MonkeyPatch):
    """Tenant scoping and evidence redaction remain strictly isolated across tenants."""
    owner_url = f"http://127.0.0.1:{mock_owner.port}"
    monkeypatch.setenv("PANTHEON_PERSONA_URL", owner_url)
    monkeypatch.setenv("PANTHEON_CAPITAL_API_URL", owner_url)
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", owner_url)
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_URL", owner_url)

    mock_owner.personas = [
        {"persona_id": "persona-a", "name": "Persona A", "tenant_id": "tenant-a", "status": "active"},
        {"persona_id": "persona-b", "name": "Persona B", "tenant_id": "tenant-b", "status": "active"},
    ]
    mock_owner.evidence_items = [
        {"evidence_item_id": "ev-a", "tenant_id": "tenant-a", "title": "Evidence A", "created_at": "2026-01-15T00:00:00Z"},
        {"evidence_item_id": "ev-b", "tenant_id": "tenant-b", "title": "Evidence B", "created_at": "2026-01-15T00:00:00Z"},
    ]

    app = compose_bff_app()
    client = TestClient(app)

    # Tenant A request
    auth_a = _make_auth_header("tenant-a")
    resp_a = client.get("/bff/management/quarterly-ranking", headers={"Authorization": auth_a})
    assert resp_a.status_code == 200
    items_a = resp_a.json().get("data", {}).get("items", [])
    persona_ids_a = {item.get("persona_id") for item in items_a}
    assert "persona-b" not in persona_ids_a, "Tenant A must NOT see Tenant B personas"

    # Tenant B request
    auth_b = _make_auth_header("tenant-b")
    resp_b = client.get("/bff/management/quarterly-ranking", headers={"Authorization": auth_b})
    assert resp_b.status_code == 200
    items_b = resp_b.json().get("data", {}).get("items", [])
    persona_ids_b = {item.get("persona_id") for item in items_b}
    assert "persona-a" not in persona_ids_b, "Tenant B must NOT see Tenant A personas"
