from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from .. import dev_bridge_admission, dev_bridge_dispatcher, dev_bridge_inbox
from ..dev_bridge_dispatcher import _task_metadata, dispatch_task_packet
from ..dev_bridge_inbox import drain_task_packet_inbox, queue_task_packet
from ..dev_bridge_models import (
    BridgeActor,
    BridgeDispatchRequest,
    BridgeTask,
    DevTaskPacket,
    TaskDispatchRecord,
)
from ..dev_bridge_signer import (
    has_seen_packet,
    mark_packet_seen,
    packet_digest,
    sign_packet,
)
from .dev_bridge_test_support import write_materializing_ai_status


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
    write_materializing_ai_status(root)
    return root


def _git_stdout(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _authoritative_status_root(tmp_path: Path) -> tuple[Path, Path, dict]:
    root = tmp_path / "status-root"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    state = AI_STATUS.default_state()
    state["tasks"] = []
    state["handoffs"] = []
    state["blockers"] = []
    state["wave_state"] = {"status": "open"}
    (root / "ai-status.json").write_text(
        json.dumps(state, indent=2) + "\n",
        encoding="utf-8",
    )
    event_log = tmp_path / "runtime" / "task-state-events.jsonl"
    event_log.parent.mkdir()
    AI_STATUS.append_state_commit(event_log, state, source="bridge-test-fixture")
    return root, event_log, state


def test_verified_bridge_uses_trusted_status_actor_without_worker_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_trusted_status_actor")
    monkeypatch.setenv("ORCH_RUN_ID", "untrusted-worker-run")
    monkeypatch.setenv("ORCH_TASK_ID", "UNRELATED-TASK")
    monkeypatch.setenv("PANTHEON_WORKTREE_ROOT", str(tmp_path / "worker"))
    monkeypatch.setenv("ORCH_WORKSPACE_PATH", str(tmp_path / "worker"))
    monkeypatch.setenv(
        "ORCH_RUNNER_STATUS_PATH",
        str(tmp_path / ".orchestrator" / "worker-runtime" / "status" / "run.json"),
    )
    monkeypatch.setenv(
        "ORCH_HEARTBEAT_PATH",
        str(tmp_path / ".orchestrator" / "worker-runtime" / "heartbeats" / "run.json"),
    )

    result = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )

    assert result.errors == []
    call = json.loads(
        (repo_root / "calls.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert call["ai_name"] == "Human/Ops"
    assert call["auto_worker_markers"] == {}
    assert call["metadata"]["dev_bridge"]["actor"]["id"] == "management-ai"


def test_untrusted_direct_status_mutation_still_requires_worker_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_NAME", "Codex")
    for marker in (
        "ORCH_RUN_ID",
        "ORCH_TASK_ID",
        "PANTHEON_WORKTREE_ROOT",
        "ORCH_WORKSPACE_PATH",
        "ORCH_RUNNER_STATUS_PATH",
        "ORCH_HEARTBEAT_PATH",
    ):
        monkeypatch.delenv(marker, raising=False)

    with pytest.raises(RuntimeError, match="status command lease required"):
        AI_STATUS.validate_active_status_command_lease(
            "assign",
            ["UNTRUSTED-TASK", "Codex", "Claude", "Untrusted mutation"],
        )


def test_supervisor_runtime_state_discovers_authoritative_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_root = tmp_path / "status-root"
    runtime_dir = status_root / ".orchestrator"
    runtime_dir.mkdir(parents=True)
    event_log = tmp_path / "runtime" / "task-state-events.jsonl"
    event_log.parent.mkdir()
    event_log.touch()
    (runtime_dir / "state.json").write_text(
        json.dumps(
            {
                "supervisor": {
                    "task_state_shadow": {
                        "mode": "authoritative",
                        "ok": True,
                        "event_log": str(event_log),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    for name in (
        "PANTHEON_STATUS_ROOT",
        "PANTHEON_COMMAND_ROOT",
        "PANTHEON_COMMAND_RUNTIME_SHA",
        "PANTHEON_TASK_STATE_STORE_MODE",
        "PANTHEON_TASK_STATE_EVENT_LOG",
    ):
        monkeypatch.delenv(name, raising=False)

    task_state_env = dev_bridge_dispatcher._runtime_task_state_env(
        str(status_root)
    )

    assert task_state_env == {
        "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
        "PANTHEON_TASK_STATE_EVENT_LOG": str(event_log),
    }
    with patch.object(
        dev_bridge_dispatcher,
        "_code_repo_root",
        return_value=REPO_ROOT,
    ):
        assert dev_bridge_dispatcher._governed_command_root(
            str(status_root),
            task_state_env=task_state_env,
        ) == REPO_ROOT


def test_supervisor_runtime_state_rejects_symlinked_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_root = tmp_path / "status-root"
    runtime_dir = status_root / ".orchestrator"
    runtime_dir.mkdir(parents=True)
    real_event_log = tmp_path / "runtime" / "task-state-events.jsonl"
    real_event_log.parent.mkdir()
    real_event_log.touch()
    linked_event_log = tmp_path / "linked-task-state-events.jsonl"
    linked_event_log.symlink_to(real_event_log)
    (runtime_dir / "state.json").write_text(
        json.dumps(
            {
                "supervisor": {
                    "task_state_shadow": {
                        "mode": "authoritative",
                        "ok": True,
                        "event_log": str(linked_event_log),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    for name in (
        "PANTHEON_STATUS_ROOT",
        "PANTHEON_TASK_STATE_STORE_MODE",
        "PANTHEON_TASK_STATE_EVENT_LOG",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="symlink component"):
        dev_bridge_dispatcher._runtime_task_state_env(str(status_root))


def test_authoritative_bridge_dispatch_survives_next_projection_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_root, event_log, initial_state = _authoritative_status_root(tmp_path)
    command_sha = _git_stdout(REPO_ROOT, "rev-parse", "HEAD")
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(status_root))
    monkeypatch.setenv("PANTHEON_COMMAND_ROOT", str(REPO_ROOT))
    monkeypatch.setenv("PANTHEON_COMMAND_RUNTIME_SHA", command_sha)
    monkeypatch.setenv("PANTHEON_COMMAND_REMOTE", "ajoe734/pantheon")
    monkeypatch.setenv("PANTHEON_COMMAND_BASE_REF", "HEAD")
    monkeypatch.setenv("PANTHEON_TASK_STATE_STORE_MODE", "authoritative")
    monkeypatch.setenv("PANTHEON_TASK_STATE_EVENT_LOG", str(event_log))
    for marker in (
        "ORCH_RUN_ID",
        "ORCH_TASK_ID",
        "PANTHEON_WORKTREE_ROOT",
        "ORCH_WORKSPACE_PATH",
        "ORCH_RUNNER_STATUS_PATH",
        "ORCH_HEARTBEAT_PATH",
    ):
        monkeypatch.delenv(marker, raising=False)
    packet = _signed("pkt_authoritative_projection")
    initial_event_count = AI_STATUS.load_snapshot(event_log)["event_count"]

    queued = queue_task_packet(
        packet,
        repo_root=str(status_root),
        key_store=KEY_STORE,
    )
    assert queued["status"] == "queued"
    drained = drain_task_packet_inbox(repo_root=str(status_root), limit=1)

    assert drained["processedCount"] == 1
    assert drained["errorCount"] == 0
    receipt = drained["packets"][0]
    assert receipt["status"] == "processed"
    assert receipt["result"]["admissionStatus"] == "admitted"
    admission_path = status_root / receipt["result"]["admissionRecord"][
        "admission_record_path"
    ]
    assert admission_path.is_file()
    snapshot = AI_STATUS.load_snapshot(event_log)
    assert snapshot["event_count"] > initial_event_count
    assert any(
        task.get("id") == packet.tasks[0].id
        for task in snapshot["state"].get("tasks", [])
    )

    # Recreate the failure boundary: a stale file-only writer can place the
    # old state on disk, but the next authoritative projection must restore
    # the journaled bridge assignment rather than wash it out.
    status_path = status_root / "ai-status.json"
    status_path.write_text(
        json.dumps(initial_state, indent=2) + "\n",
        encoding="utf-8",
    )
    status_path.write_text(
        json.dumps(snapshot["state"], indent=2) + "\n",
        encoding="utf-8",
    )
    projected = json.loads(status_path.read_text(encoding="utf-8"))
    assert any(
        task.get("id") == packet.tasks[0].id
        for task in projected.get("tasks", [])
    )


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


def test_success_persists_nonterminal_bridge_admission_with_exact_provenance(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_admission_record", task_count=2)

    result = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )

    admission = result.admission_record
    assert admission is not None
    assert admission["schema"] == "pantheon.assistant-dev-bridge-admission.v1"
    assert admission["record_kind"] == "assistant_dev_bridge_admission"
    assert admission["durable"] is True
    assert admission["record_payload_sha256"] == (
        dev_bridge_admission.admission_record_payload_digest(admission)
    )
    assert admission["packet_id"] == packet.packet_id
    assert admission["packet_digest"] == result.audit_refs["packetDigest"]
    assert admission["conversation_id"] == packet.source_conversation_id
    assert admission["source_turn_ids"] == packet.source_turn_ids
    assert admission["documents"] == [
        document.model_dump(mode="json", by_alias=True)
        for document in packet.documents
    ]
    assert [item["task_id"] for item in admission["tasks"]] == [
        task.id for task in packet.tasks
    ]
    assert all(len(item["task_spec_hash"]) == 64 for item in admission["tasks"])
    relative_path = Path(admission["admission_record_path"])
    assert relative_path.parts[:3] == (
        "ai-task-archive",
        "tasks",
        "assistant-dev-bridge-admissions",
    )
    persisted = repo_root / relative_path
    assert persisted.is_file()
    assert (persisted.stat().st_mode & 0o777) == 0o600
    assert not (repo_root / "ai-task-archive" / "tasks" / f"{packet.tasks[0].id}.json").exists()

    replay = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )
    assert replay.replay_rejected is True
    assert replay.admission_record == admission


def test_admission_persistence_failure_keeps_packet_retryable(tmp_path: Path) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_admission_retry")
    request = BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root))

    with patch.object(
        dev_bridge_dispatcher,
        "persist_admission_record",
        side_effect=OSError("injected admission fsync failure"),
    ):
        first = dispatch_task_packet(request, key_store=KEY_STORE)

    assert first.admission_record is None
    assert first.errors == ["bridge admission: injected admission fsync failure"]
    assert first.retryable is True
    assert first.admission_status == "admission_persistence_retryable"
    assert not has_seen_packet(packet.packet_id, repo_root=str(repo_root))

    retry = dispatch_task_packet(request, key_store=KEY_STORE)
    assert retry.errors == []
    assert retry.admission_record is not None
    assert has_seen_packet(packet.packet_id, repo_root=str(repo_root))


def test_crash_after_admission_before_replay_mark_recovers_exact_record(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_admission_before_seen")
    request = BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root))

    with patch.object(
        dev_bridge_dispatcher,
        "mark_packet_seen",
        side_effect=OSError("injected replay-store fsync failure"),
    ):
        first = dispatch_task_packet(request, key_store=KEY_STORE)

    assert first.retryable is True
    assert first.admission_status == "replay_mark_persistence_retryable"
    assert first.errors == ["bridge replay mark: injected replay-store fsync failure"]

    digest = dev_bridge_dispatcher.packet_digest(packet)
    durable = dev_bridge_admission.load_admission_record(
        repo_root=str(repo_root),
        packet_id=packet.packet_id,
        packet_digest=digest,
    )
    assert durable is not None
    assert not has_seen_packet(packet.packet_id, repo_root=str(repo_root))

    recovered = dispatch_task_packet(request, key_store=KEY_STORE)
    assert recovered.errors == []
    assert recovered.admission_record == durable
    assert has_seen_packet(packet.packet_id, repo_root=str(repo_root))


def test_seen_digest_without_admission_fails_closed(tmp_path: Path) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_seen_without_admission")
    mark_packet_seen(
        packet.packet_id,
        repo_root=str(repo_root),
        digest=packet_digest(packet),
    )

    replay = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )

    assert replay.replay_rejected is True
    assert replay.admission_record is None
    assert replay.admission_status == "missing_replay_admission"
    assert replay.retryable is False
    assert replay.errors == [
        "bridge admission replay validation: durable admission record is missing"
    ]


def test_success_exit_without_materialized_task_cannot_create_admission(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    (repo_root / "scripts" / "ai_status.py").write_text(
        "import sys\nsys.exit(0)\n",
        encoding="utf-8",
    )
    packet = _signed("pkt_fake_dispatch_success")

    result = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )

    assert result.admission_record is None
    assert result.admission_status == "invalid_materialization"
    assert result.retryable is False
    assert result.errors == [
        f"bridge materialization: materialized task {packet.tasks[0].id!r} is missing"
    ]
    assert not has_seen_packet(packet.packet_id, repo_root=str(repo_root))


def test_missing_admission_replay_is_failed_not_processed_by_inbox(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_inbox_missing_admission")
    queue_task_packet(packet, repo_root=str(repo_root), key_store=KEY_STORE)
    mark_packet_seen(
        packet.packet_id,
        repo_root=str(repo_root),
        digest=packet_digest(packet),
    )

    drained = drain_task_packet_inbox(repo_root=str(repo_root))

    receipt = drained["packets"][0]
    inbox = repo_root / ".orchestrator" / "assistant-dev-packets"
    assert receipt["status"] == "failed"
    assert receipt["nonAdmittedReplay"] is True
    assert receipt.get("recoveredFromReplay") is None
    assert receipt["result"]["admissionStatus"] == "missing_replay_admission"
    assert receipt["result"]["admissionRecord"] is None
    assert drained["errorCount"] == 1
    assert (inbox / "failed" / f"{packet.packet_id}.json").is_file()


def test_legacy_id_only_replay_is_nonadmitted_and_cannot_recover_inbox(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_legacy_nonadmitted")
    queue_task_packet(packet, repo_root=str(repo_root), key_store=KEY_STORE)
    mark_packet_seen(packet.packet_id, repo_root=str(repo_root), digest=None)

    drained = drain_task_packet_inbox(repo_root=str(repo_root))

    receipt = drained["packets"][0]
    assert receipt["status"] == "failed"
    assert receipt["nonAdmittedReplay"] is True
    assert receipt["result"]["admissionStatus"] == "legacy_non_admitted_replay"
    assert "non-admitted" in receipt["result"]["errors"][0]
    assert drained["errorCount"] == 1


def test_admission_tamper_fails_payload_and_signed_provenance_validation(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_admission_tamper")
    first = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )
    assert first.admission_record is not None
    relative_path = Path(first.admission_record["admission_record_path"])
    persisted = repo_root / relative_path
    tampered = json.loads(persisted.read_text(encoding="utf-8"))
    tampered["actor"] = {"id": "tampered"}
    persisted.write_text(json.dumps(tampered), encoding="utf-8")

    accidental = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )
    assert accidental.admission_status == "invalid_replay_admission"
    assert "payload digest mismatch" in accidental.errors[0]

    tampered["record_payload_sha256"] = (
        dev_bridge_admission.admission_record_payload_digest(tampered)
    )
    persisted.write_text(json.dumps(tampered), encoding="utf-8")
    recomputed = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )
    assert recomputed.admission_status == "invalid_replay_admission"
    assert "signed provenance mismatch: actor" in recomputed.errors[0]


def test_replay_requires_authoritative_materialized_task_provenance(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_materialized_tamper")
    first = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )
    assert first.admission_status == "admitted"
    status_path = repo_root / "ai-status.json"
    state = json.loads(status_path.read_text(encoding="utf-8"))
    state["tasks"][0]["dev_bridge"]["conversation_id"] = "tampered-conversation"
    status_path.write_text(json.dumps(state), encoding="utf-8")

    replay = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )

    assert replay.replay_rejected is True
    assert replay.admission_status == "invalid_replay_materialization"
    assert replay.retryable is False
    assert "signed bridge provenance" in replay.errors[0]


def test_replay_accepts_exact_terminal_task_snapshot_after_active_prune(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_materialized_terminal")
    first = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )
    assert first.admission_status == "admitted"
    status_path = repo_root / "ai-status.json"
    state = json.loads(status_path.read_text(encoding="utf-8"))
    task = state["tasks"].pop()
    status_path.write_text(json.dumps(state), encoding="utf-8")
    archive_path = repo_root / "ai-task-archive" / "tasks" / f"{task['id']}.json"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(json.dumps({"task": task}), encoding="utf-8")

    replay = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )

    assert replay.replay_rejected is True
    assert replay.admission_status == "admitted_replay"
    assert replay.errors == []


def test_admission_parent_symlink_fails_closed_without_outside_write(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    archive_tasks = repo_root / "ai-task-archive" / "tasks"
    archive_tasks.mkdir(parents=True)
    (archive_tasks / "assistant-dev-bridge-admissions").symlink_to(
        outside,
        target_is_directory=True,
    )

    packet = _signed("pkt_admission_symlink")
    result = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
    )

    assert result.admission_status == "invalid_admission"
    assert result.retryable is False
    assert any("symlink or non-directory" in error for error in result.errors)
    assert list(outside.iterdir()) == []
    assert not has_seen_packet(packet.packet_id, repo_root=str(repo_root))


@pytest.mark.parametrize(
    ("patched_name", "expected_status"),
    [
        ("persist_admission_record", "admission_persistence_retryable"),
        ("mark_packet_seen", "replay_mark_persistence_retryable"),
    ],
)
def test_inbox_retries_transient_admission_commit_failures_without_terminal_receipt(
    tmp_path: Path,
    patched_name: str,
    expected_status: str,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed(f"pkt_transient_{patched_name}")
    queue_task_packet(packet, repo_root=str(repo_root), key_store=KEY_STORE)
    inbox = repo_root / ".orchestrator" / "assistant-dev-packets"

    with patch.object(
        dev_bridge_dispatcher,
        patched_name,
        side_effect=OSError("injected transient durability failure"),
    ):
        first = drain_task_packet_inbox(repo_root=str(repo_root))

    first_error = first["errors"][0]
    assert first_error["status"] == "retryable"
    assert first_error["result"]["retryable"] is True
    assert first_error["result"]["admissionStatus"] == expected_status
    assert (inbox / "processing" / f"{packet.packet_id}.json").is_file()
    assert not (inbox / "receipts" / f"{packet.packet_id}.json").exists()
    assert not (inbox / "failed" / f"{packet.packet_id}.json").exists()

    recovered = drain_task_packet_inbox(repo_root=str(repo_root))
    receipt = recovered["packets"][0]
    assert receipt["status"] == "processed"
    assert receipt["result"]["admissionRecord"] is not None
    assert (inbox / "processed" / f"{packet.packet_id}.json").is_file()
    assert (inbox / "receipts" / f"{packet.packet_id}.json").is_file()
    assert not (inbox / "processing" / f"{packet.packet_id}.json").exists()


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
    recovered = retry["packets"][0]
    assert recovered["status"] == "processed"
    assert recovered["recoveredFromReplay"] is True
    assert recovered["result"]["replayRejected"] is True
    assert receipt.exists()
    persisted = json.loads(receipt.read_text(encoding="utf-8"))
    assert persisted["status"] == "processed"
    assert persisted["recoveredFromReplay"] is True
    assert persisted["result"]["replayRejected"] is True
    assert not processing.exists()
