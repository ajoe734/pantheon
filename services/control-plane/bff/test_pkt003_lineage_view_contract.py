#!/usr/bin/env python3
"""HTTP contract tests for PKT-003 Lineage View BFF surfaces."""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

AUTH = "Bearer test-operator:operator,admin"


@contextmanager
def _seeded_client():
    original_store = bff_main.read_store
    bff_main.read_store = create_in_memory_read_surface_ports(
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
    client = TestClient(bff_main.app)
    try:
        yield client
    finally:
        bff_main.read_store = original_store


@contextmanager
def _registry_backed_client():
    original_store = bff_main.read_store
    bff_main.read_store = create_in_memory_read_surface_ports(
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
    client = TestClient(bff_main.app)
    try:
        yield client
    finally:
        bff_main.read_store = original_store


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
