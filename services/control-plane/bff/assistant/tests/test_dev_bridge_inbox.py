from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from .. import dev_bridge_dispatcher, dev_bridge_inbox
from ..dev_bridge_inbox import (
    drain_task_packet_inbox,
    queue_payload,
    queue_task_packet,
    recover_failed_task_packet,
)
from ..dev_bridge_models import (
    BridgeActor,
    BridgeDispatchResult,
    BridgeTask,
    DevTaskPacket,
    TaskDispatchRecord,
)
from ..dev_bridge_signer import mark_packet_seen, packet_digest, sign_packet
from .dev_bridge_test_support import write_materializing_ai_status


TEST_KEY = b"test-key-for-dev-bridge-inbox"
REPO_ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture(autouse=True)
def _isolated_bridge_status_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_root = tmp_path / "ambient-status-root"
    status_root.mkdir()
    (status_root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
    (status_root / "ai-activity-log.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(status_root))
    monkeypatch.setenv(
        "PANTHEON_ASSISTANT_DEV_PACKET_INBOX",
        ".orchestrator/assistant-dev-packets",
    )
    for name in (
        "PANTHEON_COMMAND_ROOT",
        "PANTHEON_COMMAND_RUNTIME_SHA",
        "PANTHEON_COMMAND_REMOTE",
        "PANTHEON_COMMAND_BASE_REF",
        "PANTHEON_TASK_STATE_STORE_MODE",
        "PANTHEON_TASK_STATE_EVENT_LOG",
    ):
        monkeypatch.delenv(name, raising=False)


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


def _archive_as_failed(repo_root: Path, packet: DevTaskPacket) -> Path:
    queued = queue_task_packet(
        packet,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    failed_result = BridgeDispatchResult(
        packetId=packet.packet_id,
        dispatchedAt="2026-08-09T08:00:00Z",
        taskRecords=[
            TaskDispatchRecord(
                taskId=task.id,
                owner=task.owner,
                reviewer=task.reviewer,
                status="error",
                error="injected historical partial assign failure",
            )
            for task in packet.tasks
        ],
        auditRefs={
            "packetId": packet.packet_id,
            "packetDigest": packet_digest(packet),
        },
        admissionStatus="not_attempted",
        errors=["injected historical partial assign failure"],
    )
    with patch.object(
        dev_bridge_inbox,
        "dispatch_task_packet",
        return_value=failed_result,
    ):
        drained = drain_task_packet_inbox(repo_root=str(repo_root))
    assert drained["errorCount"] == 1
    failed = (
        Path(queued["path"]).parent.parent
        / "failed"
        / f"{packet.packet_id}.json"
    )
    assert failed.is_file()
    return failed


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


def test_exact_failed_packet_recovery_is_lock_safe_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    packet = sign_packet(
        _make_packet("pkt_exact_failed_recovery"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    failed = _archive_as_failed(repo_root, packet)

    recovered = recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    duplicate = recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )

    assert recovered["status"] == "recovered"
    assert recovered["packetDigest"] == packet_digest(packet)
    assert duplicate["status"] == "already_recovered"
    assert not failed.exists()
    pending = failed.parent.parent / "pending" / failed.name
    assert pending.is_file()
    recovery_record = json.loads(
        (failed.parent.parent / "recoveries" / failed.name).read_text(
            encoding="utf-8"
        )
    )
    assert recovery_record["state"] == "queued"
    assert recovery_record["identity"]["packet_digest"] == packet_digest(packet)
    assert recovery_record["identity"]["signed_provenance"]["actor"] == (
        packet.actor.model_dump(mode="json", by_alias=True)
    )

    drained = drain_task_packet_inbox(repo_root=str(repo_root))
    assert drained["processedCount"] == 1
    assert drained["packets"][0]["result"]["admissionStatus"] == "admitted"


@pytest.mark.parametrize("state_name", ["pending", "processing", "processed"])
def test_failed_recovery_rejects_queue_state_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state_name: str,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    packet = sign_packet(
        _make_packet("pkt_failed_collision"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    failed = _archive_as_failed(repo_root, packet)
    conflicting = failed.parent.parent / state_name / failed.name
    conflicting.parent.mkdir(parents=True, exist_ok=True)
    conflicting.write_bytes(failed.read_bytes())

    with pytest.raises(ValueError, match=f"conflicting failed and {state_name}"):
        recover_failed_task_packet(
            packet.packet_id,
            repo_root=str(repo_root),
            key_store={"assistant-bridge-dev": TEST_KEY},
        )


def test_failed_recovery_rejects_live_dispatch_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    packet = sign_packet(
        _make_packet("pkt_failed_fence_collision"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    _archive_as_failed(repo_root, packet)
    with (
        patch.object(dev_bridge_inbox, "_try_acquire_dispatch_fence", return_value=None),
        pytest.raises(ValueError, match="live dispatcher"),
    ):
        recover_failed_task_packet(
            packet.packet_id,
            repo_root=str(repo_root),
            key_store={"assistant-bridge-dev": TEST_KEY},
        )


@pytest.mark.parametrize("collision_kind", ["receipt", "admission", "replay"])
def test_failed_recovery_rejects_receipt_admission_and_replay_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collision_kind: str,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    packet = sign_packet(
        _make_packet(f"pkt_failed_{collision_kind}_collision"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    failed = _archive_as_failed(repo_root, packet)
    if collision_kind == "receipt":
        receipt_path = failed.parent.parent / "receipts" / failed.name
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["result"]["auditRefs"]["packetDigest"] = "0" * 64
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        expected = "receipt does not bind"
    elif collision_kind == "admission":
        admission_dir = (
            repo_root
            / "ai-task-archive"
            / "tasks"
            / "assistant-dev-bridge-admissions"
        )
        admission_dir.mkdir(parents=True)
        (admission_dir / f"{packet.packet_id}--{'0' * 16}.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
        expected = "conflicting admission"
    else:
        mark_packet_seen(
            packet.packet_id,
            repo_root=str(repo_root),
            digest="0" * 64,
        )
        expected = "conflicting replay"

    with pytest.raises(ValueError, match=expected):
        recover_failed_task_packet(
            packet.packet_id,
            repo_root=str(repo_root),
            key_store={"assistant-bridge-dev": TEST_KEY},
        )


@pytest.mark.parametrize(
    ("identity_path", "replacement"),
    [
        (("packet_digest",), "0" * 64),
        (("signed_provenance", "tasks", 0, "task_spec_hash"), "1" * 64),
        (("signed_provenance", "conversation_id"), "other-conversation"),
    ],
)
def test_failed_recovery_rejects_digest_task_spec_and_provenance_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity_path: tuple[object, ...],
    replacement: str,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    packet = sign_packet(
        _make_packet("pkt_failed_identity_mismatch"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    failed = _archive_as_failed(repo_root, packet)
    identity = dev_bridge_inbox._recovery_identity(packet)
    cursor: object = identity
    for component in identity_path[:-1]:
        cursor = cursor[component]  # type: ignore[index]
    cursor[identity_path[-1]] = replacement  # type: ignore[index]
    recovery_path = failed.parent.parent / "recoveries" / failed.name
    recovery_path.parent.mkdir(parents=True)
    recovery_path.write_text(
        json.dumps(
            {
                "schema": dev_bridge_inbox.FAILED_RECOVERY_SCHEMA,
                "state": "prepared",
                "packet_id": packet.packet_id,
                "identity": identity,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="identity or provenance mismatch"):
        recover_failed_task_packet(
            packet.packet_id,
            repo_root=str(repo_root),
            key_store={"assistant-bridge-dev": TEST_KEY},
        )


def test_failed_recovery_resumes_crash_after_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    packet = sign_packet(
        _make_packet("pkt_failed_recovery_crash"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    failed = _archive_as_failed(repo_root, packet)
    real_write = dev_bridge_inbox._write_json_atomic

    def crash_after_rename(path: Path, payload: dict) -> None:
        if path.parent.name == "recoveries" and payload.get("state") == "queued":
            raise SystemExit("injected recovery crash after rename")
        real_write(path, payload)

    with (
        patch.object(
            dev_bridge_inbox,
            "_write_json_atomic",
            side_effect=crash_after_rename,
        ),
        pytest.raises(SystemExit, match="after rename"),
    ):
        recover_failed_task_packet(
            packet.packet_id,
            repo_root=str(repo_root),
            key_store={"assistant-bridge-dev": TEST_KEY},
        )

    pending = failed.parent.parent / "pending" / failed.name
    assert pending.is_file()
    assert not failed.exists()
    resumed = recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    assert resumed["status"] == "already_recovered"
    recovery = json.loads(
        (failed.parent.parent / "recoveries" / failed.name).read_text(
            encoding="utf-8"
        )
    )
    assert recovery["state"] == "queued"


def test_recovered_packet_dispatches_after_exact_claim_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    packet = sign_packet(
        _make_packet("pkt_recovery_expired_claim"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    _archive_as_failed(repo_root, packet)
    recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    with dev_bridge_dispatcher.packet_replay_lock(repo_root=str(repo_root)):
        state, _claim = dev_bridge_dispatcher._claim_packet_dispatch_locked(
            packet,
            repo_root=str(repo_root),
            digest=packet_digest(packet),
            environment={},
        )
    assert state == "claimed"
    claim_path = dev_bridge_dispatcher._dispatch_claim_path(
        str(repo_root),
        packet.packet_id,
    )
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["expires_at"] = "2026-08-01T00:00:00Z"
    claim_path.write_text(json.dumps(claim), encoding="utf-8")

    drained = drain_task_packet_inbox(repo_root=str(repo_root))

    assert drained["processedCount"] == 1
    assert drained["packets"][0]["result"]["admissionStatus"] == "admitted"
    assert not claim_path.exists()


def test_drain_cli_recovers_only_the_requested_failed_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    packet = sign_packet(
        _make_packet("pkt_cli_failed_recovery"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    failed = _archive_as_failed(repo_root, packet)
    script = REPO_ROOT / "scripts" / "drain_assistant_dev_task_packet_inbox.py"
    environment = {
        **os.environ,
        "PANTHEON_STATUS_ROOT": str(repo_root),
        "BRIDGE_SIGNING_KEY": TEST_KEY.hex(),
    }

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--repo-root",
            str(repo_root),
            "--recover-failed-packet-id",
            packet.packet_id,
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "recovered"
    assert not failed.exists()
    assert (failed.parent.parent / "pending" / failed.name).is_file()
