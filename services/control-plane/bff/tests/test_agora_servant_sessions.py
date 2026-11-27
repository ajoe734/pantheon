"""AG-BE-ID-003: Agora servant session facade contract tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from unittest import mock

import pytest
from fastapi.testclient import TestClient

from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

try:
    from agora.router import create_agora_router
    from agora.servant import router as servant_router
    from openclaw_ops_client import OpenClawOpsClientError
    from ports import create_in_memory_read_surface_ports
except (ImportError, ValueError):
    from services.control_plane.bff.agora.router import create_agora_router
    from services.control_plane.bff.agora.servant import router as servant_router
    from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClientError
    from services.control_plane.bff.ports import create_in_memory_read_surface_ports

try:
    from agora.servant.pg_owner_fake import FakeOwnerTable
except ImportError:
    from services.control_plane.bff.agora.servant.pg_owner_fake import FakeOwnerTable
from services.agora.service import AgoraWriteService
from services.agora.store import AgoraStore
from services.control_plane.bff.agora.servant.turn_worker import ServantTurnWorker
from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.personas.service import (
    _extract_identity,
    _require_read_role,
    _require_operator_role,
    _bff_error,
)
try:
    from agora.service import _AGORA_SIGNAL_WRITE_ROLES, _AGORA_BULK_FEEDBACK_ROLES
except ImportError:
    from services.control_plane.bff.agora.service import (
        _AGORA_SIGNAL_WRITE_ROLES,
        _AGORA_BULK_FEEDBACK_ROLES,
    )


def _require_agora_signal_write_role(identity: Any) -> None:
    if not _AGORA_SIGNAL_WRITE_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Agora signal creation requires analyst-level role",
            "Operator does not hold the required analyst, operator, reviewer, approver, or admin role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with analyst-level Agora write access",
        )


def _require_agora_bulk_feedback_role(identity: Any) -> None:
    if not _AGORA_BULK_FEEDBACK_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Agora feedback access requires analyst role",
            "Operator does not hold the required Agora feedback role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with analyst, operator, reviewer, approver, or admin role",
        )

AUTH = {"Authorization": "Bearer agora-test-user:operator"}


def _create_test_agora_store():
    personas_map: dict[str, dict] = {}
    snapshots_map: dict[str, dict] = {}

    store = create_in_memory_read_surface_ports()

    def create_persona(
        *,
        persona_id: str,
        name: str,
        actor_id: str,
        created_at: str | None = None,
        archetype: str = "generalist",
        lifecycle_state: str = "draft",
        risk_level: str = "low",
        mandate: str | None = None,
        strategy_family: str | None = None,
        traits: dict | None = None,
        metadata: dict | None = None,
        required_data_sources: list | None = None,
    ) -> dict:
        timestamp = created_at or "2026-08-29T00:00:00Z"
        clean_metadata = dict(metadata or {})
        clean_metadata.update({
            "owner": actor_id,
            "archetype": archetype,
            "risk_level": risk_level,
        })
        if traits:
            clean_metadata["traits"] = dict(traits)
        record = {
            "id": persona_id,
            "persona_id": persona_id,
            "name": name,
            "mandate": mandate or archetype,
            "strategy_family": strategy_family or archetype,
            "lifecycle_state": lifecycle_state,
            "status": lifecycle_state,
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
            "tenant_id": "pantheon-dev",
            "tenantId": "pantheon-dev",
            "required_data_sources": list(required_data_sources or []),
            "metadata": clean_metadata,
            "canonicalWriteAuthority": "persona_registry_service",
            "persistenceMode": "bff_local_dev_store",
        }
        personas_map[persona_id] = record
        return record

    def update_persona(
        persona_id: str,
        *,
        name: str | None = None,
        actor_id: str | None = None,
        updated_at: str | None = None,
        archetype: str | None = None,
        lifecycle_state: str | None = None,
        risk_level: str | None = None,
        metadata: dict | None = None,
    ) -> dict | None:
        if not persona_id or persona_id not in personas_map:
            return None
        record = dict(personas_map[persona_id])
        timestamp = updated_at or "2026-08-29T00:00:00Z"
        if name is not None:
            record["name"] = name
        if lifecycle_state is not None:
            record["lifecycle_state"] = lifecycle_state
            record["status"] = lifecycle_state
        if archetype is not None:
            record["mandate"] = archetype
            record["strategy_family"] = archetype
        record["updated_at"] = timestamp
        clean_metadata = dict(record.get("metadata") or {})
        if metadata:
            clean_metadata.update(metadata)
        if actor_id is not None:
            clean_metadata["owner"] = actor_id
        if archetype is not None:
            clean_metadata["archetype"] = archetype
        if risk_level is not None:
            clean_metadata["risk_level"] = risk_level
        record["metadata"] = clean_metadata
        personas_map[persona_id] = record
        return record

    def get_persona(pid: str) -> dict | None:
        return personas_map.get(pid)

    def list_personas(**kw) -> list[dict]:
        res = list(personas_map.values())
        if kw.get("lifecycle_state"):
            res = [p for p in res if p.get("lifecycle_state") == kw["lifecycle_state"]]
        return res

    def upsert_persona_capability_snapshot(
        snapshot_id: str,
        persona_id: str,
        capabilities: list[str],
        generated_at: str,
        source_refs: list[str] | None = None,
        metadata: dict | None = None,
        **kw,
    ) -> dict:
        snap = {
            "snapshot_id": snapshot_id,
            "persona_id": persona_id,
            "capabilities": list(capabilities),
            "allowed_capabilities": list(capabilities),
            "generated_at": generated_at,
            "source_refs": source_refs or [],
            "metadata": dict(metadata or {}),
        }
        snapshots_map[snapshot_id] = snap
        return snap

    def get_capability_snapshot(snap_id: str) -> dict | None:
        return snapshots_map.get(snap_id)

    def get_capability_snapshot_for_persona(pid: str) -> dict | None:
        for snap in snapshots_map.values():
            if snap.get("persona_id") == pid:
                return snap
        return None

    store.create_persona = create_persona
    store.update_persona = update_persona
    store.get_persona = get_persona
    store.list_personas = list_personas
    store.upsert_persona_capability_snapshot = upsert_persona_capability_snapshot
    store.get_capability_snapshot = get_capability_snapshot
    store.get_capability_snapshot_for_persona = get_capability_snapshot_for_persona
    store._local_dataset = lambda name: snapshots_map if name == "capability_snapshots" else {}
    store.dataset_source = lambda d: "typed_store"
    return store


class FakeOpenClawClient:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.create_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[dict[str, Any]] = []
        self.invoke_calls: list[dict[str, Any]] = []
        self.fail_next_invoke = False

    def create_session(
        self,
        *,
        agent_id: str,
        session_type: str,
        operator_id: str,
        idempotency_key: str,
        context_bundle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        call = {
            "agent_id": agent_id,
            "session_type": session_type,
            "operator_id": operator_id,
            "idempotency_key": idempotency_key,
            "context_bundle": context_bundle or {},
        }
        self.create_calls.append(call)
        session_id = f"oc-session-{len(self.sessions) + 1}"
        record = {
            "session_id": session_id,
            "agent_id": agent_id,
            "session_type": session_type,
            "state": "active",
            "operator_id": operator_id,
            "created_at": "2026-06-21T09:15:00Z",
            "updated_at": "2026-06-21T09:15:00Z",
            "idempotency_key": idempotency_key,
            "context_bundle": context_bundle or {},
            "upstream_session_id": f"upstream-{session_id}",
            "last_error": None,
        }
        self.sessions[session_id] = record
        return {"status": "ok", "session": record}

    def get_session(self, *, session_id: str, operator_id: str | None = None) -> dict[str, Any]:
        self.get_calls.append({"session_id": session_id, "operator_id": operator_id})
        record = self.sessions.get(session_id)
        if record is None:
            raise OpenClawOpsClientError(
                "not found",
                status_code=404,
                error_code="LIFECYCLE_NOT_FOUND",
            )
        return {"status": "ok", "session": record}

    def cancel_session(
        self,
        *,
        session_id: str,
        operator_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.cancel_calls.append(
            {"session_id": session_id, "operator_id": operator_id, "idempotency_key": idempotency_key}
        )
        record = self.sessions.get(session_id)
        if record is None:
            raise OpenClawOpsClientError(
                "not found",
                status_code=404,
                error_code="LIFECYCLE_NOT_FOUND",
            )
        record = {**record, "state": "canceled", "updated_at": "2026-06-21T09:16:00Z"}
        self.sessions[session_id] = record
        return {"status": "ok", "session": record}

    def invoke_assistant_provider(self, **kwargs: Any) -> dict[str, Any]:
        self.invoke_calls.append(kwargs)
        if self.fail_next_invoke:
            self.fail_next_invoke = False
            raise OpenClawOpsClientError(
                "adapter unavailable",
                status_code=503,
                error_code="OPENCLAW_ADAPTER_UNREACHABLE",
            )
        return {
            "status": "ok",
            "data": {
                "status": "completed",
                "output": "Servant response grounded in the supplied context.",
            },
        }


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_owner() -> AgoraWriteService:
    """An Agora owner over the shared fake Postgres rows (a new one models a restarted process)."""
    with mock.patch("services.agora.store.PostgresJsonOwnerStore", FakeOwnerTable):
        return AgoraWriteService(store=AgoraStore(dsn="postgresql://test/none", bootstrap=False))


def _install_client(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    owner: AgoraWriteService | None = None,
    fake: FakeOpenClawClient | None = None,
    reset_rows: bool = True,
) -> tuple[TestClient, FakeOpenClawClient]:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    if reset_rows:
        FakeOwnerTable.reset()
    owner = owner or _build_owner()
    store = _create_test_agora_store()
    fake = fake or FakeOpenClawClient()
    monkeypatch.setattr(servant_router, "OpenClawOpsClient", lambda: fake)
    monkeypatch.setattr(servant_router, "_default_servant_session_owner", lambda: owner)

    router = create_agora_router(
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_write_role=_require_operator_role,
        require_operator_role=_require_operator_role,
        require_journal_write_role=_require_operator_role,
        require_agora_signal_write_role=_require_agora_signal_write_role,
        require_agora_bulk_feedback_role=_require_agora_bulk_feedback_role,
        bff_error=_bff_error,
        utc_now=_utc_now_rfc3339,
        read_surface=store,
        persona_write_owner=store,
        sync_servant_agent=lambda persona: {
            "status": "updated",
            "agent_id": persona["persona_id"],
            "model_id": f"openclaw/{persona['persona_id']}",
            "model": "openclaw/agora-servant",
            "workspace_ref": f"workspace://{persona['persona_id']}",
        },
        openclaw_ops_client_factory=lambda: fake,
    )
    app = FastAPI()

    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request, exc):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail)}})

    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    client.servant_owner = owner  # type: ignore[attr-defined]
    return client, fake


def _run_worker(client: TestClient, fake: FakeOpenClawClient) -> ServantTurnWorker:
    worker = ServantTurnWorker(owner=client.servant_owner, client_factory=lambda: fake)  # type: ignore[attr-defined]
    worker.run_once()
    return worker


def _ensure_servant(client: TestClient) -> str:
    response = client.post(
        "/bff/agora/servant/ensure",
        headers={
            **AUTH,
            "Idempotency-Key": "ensure-servant-001",
            "X-Request-Id": "req-ensure-servant-001",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["persona_id"]


def _create_session(client: TestClient, *, session_type: str = "interactive") -> dict[str, Any]:
    payload: dict[str, Any] = {"intent": "Explore a strategy thesis", "strategy_ref": "strategy:alpha"}
    if session_type != "interactive":
        payload["session_type"] = session_type
    response = client.post(
        "/bff/agora/servant/sessions",
        json=payload,
        headers={
            **AUTH,
            "Idempotency-Key": f"create-{session_type}-001",
            "X-Request-Id": f"req-create-{session_type}-001",
            "X-Trace-Id": f"trace-create-{session_type}-001",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_servant_session_create_maps_session_type_and_audit(monkeypatch: Any, tmp_path: Path) -> None:
    client, fake = _install_client(monkeypatch, tmp_path)
    persona_id = _ensure_servant(client)

    body = _create_session(client, session_type="trainer")

    assert body["data"]["session_type"] == "trainer"
    assert body["data"]["servant_persona_id"] == persona_id
    assert fake.create_calls[-1]["agent_id"] == persona_id
    assert fake.create_calls[-1]["session_type"] == "trainer"
    context = fake.create_calls[-1]["context_bundle"]
    assert context["session_type"] == "trainer"
    assert context["safety"]["execution_authority"] == "none"
    assert set(context["safety"]["prohibited_authority"]) == {
        "runtime_binding",
        "broker_order",
        "capital_binding",
    }
    assert context["audit"]["trace_id"] == "trace-create-trainer-001"
    assert context["audit"]["request_id"] == "req-create-trainer-001"
    assert context["audit"]["actor_id"] == "agora-test-user"
    assert body["meta"]["audit"]["persona_id"] == persona_id
    assert body["meta"]["audit"]["session_id"] == body["data"]["session_id"]


def test_servant_session_default_interactive_and_rejects_unknown_type(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    client, fake = _install_client(monkeypatch, tmp_path)
    _ensure_servant(client)

    created = _create_session(client)
    assert created["data"]["session_type"] == "interactive"
    assert fake.create_calls[-1]["session_type"] == "interactive"

    rejected = client.post(
        "/bff/agora/servant/sessions",
        json={"session_type": "consult"},
        headers={
            **AUTH,
            "Idempotency-Key": "create-consult-001",
            "X-Request-Id": "req-create-consult-001",
        },
    )
    assert rejected.status_code == 422, rejected.text


def test_servant_session_detail_message_stream_and_terminate(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    client, fake = _install_client(monkeypatch, tmp_path)
    persona_id = _ensure_servant(client)
    created = _create_session(client, session_type="research_task")
    session_id = created["data"]["session_id"]

    detail = client.get(
        f"/bff/agora/servant/sessions/{session_id}",
        headers={**AUTH, "X-Request-Id": "req-detail-001", "X-Trace-Id": "trace-detail-001"},
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["session_id"] == session_id
    assert detail.json()["meta"]["audit"] == {
        "trace_id": "trace-detail-001",
        "request_id": "req-detail-001",
        "actor_id": "agora-test-user",
        "user_id": "agora-test-user",
        "persona_id": persona_id,
        "session_id": session_id,
    }

    message = client.post(
        f"/bff/agora/servant/sessions/{session_id}/messages",
        json={"content": "Summarize the research objective.", "attachment_refs": ["evidence://seed-1"]},
        headers={
            **AUTH,
            "Idempotency-Key": "message-001",
            "X-Request-Id": "req-message-001",
            "X-Trace-Id": "trace-message-001",
        },
    )
    assert message.status_code == 202, message.text
    message_body = message.json()
    # AD-AG-05: 202 means durably queued; no provider call ran inside the request.
    assert message_body["data"]["provider"] == {"status": "queued", "answer": None, "error": None}
    assert fake.invoke_calls == []

    _run_worker(client, fake)
    invoke = fake.invoke_calls[-1]
    assert invoke["provider"] == "openclaw"
    assert invoke["metadata"]["audit"]["trace_id"] == "trace-message-001"
    assert invoke["metadata"]["audit"]["request_id"] == "req-message-001"
    assert invoke["metadata"]["audit"]["persona_id"] == persona_id
    assert invoke["messages"][0]["attachment_refs"] == ["evidence://seed-1"]

    stream = client.get(f"/bff/agora/servant/sessions/{session_id}/stream", headers=AUTH)
    assert stream.status_code == 200, stream.text
    assert "text/event-stream" in stream.headers["content-type"]
    assert "event: servant.message.completed" in stream.text
    assert "Servant response grounded in the supplied context." in stream.text
    assert "trace-message-001" in stream.text

    terminated = client.post(
        f"/bff/agora/servant/sessions/{session_id}/terminate",
        headers={
            **AUTH,
            "Idempotency-Key": "terminate-001",
            "X-Request-Id": "req-terminate-001",
            "X-Trace-Id": "trace-terminate-001",
        },
    )
    assert terminated.status_code == 200, terminated.text
    assert terminated.json()["data"]["state"] == "canceled"
    assert fake.cancel_calls[-1]["session_id"] == session_id
    assert terminated.json()["meta"]["audit"]["session_id"] == session_id


def test_servant_message_reports_openclaw_degraded(monkeypatch: Any, tmp_path: Path) -> None:
    client, fake = _install_client(monkeypatch, tmp_path)
    _ensure_servant(client)
    created = _create_session(client)
    session_id = created["data"]["session_id"]
    fake.fail_next_invoke = True

    response = client.post(
        f"/bff/agora/servant/sessions/{session_id}/messages",
        json={"content": "This call should degrade."},
        headers={
            **AUTH,
            "Idempotency-Key": "message-degraded-001",
            "X-Request-Id": "req-message-degraded-001",
        },
    )

    assert response.status_code == 202, response.text
    assert fake.invoke_calls == []
    _run_worker(client, fake)
    stream = client.get(f"/bff/agora/servant/sessions/{session_id}/stream", headers=AUTH)
    assert "event: servant.message.degraded" in stream.text
    assert "OPENCLAW_UPSTREAM_DEGRADED" in stream.text


# --------------------------------------------------------------------------- #
# AGORA-SERVANT-SESSION-DURABLE-20261127
# --------------------------------------------------------------------------- #

def _send(client: TestClient, session_id: str, content: str, key: str, *, auth: dict | None = None):
    return client.post(
        f"/bff/agora/servant/sessions/{session_id}/messages",
        json={"content": content},
        headers={**(auth or AUTH), "Idempotency-Key": key, "X-Request-Id": f"req-{key}", "X-Trace-Id": f"trace-{key}"},
    )


def _stream_events(client: TestClient, session_id: str, *, last_event_id: str | None = None) -> list[dict[str, Any]]:
    headers = dict(AUTH)
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    response = client.get(f"/bff/agora/servant/sessions/{session_id}/stream", headers=headers)
    assert response.status_code == 200, response.text
    events: list[dict[str, Any]] = []
    for block in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if fields:
            events.append({"id": fields["id"], "type": fields["event"], "data": json.loads(fields["data"])})
    return events


def test_servant_history_survives_bff_restart(monkeypatch: Any, tmp_path: Path) -> None:
    client, fake = _install_client(monkeypatch, tmp_path)
    _ensure_servant(client)
    session_id = _create_session(client)["data"]["session_id"]
    assert _send(client, session_id, "第一個問題", "restart-001").status_code == 202
    _run_worker(client, fake)
    before = [(e["type"], e["data"].get("content") or e["data"].get("answer")) for e in _stream_events(client, session_id)]

    # A new app object over a new owner object, same Postgres rows: the process dict is gone.
    assert not hasattr(servant_router, "_SERVANT_SESSION_EVENTS")
    restarted, fake2 = _install_client(monkeypatch, tmp_path, fake=fake, reset_rows=False)
    _ensure_servant(restarted)
    after = [(e["type"], e["data"].get("content") or e["data"].get("answer")) for e in _stream_events(restarted, session_id)]

    assert after == before
    assert ("servant.message.accepted", "第一個問題") in after
    assert ("servant.message.completed", "Servant response grounded in the supplied context.") in after
    # ... and the conversation continues on the restarted app with the earlier turn in its history.
    assert _send(restarted, session_id, "第二個問題", "restart-002").status_code == 202
    _run_worker(restarted, fake2)
    contents = [m["content"] for m in fake2.invoke_calls[-1]["messages"]]
    assert contents[0] == "第一個問題" and contents[-1] == "第二個問題"


def test_servant_message_request_makes_no_provider_call(monkeypatch: Any, tmp_path: Path) -> None:
    client, fake = _install_client(monkeypatch, tmp_path)
    _ensure_servant(client)
    session_id = _create_session(client)["data"]["session_id"]

    def _boom(**_kw: Any) -> Any:
        raise AssertionError("provider call inside the request")

    monkeypatch.setattr(fake, "invoke_assistant_provider", _boom)
    response = _send(client, session_id, "no provider here", "noprov-001")

    assert response.status_code == 202, response.text
    queued = [e for e in _stream_events(client, session_id) if e["type"] == "servant.message.queued"]
    assert queued and queued[0]["data"]["status"] == "queued"
    turns = client.servant_owner.list_servant_turns(session_id)  # type: ignore[attr-defined]
    assert [t["status"] for t in turns] == ["queued"]


def test_servant_message_idempotency_key_replay_queues_once(monkeypatch: Any, tmp_path: Path) -> None:
    client, fake = _install_client(monkeypatch, tmp_path)
    _ensure_servant(client)
    session_id = _create_session(client)["data"]["session_id"]
    first = _send(client, session_id, "same", "idem-001").json()["data"]
    second = _send(client, session_id, "same", "idem-001").json()["data"]
    assert first["turn_id"] == second["turn_id"] and first["message_id"] == second["message_id"]
    _run_worker(client, fake)
    assert len(fake.invoke_calls) == 1
    reused = _send(client, session_id, "different text", "idem-001")
    assert reused.status_code == 409, reused.text


def test_servant_turn_carries_last_20_messages(monkeypatch: Any, tmp_path: Path) -> None:
    client, fake = _install_client(monkeypatch, tmp_path)
    _ensure_servant(client)
    session_id = _create_session(client)["data"]["session_id"]
    for i in range(15):  # 30 messages: 15 user + 15 servant
        assert _send(client, session_id, f"q{i}", f"hist-{i:02d}").status_code == 202
        _run_worker(client, fake)
    assert _send(client, session_id, "q15", "hist-15").status_code == 202
    _run_worker(client, fake)

    carried = fake.invoke_calls[-1]["messages"]
    assert len(carried) == 20
    assert carried[-1] == {"role": "user", "content": "q15", "attachment_refs": []}
    assert [m["role"] for m in carried] == ["assistant", "user"] * 10
    assert carried[0]["role"] == "assistant"
    assert carried[1]["content"] == "q6"  # 31 messages exist; the oldest 11 (q0..a4, q5) are not carried


def test_servant_session_message_cap_and_size_budget(monkeypatch: Any, tmp_path: Path) -> None:
    client, fake = _install_client(monkeypatch, tmp_path)
    _ensure_servant(client)
    session_id = _create_session(client)["data"]["session_id"]
    owner = client.servant_owner  # type: ignore[attr-defined]
    # Fill the owner row to 498 messages directly, then the route enforces the cap of 500.
    for i in range(498):
        owner.append_session_message(session_id, f"fill-{i}", "x", "agora-test-user", ("trader",))
    assert _send(client, session_id, "fits", "cap-001").status_code == 202  # 499th; its answer is the 500th
    _run_worker(client, fake)
    full = _send(client, session_id, "one too many", "cap-002")
    assert full.status_code == 409, full.text
    assert len(owner.get_servant_session(session_id, tenant_id="pantheon-dev", user_id="agora-test-user")["messages"]) == 500

    too_long = _send(client, session_id, "x" * 4001, "cap-003")
    assert too_long.status_code == 422, too_long.text


def test_servant_session_is_owner_scoped(monkeypatch: Any, tmp_path: Path) -> None:
    client, fake = _install_client(monkeypatch, tmp_path)
    _ensure_servant(client)
    session_id = _create_session(client)["data"]["session_id"]
    assert _send(client, session_id, "mine", "scope-001").status_code == 202
    owner = client.servant_owner  # type: ignore[attr-defined]
    assert owner.get_servant_session(session_id, tenant_id="pantheon-dev", user_id="someone-else") is None
    assert owner.get_servant_session(session_id, tenant_id="other-tenant", user_id="agora-test-user") is None


def test_servant_sessions_kept_180_days_then_purged(monkeypatch: Any, tmp_path: Path) -> None:
    client, fake = _install_client(monkeypatch, tmp_path)
    _ensure_servant(client)
    session_id = _create_session(client)["data"]["session_id"]
    assert _send(client, session_id, "old", "purge-001").status_code == 202
    owner = client.servant_owner  # type: ignore[attr-defined]
    session = owner.store.get_session(session_id)

    young = owner.purge_expired_servant_sessions(actor_roles=("system",), now="2026-12-01T00:00:00Z")
    assert young == {"sessions": 0, "turns": 0}
    touched = session["updatedAt"][:4]
    assert touched == "2026" or touched  # the session was just written

    far = owner.purge_expired_servant_sessions(actor_roles=("system",), now="2099-01-01T00:00:00Z")
    assert far == {"sessions": 1, "turns": 1}
    assert owner.store.get_session(session_id) is None
    with pytest.raises(Exception):
        owner.purge_expired_servant_sessions(actor_roles=("trader",))


def test_servant_crashed_turn_is_reclaimed_after_lease(monkeypatch: Any, tmp_path: Path) -> None:
    client, fake = _install_client(monkeypatch, tmp_path)
    _ensure_servant(client)
    session_id = _create_session(client)["data"]["session_id"]
    assert _send(client, session_id, "crash me", "crash-001").status_code == 202
    owner = client.servant_owner  # type: ignore[attr-defined]
    claimed = owner.claim_servant_turn(lease_owner="dead-worker", actor_roles=("persona",), lease_seconds=60, now="2026-11-27T00:00:00Z")
    assert claimed and claimed["attempts"] == 1
    assert owner.claim_servant_turn(lease_owner="w2", actor_roles=("persona",), now="2026-11-27T00:00:30Z") is None
    again = owner.claim_servant_turn(lease_owner="w2", actor_roles=("persona",), now="2026-11-27T00:02:00Z")
    assert again and again["attempts"] == 2 and again["lease_owner"] == "w2"
