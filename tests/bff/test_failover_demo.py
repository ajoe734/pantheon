from __future__ import annotations

import importlib.util
import json
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
README = ROOT / "support" / "evidence" / "bff-ha-failover-demo" / "README.md"
SLA_TARGETS = ROOT / "services" / "bff" / "ha" / "sla_targets.json"
DEMO_SCRIPT = ROOT / "scripts" / "bff" / "failover_demo.sh"

AUTH_HEADERS = {
    "Authorization": "Bearer op-ha010:operator,approver,admin,reviewer:mfa",
    "X-MFA-Token": "000000",
}


def bff_env(data_dir: Path) -> dict[str, str]:
    return {
        "BFF_DATA_DIR": str(data_dir),
        "PANTHEON_BFF_AUTH_STUB": "true",
        "PANTHEON_BFF_AUTH_MODE": "permissive",
        "PANTHEON_ENV": "dev",
        "PANTHEON_DEPLOYMENT_STAGE": "dev",
        "PANTHEON_BFF_JWT_SECRET": "ha-010-v2-test-secret",
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
        module_name = f"_ha010_bff_replica_{name}"
        spec = importlib.util.spec_from_file_location(module_name, BFF_MAIN)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        module._process_command_stub = _noop_process_command
        return module


def command_payload(target_id: str, reason: str) -> dict[str, object]:
    return {
        "command": "RuntimeAction",
        "target": {"type": "Runtime", "id": target_id},
        "params": {
            "action_id": "ha_010_v2_pause_demo",
            "entity_type": "runtime",
            "entity_id": target_id,
            "audit_event": "ha_010_v2_failover_demo",
            "live_capital_side_effects": False,
        },
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


def test_failover_demo_readme_records_dev_only_evidence_boundary() -> None:
    text = README.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "dev-only failover demonstration" in normalized
    assert "without changing canonical L1 policy" in normalized
    assert "No production topology change" in normalized
    assert "No load balancer or compose change" in normalized
    assert "No L1 canonical policy change" in normalized
    assert "observed RPO is 0 committed commands lost" in normalized
    assert "Transport failure/error for A" in normalized
    assert "HTTP 202 receipt from B" in normalized
    assert "no silent loss detected" in normalized


def test_failover_demo_script_launches_two_replicas_and_records_required_rows() -> None:
    text = DEMO_SCRIPT.read_text(encoding="utf-8")

    required_terms = [
        "REPLICA_COUNT=\"${PANTHEON_BFF_FAILOVER_REPLICA_COUNT:-2}\"",
        "PANTHEON_BFF_PYTHON",
        "-m uvicorn main:app",
        "BFF_DATA_DIR=\"$DATA_DIR\"",
        "PANTHEON_BFF_AUTH_STUB=true",
        "/bff/v1/commands",
        "/api/v1/operator/commands/",
        "failover-rto-met",
        "committed-command-rpo-met",
        "changed-retry-fails-closed",
        "inflight-command-fail-closed-no-silent-loss",
        "\"production_topology_changed\": False",
        "\"live_capital_side_effects\": False",
    ]

    for term in required_terms:
        assert term in text

    assert "docker compose" not in text.lower()


def test_in_process_failover_preserves_committed_command_rpo_across_replicas() -> None:
    targets = json.loads(SLA_TARGETS.read_text(encoding="utf-8"))["targets"]["dev"]

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "bff-data"
        with patched_env(**bff_env(data_dir)):
            replicas = [load_bff_replica(str(index), data_dir) for index in range(2)]
            clients = [TestClient(replica.app) for replica in replicas]
            idempotency_key = "ha-010-v2-rpo-key"
            payload = command_payload(
                "runtime-ha-010-v2-rpo",
                "HA-010-V2 command committed on replica A before failover",
            )

            first = clients[0].post(
                "/bff/v1/commands",
                headers={
                    **AUTH_HEADERS,
                    "Idempotency-Key": idempotency_key,
                    "X-Trace-Id": "trace-ha-010-v2",
                    "X-Correlation-Id": "corr-ha-010-v2",
                    "X-Request-Id": "req-ha-010-v2",
                },
                json=payload,
            )
            assert first.status_code == 202, first.text
            first_receipt = receipt_id(first.json())
            assert first_receipt

            replay = clients[1].post(
                "/bff/v1/commands",
                headers={**AUTH_HEADERS, "Idempotency-Key": idempotency_key},
                json=payload,
            )
            assert replay.status_code == 202, replay.text
            assert receipt_id(replay.json()) == first_receipt

            status = clients[1].get(f"/api/v1/operator/commands/{first_receipt}", headers=AUTH_HEADERS)
            assert status.status_code == 200, status.text
            body = status.json()
            foundation = body["audit"]["foundation"]
            assert body["command_id"] == first_receipt
            assert foundation["idempotency_record"]["idempotency_key"] == idempotency_key
            assert foundation["idempotency_record"]["request_hash"]
            assert 0 <= targets["rpo_seconds"]

            records = replicas[1].command_store._get_all_commands()
            assert len(records) == 1
            assert records[0]["command_id"] == first_receipt


def test_changed_retry_after_failover_fails_closed_without_duplicate_command() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "bff-data"
        with patched_env(**bff_env(data_dir)):
            replicas = [load_bff_replica(str(index), data_dir) for index in range(2)]
            clients = [TestClient(replica.app) for replica in replicas]
            idempotency_key = "ha-010-v2-fail-closed-key"
            headers = {**AUTH_HEADERS, "Idempotency-Key": idempotency_key}

            first = clients[0].post(
                "/bff/v1/commands",
                headers=headers,
                json=command_payload("runtime-ha-010-v2-fail-closed", "HA-010-V2 first command"),
            )
            assert first.status_code == 202, first.text
            first_receipt = receipt_id(first.json())

            conflict = clients[1].post(
                "/bff/v1/commands",
                headers=headers,
                json=command_payload(
                    "runtime-ha-010-v2-fail-closed",
                    "HA-010-V2 changed retry after failover must conflict",
                ),
            )
            assert conflict.status_code == 409, conflict.text
            assert bff_error_code(conflict.json()) == "IDEMPOTENCY_CONFLICT"

            records = replicas[1].command_store._get_all_commands()
            assert len(records) == 1
            assert records[0]["command_id"] == first_receipt
            assert records[0]["foundation"]["idempotency_record"]["idempotency_key"] == idempotency_key
