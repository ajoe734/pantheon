from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from ..dev_bridge_inbox import drain_task_packet_inbox, queue_payload, queue_task_packet
from ..dev_bridge_models import BridgeActor, BridgeTask, DevTaskPacket
from ..dev_bridge_signer import sign_packet
from .dev_bridge_test_support import write_materializing_ai_status


TEST_KEY = b"test-key-for-dev-bridge-inbox"


def _make_packet(packet_id: str) -> DevTaskPacket:
    return DevTaskPacket(
        packetId=packet_id,
        emittedAt="2026-06-07T00:00:00Z",
        actor=BridgeActor(
            id="management-ai",
            roles=["operator"],
            capabilities=["assistant.kernel.debug"],
        ),
        mode="kernel_debug",
        sourceConversationId="mgmt-nl-inbox",
        sourceTurnIds=["turn-user", "turn-assistant"],
        tasks=[
            BridgeTask(
                id="INBOX-TASK-001",
                title="Materialize queued assistant task",
                owner="Codex",
                reviewer="Claude",
                phase="Sprint Inbox / Dev bridge",
                artifacts=["execute-plans/src/agora/pages/AskPersonas.tsx"],
                acceptance=["Task is queued then assigned through supervisor drain"],
                summary="Verify assistant dev packet inbox path.",
            )
        ],
        auditConversationHref="/bff/assistant/sessions/mgmt-nl-inbox/transcript",
    )


def _write_fake_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_materializing_ai_status(repo_root)
    return repo_root


def test_queue_and_drain_packet_inbox_materializes_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    signed = sign_packet(_make_packet("pkt_inbox_live"), key_store={"assistant-bridge-dev": TEST_KEY})

    queued = queue_task_packet(signed, repo_root=str(repo_root))

    assert queued["status"] == "queued"
    pending = repo_root / ".orchestrator" / "assistant-dev-packets" / "pending" / "pkt_inbox_live.json"
    assert pending.exists()
    assert stat.S_IMODE(pending.stat().st_mode) == 0o664

    result = drain_task_packet_inbox(repo_root=str(repo_root), limit=4)

    assert result["processedCount"] == 1
    assert result["errorCount"] == 0
    assert result["packets"][0]["packetId"] == "pkt_inbox_live"
    assert result["packets"][0]["status"] == "processed"
    assert not pending.exists()
    assert (repo_root / ".orchestrator" / "assistant-dev-packets" / "processed" / "pkt_inbox_live.json").exists()
    assert (repo_root / ".orchestrator" / "assistant-dev-packets" / "receipts" / "pkt_inbox_live.json").exists()

    calls = (repo_root / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(calls[0])
    assert record["argv"] == [
        "assign",
        "INBOX-TASK-001",
        "Codex",
        "Claude",
        "Materialize queued assistant task",
    ]
    assert record["ai_name"] == "Human/Ops"
    assert record["auto_worker_markers"] == {}
    bridge = record["metadata"]["dev_bridge"]
    assert bridge["packet_id"] == "pkt_inbox_live"
    assert bridge["conversation_id"] == "mgmt-nl-inbox"
    assert bridge["source_turn_ids"] == ["turn-user", "turn-assistant"]
    assert bridge["task_spec"]["phase"] == "Sprint Inbox / Dev bridge"
    assert bridge["task_spec"]["artifacts"] == [
        "execute-plans/src/agora/pages/AskPersonas.tsx"
    ]
    assert bridge["task_spec"]["acceptance"] == [
        "Task is queued then assigned through supervisor drain"
    ]


def test_queue_accepts_dev_docs_response_envelope_and_rejects_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    signed = sign_packet(_make_packet("pkt_inbox_envelope"), key_store={"assistant-bridge-dev": TEST_KEY})
    payload = {
        "data": {"packetId": "dev-doc-packet"},
        "meta": {"taskPacket": signed.model_dump(mode="json", by_alias=True)},
    }

    first = queue_payload(payload, repo_root=str(repo_root))
    duplicate = queue_payload(payload, repo_root=str(repo_root))

    assert first["status"] == "queued"
    assert duplicate["status"] == "duplicate"
    assert duplicate["existing"] == "pending"


def test_queue_rejects_unsigned_packet(tmp_path: Path) -> None:
    repo_root = _write_fake_repo(tmp_path)

    with pytest.raises(ValueError, match="Packet has no signature"):
        queue_task_packet(_make_packet("pkt_inbox_unsigned"), repo_root=str(repo_root))
