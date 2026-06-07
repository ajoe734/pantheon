from __future__ import annotations

import os
import sys
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient


BFF_DIR = os.path.dirname(os.path.dirname(__file__))
if BFF_DIR not in sys.path:
    sys.path.insert(0, BFF_DIR)

from assistant.control_mode import ControlModeStore  # noqa: E402
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
        self.claims = {"capabilities": ["assistant.kernel.debug"]}


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


def _make_client(tmp_path, *, active_control: bool) -> tuple[TestClient, InMemoryTranscriptStore]:
    identity = _DevDocsIdentity()
    control_store = ControlModeStore(storage_path="off", initial_passphrase=CONTROL_PHRASE)
    transcript_store = InMemoryTranscriptStore()

    router = create_assistant_router(
        build_context_pack=_context_pack,
        extract_identity=lambda _authorization: identity,
        require_read_role=lambda _identity: None,
        session_store=InMemorySessionStore(),
        transcript_store=transcript_store,
        control_mode_store=control_store,
        dev_docs_repo_root=str(tmp_path),
        bridge_key_store={"assistant-bridge-dev": b"test-dev-bridge-key"},
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=True)

    if active_control:
        response = client.post(
            "/bff/assistant/control-mode/activate",
            json={
                "passphrase": CONTROL_PHRASE,
                "reason": "generate assistant SA/SD for test",
                "mode": AssistantMode.KERNEL_DEBUG.value,
            },
            headers=HEADERS,
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
    assert meta["devBridge"]["drainCommand"] == "python3 scripts/drain_assistant_dev_task_packet_inbox.py"
    assert meta["devBridge"]["supervisorInboxPath"] == ".orchestrator/assistant-dev-packets"

    task_packet = meta["taskPacket"]
    assert task_packet["packetId"] == f"bridge_{packet['packetId']}"
    assert task_packet["signature"]["algorithm"] == "HMAC-SHA256"
    assert task_packet["constraints"]["noDirectShellFromWeb"] is True
    assert task_packet["tasks"][0]["owner"] == "Codex"
    assert task_packet["documents"]

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
