from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from .. import dev_bridge_dispatcher, dev_bridge_inbox
from ..dev_bridge_dispatcher import _task_metadata, dispatch_task_packet
from ..dev_bridge_inbox import drain_task_packet_inbox, queue_task_packet
from ..dev_bridge_models import (
    BridgeActor,
    BridgeDispatchRequest,
    BridgeTask,
    DevTaskPacket,
    TaskDispatchRecord,
)
from ..dev_bridge_signer import has_seen_packet, sign_packet


REPO_ROOT = Path(__file__).resolve().parents[5]
TEST_KEY = b"test-key-for-dev-bridge-reliability"
KEY_STORE = {"assistant-bridge-dev": TEST_KEY}


@pytest.fixture(autouse=True)
def _bridge_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())


def _load_ai_status_module():
    spec = importlib.util.spec_from_file_location(
        "dev_bridge_reliability_ai_status",
        REPO_ROOT / "scripts" / "ai_status.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AI_STATUS = _load_ai_status_module()


def _task(task_id: str) -> BridgeTask:
    return BridgeTask(
        id=task_id,
        title=f"Materialize {task_id}",
        owner="Codex",
        reviewer="Claude",
        phase="Sprint Reliable / Dev bridge",
        dependsOn=["DEP,WITH,COMMAS", "DEP||WITH||PIPES"],
        artifacts=["path,with,commas.py", "path||with||pipes.py"],
        acceptance=["preserve, exact, commas", "preserve || exact || pipes"],
        summary=f"Summary for {task_id}",
    )


def _packet(packet_id: str, *, task_count: int = 1) -> DevTaskPacket:
    return DevTaskPacket(
        packetId=packet_id,
        emittedAt="2026-07-15T00:00:00Z",
        actor=BridgeActor(
            id="management-ai",
            roles=["operator"],
            capabilities=["assistant.kernel.repair"],
        ),
        mode="kernel_repair",
        sourceConversationId="conversation-reliability",
        sourceTurnIds=["turn-user", "turn-assistant"],
        documents=[
            {
                "path": "docs/04/sa_sd_reliability/system_analysis.md",
                "kind": "SA_SD_PLAN",
                "sourceRefs": ["turn-user"],
            }
        ],
        tasks=[_task(f"RELIABLE-TASK-{index:03d}") for index in range(1, task_count + 1)],
        auditConversationHref="/bff/assistant/sessions/conversation-reliability/transcript",
    )


def _signed(packet_id: str, *, task_count: int = 1) -> DevTaskPacket:
    return sign_packet(_packet(packet_id, task_count=task_count), key_store=KEY_STORE)


def _fake_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "ai-status.json").write_text("{}\n", encoding="utf-8")
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "ai_status.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    return root


def test_partial_dispatch_failure_is_retryable_and_only_full_success_marks_seen(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_partial_retry", task_count=2)
    request = BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root))
    first_outcomes = [
        TaskDispatchRecord(
            taskId=packet.tasks[0].id,
            owner="Codex",
            reviewer="Claude",
            status="dispatched",
        ),
        TaskDispatchRecord(
            taskId=packet.tasks[1].id,
            owner="Codex",
            reviewer="Claude",
            status="error",
            error="injected failure",
        ),
    ]
    with patch.object(dev_bridge_dispatcher, "_dispatch_task", side_effect=first_outcomes):
        first = dispatch_task_packet(request, key_store=KEY_STORE)

    assert first.errors == [f"{packet.tasks[1].id}: injected failure"]
    assert not has_seen_packet(packet.packet_id, repo_root=str(repo_root))

    retry_outcomes = [
        TaskDispatchRecord(
            taskId=task.id,
            owner=task.owner,
            reviewer=task.reviewer,
            status="dispatched",
        )
        for task in packet.tasks
    ]
    with patch.object(dev_bridge_dispatcher, "_dispatch_task", side_effect=retry_outcomes):
        retry = dispatch_task_packet(request, key_store=KEY_STORE)

    assert retry.errors == []
    assert has_seen_packet(packet.packet_id, repo_root=str(repo_root))


