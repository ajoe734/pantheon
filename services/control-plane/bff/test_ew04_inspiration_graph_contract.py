from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_AUTH = "Bearer test-operator:operator"


@contextmanager
def _seeded_client(*, allow_local_snapshot_fallback: bool = True):
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=allow_local_snapshot_fallback,
        )
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store


def test_ew04_inspiration_graph_contract_returns_published_projection() -> None:
    with _seeded_client() as client:
        bff_main.read_store._data["inspiration_graphs"]["artifact-042"]["page_info"] = {
            "next_page_token": "cursor:artifact-042:2",
        }
        bff_main.read_store._save()

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
        original_get = bff_main.read_store.get_inspiration_graph
        original_source = bff_main.read_store.dataset_source
        try:
            bff_main.read_store.get_inspiration_graph = lambda artifact_id: None
            bff_main.read_store.dataset_source = lambda dataset: "missing"

            response = client.get(
                "/api/v1/lineage/inspiration/artifact-042",
                headers={"Authorization": OPERATOR_AUTH},
            )
        finally:
            bff_main.read_store.get_inspiration_graph = original_get
            bff_main.read_store.dataset_source = original_source

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["artifact_id"] == "artifact-042"
        assert payload["inspiration_edges"] == []
        assert payload["strategy_tags"] == []
        assert payload["meta"]["surfaces"]["inspiration"] == "unavailable"


def test_ew04_inspiration_graph_returns_404_for_unknown_artifact_even_when_dataset_is_missing() -> None:
    with _seeded_client() as client:
        original_source = bff_main.read_store.dataset_source
        try:
            bff_main.read_store.dataset_source = lambda dataset: "missing"
            response = client.get(
                "/api/v1/lineage/inspiration/artifact-missing",
                headers={"Authorization": OPERATOR_AUTH},
            )
        finally:
            bff_main.read_store.dataset_source = original_source

        assert response.status_code == 404, response.text
        payload = response.json()
        assert payload["error"]["code"] == "RESOURCE_NOT_FOUND"
        assert payload["error"]["message"] == "Artifact not found"
