from __future__ import annotations

import json
import os
import stat
import sys
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient


BFF_DIR = os.path.dirname(os.path.dirname(__file__))
if BFF_DIR not in sys.path:
    sys.path.insert(0, BFF_DIR)

from assistant.control_mode import ControlModeStore  # noqa: E402
from assistant.development_routes import create_development_router  # noqa: E402
from assistant.models import AssistantMode  # noqa: E402
from assistant.routes import create_assistant_router  # noqa: E402
from assistant.transcript_store import (  # noqa: E402
    InMemorySessionStore,
    InMemoryTranscriptStore,
    TurnRole,
    build_turn,
)


HEADERS = {"Authorization": "Bearer test"}
CONTROL_PHRASE = "control phrase ok"


class _DevDocsIdentity:
    def __init__(self) -> None:
        self.operator_id = "op-dev-docs"
        self.roles = ["operator"]
        self.mfa_verified = True
        self.claims = {
            "capabilities": [
                "assistant.kernel.debug",
                "assistant.canonical.mutate",
            ]
        }


def _context_pack(session_id: str, request: Any, actor: Any) -> dict[str, Any]:
    return {
        "actor": {
            "operator_id": actor.operator_id,
            "roles": list(actor.roles),
            "capabilities": list(actor.claims["capabilities"]),
        },
        "mode": {"value": request.mode.value if hasattr(request.mode, "value") else str(request.mode)},
        "sources": [
            {
                "source_id": "control_room",
                "href": "/bff/v5/control-room",
                "snapshot_at": "2026-06-07T00:00:00Z",
                "source_kind": "bff",
            },
            {
                "source_id": "repo_status",
                "href": "/bff/assistant/internal/repo-status",
                "snapshot_at": "2026-06-07T00:00:00Z",
                "source_kind": "bff",
            },
        ],
        "backend": {
            "control_room": {"status": "ok", "session_id": session_id},
            "jobs": None,
        },
    }


def _allow_skill(skill_id: str, payload: dict[str, Any], operator_id: str, trace_id: str | None) -> dict[str, Any]:
    return {
        "status": "ok",
        "data": {
            "status": "allowed",
            "skillId": skill_id,
            "operatorId": operator_id,
            "traceId": trace_id,
            "payload": payload,
        },
    }


def _make_client(
    tmp_path,
    *,
    active_control: bool,
    skill_authorizer: Any = _allow_skill,
    activation_idempotency_key: str | None = None,
) -> tuple[TestClient, InMemoryTranscriptStore]:
    identity = _DevDocsIdentity()
    control_store = ControlModeStore(storage_path="off", initial_passphrase=CONTROL_PHRASE)
    transcript_store = InMemoryTranscriptStore()

    product_router = create_assistant_router(
        build_context_pack=_context_pack,
        extract_identity=lambda _authorization: identity,
        require_read_role=lambda _identity: None,
        session_store=InMemorySessionStore(),
        transcript_store=transcript_store,
        control_mode_store=control_store,
    )
    development_router = create_development_router(
        build_context_pack=_context_pack,
        extract_identity=lambda _authorization: identity,
        require_read_role=lambda _identity: None,
        transcript_store=transcript_store,
        control_mode_store=control_store,
        dev_docs_repo_root=str(tmp_path),
        bridge_key_store={"assistant-bridge-dev": b"test-dev-bridge-key"},
        authorize_assistant_skill=skill_authorizer,
    )
    app = FastAPI()
    app.include_router(product_router)
    app.include_router(development_router)
    client = TestClient(app, raise_server_exceptions=True)

    if active_control:
        activation_headers = dict(HEADERS)
        if activation_idempotency_key:
            activation_headers["Idempotency-Key"] = activation_idempotency_key
        response = client.post(
            "/bff/assistant/control-mode/activate",
            json={
                "passphrase": CONTROL_PHRASE,
                "reason": "generate assistant SA/SD for test",
                "mode": AssistantMode.KERNEL_DEBUG.value,
            },
            headers=activation_headers,
        )
        assert response.status_code == 202, response.text

    return client, transcript_store


def _seed_turns(transcript_store: InMemoryTranscriptStore, conversation_id: str) -> None:
    transcript_store.append(
        build_turn(
            session_id=conversation_id,
            role=TurnRole.USER,
            content="Management AI should generate SA/SD and dispatchable worker tasks.",
        )
    )
    transcript_store.append(
        build_turn(
            session_id=conversation_id,
            role=TurnRole.ASSISTANT,
            content="I will inspect the BFF assistant bridge and propose a governed flow.",
        )
    )


