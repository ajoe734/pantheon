from __future__ import annotations

import json
import os
import sys
from typing import Any
from unittest import mock

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
from openclaw_ops_client import OpenClawOpsClient  # noqa: E402


CONVERSATION_ID = "mgmt-ai-openclaw-repair-smoke-20260612T011551Z"
SMOKE_TASK_ID = "MGMT-AI-OPENCLAW-REPAIR-SMOKE-20260612T011551Z"
SENTINEL_PATH = "tmp/management-ai-openclaw-smoke/MGMT-AI-OPENCLAW-REPAIR-SMOKE-20260612T011551Z.md"
HEADERS = {"Authorization": "Bearer repair-smoke"}
CONTROL_PHRASE = "repair control phrase"


class _Identity:
    operator_id = "op-repair-smoke"
    roles = ["operator"]
    mfa_verified = True
    claims = {
        "capabilities": [
            "assistant.kernel.debug",
            "assistant.kernel.repair",
            "assistant.canonical.mutate",
        ]
    }


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 201) -> None:
        self.payload = payload
        self.status_code = status_code

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def getcode(self) -> int:
        return self.status_code

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _context_pack(session_id: str, request: Any, actor: Any) -> dict[str, Any]:
    mode = request.mode.value if hasattr(request.mode, "value") else str(request.mode)
    return {
        "context_pack_id": f"ctx-{session_id}",
        "session_id": session_id,
        "mode": {"value": mode},
        "actor": {
            "operator_id": actor.operator_id,
            "roles": list(actor.roles),
            "capabilities": list(actor.claims["capabilities"]),
        },
        "sources": [
            {
                "source_id": "management_nl",
                "href": f"/bff/management/ai/conversations/{session_id}",
                "snapshot_at": "2026-06-12T01:15:51Z",
                "source_kind": "conversation",
            },
            {
                "source_id": "repair_worktree_prepare",
                "href": "/bff/assistant/repair-worktrees/prepare",
                "snapshot_at": "2026-06-12T01:15:52Z",
                "source_kind": "bff",
            },
            {
                "source_id": "orchestrator_status",
                "href": "/bff/assistant/orchestrator/status",
                "snapshot_at": "2026-06-12T01:15:53Z",
                "source_kind": "bff",
            },
        ],
        "backend": {
            "management_nl": {
                "data": {
                    "conversation": {"source": "server", "storedTurnCount": 2},
                    "ui": {"currentRoute": "/management/cockpit"},
                }
            },
            "orchestrator_status": {
                "data": {
                    "assistantDevBridge": {"inbox": {"exists": True}},
                    "providerReadiness": {"ready": True},
                }
            },
        },
    }


def _allow_skill(
    skill_id: str,
    payload: dict[str, Any],
    operator_id: str,
    trace_id: str | None,
) -> dict[str, Any]:
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
    monkeypatch,
    *,
    mode: AssistantMode,
    prepare_repair_worktree: Any = None,
) -> tuple[TestClient, InMemoryTranscriptStore]:
    monkeypatch.setenv("PANTHEON_ASSISTANT_KERNEL_ENABLED", "true")
    monkeypatch.setenv("PANTHEON_ASSISTANT_REPAIR_RECEIPT_KEY", "test-repair-receipt-key")
    monkeypatch.delenv("PANTHEON_STATUS_ROOT", raising=False)

    identity = _Identity()
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
        bridge_key_store={"assistant-bridge-dev": b"repair-smoke-key"},
        authorize_assistant_skill=_allow_skill,
        prepare_repair_worktree=prepare_repair_worktree,
    )
    app = FastAPI()
    app.include_router(product_router)
    app.include_router(development_router)
    client = TestClient(app, raise_server_exceptions=True)

    response = client.post(
        "/bff/assistant/control-mode/activate",
        json={
            "passphrase": CONTROL_PHRASE,
            "mode": mode.value,
            "reason": "smoke management ai openclaw repair regression",
            "managementSessionId": CONVERSATION_ID,
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
            content=(
                "Run the OpenClaw repair smoke: prepare a clean task worktree, write only "
                f"{SENTINEL_PATH}, then generate SA/SD and queue the DevTaskPacket."
            ),
        )
    )
    transcript_store.append(
        build_turn(
            session_id=conversation_id,
            role=TurnRole.ASSISTANT,
            content="I will use BFF control mode, repair-worktrees/prepare, management/nl/ask, and dev-docs/generate.",
        )
    )