def test_reusing_completed_packet_id_for_different_payload_fails_closed(tmp_path: Path) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_digest_collision")
    dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )

    changed = _packet("pkt_digest_collision")
    changed = changed.model_copy(update={"intent": "different-intent"})
    changed = sign_packet(changed, key_store=KEY_STORE)
    with pytest.raises(ValueError, match="different payload"):
        dispatch_task_packet(
            BridgeDispatchRequest(packet=changed, repoRoot=str(repo_root)),
            key_store=KEY_STORE,
        )


def test_ai_status_bridge_assignment_preserves_exact_spec_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _signed("pkt_ai_status_exact")
    task = packet.tasks[0]
    metadata = _task_metadata(packet, task)
    monkeypatch.setenv("TASK_METADATA_JSON", json.dumps(metadata))
    monkeypatch.setenv("AI_NAME", "Codex")
    state = AI_STATUS.default_state()
    state["tasks"] = []
    state["handoffs"] = []
    state["blockers"] = []
    state["wave_state"] = {"status": "open"}

    with (
        patch.object(AI_STATUS, "archived_task_snapshot", return_value=None),
        patch.object(AI_STATUS, "append_log") as append_log,
    ):
        first = AI_STATUS.command_assign(
            state,
            [task.id, task.owner, task.reviewer, task.title],
        )
        snapshot = copy.deepcopy(state)
        second = AI_STATUS.command_assign(
            state,
            [task.id, task.owner, task.reviewer, task.title],
        )

    assert first is None
    assert second is False
    assert state == snapshot
    assert append_log.call_count == 1
    assigned = state["tasks"][0]
    spec = metadata["dev_bridge"]["task_spec"]
    assert assigned["depends_on"] == spec["depends_on"]
    assert assigned["artifacts"] == spec["artifacts"]
    assert assigned["acceptance"] == spec["acceptance"]
    assert assigned["dev_bridge"]["packet_id"] == packet.packet_id
    assert assigned["dev_bridge"]["conversation_id"] == packet.source_conversation_id
    assert assigned["dev_bridge"]["source_turn_ids"] == packet.source_turn_ids
    assert assigned["dev_bridge"]["documents"][0]["path"].endswith("system_analysis.md")


def test_ai_status_bridge_assignment_conflicts_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _signed("pkt_ai_status_conflict")
    task = packet.tasks[0]
    metadata = _task_metadata(packet, task)
    monkeypatch.setenv("TASK_METADATA_JSON", json.dumps(metadata))
    state = AI_STATUS.default_state()
    state["tasks"] = []
    state["wave_state"] = {"status": "open"}

    with (
        patch.object(AI_STATUS, "archived_task_snapshot", return_value=None),
        patch.object(AI_STATUS, "append_log"),
    ):
        AI_STATUS.command_assign(state, [task.id, task.owner, task.reviewer, task.title])
        conflicting = copy.deepcopy(metadata)
        conflicting["dev_bridge"]["packet_id"] = "pkt_other_packet"
        monkeypatch.setenv("TASK_METADATA_JSON", json.dumps(conflicting))
        with pytest.raises(SystemExit, match="Bridge assignment conflict"):
            AI_STATUS.command_assign(state, [task.id, task.owner, task.reviewer, task.title])
        provenance_conflict = copy.deepcopy(metadata)
        provenance_conflict["dev_bridge"]["conversation_id"] = "different-conversation"
        monkeypatch.setenv("TASK_METADATA_JSON", json.dumps(provenance_conflict))
        with pytest.raises(SystemExit, match="Bridge assignment conflict"):
            AI_STATUS.command_assign(state, [task.id, task.owner, task.reviewer, task.title])


def test_ai_status_bridge_assignment_rejects_existing_unprovenanced_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _signed("pkt_ai_status_unprovenanced")
    task = packet.tasks[0]
    metadata = _task_metadata(packet, task)
    monkeypatch.setenv("TASK_METADATA_JSON", json.dumps(metadata))
    state = AI_STATUS.default_state()
    state["tasks"] = [
        {
            "id": task.id,
            "title": task.title,
            "owner": task.owner,
            "reviewer": task.reviewer,
            "status": "todo",
        }
    ]
    state["wave_state"] = {"status": "open"}

    with pytest.raises(SystemExit, match="without bridge provenance"):
        AI_STATUS.command_assign(state, [task.id, task.owner, task.reviewer, task.title])


