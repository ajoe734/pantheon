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
from ..dev_bridge_signer import (
    mark_packet_seen,
    packet_digest,
    public_key_environment,
    sign_packet,
)
from .dev_bridge_test_support import (
    authoritative_test_runtime_env,
    write_materializing_ai_status,
)


TEST_KEY = b"test-key-for-dev-bridge-inbox"
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _isolated_bridge_status_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_root = tmp_path / "ambient-status-root"
    status_root.mkdir()
    (status_root / "ai-status.json").write_text('{"tasks": []}\n', encoding="utf-8")
    monkeypatch.setenv(
        "BRIDGE_SIGNING_PUBLIC_KEYS_JSON",
        public_key_environment({"assistant-bridge-dev": TEST_KEY}),
    )
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


def _write_fake_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_materializing_ai_status(repo_root)
    if monkeypatch is not None:
        for name, value in authoritative_test_runtime_env(repo_root).items():
            monkeypatch.setenv(name, value)
    return repo_root


def _archive_as_failed(repo_root: Path, packet: DevTaskPacket) -> Path:
    queue_task_packet(
        packet,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    return _drain_as_failed(
        repo_root,
        packet,
        error="injected historical partial assign failure",
        dispatched_at="2026-08-09T08:00:00Z",
    )


def _drain_as_failed(
    repo_root: Path,
    packet: DevTaskPacket,
    *,
    error: str,
    dispatched_at: str,
) -> Path:
    failed_result = BridgeDispatchResult(
        packetId=packet.packet_id,
        dispatchedAt=dispatched_at,
        taskRecords=[
            TaskDispatchRecord(
                taskId=task.id,
                owner=task.owner,
                reviewer=task.reviewer,
                status="error",
                error=error,
            )
            for task in packet.tasks
        ],
        auditRefs={
            "packetId": packet.packet_id,
            "packetDigest": packet_digest(packet),
        },
        admissionStatus="not_attempted",
        errors=[error],
    )
    with patch.object(
        dev_bridge_inbox,
        "dispatch_task_packet",
        return_value=failed_result,
    ):
        drained = drain_task_packet_inbox(repo_root=str(repo_root))
    assert drained["errorCount"] == 1
    failed = (
        repo_root
        / ".orchestrator"
        / "assistant-dev-packets"
        / "failed"
        / f"{packet.packet_id}.json"
    )
    assert failed.is_file()
    return failed


def test_queue_and_drain_packet_inbox_materializes_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path, monkeypatch)
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
    assert record["argv"][0] == "dev-bridge-materialize-batch"
    assert len(record["argv"]) == 2
    assert record["ai_name"] == "assistant.dev.source"
    assert record["auto_worker_markers"] == {}
    assert record["packet_id"] == "pkt_inbox_live"
    assert len(record["tasks"]) == 1
    assert record["tasks"][0]["task_id"] == "INBOX-TASK-001"
    bridge = record["tasks"][0]["task_metadata"]["dev_bridge"]
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
    repo_root = _write_fake_repo(tmp_path, monkeypatch)
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
    repo_root = _write_fake_repo(tmp_path, monkeypatch)
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


