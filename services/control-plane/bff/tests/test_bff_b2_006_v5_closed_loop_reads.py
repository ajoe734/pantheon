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
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from ports import create_in_memory_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-b2-006:operator"}
NO_AUTH_HEADERS: dict = {}


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = create_in_memory_read_surface_ports()
    bff_main._GOV_BFF_IDEMPOTENCY.clear()
    return TestClient(bff_main.app)


# ---------------------------------------------------------------------------
# 1. GET /bff/v5/control-room
# ---------------------------------------------------------------------------

def test_v5_control_room_returns_aggregate_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
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
        finally:
            bff_main.read_store = original


def test_v5_control_room_loops_and_sentinel_have_items() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/control-room", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "items" in body["loops"]
            assert "items" in body["sentinel"]
            assert "items" in body["interventions"]
        finally:
            bff_main.read_store = original


def test_v5_control_room_unauthenticated_returns_401() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/control-room", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 2. GET /bff/v5/execution/persona-health
# ---------------------------------------------------------------------------

def test_v5_persona_health_returns_items_and_meta() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
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
        finally:
            bff_main.read_store = original


def test_v5_persona_health_items_have_required_fields() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/execution/persona-health", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json().get("items", [])
            for item in items:
                assert "id" in item or "persona_id" in item
                assert "health" in item
                assert item["health"] in ("healthy", "degraded")
        finally:
            bff_main.read_store = original


def test_v5_persona_health_treats_deployed_lifecycle_as_healthy() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
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
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/execution/persona-health", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            item = next(
                item for item in resp.json().get("items", [])
                if item.get("persona_id") == "persona-deployed" or item.get("id") == "persona-deployed"
            )
            assert item["health"] == "healthy"
        finally:
            bff_main.read_store = original


def test_v5_persona_health_unauthenticated_returns_401() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/execution/persona-health", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 3. GET /bff/v5/execution/strategy-health
# ---------------------------------------------------------------------------

def test_v5_strategy_health_returns_items_and_meta() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
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
        finally:
            bff_main.read_store = original


def test_v5_strategy_health_items_have_required_fields() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/execution/strategy-health", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json().get("items", [])
            for item in items:
                assert "id" in item or "strategy_id" in item
                assert "health" in item
                assert item["health"] in ("healthy", "degraded")
        finally:
            bff_main.read_store = original


def test_v5_strategy_health_unauthenticated_returns_401() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/execution/strategy-health", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 4. GET /bff/v5/interventions/{id}
# ---------------------------------------------------------------------------

def test_v5_intervention_detail_unknown_id_returns_404() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/interventions/unknown-intv-999", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
            body = resp.json()
            assert "error" in body or "detail" in body or "code" in body
        finally:
            bff_main.read_store = original


def test_v5_intervention_detail_known_id_returns_data() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_v5 = list(bff_main._V5_INTERVENTIONS_STORE)
        try:
            client = _fresh_client(td)
            test_intv_id = "intv-b2-006-test-001"
            bff_main._V5_INTERVENTIONS_STORE.append({
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
            bff_main.read_store = original_store
            bff_main._V5_INTERVENTIONS_STORE[:] = original_v5


def test_v5_intervention_detail_unauthenticated_returns_401() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/interventions/any-id", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 5. Dedicated handlers — not served by catch-all (routing check)
# ---------------------------------------------------------------------------

def test_v5_routes_are_served_by_dedicated_handlers() -> None:
    """Verify each route is bound to its dedicated handler function name."""
    routes_by_path = {str(r.path): r for r in bff_main.app.routes if hasattr(r, "path")}
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
