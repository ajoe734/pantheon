from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from services.control_plane.bff.evolution.router import create_evolution_router
from services.control_plane.bff.evolution.service import ew04_inspiration_projection_from_lineage_edges
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_AUTH = "Bearer test-operator:operator"

_DEFAULT_INSPIRATION_GRAPHS = {
    "artifact-042": {
        "artifact_id": "artifact-042",
        "inspiration_edges": [
            {
                "source_artifact_id": "artifact-041",
                "relationship_type": "derived_from",
                "influence_weight": 0.85,
            },
            {
                "source_artifact_id": "artifact-039",
                "relationship_type": "strategy_applied",
                "influence_weight": 0.6,
            },
            {
                "source_artifact_id": "artifact-038",
                "relationship_type": "inspired_by",
                "influence_weight": 0.4,
            },
        ],
        "strategy_tags": [
            "momentum-alpha",
            "low-volatility",
            "sector-rotation",
        ],
        "page_info": {
            "next_page_token": None,
        },
        "snapshot_at": "2026-04-19T03:00:00Z",
        "surfaces": {
            "inspiration": "fresh",
        },
    }
}


@contextmanager
def _seeded_client(
    *,
    inspiration_graphs: dict | None = None,
    lineage_edges: list | None = None,
):
    graphs = dict(_DEFAULT_INSPIRATION_GRAPHS if inspiration_graphs is None else inspiration_graphs)
    store = create_in_memory_read_surface_ports(
        lifecycle_telemetry_governance_kwargs={
            "inspiration_graphs": graphs,
            "lineage_edges": lineage_edges or [],
        }
    )

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

    app = FastAPI(title="Inspiration Graph Contract")

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
    client = TestClient(app)
    client.store = store
    yield client


def test_ew04_inspiration_graph_contract_returns_published_projection() -> None:
    graphs = {
        "artifact-042": {
            **_DEFAULT_INSPIRATION_GRAPHS["artifact-042"],
            "page_info": {
                "next_page_token": "cursor:artifact-042:2",
            },
        }
    }
    with _seeded_client(inspiration_graphs=graphs) as client:

        response = client.get(
            "/api/v1/lineage/inspiration/artifact-042",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["artifact_id"] == "artifact-042"
        assert payload["inspiration_edges"] == [
            {
                "source_artifact_id": "artifact-041",
                "relationship_type": "derived_from",
                "influence_weight": 0.85,
            },
            {
                "source_artifact_id": "artifact-039",
                "relationship_type": "strategy_applied",
                "influence_weight": 0.6,
            },
            {
                "source_artifact_id": "artifact-038",
                "relationship_type": "inspired_by",
                "influence_weight": 0.4,
            },
        ]
        assert payload["strategy_tags"] == [
            "momentum-alpha",
            "low-volatility",
            "sector-rotation",
        ]
        assert payload["page_info"] == {
            "next_page_token": "cursor:artifact-042:2",
        }
        assert payload["meta"]["snapshot_at"] == "2026-04-19T03:00:00Z"
        assert payload["meta"]["surfaces"]["inspiration"] == "fresh"


def test_ew04_inspiration_graph_returns_unavailable_surface_when_dataset_is_missing() -> None:
    with _seeded_client() as client:
        client.store.get_inspiration_graph = lambda artifact_id: None
        client.store.dataset_source = lambda dataset: "missing"

        response = client.get(
            "/api/v1/lineage/inspiration/artifact-042",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["artifact_id"] == "artifact-042"
        assert payload["inspiration_edges"] == []
        assert payload["strategy_tags"] == []
        assert payload["meta"]["surfaces"]["inspiration"] == "unavailable"


def test_ew04_inspiration_graph_returns_404_for_unknown_artifact_even_when_dataset_is_missing() -> None:
    with _seeded_client() as client:
        client.store.dataset_source = lambda dataset: "missing"
        response = client.get(
            "/api/v1/lineage/inspiration/artifact-missing",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 404, response.text
        payload = response.json()
        assert payload["error"]["code"] == "RESOURCE_NOT_FOUND"
        assert payload["error"]["message"] == "Artifact not found"


def test_ew04_inspiration_graph_fallback_from_lineage_edges_does_not_synthesize_constant_weight() -> None:
    edges = [
        {
            "id": "edge-001",
            "from_artifact_id": "artifact-upstream",
            "to_artifact_id": "artifact-fallback-01",
            "edge_type": "inspired_by",
            "strategy_id": "alpha-strategy",
        }
    ]
    with _seeded_client(lineage_edges=edges) as client:
        projection = ew04_inspiration_projection_from_lineage_edges(
            "artifact-fallback-01",
            client.store,
            utc_now=lambda: "2026-04-19T03:00:00Z",
        )
        assert projection is not None
        assert projection["artifact_id"] == "artifact-fallback-01"
        assert len(projection["inspiration_edges"]) == 1
        edge = projection["inspiration_edges"][0]
        assert edge["source_artifact_id"] == "artifact-upstream"
        assert edge["relationship_type"] == "inspired_by"
        assert edge["influence_weight"] is None
        assert edge["influence_state"] == "influence_unknown"
        assert edge["influence_weight"] != 1.0  # Must not synthesize constant 1.0

