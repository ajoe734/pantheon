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
from command_queue import CommandStore  # noqa: E402
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


# ---------------------------------------------------------------------------
# P0-4: POST /bff/command-confirmations/{token}/confirm
# ---------------------------------------------------------------------------

_CONFIRM_HEADERS = {
    "Authorization": "Bearer test-confirm:operator",
    "X-BFF-Api-Version": "2026-05-07",
    "X-Request-Id": "req-confirm-by-token-test",
    "X-Correlation-Id": "corr-confirm-by-token-test",
}


@contextmanager
def _isolated_confirm_bff() -> Iterator[TestClient]:
    original_command_store = bff_main.command_store
    original_idempotency = dict(bff_main._GOV_BFF_IDEMPOTENCY)
    original_audit_events = list(bff_main._sse_buffers["audit"])
    with tempfile.TemporaryDirectory(prefix="bff_confirm_token_") as td:
        store_path = Path(td) / "commands.jsonl"
        store_path.touch()
        bff_main.command_store = CommandStore(str(store_path))
        bff_main._GOV_BFF_IDEMPOTENCY.clear()
        bff_main._sse_buffers["audit"].clear()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.command_store = original_command_store
            bff_main._GOV_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.update(original_idempotency)
            bff_main._sse_buffers["audit"].clear()
            bff_main._sse_buffers["audit"].extend(original_audit_events)


def _create_confirm_token(client: TestClient, token_id: str) -> None:
    resp = client.post(
        "/bff/confirm-tokens",
        headers={**_CONFIRM_HEADERS, "Idempotency-Key": f"create-token-{token_id}"},
        json={"tokenId": token_id},
    )
    assert resp.status_code == 201, f"Failed to seed token {token_id}: {resp.text}"


def test_post_bff_confirm_by_token_unknown_returns_typed_404() -> None:
    """Acceptance gate: unknown token returns typed 404, NOT generic 'Not Found'."""
    with _isolated_confirm_bff() as client:
        response = client.post(
            "/bff/command-confirmations/token-dev/confirm",
            headers={**_CONFIRM_HEADERS, "Idempotency-Key": "confirm-by-token-unknown-001"},
            json={"command_id": "cmd-test-unknown"},
        )

    assert response.status_code == 404, response.text
    error = response.json()["error"]
    assert error["code"] == "RESOURCE_NOT_FOUND"
    assert error["message"] != "Not Found"
    assert error["details"]["precondition_failed"] == "confirm_token_not_found"


def test_post_bff_confirm_by_token_mismatched_body_token_returns_412() -> None:
    with _isolated_confirm_bff() as client:
        response = client.post(
            "/bff/command-confirmations/path-token-abc/confirm",
            headers={**_CONFIRM_HEADERS, "Idempotency-Key": "confirm-by-token-mismatch-001"},
            json={"confirm_token": "different-token-xyz", "command_id": "cmd-test-mismatch"},
        )

    assert response.status_code == 412, response.text
    error = response.json()["error"]
    assert error["code"] == "PRECONDITION_FAILED"
    assert error["details"]["precondition_failed"] == "confirm_token_invalid"


def test_post_bff_confirm_by_token_dry_run_returns_200_no_side_effects() -> None:
    with _isolated_confirm_bff() as client:
        _create_confirm_token(client, "token-dry-run-p04")
        response = client.post(
            "/bff/command-confirmations/token-dry-run-p04/confirm",
            headers={
                **_CONFIRM_HEADERS,
                "Idempotency-Key": "confirm-by-token-dry-run-001",
                "X-Dry-Run": "1",
            },
            json={"command_id": "cmd-test-dry-run"},
        )
        audit_events = list(bff_main._sse_buffers["audit"])

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["status"] == "accepted"
    assert payload["data"]["commandId"] == "cmd-test-dry-run"
    assert payload["meta"]["dryRun"] is True
    assert payload["meta"]["evidenceKind"] == "command.confirm"
    assert len(audit_events) == 0


def test_post_bff_confirm_by_token_valid_returns_202_and_publishes_audit() -> None:
    with _isolated_confirm_bff() as client:
        _create_confirm_token(client, "token-valid-p04")
        response = client.post(
            "/bff/command-confirmations/token-valid-p04/confirm",
            headers={**_CONFIRM_HEADERS, "Idempotency-Key": "confirm-by-token-valid-001"},
            json={"command_id": "cmd-test-valid-001"},
        )
        replay = client.post(
            "/bff/command-confirmations/token-valid-p04/confirm",
            headers={**_CONFIRM_HEADERS, "Idempotency-Key": "confirm-by-token-valid-001"},
            json={"command_id": "cmd-test-valid-001"},
        )
        audit_events = list(bff_main._sse_buffers["audit"])

    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["data"]["status"] == "accepted"
    assert payload["data"]["commandId"] == "cmd-test-valid-001"
    assert payload["data"]["confirmed_at"]
    assert payload["meta"]["dryRun"] is False
    assert payload["meta"]["evidenceKind"] == "command.confirm"
    assert payload["meta"]["correlationId"] == "corr-confirm-by-token-test"

    assert replay.status_code == 202, replay.text
    assert replay.json()["data"] == payload["data"]

    event_types = [event["type"] for _event_id, event in audit_events]
    assert "command.confirm" in event_types
