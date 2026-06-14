from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore, _default_read_data


AUTH = {"Authorization": "Bearer operator_001"}


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


def _snapshot_store_path(root: str) -> str:
    path = Path(root) / "read_surfaces.json"
    path.write_text(
        json.dumps(_default_read_data(), indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return str(path)


def _install_store(monkeypatch, store: ReadSurfaceStore) -> None:
    monkeypatch.setattr(bff_main, "read_store", store)


def test_prod_catalog_read_does_not_mask_cutoff_with_local_snapshot(monkeypatch) -> None:
    for env_name, value in _READ_SOURCE_ENVS.items():
        monkeypatch.setenv(env_name, value)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")

    with tempfile.TemporaryDirectory() as td:
        store_path = _snapshot_store_path(td)
        _install_store(
            monkeypatch,
            ReadSurfaceStore(store_path, allow_local_snapshot_fallback=False),
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

    with tempfile.TemporaryDirectory() as td:
        store_path = _snapshot_store_path(td)
        _install_store(
            monkeypatch,
            ReadSurfaceStore(store_path, allow_local_snapshot_fallback=True),
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
