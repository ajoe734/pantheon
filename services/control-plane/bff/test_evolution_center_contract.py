#!/usr/bin/env python3
"""HTTP contract tests for PKT-003 Evolution Center BFF surfaces."""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

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


def test_evolution_decisions_list_contract():
    with _seeded_client() as client:
        resp = client.get(
            "/api/v1/evolution-decisions?page_token=0&page_size=20",
            headers={"Authorization": AUTH},
        )
        assert resp.status_code == 200

        body = resp.json()
        assert "data" not in body
        assert "items" in body
        assert "page_info" in body
        assert body["page_info"]["next_page_token"] is None
        assert "snapshot_at" in body["meta"]
        assert len(body["items"]) >= 1

        item = body["items"][0]
        for key in ["id", "action_type", "risk_level", "status", "incident_ref", "artifact_id"]:
            assert key in item


def test_evolution_decision_detail_contract():
    with _seeded_client() as client:
        resp = client.get(
            "/api/v1/evolution-decisions/evo-dec-001",
            headers={"Authorization": AUTH},
        )
        assert resp.status_code == 200

        body = resp.json()
        assert "data" not in body
        for key in [
            "id",
            "action_type",
            "risk_level",
            "status",
            "incident_ref",
            "artifact_id",
            "created_at",
            "updated_at",
            "notes",
            "meta",
        ]:
            assert key in body
        assert "snapshot_at" in body["meta"]
        assert body["updated_at"] == "2026-04-11T09:00:00Z"
        assert body["notes"] == "Approved for retrain after promotion gate timeout root cause confirmed."


def test_freeze_orders_contract():
    with _seeded_client() as client:
        resp = client.get("/api/v1/freeze-orders", headers={"Authorization": AUTH})
        assert resp.status_code == 200

        body = resp.json()
        assert "data" not in body
        assert "items" in body
        assert "snapshot_at" in body["meta"]
        assert len(body["items"]) >= 1

        item = body["items"][0]
        for key in ["freeze_order_id", "status", "scope", "issued_at"]:
            assert key in item
        assert item["freeze_order_id"] == "fo-001"
        assert item["issued_at"] == "2026-04-10T14:35:00Z"


def test_rollbacks_contract():
    with _seeded_client() as client:
        resp = client.get("/api/v1/rollbacks", headers={"Authorization": AUTH})
        assert resp.status_code == 200

        body = resp.json()
        assert "data" not in body
        assert "items" in body
        assert "snapshot_at" in body["meta"]
        assert len(body["items"]) >= 1

        item = body["items"][0]
        for key in ["rollback_id", "action_type", "runtime_id", "executed_at"]:
            assert key in item
        assert item["rollback_id"] == "rb-001"
        assert item["executed_at"] == "2026-04-10T14:45:00Z"
