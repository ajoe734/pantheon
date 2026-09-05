from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import ReadSurfacePorts


AUTH = {"Authorization": "Bearer operator_001"}


def _local_read_cutoff_data() -> dict[str, Any]:
    return {
        "deployment_plans": {
            "plan-F-042": {
                "id": "plan-F-042",
                "plan_id": "plan-F-042",
                "persona_id": "persona-alpha",
                "binding_id": "binding-alpha",
                "capital_pool_id": "pool-main",
                "deployment_mode": "paper",
                "status": "active",
                "created_at": "2026-05-01T00:00:00Z",
                "updated_at": "2026-05-01T00:00:00Z",
            }
        }
    }


_READ_SOURCE_ENVS = {
    "PANTHEON_DEPLOYMENT_API_URL": "",
    "PANTHEON_DEPLOYMENT_SERVICE_URL": "",
    "PANTHEON_GOVERNANCE_APPROVAL_API_URL": "",
    "PANTHEON_GOVERNANCE_SERVICE_URL": "",
    "PANTHEON_CAPITAL_API_URL": "",
    "PANTHEON_CAPITAL_SERVICE_URL": "",
    "PANTHEON_RUNTIME_MANAGER_URL": "",
    "PANTHEON_INTERNAL_API_URL": "",
    "PANTHEON_GOVERNANCE_DATA_DIR": "",
    "PANTHEON_RUNTIME_DATA_DIR": "",
}


class ReadCutoffWave4TestReadPorts(ReadSurfacePorts):
    def __init__(self, *, allow_local_snapshot_fallback: bool = False, seed_data: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._allow_local_snapshot_fallback = allow_local_snapshot_fallback
        self._data = seed_data if seed_data is not None else (_local_read_cutoff_data() if allow_local_snapshot_fallback else {})

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        if self._allow_local_snapshot_fallback:
            return "local_snapshot"
        return "missing"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        if self._allow_local_snapshot_fallback:
            return {
                "status": "degraded",
                "source": "local_snapshot",
                "snapshot_at": snapshot_at,
                "freshness": "degraded",
                "observed_time": snapshot_at,
                "coverage": 1.0,
                "missing_bindings": False,
                "note": "using local BFF snapshot fallback",
            }
        return {
            "status": "unavailable",
            "source": "missing",
            "snapshot_at": snapshot_at,
            "freshness": "unavailable",
            "observed_time": snapshot_at,
            "coverage": 0.0,
            "missing_bindings": True,
        }

    def list_deployment_plans(self, status: str | None = None, capital_pool_id: str | None = None, include_fixture_pack: bool = True, **kwargs: Any) -> list[dict[str, Any]]:
        if not self._allow_local_snapshot_fallback:
            return []
        ds = self._data.get("deployment_plans", {})
        plans = list(ds.values()) if isinstance(ds, dict) else list(ds)
        if not include_fixture_pack:
            plans = [p for p in plans if "pack" not in str(p.get("id") or p.get("plan_id") or "")]
        return plans

    def get_deployment_plan(self, plan_id: str | None) -> dict[str, Any] | None:
        if not self._allow_local_snapshot_fallback:
            return None
        ds = self._data.get("deployment_plans", {})
        if isinstance(ds, dict):
            return ds.get(str(plan_id or ""))
        return next((p for p in ds if p.get("id") == plan_id or p.get("plan_id") == plan_id), None)


def _install_store(monkeypatch, store: ReadSurfacePorts) -> None:
    monkeypatch.setattr(bff_main, "read_store", store)


def test_prod_catalog_read_does_not_mask_cutoff_with_local_snapshot(monkeypatch) -> None:
    for env_name, value in _READ_SOURCE_ENVS.items():
        monkeypatch.setenv(env_name, value)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")

    _install_store(
        monkeypatch,
        ReadCutoffWave4TestReadPorts(allow_local_snapshot_fallback=False),
    )

    client = TestClient(bff_main.app)
    list_response = client.get("/api/v1/deployment-plans", headers=AUTH)

    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["data"] == []
    surface = list_payload["meta"]["surfaces"]["deployment_plan_list"]
    assert surface["status"] == "unavailable"
    assert surface["source"] == "missing"
    assert list_payload["meta"]["degradation"]["reason"] == "deployment plan list is currently unavailable."

    detail_response = client.get("/api/v1/deployment-plans/plan-F-042", headers=AUTH)
    assert detail_response.status_code == 503
    assert detail_response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_dev_catalog_snapshot_fallback_is_explicitly_degraded(monkeypatch) -> None:
    for env_name, value in _READ_SOURCE_ENVS.items():
        monkeypatch.setenv(env_name, value)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")

    _install_store(
        monkeypatch,
        ReadCutoffWave4TestReadPorts(allow_local_snapshot_fallback=True),
    )

    client = TestClient(bff_main.app)
    response = client.get("/api/v1/deployment-plans", headers=AUTH)

    assert response.status_code == 200
    payload = response.json()
    assert [plan["plan_id"] for plan in payload["data"]] == ["plan-F-042"]
    surface = payload["meta"]["surfaces"]["deployment_plan_list"]
    assert surface["status"] == "degraded"
    assert surface["source"] == "local_snapshot"
    assert "local BFF snapshot fallback" in surface["note"]
