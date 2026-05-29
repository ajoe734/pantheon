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


HEADERS = {
    "Authorization": "Bearer bff-write-gap-runtime:operator",
    "Idempotency-Key": "bff-write-gap-runtime-create-001",
}
_TRACKED_ENV = (
    "PANTHEON_BFF_RUNTIME_BINDING_STORE",
    "PANTHEON_RUNTIME_DATA_DIR",
    "PANTHEON_RUNTIME_MANAGER_URL",
    "PANTHEON_INTERNAL_API_URL",
    "PANTHEON_RUNTIME_MANAGER_TOKEN",
)


@contextmanager
def _isolated_bff(runtime_bindings: list[dict[str, Any]]) -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_env = {key: os.environ.get(key) for key in _TRACKED_ENV}
    original_idempotency = dict(bff_main._GOV_BFF_IDEMPOTENCY)
    original_runtime_events = list(bff_main._sse_buffers["runtime"])
    with tempfile.TemporaryDirectory(prefix="bff_write_gap_runtime_") as td:
        root = Path(td)
        runtime_dir = root / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        for key in _TRACKED_ENV:
            os.environ.pop(key, None)
        (runtime_dir / "runtime_bindings.json").write_text(
            json.dumps(runtime_bindings, indent=2),
            encoding="utf-8",
        )
        os.environ["PANTHEON_RUNTIME_DATA_DIR"] = str(runtime_dir)
        bff_main._GOV_BFF_IDEMPOTENCY.clear()
        bff_main._sse_buffers["runtime"].clear()
        bff_main.read_store = ReadSurfaceStore(
            str(root / "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            bff_main._GOV_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.update(original_idempotency)
            bff_main._sse_buffers["runtime"].clear()
            bff_main._sse_buffers["runtime"].extend(original_runtime_events)
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _runtime_create_payload(binding_id: str = "binding-runtime-create-001") -> dict[str, Any]:
    return {
        "name": "Paper Runtime 001",
        "persona_id": "persona-runtime-create-001",
        "binding_id": binding_id,
        "deployment_plan_id": "plan-runtime-create-001",
        "runtime_kind": "paper",
        "params": {"broker": "simulated"},
    }


def test_post_bff_runtimes_creates_stopped_runtime_and_replays_idempotently() -> None:
    with _isolated_bff([]) as client:
        response = client.post("/bff/runtimes", json=_runtime_create_payload(), headers=HEADERS)
        replay = client.post("/bff/runtimes", json=_runtime_create_payload(), headers=HEADERS)
        runtime_id = response.json()["data"]["id"]
        detail = client.get(f"/bff/runtimes/{runtime_id}", headers={"Authorization": HEADERS["Authorization"]})
        event_types = [event["type"] for _event_id, event in bff_main._sse_buffers["runtime"]]

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["data"]["name"] == "Paper Runtime 001"
    assert payload["data"]["state"] == "stopped"
    assert payload["data"]["persona_id"] == "persona-runtime-create-001"
    assert payload["data"]["binding_id"] == "binding-runtime-create-001"
    assert payload["data"]["deployment_plan_id"] == "plan-runtime-create-001"
    assert payload["data"]["runtime_kind"] == "paper"
    assert payload["data"]["created_at"]
    assert payload["meta"]["evidenceKind"] == "runtime.create"

    assert replay.status_code == 201, replay.text
    assert replay.json()["data"] == payload["data"]
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["runtime_id"] == runtime_id
    assert detail.json()["data"]["status"] == "stopped"

    assert event_types == ["runtime.created", "management.runtime-status"]


def test_post_bff_runtimes_rejects_binding_that_already_has_runtime() -> None:
    existing = {
        "binding_id": "binding-runtime-create-occupied",
        "runtime_id": "runtime-existing-001",
        "status": "active",
        "deployment_mode": "paper",
        "plan_id": "plan-existing-001",
        "persona_capital_binding_id": "binding-runtime-create-occupied",
    }
    with _isolated_bff([existing]) as client:
        response = client.post(
            "/bff/runtimes",
            json=_runtime_create_payload(binding_id="binding-runtime-create-occupied"),
            headers={**HEADERS, "Idempotency-Key": "bff-write-gap-runtime-conflict-001"},
        )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "RESOURCE_CONFLICT"


def test_post_bff_runtimes_validates_runtime_kind() -> None:
    payload = _runtime_create_payload()
    payload["runtime_kind"] = "sandbox"
    with _isolated_bff([]) as client:
        response = client.post(
            "/bff/runtimes",
            json=payload,
            headers={**HEADERS, "Idempotency-Key": "bff-write-gap-runtime-validation-001"},
        )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
