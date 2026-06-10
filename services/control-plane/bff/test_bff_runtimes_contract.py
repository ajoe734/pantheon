from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


HEADERS = {"Authorization": "Bearer rt-003-operator:operator"}
_TRACKED_ENV = (
    "PANTHEON_BFF_RUNTIME_BINDING_STORE",
    "PANTHEON_RUNTIME_DATA_DIR",
    "PANTHEON_RUNTIME_MANAGER_URL",
    "PANTHEON_INTERNAL_API_URL",
    "PANTHEON_RUNTIME_MANAGER_TOKEN",
)


@contextmanager
def _isolated_runtime_bff(
    runtime_bindings: list[dict[str, Any]] | None,
) -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_env = {key: os.environ.get(key) for key in _TRACKED_ENV}
    with tempfile.TemporaryDirectory(prefix="rt003_bff_") as td:
        root = Path(td)
        runtime_dir = root / "runtime"
        for key in _TRACKED_ENV:
            os.environ.pop(key, None)
        if runtime_bindings is not None:
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "runtime_bindings.json").write_text(
                json.dumps(runtime_bindings, indent=2),
                encoding="utf-8",
            )
            os.environ["PANTHEON_RUNTIME_DATA_DIR"] = str(runtime_dir)

        bff_main.read_store = ReadSurfaceStore(
            str(root / "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _runtime_records() -> list[dict[str, Any]]:
    return [
        {
            "binding_id": "rb-rt003-paper-001",
            "runtime_id": "runtime-rt003-paper-001",
            "capital_pool_id": "pool-rt003-paper",
            "artifact_id": "artifact-rt003-paper",
            "artifact_version": "1.2.3",
            "deployment_mode": "paper",
            "effective_at": "2026-05-16T05:40:00Z",
            "status": "active",
            "plan_id": "plan-rt003-paper",
            "persona_capital_binding_id": "pcb-rt003-paper",
            "metadata": {
                "engine_bridge_repo": "ajoe734/pantheon-lean",
                "engine_bridge_commit": "rt003abc",
            },
        },
        {
            "binding_id": "rb-rt003-paused-001",
            "runtime_id": "runtime-rt003-paused-001",
            "capital_pool_id": "pool-rt003-paper",
            "artifact_id": "artifact-rt003-paused",
            "artifact_version": "1.2.0",
            "deployment_mode": "paper",
            "effective_at": "2026-05-16T05:00:00Z",
            "status": "paused",
            "plan_id": "plan-rt003-paused",
            "persona_capital_binding_id": "pcb-rt003-paper",
        },
    ]


def test_bff_runtimes_list_filters_canonical_runtime_bindings_without_local_fallback() -> None:
    with _isolated_runtime_bff(_runtime_records()) as client:
        response = client.get(
            "/bff/runtimes?status=active&deployment_stage=paper",
            headers=HEADERS,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["meta"]["surfaces"]["runtimes"]["source"] == "canonical"
    assert payload["meta"]["total"] == 1
    assert payload["page_info"] == {"next_page_token": None}
    assert len(payload["items"]) == 1
    runtime = payload["items"][0]
    assert runtime["id"] == "rb-rt003-paper-001"
    assert runtime["binding_id"] == "rb-rt003-paper-001"
    assert runtime["runtime_binding_id"] == "rb-rt003-paper-001"
    assert runtime["runtime_id"] == "runtime-rt003-paper-001"
    assert runtime["deployment_stage"] == "paper"
    assert runtime["deployment_mode"] == "paper"
    assert runtime["artifact_id"] == "artifact-rt003-paper"
    assert runtime["metadata"]["engine_bridge_commit"] == "rt003abc"


def test_bff_runtime_detail_resolves_runtime_id_and_binding_id() -> None:
    with _isolated_runtime_bff(_runtime_records()) as client:
        by_runtime_id = client.get("/bff/runtimes/runtime-rt003-paper-001", headers=HEADERS)
        by_binding_id = client.get("/bff/runtimes/rb-rt003-paper-001", headers=HEADERS)

    assert by_runtime_id.status_code == 200, by_runtime_id.text
    runtime_payload = by_runtime_id.json()
    assert runtime_payload["meta"]["surfaces"]["runtime"]["source"] == "canonical"
    assert runtime_payload["data"]["binding_id"] == "rb-rt003-paper-001"
    assert runtime_payload["data"]["effective_at"] == "2026-05-16T05:40:00Z"

    assert by_binding_id.status_code == 200, by_binding_id.text
    assert by_binding_id.json()["data"]["runtime_id"] == "runtime-rt003-paper-001"


def test_bff_runtime_detail_reports_downstream_unavailable_when_runtime_store_missing() -> None:
    with _isolated_runtime_bff(None) as client:
        list_response = client.get("/bff/runtimes", headers=HEADERS)
        detail_response = client.get("/bff/runtimes/runtime-missing", headers=HEADERS)

    assert list_response.status_code == 200, list_response.text
    assert list_response.json()["items"] == []
    assert list_response.json()["meta"]["surfaces"]["runtimes"]["status"] == "unavailable"

    assert detail_response.status_code == 503, detail_response.text
    assert detail_response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