def test_processing_item_is_recovered_after_restart(tmp_path: Path) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_restart_processing")
    queued = queue_task_packet(packet, repo_root=str(repo_root), key_store=KEY_STORE)
    pending = Path(queued["path"])
    processing = pending.parent.parent / "processing" / pending.name
    processing.parent.mkdir(parents=True)
    os.replace(pending, processing)

    result = drain_task_packet_inbox(repo_root=str(repo_root))

    assert result["processedCount"] == 1
    assert result["packets"][0]["packetId"] == packet.packet_id
    assert not processing.exists()
    assert (processing.parent.parent / "processed" / processing.name).exists()


def test_existing_durable_receipt_recovers_without_redispatch(tmp_path: Path) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_receipt_recovery")
    queued = queue_task_packet(packet, repo_root=str(repo_root), key_store=KEY_STORE)
    pending = Path(queued["path"])
    processing = pending.parent.parent / "processing" / pending.name
    processing.parent.mkdir(parents=True)
    os.replace(pending, processing)
    receipt_path = processing.parent.parent / "receipts" / processing.name
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            {
                "packetId": packet.packet_id,
                "status": "processed",
                "result": {"packetId": packet.packet_id},
            }
        ),
        encoding="utf-8",
    )

    with patch.object(
        dev_bridge_inbox,
        "dispatch_task_packet",
        side_effect=AssertionError("receipt recovery must not redispatch"),
    ):
        result = drain_task_packet_inbox(repo_root=str(repo_root))

    assert result["processedCount"] == 1
    assert result["packets"][0]["recoveredFromReceipt"] is True
    assert not processing.exists()
    assert (processing.parent.parent / "processed" / processing.name).exists()


def test_crash_after_receipt_before_archive_is_recovered_without_redispatch(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_crash_after_receipt")
    queued = queue_task_packet(packet, repo_root=str(repo_root), key_store=KEY_STORE)
    pending = Path(queued["path"])
    processing = pending.parent.parent / "processing" / pending.name
    receipt = pending.parent.parent / "receipts" / pending.name

    with patch.object(
        dev_bridge_inbox,
        "_finalize_processing",
        side_effect=OSError("injected rename failure"),
    ):
        first = drain_task_packet_inbox(repo_root=str(repo_root))

    assert first["errorCount"] == 1
    assert processing.exists()
    assert receipt.exists()

    with patch.object(
        dev_bridge_inbox,
        "dispatch_task_packet",
        side_effect=AssertionError("durable receipt must suppress redispatch"),
    ):
        recovered = drain_task_packet_inbox(repo_root=str(repo_root))

    assert recovered["processedCount"] == 1
    assert recovered["packets"][0]["recoveredFromReceipt"] is True
    assert not processing.exists()


def test_receipt_persistence_failure_leaves_processing_for_safe_retry(tmp_path: Path) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_receipt_write_failure")
    queued = queue_task_packet(packet, repo_root=str(repo_root), key_store=KEY_STORE)
    pending = Path(queued["path"])
    processing = pending.parent.parent / "processing" / pending.name
    receipt = pending.parent.parent / "receipts" / pending.name

    with patch.object(
        dev_bridge_inbox,
        "_write_json_atomic",
        side_effect=OSError("injected receipt fsync failure"),
    ):
        first = drain_task_packet_inbox(repo_root=str(repo_root))

    assert first["errorCount"] == 1
    assert processing.exists()
    assert not receipt.exists()
    assert has_seen_packet(packet.packet_id, repo_root=str(repo_root))

    retry = drain_task_packet_inbox(repo_root=str(repo_root))

    assert retry["processedCount"] == 1
    assert retry["packets"][0]["status"] == "replay_rejected"
    assert receipt.exists()
    assert not processing.exists()
