#!/usr/bin/env python3
"""MGMT-SAFE-006 command idempotency regression smoke."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
BFF_DIR = REPO_ROOT / "services" / "control-plane" / "bff"
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from command_queue import CommandStore  # noqa: E402


TASK_ID = "MGMT-SAFE-006"

HEADERS = {
    "Authorization": "Bearer op-safe-006:operator,approver,admin:mfa",
    "Idempotency-Key": "mgmt-safe-006-final-command",
    "X-Trace-Id": "trace-mgmt-safe-006",
    "X-Correlation-Id": "corr-mgmt-safe-006",
    "X-Request-Id": "req-mgmt-safe-006",
}


@dataclass
class SmokeRow:
    row: str
    status: str
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str = ""


async def _noop_process_command(_command_id: str) -> None:
    return None


def _pass(row: str, evidence: dict[str, Any] | None = None) -> SmokeRow:
    return SmokeRow(row=row, status="passed", evidence=evidence or {})


def _fail(row: str, error: str, evidence: dict[str, Any] | None = None) -> SmokeRow:
    return SmokeRow(row=row, status="failed", error=error, evidence=evidence or {})


def _check(row: str, condition: bool, evidence: dict[str, Any], error: str) -> SmokeRow:
    return _pass(row, evidence) if condition else _fail(row, error, evidence)


@contextmanager
def _isolated_bff_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as tmpdir:
        original_command_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        original_auth_stub = os.environ.get("PANTHEON_BFF_AUTH_STUB")
        original_auth_mode = os.environ.get("PANTHEON_BFF_AUTH_MODE")
        bff_main.command_store = CommandStore(os.path.join(tmpdir, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
        os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
        for ledger_name in (
            "_FINAL_CONTRACT_IDEMPOTENCY",
            "_CAPITAL_BFF_IDEMPOTENCY",
            "_GOV_BFF_IDEMPOTENCY",
        ):
            ledger = getattr(bff_main, ledger_name, None)
            if hasattr(ledger, "clear"):
                ledger.clear()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.command_store = original_command_store
            bff_main._process_command_stub = original_worker
            if original_auth_stub is None:
                os.environ.pop("PANTHEON_BFF_AUTH_STUB", None)
            else:
                os.environ["PANTHEON_BFF_AUTH_STUB"] = original_auth_stub
            if original_auth_mode is None:
                os.environ.pop("PANTHEON_BFF_AUTH_MODE", None)
            else:
                os.environ["PANTHEON_BFF_AUTH_MODE"] = original_auth_mode
            for ledger_name in (
                "_FINAL_CONTRACT_IDEMPOTENCY",
                "_CAPITAL_BFF_IDEMPOTENCY",
                "_GOV_BFF_IDEMPOTENCY",
            ):
                ledger = getattr(bff_main, ledger_name, None)
                if hasattr(ledger, "clear"):
                    ledger.clear()


def _pause_execution_payload(reason: str = "MGMT-SAFE-006 idempotency replay proof") -> dict[str, Any]:
    return {
        "command": "PauseExecution",
        "target": {"type": "Runtime", "id": "runtime-mgmt-safe-006"},
        "params": {"pause_new_entries": True, "cancel_open_orders": False},
        "audit_context": {"reason": reason},
    }


def _receipt_id(payload: dict[str, Any]) -> str:
    return str(payload.get("data", {}).get("receipt_id") or "")


def _json_response(response) -> dict[str, Any]:
    try:
        loaded = response.json()
    except Exception:
        return {"raw": response.text}
    return loaded if isinstance(loaded, dict) else {"raw": loaded}


def _record_evidence(record: dict[str, Any]) -> dict[str, Any]:
    foundation = record.get("foundation") if isinstance(record.get("foundation"), dict) else {}
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    command_envelope = foundation.get("command_envelope") if isinstance(foundation.get("command_envelope"), dict) else {}
    trace_context = foundation.get("trace_context") if isinstance(foundation.get("trace_context"), dict) else {}
    idempotency_record = foundation.get("idempotency_record") if isinstance(foundation.get("idempotency_record"), dict) else {}
    audit_action = foundation.get("audit_action") if isinstance(foundation.get("audit_action"), dict) else {}
    return {
        "command_id": record.get("command_id"),
        "command_type": record.get("type"),
        "target": record.get("target"),
        "stored_status": record.get("status"),
        "admission_route": foundation.get("admission_route"),
        "idempotency_record": {
            "idempotency_key": idempotency_record.get("idempotency_key"),
            "status": idempotency_record.get("status"),
            "request_hash_present": bool(idempotency_record.get("request_hash")),
        },
        "command_envelope": {
            "idempotency_key": command_envelope.get("idempotency_key"),
            "trace_id": (command_envelope.get("trace") or {}).get("trace_id"),
            "correlation_id": (command_envelope.get("trace") or {}).get("correlation_id"),
            "request_id": (command_envelope.get("trace") or {}).get("request_id"),
        },
        "trace_context": {
            "trace_id": trace_context.get("trace_id"),
            "correlation_id": trace_context.get("correlation_id"),
            "request_id": trace_context.get("request_id"),
            "idempotency_key": trace_context.get("idempotency_key"),
        },
        "audit_action_type": audit_action.get("action_type"),
        "audit_reason": audit.get("reason"),
    }


def run_smoke() -> dict[str, Any]:
    rows: list[SmokeRow] = []

    with _isolated_bff_client() as client:
        first = client.post("/bff/v1/commands", headers=HEADERS, json=_pause_execution_payload())
        first_payload = _json_response(first)
        first_receipt_id = _receipt_id(first_payload)
        rows.append(
            _check(
                "first-command-accepted",
                first.status_code == 202 and bool(first_receipt_id),
                {
                    "status_code": first.status_code,
                    "receipt_id": first_receipt_id,
                    "command": first_payload.get("data", {}).get("command"),
                },
                "first command admission must return HTTP 202 with a receipt id",
            )
        )

        replay = client.post("/bff/v1/commands", headers=HEADERS, json=_pause_execution_payload())
        replay_payload = _json_response(replay)
        replay_receipt_id = _receipt_id(replay_payload)
        rows.append(
            _check(
                "same-key-same-payload-replays",
                replay.status_code == 202 and replay_receipt_id == first_receipt_id,
                {
                    "status_code": replay.status_code,
                    "receipt_id": replay_receipt_id,
                    "original_receipt_id": first_receipt_id,
                },
                "same Idempotency-Key and same payload must replay the original receipt",
            )
        )

        conflict = client.post(
            "/bff/v1/commands",
            headers=HEADERS,
            json=_pause_execution_payload(reason="MGMT-SAFE-006 changed payload must conflict"),
        )
        conflict_payload = _json_response(conflict)
        conflict_detail = conflict_payload.get("detail") if isinstance(conflict_payload.get("detail"), dict) else conflict_payload
        rows.append(
            _check(
                "same-key-different-payload-conflicts",
                conflict.status_code == 409
                and (conflict_detail.get("error") or {}).get("code") == "IDEMPOTENCY_CONFLICT"
                and (conflict_detail.get("foundation_error") or {}).get("error_code") == "IDEMPOTENCY_CONFLICT"
                and (conflict_detail.get("audit_action") or {}).get("action_type")
                == "bff.command.idempotency_conflict",
                {
                    "status_code": conflict.status_code,
                    "error_code": (conflict_detail.get("error") or {}).get("code"),
                    "foundation_error_code": (conflict_detail.get("foundation_error") or {}).get("error_code"),
                    "audit_action_type": (conflict_detail.get("audit_action") or {}).get("action_type"),
                },
                "same Idempotency-Key with a changed payload must return 409 IDEMPOTENCY_CONFLICT",
            )
        )

        body_key_payload = _pause_execution_payload()
        body_key_payload["idempotencyKey"] = "body-key-must-not-be-accepted"
        body_key = client.post(
            "/bff/v1/commands",
            headers={**HEADERS, "Idempotency-Key": "mgmt-safe-006-body-key"},
            json=body_key_payload,
        )
        body_key_payload_json = _json_response(body_key)
        body_key_detail = (
            body_key_payload_json.get("detail")
            if isinstance(body_key_payload_json.get("detail"), dict)
            else body_key_payload_json
        )
        rows.append(
            _check(
                "body-idempotency-key-rejected",
                body_key.status_code == 400
                and (body_key_detail.get("error") or {}).get("code") == "VALIDATION_FAILED",
                {
                    "status_code": body_key.status_code,
                    "error_code": (body_key_detail.get("error") or {}).get("code"),
                    "precondition_failed": ((body_key_detail.get("error") or {}).get("details") or {}).get(
                        "precondition_failed"
                    ),
                },
                "final command route must reject body-level idempotencyKey",
            )
        )

        records = bff_main.command_store._get_all_commands()
        record = records[0] if records else {}
        record_evidence = _record_evidence(record)
        rows.append(
            _check(
                "single-durable-command-record",
                len(records) == 1
                and record_evidence["idempotency_record"]["idempotency_key"] == HEADERS["Idempotency-Key"]
                and record_evidence["idempotency_record"]["status"] == "succeeded"
                and record_evidence["idempotency_record"]["request_hash_present"]
                and record_evidence["command_envelope"]["trace_id"] == HEADERS["X-Trace-Id"]
                and record_evidence["trace_context"]["correlation_id"] == HEADERS["X-Correlation-Id"]
                and record_evidence["audit_action_type"] == "bff.command.accepted",
                {
                    "record_count": len(records),
                    **record_evidence,
                },
                "accepted/replayed/conflicted sequence must leave exactly one durable command record with trace/audit/idempotency",
            )
        )

    passed = all(row.status == "passed" for row in rows)
    return {
        "task_id": TASK_ID,
        "status": "passed" if passed else "failed",
        "summary": {
            "smoke_passed": passed,
            "route": "POST /bff/v1/commands",
            "idempotency_key_source": "Idempotency-Key header",
            "conflict_status_code": 409,
            "live_capital_side_effects": False,
        },
        "rows": [asdict(row) for row in rows],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=None, help="Write JSON evidence to this path.")
    args = parser.parse_args(argv)

    report = run_smoke()
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["summary"]["smoke_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
