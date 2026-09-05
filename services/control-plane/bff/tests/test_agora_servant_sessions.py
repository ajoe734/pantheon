"""AG-BE-ID-003: Agora servant session facade contract tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.agora.servant import router as servant_router
from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClientError
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

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


def _install_client(monkeypatch: Any, tmp_path: Path) -> tuple[TestClient, FakeOpenClawClient]:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    store = _create_test_agora_store()
    monkeypatch.setattr(bff_main, "read_store", store)
    monkeypatch.setattr(bff_main, "persona_write_owner", store)
    monkeypatch.setattr(
        bff_main,
        "_ensure_agora_servant_openclaw_agent",
        lambda persona: {
            "status": "updated",
            "agent_id": persona["persona_id"],
            "model_id": f"openclaw/{persona['persona_id']}",
            "model": "openclaw/agora-servant",
            "workspace_ref": f"workspace://{persona['persona_id']}",
        },
    )
    fake = FakeOpenClawClient()
    monkeypatch.setattr(servant_router, "OpenClawOpsClient", lambda: fake)
    servant_router._SERVANT_SESSION_EVENTS.clear()
    return TestClient(bff_main.app, raise_server_exceptions=False), fake


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
    assert message_body["data"]["provider"]["status"] == "completed"
    assert message_body["data"]["provider"]["answer"] == "Servant response grounded in the supplied context."
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
    provider = response.json()["data"]["provider"]
    assert provider["status"] == "degraded"
    assert provider["error"]["code"] == "OPENCLAW_UPSTREAM_DEGRADED"