def test_recovered_packet_can_be_rearmed_after_a_new_failed_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path, monkeypatch)
    packet = sign_packet(
        _make_packet("pkt_failed_recovery_rearm"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    failed = _archive_as_failed(repo_root, packet)
    inbox = failed.parent.parent
    initial_receipt = json.loads(
        (inbox / "receipts" / failed.name).read_text(encoding="utf-8")
    )

    recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    _drain_as_failed(
        repo_root,
        packet,
        error="injected recovered-packet drain failure before assignment",
        dispatched_at="2026-08-09T08:01:00Z",
    )
    current_receipt = json.loads(
        (inbox / "receipts" / failed.name).read_text(encoding="utf-8")
    )
    previous_recovery = json.loads(
        (inbox / "recoveries" / failed.name).read_text(encoding="utf-8")
    )
    assert current_receipt != initial_receipt

    rearmed = recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )

    evidence_path = (
        inbox
        / "recovery-rearms"
        / failed.stem
        / "000001.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    recovery = json.loads(
        (inbox / "recoveries" / failed.name).read_text(encoding="utf-8")
    )
    assert rearmed["status"] == "rearmed"
    assert rearmed["rearmAttempt"] == 1
    assert rearmed["rearmEvidencePath"] == str(evidence_path)
    assert evidence["state"] == "queued"
    assert evidence["previous_recovery"] == previous_recovery
    assert evidence["previous_recovery_sha256"] == (
        dev_bridge_inbox._canonical_json_sha256(previous_recovery)
    )
    assert evidence["current_failed_receipt"] == current_receipt
    assert evidence["current_failed_receipt_sha256"] == (
        dev_bridge_inbox._canonical_json_sha256(current_receipt)
    )
    assert evidence["next_recovery"] == recovery
    assert recovery["rearm_attempt"] == 1
    assert recovery["last_rearm"]["failed_receipt_sha256"] == (
        dev_bridge_inbox._canonical_json_sha256(current_receipt)
    )
    assert not failed.exists()
    assert (inbox / "pending" / failed.name).is_file()

    _drain_as_failed(
        repo_root,
        packet,
        error="injected second recovered-packet drain failure",
        dispatched_at="2026-08-09T08:01:30Z",
    )
    rearmed_again = recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    second_evidence_path = evidence_path.with_name("000002.json")
    second_evidence = json.loads(
        second_evidence_path.read_text(encoding="utf-8")
    )
    assert rearmed_again["status"] == "rearmed"
    assert rearmed_again["rearmAttempt"] == 2
    assert second_evidence["previous_recovery"] == recovery
    assert second_evidence["next_recovery"]["rearm_attempt"] == 2

    drained = drain_task_packet_inbox(repo_root=str(repo_root))
    assert drained["processedCount"] == 1
    assert drained["errorCount"] == 0
    assert drained["packets"][0]["result"]["admissionStatus"] == "admitted"


def test_rearm_rejects_a_manual_failed_bounce_with_the_consumed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    packet = sign_packet(
        _make_packet("pkt_failed_recovery_manual_bounce"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    failed = _archive_as_failed(repo_root, packet)
    inbox = failed.parent.parent
    recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    os.replace(inbox / "pending" / failed.name, failed)

    with pytest.raises(ValueError, match="no new failed drain receipt to rearm"):
        recover_failed_task_packet(
            packet.packet_id,
            repo_root=str(repo_root),
            key_store={"assistant-bridge-dev": TEST_KEY},
        )

    assert failed.is_file()
    assert not (inbox / "pending" / failed.name).exists()
    assert not (inbox / "recovery-rearms" / failed.stem).exists()


def test_rearm_resumes_a_crash_after_evidence_and_before_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    packet = sign_packet(
        _make_packet("pkt_failed_recovery_rearm_pre_rename_crash"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    failed = _archive_as_failed(repo_root, packet)
    inbox = failed.parent.parent
    recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    _drain_as_failed(
        repo_root,
        packet,
        error="injected pre-rename rearm crash precursor",
        dispatched_at="2026-08-09T08:01:45Z",
    )
    evidence_path = inbox / "recovery-rearms" / failed.stem / "000001.json"
    real_write = dev_bridge_inbox._write_json_atomic

    def crash_after_evidence(path: Path, payload: dict) -> None:
        real_write(path, payload)
        if path == evidence_path and payload.get("state") == "prepared":
            raise SystemExit("injected crash before rearm rename")

    with (
        patch.object(
            dev_bridge_inbox,
            "_write_json_atomic",
            side_effect=crash_after_evidence,
        ),
        pytest.raises(SystemExit, match="before rearm rename"),
    ):
        recover_failed_task_packet(
            packet.packet_id,
            repo_root=str(repo_root),
            key_store={"assistant-bridge-dev": TEST_KEY},
        )

    assert failed.is_file()
    assert not (inbox / "pending" / failed.name).exists()
    assert json.loads(evidence_path.read_text(encoding="utf-8"))["state"] == (
        "prepared"
    )

    resumed = recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )

    assert resumed["status"] == "rearmed"
    assert resumed["rearmAttempt"] == 1
    assert not failed.exists()
    assert (inbox / "pending" / failed.name).is_file()
    assert json.loads(evidence_path.read_text(encoding="utf-8"))["state"] == (
        "queued"
    )


def test_rearm_resumes_a_crash_after_failed_to_pending_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    packet = sign_packet(
        _make_packet("pkt_failed_recovery_rearm_crash"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    failed = _archive_as_failed(repo_root, packet)
    inbox = failed.parent.parent
    recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    _drain_as_failed(
        repo_root,
        packet,
        error="injected rearm crash precursor",
        dispatched_at="2026-08-09T08:02:00Z",
    )
    recovery_path = inbox / "recoveries" / failed.name
    evidence_path = inbox / "recovery-rearms" / failed.stem / "000001.json"
    real_write = dev_bridge_inbox._write_json_atomic

    def crash_before_recovery_update(path: Path, payload: dict) -> None:
        if path == recovery_path and payload.get("rearm_attempt") == 1:
            raise SystemExit("injected crash after rearm rename")
        real_write(path, payload)

    with (
        patch.object(
            dev_bridge_inbox,
            "_write_json_atomic",
            side_effect=crash_before_recovery_update,
        ),
        pytest.raises(SystemExit, match="after rearm rename"),
    ):
        recover_failed_task_packet(
            packet.packet_id,
            repo_root=str(repo_root),
            key_store={"assistant-bridge-dev": TEST_KEY},
        )

    assert not failed.exists()
    assert (inbox / "pending" / failed.name).is_file()
    assert json.loads(evidence_path.read_text(encoding="utf-8"))["state"] == (
        "prepared"
    )

    resumed = recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )

    assert resumed["status"] == "already_recovered"
    assert resumed["rearmAttempt"] == 1
    assert json.loads(evidence_path.read_text(encoding="utf-8"))["state"] == (
        "queued"
    )
    assert json.loads(recovery_path.read_text(encoding="utf-8"))[
        "rearm_attempt"
    ] == 1


@pytest.mark.parametrize(
    ("digest_field", "expected_error"),
    [
        ("previous_recovery_sha256", "previous digest mismatch"),
        ("current_failed_receipt_sha256", "receipt digest mismatch"),
        ("next_recovery_sha256", "next digest mismatch"),
    ],
)
def test_rearm_rejects_tampered_retry_evidence_before_another_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    digest_field: str,
    expected_error: str,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    packet = sign_packet(
        _make_packet("pkt_failed_recovery_rearm_tamper"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    failed = _archive_as_failed(repo_root, packet)
    inbox = failed.parent.parent
    recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    _drain_as_failed(
        repo_root,
        packet,
        error="injected first rearm failure",
        dispatched_at="2026-08-09T08:03:00Z",
    )
    recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    _drain_as_failed(
        repo_root,
        packet,
        error="injected second rearm failure",
        dispatched_at="2026-08-09T08:04:00Z",
    )
    evidence_path = inbox / "recovery-rearms" / failed.stem / "000001.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence[digest_field] = "0" * 64
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match=expected_error):
        recover_failed_task_packet(
            packet.packet_id,
            repo_root=str(repo_root),
            key_store={"assistant-bridge-dev": TEST_KEY},
        )

    assert failed.is_file()
    assert not (inbox / "pending" / failed.name).exists()
    assert not (inbox / "recovery-rearms" / failed.stem / "000002.json").exists()


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
    repo_root = _write_fake_repo(tmp_path, monkeypatch)
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


def test_drain_cli_recovers_and_rearms_only_the_requested_failed_packet(
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

    _drain_as_failed(
        repo_root,
        packet,
        error="injected CLI rearm failure",
        dispatched_at="2026-08-09T08:05:00Z",
    )
    rearmed = subprocess.run(
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

    assert rearmed.returncode == 0, rearmed.stderr
    rearmed_body = json.loads(rearmed.stdout)
    assert rearmed_body["status"] == "rearmed"
    assert rearmed_body["rearmAttempt"] == 1
    assert not failed.exists()
    assert (failed.parent.parent / "pending" / failed.name).is_file()


def test_drain_retry_fairness_admits_fresh_packet_within_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIDGE_SIGNING_KEY", TEST_KEY.hex())
    repo_root = _write_fake_repo(tmp_path)
    inbox = repo_root / ".orchestrator" / "assistant-dev-packets"

    # Queue 3 retryable packets into inbox
    retryable_packets = [
        sign_packet(_make_packet(f"pkt_retryable_{i}"), key_store={"assistant-bridge-dev": TEST_KEY})
        for i in range(3)
    ]
    for pkt in retryable_packets:
        queue_task_packet(pkt, repo_root=str(repo_root))

    # Dispatch retries to move pending -> processing and create retry state
    fake_retryable_result = BridgeDispatchResult(
        packetId="temp",
        dispatchedAt="2026-08-11T00:00:00Z",
        taskRecords=[],
        auditRefs={},
        admissionStatus="not_attempted",
        retryable=True,
        errors=["retryable error"],
    )

    with patch.object(dev_bridge_inbox, "dispatch_task_packet", return_value=fake_retryable_result):
        drained_retries = drain_task_packet_inbox(repo_root=str(repo_root), limit=3)
    assert drained_retries["errorCount"] == 3

    # Backdate next_attempt_epoch on retries so they are immediately due for retry
    retries_dir = inbox / "retries"
    for r_file in retries_dir.glob("*.json"):
        data = json.loads(r_file.read_text(encoding="utf-8"))
        data["next_attempt_epoch"] = 0.0
        r_file.write_text(json.dumps(data), encoding="utf-8")

    # Queue a fresh packet into pending
    fresh_packet = sign_packet(_make_packet("pkt_fresh_001"), key_store={"assistant-bridge-dev": TEST_KEY})
    queue_task_packet(fresh_packet, repo_root=str(repo_root))

    # Drain with limit=1. With max_items=1 and due retryables present,
    # bounded retry fairness (bound = max(0, 1-1) = 0) reserves 0 slots for retryables
    # before fresh items, ensuring the fresh packet is admitted instead of being starved.
    dispatched_ids: list[str] = []

    def mock_dispatch(req: dev_bridge_inbox.BridgeDispatchRequest, **kwargs: object) -> BridgeDispatchResult:
        dispatched_ids.append(req.packet.packet_id)
        return BridgeDispatchResult(
            packetId=req.packet.packet_id,
            dispatchedAt="2026-08-11T00:00:00Z",
            taskRecords=[
                TaskDispatchRecord(
                    taskId=task.id,
                    owner=task.owner,
                    reviewer=task.reviewer,
                    status="assigned",
                )
                for task in req.packet.tasks
            ],
            auditRefs={
                "packetId": req.packet.packet_id,
                "packetDigest": packet_digest(req.packet),
            },
            admissionStatus="admitted",
        )

    with patch.object(dev_bridge_inbox, "dispatch_task_packet", side_effect=mock_dispatch):
        res = drain_task_packet_inbox(repo_root=str(repo_root), limit=1)

    assert res["processedCount"] == 1
    assert dispatched_ids == ["pkt_fresh_001"]
