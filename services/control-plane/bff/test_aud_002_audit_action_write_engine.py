from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from read_store import ReadSurfaceStore


HEADERS = {"Authorization": "Bearer op-aud-002:operator,reviewer,approver"}


@contextmanager
def _isolated_audit_client(*, allow_fallback: bool) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_read_store = bff_main.read_store
        original_command_store = bff_main.command_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=allow_fallback,
        )
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
        bff_main._GOV_BFF_IDEMPOTENCY.clear()
        try:
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.read_store = original_read_store
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.clear()


def _command_event(events: list[dict], command_id: str) -> dict:
    matches = [event for event in events if event.get("command_ref") == command_id]
    assert len(matches) == 1
    return matches[0]


def test_runtime_action_writes_audit_action_visible_in_bff_audit() -> None:
    with _isolated_audit_client(allow_fallback=True) as client:
        response = client.post(
            "/bff/runtimes/runtime-042/actions/pause",
            headers={**HEADERS, "Idempotency-Key": "aud-002-runtime-pause"},
            json={"reason": "AUD-002 runtime audit write proof"},
        )
        assert response.status_code == 202, response.text
        command_id = response.json()["data"]["receipt_id"]

        records = bff_main.command_store._get_all_commands()
        assert len(records) == 1
        foundation = records[0]["foundation"]
        assert foundation["audit_action"]["action_type"] == "bff.command.accepted"
        assert foundation["audit_action"]["target_ref"] == "RuntimeBinding:runtime-042"
        assert foundation["audit_action"]["payload_checksum"]

        audit = client.get(
            "/bff/audit",
            params={"target_type": "RuntimeBinding"},
            headers=HEADERS,
        )
        assert audit.status_code == 200, audit.text
        event = _command_event(audit.json()["data"], command_id)
        assert event["actor"] == "op-aud-002"
        assert event["action_type"] == "RuntimeAction"
        assert event["target_id"] == "runtime-042"
        assert event["audit_context"]["idempotency_key"] == "aud-002-runtime-pause"
        assert event["audit_action"]["trace_id"] == command_id

        entity = client.get("/bff/audit/entities/RuntimeBinding/runtime-042", headers=HEADERS)
        assert entity.status_code == 200, entity.text
        assert _command_event(entity.json()["events"], command_id)["entry_id"] == event["entry_id"]


def test_audit_export_write_is_queryable_without_snapshot_fallback() -> None:
    with _isolated_audit_client(allow_fallback=False) as client:
        headers = {**HEADERS, "Idempotency-Key": "aud-002-export"}
        payload = {"target_type": "Deployment", "reason": "AUD-002 export command"}
        first = client.post("/bff/audit/export", headers=headers, json=payload)
        replay = client.post("/bff/audit/export", headers=headers, json=payload)

        assert first.status_code == 202, first.text
        assert replay.status_code == 202, replay.text
        assert replay.json()["meta"]["idempotency"]["replayed"] is True
        assert replay.json()["data"]["receipt_id"] == first.json()["data"]["receipt_id"]

        audit = client.get(
            "/bff/audit",
            params={"target_type": "AuditExport"},
            headers=HEADERS,
        )
        assert audit.status_code == 200, audit.text
        events = [
            event
            for event in audit.json()["data"]
            if event.get("audit_context", {}).get("idempotency_key") == "aud-002-export"
        ]
        assert len(events) == 1
        assert events[0]["action_type"] == "AuditExport"
        assert events[0]["target_id"] == "Deployment"
        assert events[0]["audit_action"]["payload_checksum"]
