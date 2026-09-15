"""
BFF-B2-006: Integration tests for 4 dedicated v5 closed-loop read handlers.

Covers:
  - GET /bff/v5/control-room               aggregate envelope + meta.surfaces
  - GET /bff/v5/execution/persona-health   items list + meta
  - GET /bff/v5/execution/strategy-health  items list + meta
  - GET /bff/v5/interventions/{id}         detail + 404 for unknown id
  - All 4 endpoints return HTTP 401 when unauthenticated
  - Dead catch-all entries removed for these 4 paths
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Optional
from unittest.mock import MagicMock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.control_loops.router import create_control_loops_router
from services.control_plane.bff.runtime.router import create_runtime_router
from services.control_plane.bff.personas.service import PersonaService
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-b2-006:operator"}
NO_AUTH_HEADERS: dict = {}

_V5_INTERVENTIONS_STORE: list[dict[str, Any]] = []


class _Identity:
    def __init__(self) -> None:
        self.operator_id = "op-b2-006"
        self.roles = ["operator", "viewer"]
        self.claims = {"tenant_id": "tenant-dev"}
        self.mfa_verified = True

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


def _extract_identity(authorization: Optional[str] = None) -> _Identity:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    return _Identity()


def _require_read_role(identity: Any) -> None:
    pass


def _dataset_surface_status(name: str, snapshot_at: Optional[str] = None) -> dict[str, Any]:
    return {"status": "ok", "source": "in_memory"}


def _composed_dataset_surface_status(name: str, items: Any, snapshot_at: Optional[str] = None, source: str = "") -> dict[str, Any]:
    return {"status": "ok", "source": source}


def _utc_now() -> str:
    return "2026-06-03T08:00:00Z"


class _V5ClosedLoopTestStore:
    def __init__(self, personas: Optional[list] = None) -> None:
        raw_personas = personas or [
            {
                "persona_id": "persona-alpha",
                "name": "Alpha",
                "lifecycle_state": "active",
                "deployment_stage": "live",
                "runtime_id": "rt-paper-001",
                "metadata": {
                    "market_scope": "crypto",
                    "strategy_family": "momentum",
                },
            },
        ]
        self.ports = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs={
                "personas": raw_personas,
                "persona_league": [
                    {
                        "persona_id": p.get("persona_id") or p.get("id"),
                        "rank": 1,
                        "league_tier": "champion",
                        "score": 95.0,
                    }
                    for p in raw_personas
                ],
            },
        )

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.ports, name)
        if callable(attr):
            def _safe_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return attr(*args, **kwargs)
                except TypeError:
                    return attr(*args)
            return _safe_wrapper
        return attr


def _fresh_client(td: str) -> TestClient:
    snapshot_path = os.path.join(td, "read_surfaces.json")
    personas = None
    if os.path.exists(snapshot_path):
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "personas" in data:
                    personas = list(data["personas"].values())
        except Exception:
            pass
    store = _V5ClosedLoopTestStore(personas=personas)
    persona_svc = PersonaService(
        read_store=store,
        write_owner=MagicMock(),
        ranking_write_owner=MagicMock(),
        command_store=MagicMock(),
    )
    cl_router = create_control_loops_router(
        read_surface=store,
        intervention_records_provider=lambda **kw: list(_V5_INTERVENTIONS_STORE),
        extract_identity=_extract_identity,
    )
    rt_router = create_runtime_router(
        read_surface=store,
        dependencies={
            "_extract_identity": _extract_identity,
            "_require_read_role": _require_read_role,
            "_dataset_surface_status": _dataset_surface_status,
            "_composed_dataset_surface_status": _composed_dataset_surface_status,
            "_build_persona_health_items": persona_svc.build_persona_health_items,
            "utc_now": _utc_now,
        },
    )
    app = FastAPI()
    app.routes.extend(cl_router.routes)
    app.routes.extend(rt_router.routes)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 1. GET /bff/v5/control-room
# ---------------------------------------------------------------------------

def test_v5_control_room_returns_aggregate_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/v5/control-room", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "loops" in body
        assert "interventions" in body
        assert "sentinel" in body
        assert "ooda_status" in body
        assert "meta" in body
        meta = body["meta"]
        assert "snapshot_at" in meta
        assert "surfaces" in meta
        surfaces = meta["surfaces"]
        assert "control_room" in surfaces
        assert "loop_runs" in surfaces
        assert "sentinel_findings" in surfaces


def test_v5_control_room_loops_and_sentinel_have_items() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/v5/control-room", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body["loops"]
        assert "items" in body["sentinel"]
        assert "items" in body["interventions"]


def test_v5_control_room_unauthenticated_returns_401() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/v5/control-room", headers=NO_AUTH_HEADERS)
        assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# 2. GET /bff/v5/execution/persona-health
# ---------------------------------------------------------------------------

def test_v5_persona_health_returns_items_and_meta() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/v5/execution/persona-health", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body
        assert "meta" in body
        meta = body["meta"]
        assert "snapshot_at" in meta
        assert "surfaces" in meta
        assert "persona_health" in meta["surfaces"]


def test_v5_persona_health_items_have_required_fields() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/v5/execution/persona-health", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        items = resp.json().get("items", [])
        for item in items:
            assert "id" in item or "persona_id" in item
            assert "health" in item
            assert item["health"] in ("healthy", "degraded")


def test_v5_persona_health_treats_deployed_lifecycle_as_healthy() -> None:
    with tempfile.TemporaryDirectory() as td:
        snapshot_path = os.path.join(td, "read_surfaces.json")
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "personas": {
                        "persona-deployed": {
                            "persona_id": "persona-deployed",
                            "name": "Deployed Persona",
                            "lifecycle_state": "deployed",
                            "created_at": "2026-06-03T08:00:00Z",
                        }
                    }
                },
                handle,
            )
        client = _fresh_client(td)
        resp = client.get("/bff/v5/execution/persona-health", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        item = next(
            item for item in resp.json().get("items", [])
            if item.get("persona_id") == "persona-deployed" or item.get("id") == "persona-deployed"
        )
        assert item["health"] == "healthy"


def test_v5_persona_health_unauthenticated_returns_401() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/v5/execution/persona-health", headers=NO_AUTH_HEADERS)
        assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# 3. GET /bff/v5/execution/strategy-health
# ---------------------------------------------------------------------------

def test_v5_strategy_health_returns_items_and_meta() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/v5/execution/strategy-health", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body
        assert "meta" in body
        meta = body["meta"]
        assert "snapshot_at" in meta
        assert "surfaces" in meta
        assert "strategy_health" in meta["surfaces"]


def test_v5_strategy_health_items_have_required_fields() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/v5/execution/strategy-health", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        items = resp.json().get("items", [])
        for item in items:
            assert "id" in item or "strategy_id" in item
            assert "health" in item
            assert item["health"] in ("healthy", "degraded")


def test_v5_strategy_health_unauthenticated_returns_401() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/v5/execution/strategy-health", headers=NO_AUTH_HEADERS)
        assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# 4. GET /bff/v5/interventions/{id}
# ---------------------------------------------------------------------------

def test_v5_intervention_detail_unknown_id_returns_404() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/v5/interventions/unknown-intv-999", headers=OPERATOR_HEADERS)
        assert resp.status_code == 404, resp.text
        body = resp.json()
        assert "error" in body or "detail" in body or "code" in body


def test_v5_intervention_detail_known_id_returns_data() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_v5 = list(_V5_INTERVENTIONS_STORE)
        try:
            client = _fresh_client(td)
            test_intv_id = "intv-b2-006-test-001"
            _V5_INTERVENTIONS_STORE.append({
                "id": test_intv_id,
                "intervention_id": test_intv_id,
                "kind": "risk_breach",
                "status": "pending",
                "created_at": "2026-05-23T00:00:00Z",
            })
            resp = client.get(f"/bff/v5/interventions/{test_intv_id}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body or "id" in body
        finally:
            _V5_INTERVENTIONS_STORE[:] = original_v5


def test_v5_intervention_detail_unauthenticated_returns_401() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/v5/interventions/any-id", headers=NO_AUTH_HEADERS)
        assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# 5. Dedicated handlers — not served by catch-all (routing check)
# ---------------------------------------------------------------------------

def test_v5_routes_are_served_by_dedicated_handlers() -> None:
    """Verify each route is bound to its dedicated handler function name."""
    client = _fresh_client("")
    routes_by_path = {str(r.path): r for r in client.app.routes if hasattr(r, "path")}
    cr = routes_by_path.get("/bff/v5/control-room")
    assert cr is not None, "Route /bff/v5/control-room not registered"
    assert cr.endpoint.__name__ == "bff_v5_control_room", cr.endpoint.__name__

    ph = routes_by_path.get("/bff/v5/execution/persona-health")
    assert ph is not None, "Route /bff/v5/execution/persona-health not registered"
    assert ph.endpoint.__name__ == "bff_v5_execution_persona_health", ph.endpoint.__name__

    sh = routes_by_path.get("/bff/v5/execution/strategy-health")
    assert sh is not None, "Route /bff/v5/execution/strategy-health not registered"
    assert sh.endpoint.__name__ == "bff_v5_execution_strategy_health", sh.endpoint.__name__

    intv = routes_by_path.get("/bff/v5/interventions/{intervention_id}")
    assert intv is not None, "Route /bff/v5/interventions/{intervention_id} not registered"
    assert intv.endpoint.__name__ == "bff_v5_intervention_detail", intv.endpoint.__name__
