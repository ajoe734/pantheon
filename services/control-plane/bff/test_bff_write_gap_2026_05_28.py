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


AGORA_BASE_HEADERS = {
    "Authorization": "Bearer analyst-agora:analyst",
    "X-BFF-Api-Version": "2026-05-07",
    "X-Request-Id": "req-write-gap-agora-signal",
}
AGORA_READ_HEADERS = {
    **AGORA_BASE_HEADERS,
    "Authorization": "Bearer op-agora:operator",
}
RUNTIME_HEADERS = {
    "Authorization": "Bearer bff-write-gap-runtime:operator",
    "Idempotency-Key": "bff-write-gap-runtime-create-001",
}
_TRACKED_RUNTIME_ENV = (
    "PANTHEON_BFF_RUNTIME_BINDING_STORE",
    "PANTHEON_RUNTIME_DATA_DIR",
    "PANTHEON_RUNTIME_MANAGER_URL",
    "PANTHEON_INTERNAL_API_URL",
    "PANTHEON_RUNTIME_MANAGER_TOKEN",
)


def _seed_agora_read_store(path: Path) -> ReadSurfaceStore:
    path.write_text(
        json.dumps(
            {
                "agora_signals": {},
                "agora_audit_events": {},
                "agora_signal_feedback": {},
            }
        ),
        encoding="utf-8",
    )
    return ReadSurfaceStore(str(path), allow_local_snapshot_fallback=True)


@contextmanager
def _isolated_agora_bff() -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_idempotency = dict(bff_main._AGORA_CORE_BFF_IDEMPOTENCY)
    original_signal_events = list(bff_main._sse_buffers["signal"])
    original_inbox_events = list(bff_main._sse_buffers["inbox"])
    with tempfile.TemporaryDirectory(prefix="bff_write_gap_agora_") as td:
        store_path = Path(td) / "read_surfaces.json"
        bff_main.read_store = _seed_agora_read_store(store_path)
        bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
        bff_main._sse_buffers["signal"].clear()
        bff_main._sse_buffers["inbox"].clear()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
            bff_main._AGORA_CORE_BFF_IDEMPOTENCY.update(original_idempotency)
            bff_main._sse_buffers["signal"].clear()
            bff_main._sse_buffers["signal"].extend(original_signal_events)
            bff_main._sse_buffers["inbox"].clear()
            bff_main._sse_buffers["inbox"].extend(original_inbox_events)


@contextmanager
def _isolated_runtime_bff(runtime_bindings: list[dict[str, Any]]) -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_env = {key: os.environ.get(key) for key in _TRACKED_RUNTIME_ENV}
    original_idempotency = dict(bff_main._GOV_BFF_IDEMPOTENCY)
    original_runtime_events = list(bff_main._sse_buffers["runtime"])
    with tempfile.TemporaryDirectory(prefix="bff_write_gap_runtime_") as td:
        root = Path(td)
        runtime_dir = root / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        for key in _TRACKED_RUNTIME_ENV:
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


def test_bff_agora_signal_create_returns_201_persists_and_replays() -> None:
    with _isolated_agora_bff() as client:
        body = {
            "id": "sig-write-gap-001",
            "title": "Opening auction momentum",
            "body": "Review a new opening auction momentum signal.",
            "market": "US",
            "tags": ["auction", "momentum"],
            "linkedPersonaIds": ["persona-paper-owner"],
            "linkedStrategyIds": ["strategy-alpha"],
            "severity": "warn",
        }
        headers = {
            **AGORA_BASE_HEADERS,
            "Idempotency-Key": "agora-signal-create-001",
            "X-Correlation-Id": "corr-agora-signal-create-001",
        }

        response = client.post("/bff/agora/signals", headers=headers, json=body)
        replay = client.post("/bff/agora/signals", headers=headers, json=body)

        assert response.status_code == 201, response.text
        assert replay.status_code == 201, replay.text
        assert response.headers["X-Correlation-Id"] == "corr-agora-signal-create-001"
        payload = response.json()
        assert payload["data"]["id"] == "sig-write-gap-001"
        assert payload["data"]["status"] == "open"
        assert payload["data"]["reviewStatus"] == "pending_trader_review"
        assert payload["data"]["severity"] == "warn"
        assert payload["meta"]["dryRun"] is False
        assert payload["meta"]["audit"]["evidenceKind"] == "agora.signal.create"
        assert replay.json()["data"]["id"] == payload["data"]["id"]

        detail = client.get("/bff/agora/signals/sig-write-gap-001", headers=AGORA_READ_HEADERS)
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["title"] == "Opening auction momentum"
        assert len(bff_main._sse_buffers["signal"]) == 1
        assert len(bff_main._sse_buffers["inbox"]) == 1


