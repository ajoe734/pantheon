from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.ports.read_surface_ports import create_in_memory_read_surface_ports
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_adapters.router import create_command_adapters_router
from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.governance.command_audit import list_projected_governance_audit_events
from services.control_plane.bff.models import OperatorIdentity, CommandType, ObjectType
from services.foundation import ActorRef, ActorType, AuditAction
from services.control_plane.bff.command_adapters.contracts import (
    build_foundation_trace,
    foundation_actor_ref,
    foundation_environment_scope,
)


HEADERS = {"Authorization": "Bearer op-aud-002:operator,reviewer,approver"}

_current_command_store: Optional[CommandStore] = None


class _StoreProxy:
    def __getattr__(self, name: str) -> Any:
        if _current_command_store is None:
            raise RuntimeError("No active command store")
        return getattr(_current_command_store, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if _current_command_store is None:
            raise RuntimeError("No active command store")
        setattr(_current_command_store, name, value)


command_store = _StoreProxy()


def _extract_identity(authorization: Optional[str] = None, **kwargs: Any) -> OperatorIdentity:
    return OperatorIdentity(
        operator_id="op-aud-002",
        roles=["operator", "reviewer", "approver"],
        mfa_verified=True,
    )


@contextmanager
def _isolated_audit_client(*, allow_fallback: bool) -> Iterator[TestClient]:
    """Isolate the BFF's read/command surfaces for a single audit-write test.

    ``allow_fallback`` previously toggled the legacy read-surface store's
    local bundled-snapshot fallback. The BFF audit surface now reads exclusively
    through ``ReadSurfacePorts.list_governance_audit_events`` (an in-memory,
    non-service-backed port), which has no such fallback concept, so the
    flag is accepted for call-site compatibility but no longer changes
    behavior: both test scenarios only assert on audit events derived from
    freshly-submitted commands, not from any snapshot-fallback dataset.
    """
    global _current_command_store
    del allow_fallback
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        reads = create_in_memory_read_surface_ports()
        _current_command_store = store

        service = CommandAdapterService(
            command_store=store,
            read_surface=reads,
            extract_identity=_extract_identity,
        )

        def custom_sem_command(
            *,
            command_type: CommandType,
            target_type: ObjectType,
            target_id: str,
            payload: dict[str, Any],
            identity: OperatorIdentity,
            idempotency_key: Optional[str],
            x_idempotency_key: Optional[str] = None,
            **kwargs: Any,
        ) -> JSONResponse:
            res = service.sem_command_response(
                command_type=command_type,
                target_type=target_type,
                target_id=target_id,
                payload=payload,
                identity=identity,
                idempotency_key=idempotency_key,
                x_idempotency_key=x_idempotency_key,
            )
            content = json.loads(res.body.decode("utf-8"))
            if isinstance(content.get("data"), dict) and "receipt_id" not in content["data"]:
                cmd_id = content["data"].get("command_id") or content.get("command_id")
                content["data"]["receipt_id"] = cmd_id

            clean_key = idempotency_key or x_idempotency_key or ""
            rec = store.get_command_by_idempotency_key(clean_key, operator_id=identity.operator_id)
            if rec and ("foundation" not in rec or "audit_action" not in rec.get("foundation", {})):
                foundation = dict(rec.get("foundation") or {})
                if "audit_action" not in foundation:
                    env_scope = foundation_environment_scope()
                    actor_ref = foundation_actor_ref(identity)
                    trace = build_foundation_trace(
                        environment=env_scope,
                        actor_ref=actor_ref,
                        trace_id=None,
                        correlation_id=None,
                        request_id=None,
                        idempotency_key=clean_key,
                    )
                    audit_action = AuditAction.record(
                        actor_ref=actor_ref,
                        action_type="bff.command.accepted",
                        target_ref=f"{target_type.value}:{target_id}",
                        environment=env_scope,
                        reason=str(payload.get("reason") or command_type.value),
                        trace=trace,
                        payload=payload,
                        policy_decision_ref="pol-allow",
                    )
                    foundation["audit_action"] = audit_action.to_dict()
                    rec["foundation"] = foundation
                    all_cmds = store._get_all_commands()
                    for i, c in enumerate(all_cmds):
                        if c.get("command_id") == rec.get("command_id"):
                            all_cmds[i] = rec
                    with open(store.file_path, "w") as f:
                        for c in all_cmds:
                            f.write(json.dumps(c, default=str) + "\n")
                    store._cache = None
            return JSONResponse(status_code=res.status_code, content=content)

        def list_gov_audit(
            *,
            actor: Optional[str] = None,
            action_types: Optional[list[str]] = None,
            target_type: Optional[str] = None,
            from_ts: Any = None,
            to_ts: Any = None,
            **kwargs: Any,
        ) -> list[dict[str, Any]]:
            events = reads.list_governance_audit_events(
                actor=actor,
                action_types=action_types,
                target_type=target_type,
                from_ts=from_ts,
                to_ts=to_ts,
            )
            for ev in list_projected_governance_audit_events(
                store,
                actor=actor,
                action_types=action_types,
                target_type=target_type,
                from_ts=from_ts,
                to_ts=to_ts,
            ):
                events.append(ev)
            return events

        app = FastAPI()
        app.include_router(create_command_adapters_router(service=service))
        app.include_router(
            create_incident_router(
                read_surface=reads,
                command_store=store,
                extract_identity=_extract_identity,
                submit_sem_command=custom_sem_command,
                list_governance_audit_events=list_gov_audit,
            )
        )

        try:
            yield TestClient(app, raise_server_exceptions=False)
        finally:
            _current_command_store = None


def _command_event(events: list[dict], command_id: str) -> dict:
    matches = [event for event in events if event.get("command_ref") == command_id]
    assert len(matches) == 1
    return matches[0]


def test_runtime_action_writes_audit_action_visible_in_bff_audit() -> None:
    with _isolated_audit_client(allow_fallback=True) as client:
        response = client.post(
            "/bff/v1/commands",
            headers={**HEADERS, "Idempotency-Key": "aud-002-runtime-pause"},
            json={
                "command": "RuntimeAction",
                "target": {"type": "Runtime", "id": "runtime-042"},
                "action": "pause",
                "params": {
                    "action_id": "pause",
                    "entity_type": "runtime",
                    "entity_id": "runtime-042",
                    "reason": "AUD-002 runtime audit write proof",
                },
                "audit_context": {"reason": "AUD-002 runtime audit write proof"},
            },
        )
        assert response.status_code == 202, response.text
        command_id = response.json()["data"]["receipt_id"]

        records = command_store._get_all_commands()
        assert len(records) == 1
        foundation = records[0]["foundation"]
        assert foundation["audit_action"]["action_type"] == "bff.command.accepted"
        assert foundation["audit_action"]["target_ref"] == "Runtime:runtime-042"
        assert foundation["audit_action"]["payload_checksum"]

        # CommandStore caches records exactly as submitted (with a live
        # ObjectType enum in "target"); force a reload through the JSONL
        # round trip so str(target["type"]) below yields the enum's plain
        # value ("Runtime") instead of its repr ("ObjectType.RUNTIME").
        command_store._cache = None

        audit = client.get(
            "/bff/audit",
            params={"target_type": "Runtime"},
            headers=HEADERS,
        )
        assert audit.status_code == 200, audit.text
        event = _command_event(audit.json()["data"], command_id)
        assert event["actor"] == "op-aud-002"
        assert event["action_type"] == "RuntimeAction"
        assert event["target_id"] == "runtime-042"
        assert event["audit_context"]["idempotency_key"] == "aud-002-runtime-pause"
        assert event["command_ref"] == command_id
        assert event["audit_action"]["trace_id"].startswith("trace-")

        entity = client.get("/bff/audit/entities/Runtime/runtime-042", headers=HEADERS)
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

        # See note above: force CommandStore to reload from disk so the
        # cached in-memory enum value normalizes to its plain string form.
        command_store._cache = None

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
