#!/usr/bin/env python3
"""HTTP contract tests for PKT-003 Lineage View BFF surfaces."""
from __future__ import annotations

from contextlib import contextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.control_plane.bff.evolution.router import create_evolution_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

AUTH = "Bearer test-operator:operator,admin"


def _build_test_client(store) -> TestClient:
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

    app = FastAPI(title="Lineage View Contract")

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc_handler(req, exc):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        elif isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "ERROR", "message": str(exc.detail)}},
        )

    router = create_evolution_router(
        read_surface=store,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_read_role,
        utc_now=lambda: "2026-04-19T03:00:00Z",
    )
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@contextmanager
def _seeded_client():
    store = create_in_memory_read_surface_ports(
        lifecycle_telemetry_governance_kwargs={
            "lineage_edges": [
                {
                    "id": "ln-edge-001",
                    "from_artifact_id": "artifact-041",
                    "to_artifact_id": "artifact-042",
                    "relationship": "derived_from",
                    "created_at": "2026-04-09T00:00:00Z",
                },
                {
                    "id": "ln-edge-002",
                    "from_artifact_id": "artifact-042",
                    "to_artifact_id": "artifact-043",
                    "relationship": "promoted_to",
                    "created_at": "2026-04-10T00:00:00Z",
                },
            ],
            "artifact_registry_entries": [
                {
                    "artifact_id": "artifact-041",
                    "artifact_version": "v2.0.0",
                    "artifact_type": "strategy",
                },
                {
                    "artifact_id": "artifact-042",
                    "artifact_version": "v2.1.0",
                    "artifact_type": "strategy",
                },
                {
                    "artifact_id": "artifact-043",
                    "artifact_version": "v2.2.0",
                    "artifact_type": "strategy",
                },
            ],
        }
    )
    client = _build_test_client(store)
    yield client


@contextmanager
def _registry_backed_client():
    store = create_in_memory_read_surface_ports(
        lifecycle_telemetry_governance_kwargs={
            "lineage_edges": [
                {
                    "id": "ln-edge-101",
                    "from_artifact_id": "artifact-alpha",
                    "to_artifact_id": "artifact-beta",
                    "relationship": "derived_from",
                    "created_at": "2026-04-16T08:00:00Z",
                },
                {
                    "id": "ln-edge-102",
                    "from_artifact_id": "artifact-beta",
                    "to_artifact_id": "artifact-gamma",
                    "relationship": "promoted_to",
                    "created_at": "2026-04-16T09:00:00Z",
                },
            ],
            "artifact_registry_entries": [
                {
                    "artifact_id": "artifact-alpha",
                    "registry_id": "artifact-alpha",
                    "artifact_type": "strategy_bundle",
                    "version": "v1.0.0",
                },
                {
                    "artifact_id": "artifact-beta",
                    "registry_id": "artifact-beta",
                    "artifact_type": "strategy_bundle",
                    "version": "v1.1.0",
                },
                {
                    "artifact_id": "artifact-gamma",
                    "registry_id": "artifact-gamma",
                    "artifact_type": "strategy_bundle",
                    "version": "v1.2.0",
                },
            ],
        }
    )
    client = _build_test_client(store)
    yield client


def test_lineage_list_contract():
    with _seeded_client() as client:
        resp = client.get(
            "/api/v1/lineage?page_size=1",
            headers={"Authorization": AUTH},
        )
        assert resp.status_code == 200

        body = resp.json()
        assert "data" not in body
        assert "items" in body
        assert "page_info" in body
        assert body["page_info"]["next_page_token"] == "1"
        assert "snapshot_at" in body["meta"]

        item = body["items"][0]
        assert item == {
            "artifact_id": "artifact-042",
            "edge_count": 2,
            "last_edge_at": "2026-04-10T00:00:00Z",
        }


def test_lineage_list_artifact_filter_contract():
    with _seeded_client() as client:
        resp = client.get(
            "/api/v1/lineage?artifact_id=artifact-042",
            headers={"Authorization": AUTH},
        )
        assert resp.status_code == 200

        body = resp.json()
        assert body["page_info"]["next_page_token"] is None
        assert body["items"] == [
            {
                "artifact_id": "artifact-042",
                "edge_count": 2,
                "last_edge_at": "2026-04-10T00:00:00Z",
            }
        ]


def test_lineage_edge_detail_contract():
    with _seeded_client() as client:
        resp = client.get(
            "/api/v1/lineage/edges/ln-edge-001",
            headers={"Authorization": AUTH},
        )
        assert resp.status_code == 200

        body = resp.json()
        assert "data" not in body
        for key in [
            "id",
            "from_artifact_id",
            "to_artifact_id",
            "relationship",
            "created_at",
            "meta",
        ]:
            assert key in body
        assert body["id"] == "ln-edge-001"
        assert body["relationship"] == "derived_from"
        assert "snapshot_at" in body["meta"]


def test_lineage_graph_contract():
    with _seeded_client() as client:
        resp = client.get(
            "/api/v1/lineage/graph?root_id=artifact-042&depth=99",
            headers={"Authorization": AUTH},
        )
        assert resp.status_code == 200

        body = resp.json()
        assert "data" not in body
        assert sorted(body.keys()) == ["edges", "meta", "nodes"]
        assert "snapshot_at" in body["meta"]
        assert [edge["id"] for edge in body["edges"]] == ["ln-edge-002", "ln-edge-001"]
        assert body["nodes"] == [
            {
                "artifact_id": "artifact-041",
                "artifact_version": "v2.0.0",
                "artifact_type": "strategy",
            },
            {
                "artifact_id": "artifact-042",
                "artifact_version": "v2.1.0",
                "artifact_type": "strategy",
            },
            {
                "artifact_id": "artifact-043",
                "artifact_version": "v2.2.0",
                "artifact_type": "strategy",
            },
        ]


def test_lineage_graph_uses_registry_snapshot_for_node_metadata():
    with _registry_backed_client() as client:
        resp = client.get(
            "/api/v1/lineage/graph?root_id=artifact-beta&depth=4",
            headers={"Authorization": AUTH},
        )
        assert resp.status_code == 200

        body = resp.json()
        assert body["nodes"] == [
            {
                "artifact_id": "artifact-alpha",
                "artifact_version": "v1.0.0",
                "artifact_type": "strategy_bundle",
            },
            {
                "artifact_id": "artifact-beta",
                "artifact_version": "v1.1.0",
                "artifact_type": "strategy_bundle",
            },
            {
                "artifact_id": "artifact-gamma",
                "artifact_version": "v1.2.0",
                "artifact_type": "strategy_bundle",
            },
        ]