def test_bff_agora_signal_create_dry_run_returns_200_without_persistence() -> None:
    with _isolated_agora_bff() as client:
        body = {
            "id": "sig-write-gap-dry-run",
            "title": "Dry-run signal",
            "body": "Validate the signal create shape without persisting.",
        }
        response = client.post(
            "/bff/agora/signals",
            headers={
                **AGORA_BASE_HEADERS,
                "Idempotency-Key": "agora-signal-dry-run-001",
                "X-Correlation-Id": "corr-agora-signal-dry-run-001",
                "X-Dry-Run": "1",
            },
            json=body,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["id"] == "sig-write-gap-dry-run"
        assert payload["meta"]["dryRun"] is True
        detail = client.get("/bff/agora/signals/sig-write-gap-dry-run", headers=AGORA_READ_HEADERS)
        assert detail.status_code == 404, detail.text
        assert len(bff_main._sse_buffers["signal"]) == 0
        assert len(bff_main._sse_buffers["inbox"]) == 0


def test_bff_agora_signal_create_rejects_invalid_payload() -> None:
    with _isolated_agora_bff() as client:
        response = client.post(
            "/bff/agora/signals",
            headers={**AGORA_BASE_HEADERS, "Idempotency-Key": "agora-signal-invalid-001"},
            json={"title": "Missing body", "severity": "critical"},
        )

        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_FAILED"
        assert error["details"]["precondition_failed"] == "body"


def test_post_bff_runtimes_creates_stopped_runtime_and_replays_idempotently() -> None:
    with _isolated_runtime_bff([]) as client:
        response = client.post("/bff/runtimes", json=_runtime_create_payload(), headers=RUNTIME_HEADERS)
        replay = client.post("/bff/runtimes", json=_runtime_create_payload(), headers=RUNTIME_HEADERS)
        runtime_id = response.json()["data"]["id"]
        detail = client.get(
            f"/bff/runtimes/{runtime_id}",
            headers={"Authorization": RUNTIME_HEADERS["Authorization"]},
        )
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
    with _isolated_runtime_bff([existing]) as client:
        response = client.post(
            "/bff/runtimes",
            json=_runtime_create_payload(binding_id="binding-runtime-create-occupied"),
            headers={**RUNTIME_HEADERS, "Idempotency-Key": "bff-write-gap-runtime-conflict-001"},
        )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "RESOURCE_CONFLICT"


def test_post_bff_runtimes_validates_runtime_kind() -> None:
    payload = _runtime_create_payload()
    payload["runtime_kind"] = "sandbox"
    with _isolated_runtime_bff([]) as client:
        response = client.post(
            "/bff/runtimes",
            json=payload,
            headers={**RUNTIME_HEADERS, "Idempotency-Key": "bff-write-gap-runtime-validation-001"},
        )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


# --------------------------------------------------------------------------- #
# P0-6 — POST /api/v1/deployment-plans (persona onboarding wizard step 3)
# --------------------------------------------------------------------------- #

DEPLOYMENT_PLAN_HEADERS = {
    "Authorization": "Bearer bff-write-gap-dp:operator",
    "Idempotency-Key": "bff-write-gap-dp-create-001",
    "X-Correlation-Id": "corr-dp-create-001",
    "X-Request-Id": "req-dp-create-001",
}


def _deployment_plan_seed(registry_entries: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "deployment_plans": {},
        "bindings": {
            "binding-dp-001": {
                "id": "binding-dp-001",
                "binding_id": "binding-dp-001",
                "persona_id": "persona-dp-001",
                "capital_pool_id": "pool-dp-001",
                "role": "paper_owner",
            }
        },
        "registry_entries": registry_entries or {},
    }


@contextmanager
def _isolated_deployment_plan_bff(
    registry_entries: dict[str, Any] | None = None,
) -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_idempotency = dict(bff_main._GOV_BFF_IDEMPOTENCY)
    original_audit_events = list(bff_main._sse_buffers["audit"])
    with tempfile.TemporaryDirectory(prefix="bff_write_gap_deployment_plan_") as td:
        store_path = Path(td) / "read_surfaces.json"
        store_path.write_text(
            json.dumps(_deployment_plan_seed(registry_entries)), encoding="utf-8"
        )
        bff_main.read_store = ReadSurfaceStore(
            str(store_path), allow_local_snapshot_fallback=True
        )
        bff_main._GOV_BFF_IDEMPOTENCY.clear()
        bff_main._sse_buffers["audit"].clear()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            bff_main._GOV_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.update(original_idempotency)
            bff_main._sse_buffers["audit"].clear()
            bff_main._sse_buffers["audit"].extend(original_audit_events)


def _deployment_plan_create_payload(plan_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "binding_id": "binding-dp-001",
        "artifact_id": "artifact-dp-001",
        "deployment_mode": "paper",
        "capital_pool_id": "pool-dp-001",
        "params": {"max_notional": 100000},
        "locked": False,
    }
    if plan_id:
        body["plan_id"] = plan_id
    return body


def test_post_deployment_plan_creates_pending_approval_and_replays() -> None:
    with _isolated_deployment_plan_bff() as client:
        body = _deployment_plan_create_payload(plan_id="plan-dp-write-gap-001")
        response = client.post(
            "/api/v1/deployment-plans", headers=DEPLOYMENT_PLAN_HEADERS, json=body
        )
        replay = client.post(
            "/api/v1/deployment-plans", headers=DEPLOYMENT_PLAN_HEADERS, json=body
        )
        detail = client.get(
            "/api/v1/deployment-plans/plan-dp-write-gap-001",
            headers={"Authorization": DEPLOYMENT_PLAN_HEADERS["Authorization"]},
        )
        listing = client.get(
            "/api/v1/deployment-plans",
            headers={"Authorization": DEPLOYMENT_PLAN_HEADERS["Authorization"]},
        )
        event_types = [event["type"] for _event_id, event in bff_main._sse_buffers["audit"]]
        events = [event for _event_id, event in bff_main._sse_buffers["audit"]]

    assert response.status_code == 201, response.text
    payload = response.json()
    data = payload["data"]
    assert data["id"] == "plan-dp-write-gap-001"
    assert data["binding_id"] == "binding-dp-001"
    assert data["artifact_id"] == "artifact-dp-001"
    assert data["deployment_mode"] == "paper"
    assert data["status"] == "pending_approval"
    assert data["capital_pool_id"] == "pool-dp-001"
    assert data["locked"] is False
    assert data["created_at"]
    assert payload["meta"]["dryRun"] is False
    assert payload["meta"]["evidenceKind"] == "deployment_plan.create"
    assert payload["meta"]["correlationId"] == "corr-dp-create-001"
    assert response.headers["X-Correlation-Id"] == "corr-dp-create-001"

    assert replay.status_code == 201, replay.text
    assert replay.json()["data"] == data

    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["id"] == "plan-dp-write-gap-001"
    assert listing.status_code == 200, listing.text
    assert any(p["id"] == "plan-dp-write-gap-001" for p in listing.json()["data"])

    assert event_types == ["deployment-plan.created"]
    assert events[0]["data"]["persona_id"] == "persona-dp-001"
    assert events[0]["data"]["status"] == "pending_approval"


def test_post_deployment_plan_honors_locked_flag() -> None:
    payload = _deployment_plan_create_payload(plan_id="plan-dp-locked-001")
    payload["locked"] = True
    payload["deployment_mode"] = "live"
    with _isolated_deployment_plan_bff() as client:
        response = client.post(
            "/api/v1/deployment-plans",
            headers={**DEPLOYMENT_PLAN_HEADERS, "Idempotency-Key": "bff-write-gap-dp-locked-001"},
            json=payload,
        )
        detail = client.get(
            "/api/v1/deployment-plans/plan-dp-locked-001",
            headers={"Authorization": DEPLOYMENT_PLAN_HEADERS["Authorization"]},
        )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["locked"] is True
    assert data["deployment_mode"] == "live"
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["locked"] is True


def test_post_deployment_plan_dry_run_returns_200_without_persistence() -> None:
    with _isolated_deployment_plan_bff() as client:
        response = client.post(
            "/api/v1/deployment-plans",
            headers={
                **DEPLOYMENT_PLAN_HEADERS,
                "X-Dry-Run": "1",
                "Idempotency-Key": "bff-write-gap-dp-dry-run-001",
            },
            json=_deployment_plan_create_payload(plan_id="plan-dp-dry-run-001"),
        )
        detail = client.get(
            "/api/v1/deployment-plans/plan-dp-dry-run-001",
            headers={"Authorization": DEPLOYMENT_PLAN_HEADERS["Authorization"]},
        )
        audit_events = list(bff_main._sse_buffers["audit"])

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["id"] == "plan-dp-dry-run-001"
    assert payload["data"]["status"] == "pending_approval"
    assert payload["meta"]["dryRun"] is True
    assert detail.status_code == 404, detail.text
    assert len(audit_events) == 0


def test_post_deployment_plan_validates_deployment_mode() -> None:
    payload = _deployment_plan_create_payload()
    payload["deployment_mode"] = "shadow"
    with _isolated_deployment_plan_bff() as client:
        response = client.post(
            "/api/v1/deployment-plans",
            headers={**DEPLOYMENT_PLAN_HEADERS, "Idempotency-Key": "bff-write-gap-dp-mode-001"},
            json=payload,
        )

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["details"]["precondition_failed"] == "deployment_mode"


def test_post_deployment_plan_requires_binding_id() -> None:
    payload = _deployment_plan_create_payload()
    payload.pop("binding_id")
    with _isolated_deployment_plan_bff() as client:
        response = client.post(
            "/api/v1/deployment-plans",
            headers={**DEPLOYMENT_PLAN_HEADERS, "Idempotency-Key": "bff-write-gap-dp-missing-001"},
            json=payload,
        )

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["details"]["precondition_failed"] == "binding_id"


def test_post_deployment_plan_rejects_unapproved_artifact() -> None:
    registry = {
        "artifact-dp-001": {
            "id": "artifact-dp-001",
            "artifact_id": "artifact-dp-001",
            "status": "draft",
        }
    }
    with _isolated_deployment_plan_bff(registry_entries=registry) as client:
        response = client.post(
            "/api/v1/deployment-plans",
            headers={**DEPLOYMENT_PLAN_HEADERS, "Idempotency-Key": "bff-write-gap-dp-unapproved-001"},
            json=_deployment_plan_create_payload(),
        )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "RESOURCE_CONFLICT"


def test_post_deployment_plan_idempotency_conflict_on_changed_payload() -> None:
    with _isolated_deployment_plan_bff() as client:
        first = client.post(
            "/api/v1/deployment-plans",
            headers=DEPLOYMENT_PLAN_HEADERS,
            json=_deployment_plan_create_payload(plan_id="plan-dp-conflict-001"),
        )
        changed = _deployment_plan_create_payload(plan_id="plan-dp-conflict-002")
        conflict = client.post(
            "/api/v1/deployment-plans",
            headers=DEPLOYMENT_PLAN_HEADERS,
            json=changed,
        )

    assert first.status_code == 201, first.text
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
