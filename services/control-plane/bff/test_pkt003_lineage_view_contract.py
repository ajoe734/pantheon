#!/usr/bin/env python3
"""HTTP contract tests for PKT-003 Lineage View BFF surfaces."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


AUTH = "Bearer test-operator:operator,admin"


@contextmanager
def _seeded_client():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store


@contextmanager
def _registry_backed_client():
    tracked_env = {
        "PANTHEON_BFF_LINEAGE_EDGE_STORE": os.environ.get("PANTHEON_BFF_LINEAGE_EDGE_STORE"),
        "PANTHEON_REGISTRY_DATA_DIR": os.environ.get("PANTHEON_REGISTRY_DATA_DIR"),
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lineage_store = root / "lineage_edges.json"
        registry_dir = root / "registry"
        registry_dir.mkdir(parents=True, exist_ok=True)

        lineage_store.write_text(
            json.dumps(
                [
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
                indent=2,
            ),
            encoding="utf-8",
        )
        (registry_dir / "registry_entries.json").write_text(
            json.dumps(
                {
                    "artifact-alpha": {
                        "artifact_id": "artifact-alpha",
                        "registry_id": "artifact-alpha",
                        "artifact_type": "strategy_bundle",
                        "version": "v1.0.0",
                    },
                    "artifact-beta": {
                        "artifact_id": "artifact-beta",
                        "registry_id": "artifact-beta",
                        "artifact_type": "strategy_bundle",
                        "version": "v1.1.0",
                    },
                    "artifact-gamma": {
                        "artifact_id": "artifact-gamma",
                        "registry_id": "artifact-gamma",
                        "artifact_type": "strategy_bundle",
                        "version": "v1.2.0",
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        os.environ["PANTHEON_BFF_LINEAGE_EDGE_STORE"] = str(lineage_store)
        os.environ["PANTHEON_REGISTRY_DATA_DIR"] = str(registry_dir)

        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


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
        assert set(item) == {"artifact_id", "edge_count", "last_edge_at"}
        assert item["artifact_id"]
        assert item["edge_count"] >= 1


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