def test_dev_docs_generate_requires_active_control_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    client, transcript_store = _make_client(tmp_path, active_control=False)
    _seed_turns(transcript_store, "conv-dev-docs")

    response = client.post(
        "/bff/assistant/dev-docs/generate",
        json={
            "conversationId": "conv-dev-docs",
            "featureSummary": "Generate SA/SD from Management AI conversation",
            "affectedModules": ["assistant", "management_ai"],
        },
        headers=HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["details"]["field"] == "control_mode"


def test_dev_docs_generate_uses_built_in_openclaw_skill_authorizer(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    client, transcript_store = _make_client(tmp_path, active_control=True, skill_authorizer=None)
    _seed_turns(transcript_store, "conv-dev-docs-no-skill")

    response = client.post(
        "/bff/assistant/dev-docs/generate",
        json={
            "conversationId": "conv-dev-docs-no-skill",
            "featureSummary": "Generate SA/SD from Management AI conversation",
            "affectedModules": ["assistant", "management_ai"],
        },
        headers=HEADERS,
    )

    assert response.status_code == 503
    details = response.json()["detail"]["error"]["details"]
    assert details["precondition_failed"] == "openclaw_adapter"


def test_dev_docs_generate_archives_and_emits_signed_task_packet(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    client, transcript_store = _make_client(tmp_path, active_control=True)
    _seed_turns(transcript_store, "conv-dev-docs")

    response = client.post(
        "/bff/assistant/dev-docs/generate",
        json={
            "conversationId": "conv-dev-docs",
            "featureSummary": "Generate SA/SD from Management AI conversation",
            "affectedModules": ["assistant", "management_ai"],
            "proposedOwner": "Codex",
            "emitTaskPacket": True,
        },
        headers=HEADERS,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    packet = body["data"]
    meta = body["meta"]

    assert packet["packetId"].startswith("pkt_")
    assert packet["archiveLocations"]["systemDesign"].endswith("system_design.md")
    assert meta["archived"] is True
    assert meta["devBridge"]["noDirectShellFromWeb"] is True
    assert meta["devBridge"]["handoffMode"] == "repo_local_supervisor_inbox"
    assert meta["devBridge"]["queueCommand"] == "python3 scripts/queue_assistant_dev_task_packet.py"
    assert meta["devBridge"]["queueEndpoint"] == "/bff/assistant/dev-bridge/task-packet"
    assert meta["devBridge"]["queueTaskPacketField"] == "queueTaskPacket"
    assert meta["devBridge"]["drainCommand"] == "python3 scripts/drain_assistant_dev_task_packet_inbox.py"
    assert meta["devBridge"]["supervisorInboxPath"] == ".orchestrator/assistant-dev-packets"

    task_packet = meta["taskPacket"]
    assert task_packet["packetId"] == f"bridge_{packet['packetId']}"
    assert task_packet["signature"]["algorithm"] == "Ed25519"
    assert task_packet["constraints"]["noDirectShellFromWeb"] is True
    assert task_packet["tasks"][0]["owner"] == "Codex"
    assert task_packet["documents"]
    assert task_packet["actor"]["capabilities"] == ["assistant.dev.source"]
    assert task_packet["operatorAuthorization"]["operatorId"] == "op-dev-docs"
    assert task_packet["operatorAuthorization"]["mfaVerified"] is True
    assert task_packet["operatorAuthorization"]["capability"] == "assistant.canonical.mutate"

    system_design_path = tmp_path / packet["archiveLocations"]["systemDesign"]
    assert system_design_path.exists()
    assert "Source Citations" in system_design_path.read_text(encoding="utf-8")

    archived = client.get(
        f"/bff/assistant/dev-docs/{packet['packetId']}",
        headers=HEADERS,
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["data"]["packetId"] == packet["packetId"]

    bridge_response = client.post(
        "/bff/assistant/dev-bridge/task-packet",
        json={"devDocPacket": packet},
        headers=HEADERS,
    )
    assert bridge_response.status_code == 201, bridge_response.text
    assert bridge_response.json()["data"]["packetId"] == f"bridge_{packet['packetId']}"


def test_dev_docs_and_bridge_commands_are_exactly_replayed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    monkeypatch.setenv("PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_REQUIRED", "true")
    monkeypatch.setenv(
        "PANTHEON_ASSISTANT_COMMAND_IDEMPOTENCY_STORE_PATH",
        str(tmp_path / "assistant-command-idempotency.json"),
    )
    authorization_calls = []

    def authorize(skill_id, payload, operator_id, trace_id):
        authorization_calls.append((skill_id, payload, operator_id, trace_id))
        return _allow_skill(skill_id, payload, operator_id, trace_id)

    client, transcript_store = _make_client(
        tmp_path,
        active_control=True,
        skill_authorizer=authorize,
        activation_idempotency_key="activate-dev-doc-idempotency",
    )
    _seed_turns(transcript_store, "conv-dev-docs-idempotency")
    payload = {
        "conversationId": "conv-dev-docs-idempotency",
        "featureSummary": "Generate an exactly-once SA/SD packet",
        "affectedModules": ["assistant", "management_ai"],
        "proposedOwner": "Codex",
        "emitTaskPacket": True,
    }
    generate_headers = {**HEADERS, "Idempotency-Key": "generate-dev-doc-stable"}

    missing_generate = client.post(
        "/bff/assistant/dev-docs/generate",
        json=payload,
        headers=HEADERS,
    )
    assert missing_generate.status_code == 400
    assert (
        missing_generate.json()["detail"]["error"]["details"]["reason"]
        == "idempotency_key_required"
    )
    assert authorization_calls == []

    wrong_tenant_generate = client.post(
        "/bff/assistant/dev-docs/generate",
        json=payload,
        headers={**generate_headers, "X-Tenant-Id": "tenant-other"},
    )
    assert wrong_tenant_generate.status_code == 403
    assert (
        wrong_tenant_generate.json()["detail"]["error"]["details"]["reason"]
        == "tenant_mismatch"
    )
    assert authorization_calls == []

    first = client.post(
        "/bff/assistant/dev-docs/generate",
        json=payload,
        headers=generate_headers,
    )
    replay = client.post(
        "/bff/assistant/dev-docs/generate",
        json=payload,
        headers=generate_headers,
    )
    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert len(authorization_calls) == 1

    conflict = client.post(
        "/bff/assistant/dev-docs/generate",
        json={**payload, "featureSummary": "Different request"},
        headers=generate_headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"]["details"]["reason"] == "idempotency_payload_conflict"

    bridge_payload = {"devDocPacket": first.json()["data"]}
    bridge_headers = {**HEADERS, "X-Idempotency-Key": "bridge-packet-stable"}
    missing_bridge = client.post(
        "/bff/assistant/dev-bridge/task-packet",
        json=bridge_payload,
        headers=HEADERS,
    )
    assert missing_bridge.status_code == 400
    assert (
        missing_bridge.json()["detail"]["error"]["details"]["reason"]
        == "idempotency_key_required"
    )
    bridge = client.post(
        "/bff/assistant/dev-bridge/task-packet",
        json=bridge_payload,
        headers=bridge_headers,
    )
    bridge_replay = client.post(
        "/bff/assistant/dev-bridge/task-packet",
        json=bridge_payload,
        headers=bridge_headers,
    )
    assert bridge.status_code == bridge_replay.status_code == 201
    assert bridge_replay.json() == bridge.json()


def test_dev_docs_generate_can_queue_signed_task_packet_for_supervisor_inbox(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    monkeypatch.delenv("PANTHEON_STATUS_ROOT", raising=False)
    client, transcript_store = _make_client(tmp_path, active_control=True)
    _seed_turns(transcript_store, "conv-dev-docs-queue")

    response = client.post(
        "/bff/assistant/dev-docs/generate",
        json={
            "conversationId": "conv-dev-docs-queue",
            "featureSummary": "Queue Management AI SA/SD work for supervisor pickup",
            "affectedModules": ["assistant", "management_ai", "supervisor"],
            "proposedOwner": "Codex",
            "queueTaskPacket": True,
        },
        headers=HEADERS,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    packet = body["data"]
    meta = body["meta"]
    task_packet = meta["taskPacket"]
    receipt = meta["taskPacketQueueReceipt"]

    assert task_packet["packetId"] == f"bridge_{packet['packetId']}"
    assert task_packet["signature"]["algorithm"] == "Ed25519"
    assert meta["taskPacketQueued"] is True
    assert receipt["status"] == "queued"
    assert receipt["packetId"] == task_packet["packetId"]

    queued_path = tmp_path / ".orchestrator" / "assistant-dev-packets" / "pending" / f"{task_packet['packetId']}.json"
    assert queued_path.exists()
    assert stat.S_IMODE(queued_path.stat().st_mode) == 0o664
    queued = json.loads(queued_path.read_text(encoding="utf-8"))
    assert queued["source"] == "bff_assistant_dev_docs_generate"
    assert queued["taskPacket"]["packetId"] == task_packet["packetId"]
    assert queued["taskPacket"]["signature"] == task_packet["signature"]


def test_dev_bridge_task_packet_route_can_queue_signed_packet(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    monkeypatch.delenv("PANTHEON_STATUS_ROOT", raising=False)
    client, transcript_store = _make_client(tmp_path, active_control=True)
    _seed_turns(transcript_store, "conv-dev-bridge-queue")

    generate_response = client.post(
        "/bff/assistant/dev-docs/generate",
        json={
            "conversationId": "conv-dev-bridge-queue",
            "featureSummary": "Create a task packet then queue it through the bridge route",
            "affectedModules": ["assistant", "supervisor"],
            "proposedOwner": "Codex",
        },
        headers=HEADERS,
    )
    assert generate_response.status_code == 201, generate_response.text
    packet = generate_response.json()["data"]

    bridge_response = client.post(
        "/bff/assistant/dev-bridge/task-packet",
        json={"devDocPacket": packet, "queueTaskPacket": True},
        headers=HEADERS,
    )

    assert bridge_response.status_code == 201, bridge_response.text
    body = bridge_response.json()
    task_packet = body["data"]
    meta = body["meta"]
    receipt = meta["taskPacketQueueReceipt"]

    assert task_packet["packetId"] == f"bridge_{packet['packetId']}"
    assert meta["taskPacketQueued"] is True
    assert receipt["status"] == "queued"
    assert receipt["packetId"] == task_packet["packetId"]
    queued_path = (
        tmp_path
        / ".orchestrator"
        / "assistant-dev-packets"
        / "pending"
        / f"{task_packet['packetId']}.json"
    )
    assert queued_path.exists()
