#!/usr/bin/env python3
"""HTTP contract tests for PKT-003 Evolution Center BFF surfaces."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.evolution.router import create_evolution_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


AUTH = "Bearer test-operator:operator,admin"

_DATA_PATH = Path(__file__).parent / "data" / "read_surfaces.json"
with open(_DATA_PATH, "r", encoding="utf-8") as _f:
    _RAW_DATA = json.load(_f)


@contextmanager
def _seeded_client():
    ports = create_in_memory_read_surface_ports(
        lifecycle_telemetry_governance_kwargs={
            "evolution_decisions": _RAW_DATA.get("evolution_decisions", {}),
            "freeze_orders": _RAW_DATA.get("freeze_orders", {}),
            "all_rollbacks": _RAW_DATA.get("all_rollbacks", []),
        }
    )
    ports.list_evolution_decisions = ports.lifecycle_telemetry_governance.list_evolution_decisions
    ports.get_evolution_decision_by_id = ports.lifecycle_telemetry_governance.get_evolution_decision_by_id
    ports.list_freeze_orders = ports.lifecycle_telemetry_governance.list_freeze_orders
    ports.list_all_rollbacks = ports.lifecycle_telemetry_governance.list_all_rollbacks

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

    app = FastAPI(title="Evolution Center Contract")
    router = create_evolution_router(
        read_surface=ports,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_read_role,
    )
    app.include_router(router)
    client = TestClient(app)
    yield client


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