def _prepare_payload() -> dict[str, Any]:
    return {
        "taskId": SMOKE_TASK_ID,
        "repoKey": "execute-plans",
        "declaredScope": ["tmp/management-ai-openclaw-smoke"],
        "expectedBranch": f"task/{SMOKE_TASK_ID}",
        "mergeTarget": "dev",
        "reason": "Management AI OpenClaw repair E2E smoke",
        "traceId": "trace-prepare-smoke",
    }


def test_openclaw_client_prepare_repair_worktree_posts_adapter_contract(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-adapter:8104")
    monkeypatch.setenv("PANTHEON_ASSISTANT_REPAIR_PREPARE_TIMEOUT_SECONDS", "12.5")
    recorded: dict[str, Any] = {}

    def fake_urlopen(request, timeout):
        recorded["url"] = request.full_url
        recorded["headers"] = dict(request.header_items())
        recorded["body"] = json.loads(request.data.decode("utf-8"))
        recorded["timeout"] = timeout
        return _FakeHttpResponse(
            {
                "status": "ok",
                "data": {
                    "repair": {
                        "task_id": SMOKE_TASK_ID,
                        "task_worktree": "/srv/worktrees/repair-smoke",
                    },
                    "workflow": {"clean": True},
                },
            },
            status_code=201,
        )

    payload = {
        "task_id": SMOKE_TASK_ID,
        "repo_key": "execute-plans",
        "declared_scope": ["tmp/management-ai-openclaw-smoke"],
        "expected_branch": f"task/{SMOKE_TASK_ID}",
        "merge_target": "dev",
    }
    with mock.patch("openclaw_ops_client.urllib.request.urlopen", fake_urlopen):
        result = OpenClawOpsClient(timeout_seconds=1.5).prepare_assistant_repair_worktree(
            payload=payload,
            operator_id="op-repair-smoke",
            trace_id="trace-repair-smoke",
        )

    assert result["data"]["workflow"]["clean"] is True
    assert recorded["url"] == (
        "http://openclaw-adapter:8104/api/openclaw-adapter/assistant/repair-worktrees/prepare"
    )
    assert recorded["headers"]["X-operator-id"] == "op-repair-smoke"
    assert recorded["headers"]["X-trace-id"] == "trace-repair-smoke"
    assert recorded["body"] == payload
    assert recorded["timeout"] == 12.5


def test_repair_worktree_prepare_route_requires_kernel_repair_and_delegates(tmp_path, monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_prepare(payload: dict[str, Any], operator_id: str, trace_id: str | None) -> dict[str, Any]:
        calls.append({"payload": payload, "operator_id": operator_id, "trace_id": trace_id})
        return {
            "status": "ok",
            "data": {
                "repair": {
                    "task_id": payload["task_id"],
                    "repo_key": payload["repo_key"],
                    "task_worktree": "/srv/worktrees/repair-smoke",
                    "declared_scope": payload["declared_scope"],
                    "expected_branch": payload["expected_branch"],
                    "remote": "origin",
                    "merge_target": payload["merge_target"],
                },
                "workflow": {"clean": True},
            },
        }

    debug_client, _ = _make_client(
        tmp_path,
        monkeypatch,
        mode=AssistantMode.KERNEL_DEBUG,
        prepare_repair_worktree=fake_prepare,
    )
    denied = debug_client.post(
        "/bff/assistant/repair-worktrees/prepare",
        json=_prepare_payload(),
        headers=HEADERS,
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["detail"]["error"]["details"]["reason"] == "kernel_repair_required"
    assert calls == []

    client, _ = _make_client(
        tmp_path,
        monkeypatch,
        mode=AssistantMode.KERNEL_REPAIR,
        prepare_repair_worktree=fake_prepare,
    )
    response = client.post(
        "/bff/assistant/repair-worktrees/prepare",
        json=_prepare_payload(),
        headers=HEADERS,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["data"]["workflow"]["clean"] is True
    assert body["data"]["repair"]["task_worktree"] == "/srv/worktrees/repair-smoke"
    assert calls[0]["operator_id"] == "op-repair-smoke"
    assert calls[0]["trace_id"] == "trace-prepare-smoke"
    prepared = calls[0]["payload"]
    assert prepared["task_id"] == SMOKE_TASK_ID
    assert prepared["repo_key"] == "execute-plans"
    assert prepared["declared_scope"] == ["tmp/management-ai-openclaw-smoke"]
    assert prepared["expected_branch"] == f"task/{SMOKE_TASK_ID}"
    assert prepared["merge_target"] == "dev"
    assert prepared["operator_id"] == "op-repair-smoke"
    assert prepared["control_mode"]["mode"] == "kernel_repair"


def test_dev_docs_generate_archives_architecture_ui_and_queues_task_packet(tmp_path, monkeypatch) -> None:
    client, transcript_store = _make_client(
        tmp_path,
        monkeypatch,
        mode=AssistantMode.KERNEL_REPAIR,
    )
    _seed_turns(transcript_store, CONVERSATION_ID)

    response = client.post(
        "/bff/assistant/dev-docs/generate",
        json={
            "conversationId": CONVERSATION_ID,
            "featureSummary": "Smoke Management AI OpenClaw repair worktree write, SA/SD generation, and supervisor DevTaskPacket queueing.",
            "affectedModules": [
                "execute-plans:management-ai",
                "pantheon:bff-assistant",
                "pantheon:openclaw-dev-bridge",
                SENTINEL_PATH,
            ],
            "proposedOwner": "Codex",
            "proposedReviewer": "Claude",
            "queueTaskPacket": True,
            "extraContext": {
                "smoke": True,
                "source": "scripts/smoke_management_ai_openclaw_repair_e2e.sh",
                "repairSentinel": SENTINEL_PATH,
            },
        },
        headers=HEADERS,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    packet = body["data"]
    locations = packet["archiveLocations"]
    meta = body["meta"]

    assert locations["requirementCapture"].startswith("docs/04/")
    assert locations["systemAnalysis"].startswith("docs/04/")
    assert locations["systemDesign"].startswith("docs/04/")
    assert locations["architectureDocs"][0].startswith("docs/02-architecture/")
    assert locations["uiDocs"][0].startswith("docs/05-ui/")
    assert locations["taskBriefs"][0].startswith(".orchestrator/task-briefs/")

    for relative_path in (
        locations["requirementCapture"],
        locations["systemAnalysis"],
        locations["systemDesign"],
        locations["architectureDocs"][0],
        locations["uiDocs"][0],
        locations["taskBriefs"][0],
    ):
        text = (tmp_path / relative_path).read_text(encoding="utf-8")
        assert "Source Citations" in text
        assert CONVERSATION_ID in text

    artifacts = set(packet["executionTasks"][0]["artifacts"])
    assert any(path.startswith("docs/04/") for path in artifacts)
    assert any(path.startswith("docs/02-architecture/") for path in artifacts)
    assert any(path.startswith("docs/05-ui/") for path in artifacts)
    assert packet["executionTasks"][0]["owner"] == "Codex"
    assert packet["executionTasks"][0]["reviewer"] == "Claude"

    assert meta["taskPacketQueued"] is True
    task_packet = meta["taskPacket"]
    document_paths = {document["path"] for document in task_packet["documents"]}
    assert locations["architectureDocs"][0] in document_paths
    assert locations["uiDocs"][0] in document_paths
    assert locations["taskBriefs"][0] in document_paths

    queued_path = (
        tmp_path
        / ".orchestrator"
        / "assistant-dev-packets"
        / "pending"
        / f"{task_packet['packetId']}.json"
    )
    assert queued_path.exists()
