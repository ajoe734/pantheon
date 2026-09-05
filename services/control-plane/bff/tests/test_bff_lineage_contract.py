"""
Contract tests for GET /bff/management/lineage (BFF-B3-005).

Decoupled from composition globals using create_lineage_router.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.console_gap.lineage import create_lineage_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_HEADERS = {"Authorization": "Bearer op-lineage:operator,reviewer"}

_EDGES = [
    {
        "id": "ln-edge-bffgap-001",
        "from_artifact_id": "artifact-alpha",
        "to_artifact_id": "artifact-beta",
        "relationship": "derived_from",
        "created_at": "2026-06-01T08:00:00Z",
    },
    {
        "id": "ln-edge-bffgap-002",
        "from_artifact_id": "artifact-beta",
        "to_artifact_id": "artifact-gamma",
        "relationship": "promoted_to",
        "created_at": "2026-06-01T09:00:00Z",
    },
]


def _make_client(store: Any) -> TestClient:
    def _extract_identity(authorization: str | None) -> OperatorIdentity:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Authentication required")
        raw = authorization[len("Bearer "):].strip()
        parts = raw.split(":")
        operator_id = parts[0] if parts else "op"
        roles = parts[1].split(",") if len(parts) > 1 else []
        return OperatorIdentity(operator_id=operator_id, roles=roles, claims={})

    def _require_read_role(identity: OperatorIdentity) -> None:
        if not identity or not identity.roles:
            raise HTTPException(status_code=403, detail="Forbidden")

    app = FastAPI(title="Lineage Contract")
    router = create_lineage_router(
        read_surface=store,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        snapshot_meta=lambda snapshot_at: {"snapshot_at": snapshot_at},
        utc_now=lambda: "2026-06-15T08:00:00Z",
    )
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _seeded_client(edges=None) -> TestClient:
    store = create_in_memory_read_surface_ports()
    resolved_edges = edges if edges is not None else list(_EDGES)
    store.get_lineage_graph = lambda root_type=None, root_id=None, depth=3: (
        [e for e in resolved_edges if e.get("from_artifact_id") == root_id or e.get("to_artifact_id") == root_id]
        if root_id
        else list(resolved_edges)
    )
    store.get_lineage_graph_nodes = lambda edges: sorted(
        {
            artifact_id: {"artifact_id": artifact_id, "artifact_version": "", "artifact_type": "strategy"}
            for edge in edges
            for artifact_id in [edge.get("from_artifact_id"), edge.get("to_artifact_id")]
            if artifact_id
        }.values(),
        key=lambda n: n["artifact_id"],
    )
    store.dataset_source = lambda dataset: "service_store" if dataset == "lineage_edges" else "missing"
    return _make_client(store)


def _missing_client() -> TestClient:
    store = create_in_memory_read_surface_ports()
    store.get_lineage_graph = lambda root_type=None, root_id=None, depth=3: []
    store.get_lineage_graph_nodes = lambda edges: []
    store.dataset_source = lambda dataset: "missing"
    return _make_client(store)


def test_bff_lineage_returns_canonical_envelope() -> None:
    client = _seeded_client()
    response = client.get("/bff/lineage", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert "data" in payload
    assert "items" in payload
    assert "page_info" in payload
    assert "meta" in payload

    data = payload["data"]
    assert data["id"] == "lineage"
    assert "nodes" in data
    assert "edges" in data
    assert data["status"] == "ok"
    assert data["source"] == "service_store"

    assert len(payload["items"]) == 2
    assert payload["page_info"]["total"] == 2
    assert payload["page_info"]["next_page_token"] is None

    meta = payload["meta"]
    assert meta["status"] == "ok"
    assert "snapshot_at" in meta
    assert "surfaces" in meta
    assert meta["surfaces"]["lineage"]["status"] == "ok"


def test_bff_lineage_returns_nodes_and_edges() -> None:
    client = _seeded_client()
    response = client.get("/bff/lineage", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()

    data = payload["data"]
    edge_ids = [e["id"] for e in data["edges"]]
    assert "ln-edge-bffgap-001" in edge_ids
    assert "ln-edge-bffgap-002" in edge_ids

    artifact_ids = {n["artifact_id"] for n in data["nodes"]}
    assert "artifact-alpha" in artifact_ids
    assert "artifact-beta" in artifact_ids
    assert "artifact-gamma" in artifact_ids


def test_bff_lineage_root_id_filter() -> None:
    client = _seeded_client()
    response = client.get(
        "/bff/lineage?root_id=artifact-alpha",
        headers=OPERATOR_HEADERS,
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    data = payload["data"]
    assert len(data["edges"]) == 1
    assert data["edges"][0]["id"] == "ln-edge-bffgap-001"


def test_bff_lineage_degraded_when_store_missing() -> None:
    client = _missing_client()
    response = client.get("/bff/lineage", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()

    data = payload["data"]
    assert data["id"] == "lineage"
    assert data["nodes"] == []
    assert data["edges"] == []
    assert data["status"] == "unavailable"
    assert data["source"] == "missing"

    assert payload["items"] == []
    assert payload["page_info"]["total"] == 0
    assert payload["page_info"]["next_page_token"] is None

    meta = payload["meta"]
    assert meta["status"] == "unavailable"
    assert meta["source"] == "missing"
    assert meta["surfaces"]["lineage"]["status"] == "unavailable"


def test_bff_lineage_requires_read_auth() -> None:
    client = _seeded_client()
    response = client.get("/bff/lineage")
    assert response.status_code == 401
