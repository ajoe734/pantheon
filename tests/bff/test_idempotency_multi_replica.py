from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
BFF_DIR = ROOT / "services" / "control-plane" / "bff"
BFF_MAIN = BFF_DIR / "main.py"

AUTH_HEADERS = {
    "Authorization": "Bearer op-ha009:operator,approver,admin,reviewer:mfa",
    "X-MFA-Token": "000000",
}


def bff_env(data_dir: Path) -> dict[str, str]:
    return {
        "BFF_DATA_DIR": str(data_dir),
        "PANTHEON_BFF_AUTH_STUB": "true",
        "PANTHEON_BFF_AUTH_MODE": "permissive",
        "PANTHEON_ENV": "dev",
        "PANTHEON_DEPLOYMENT_STAGE": "dev",
        "PANTHEON_BFF_JWT_SECRET": "ha-009-v2-test-secret",
    }


@contextmanager
def patched_env(**values: str) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


async def _noop_process_command(_command_id: str) -> None:
    return None


def load_bff_replica(name: str, data_dir: Path) -> ModuleType:
    if str(BFF_DIR) not in sys.path:
        sys.path.insert(0, str(BFF_DIR))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    with patched_env(**bff_env(data_dir)):
        module_name = f"_ha009_bff_replica_{name}"
        spec = importlib.util.spec_from_file_location(module_name, BFF_MAIN)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        module._process_command_stub = _noop_process_command
        return module


def command_payload(reason: str) -> dict[str, object]:
    return {
        "command": "PauseExecution",
        "target": {"type": "Runtime", "id": "runtime-ha-009-v2"},
        "params": {"pause_new_entries": True, "cancel_open_orders": False},
        "audit_context": {
            "reason": reason,
            "timestamp": "2026-05-20T00:00:00Z",
        },
    }


def receipt_id(payload: dict[str, object]) -> str:
    data = payload.get("data")
    assert isinstance(data, dict)
    return str(data.get("receipt_id") or data.get("command_id") or data.get("commandId") or "")


def bff_error_code(payload: dict[str, object]) -> str:
    detail = payload.get("detail")
    assert isinstance(detail, dict)
    error = detail.get("error")
    assert isinstance(error, dict)
    return str(error.get("code") or "")


def test_idempotency_key_replays_same_response_across_replicas() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "bff-data"
        with patched_env(**bff_env(data_dir)):
            replicas = [load_bff_replica(str(index), data_dir) for index in range(3)]
            clients = [TestClient(replica.app) for replica in replicas]
            idempotency_key = "ha-009-v2-replay-key"
            payload = command_payload("HA-009-V2 first command")

            first = clients[0].post(
                "/bff/v1/commands",
                headers={
                    **AUTH_HEADERS,
                    "Idempotency-Key": idempotency_key,
                    "X-Trace-Id": "trace-ha-009-v2",
                    "X-Correlation-Id": "corr-ha-009-v2",
                    "X-Request-Id": "req-ha-009-v2",
                },
                json=payload,
            )
            replay = clients[1].post(
                "/bff/v1/commands",
                headers={**AUTH_HEADERS, "Idempotency-Key": idempotency_key},
                json=payload,
            )

            assert first.status_code == 202, first.text
            assert replay.status_code == 202, replay.text
            assert replay.json() == first.json()
            first_receipt = receipt_id(first.json())
            assert first_receipt

            status = clients[2].get(f"/api/v1/operator/commands/{first_receipt}", headers=AUTH_HEADERS)
            assert status.status_code == 200, status.text
            body = status.json()
            assert body["command_id"] == first_receipt
            foundation = body["audit"]["foundation"]
            assert foundation["idempotency_record"]["idempotency_key"] == idempotency_key
            assert foundation["idempotency_record"]["request_hash"]

            records = replicas[2].command_store._get_all_commands()
            assert len(records) == 1
            assert records[0]["command_id"] == first_receipt
            assert records[0]["foundation"]["idempotency_record"]["status"] == "succeeded"


def test_changed_payload_with_same_key_conflicts_across_replicas() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "bff-data"
        with patched_env(**bff_env(data_dir)):
            replicas = [load_bff_replica(str(index), data_dir) for index in range(3)]
            clients = [TestClient(replica.app) for replica in replicas]
            idempotency_key = "ha-009-v2-conflict-key"
            headers = {**AUTH_HEADERS, "Idempotency-Key": idempotency_key}

            first = clients[0].post(
                "/bff/v1/commands",
                headers=headers,
                json=command_payload("HA-009-V2 first command"),
            )
            conflict = clients[2].post(
                "/bff/v1/commands",
                headers=headers,
                json=command_payload("HA-009-V2 changed payload must conflict"),
            )

            assert first.status_code == 202, first.text
            assert conflict.status_code == 409, conflict.text
            assert bff_error_code(conflict.json()) == "IDEMPOTENCY_CONFLICT"

            records = replicas[1].command_store._get_all_commands()
            assert len(records) == 1
            assert records[0]["command_id"] == receipt_id(first.json())
            assert records[0]["foundation"]["idempotency_record"]["idempotency_key"] == idempotency_key
