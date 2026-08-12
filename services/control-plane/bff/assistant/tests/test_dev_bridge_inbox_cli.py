from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from .. import dev_bridge_inbox
from ..dev_bridge_inbox import (
    drain_task_packet_inbox,
    queue_task_packet,
    recover_failed_task_packet,
)
from ..dev_bridge_models import BridgeActor, BridgeTask, DevTaskPacket
from ..dev_bridge_models import BridgeDispatchResult, TaskDispatchRecord
from ..dev_bridge_signer import packet_digest, public_key_environment, sign_packet
from .dev_bridge_test_support import write_materializing_ai_status


REPO_ROOT = Path(__file__).resolve().parents[5]
QUEUE_SCRIPT = REPO_ROOT / "scripts" / "queue_assistant_dev_task_packet.py"
DRAIN_SCRIPT = REPO_ROOT / "scripts" / "drain_assistant_dev_task_packet_inbox.py"
TEST_KEY = b"test-key-for-dev-bridge-inbox-cli"
PUBLIC_KEYS_JSON = public_key_environment({"assistant-bridge-dev": TEST_KEY})


def _make_packet(packet_id: str) -> DevTaskPacket:
    return DevTaskPacket(
        packetId=packet_id,
        emittedAt="2026-06-07T00:00:00Z",
        actor=BridgeActor(id="management-ai", roles=["operator"], capabilities=["assistant.kernel.debug"]),
        mode="kernel_debug",
        sourceConversationId="mgmt-nl-inbox-cli",
        sourceTurnIds=["turn-user", "turn-assistant"],
        tasks=[
            BridgeTask(
                id="INBOX-CLI-TASK-001",
                title="Queue assistant generated task",
                owner="Codex",
                reviewer="Claude",
                phase="Sprint Inbox CLI / Dev bridge",
                artifacts=["scripts/queue_assistant_dev_task_packet.py"],
                acceptance=["Task is queued for supervisor pickup"],
            )
        ],
    )


def _write_fake_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_materializing_ai_status(repo_root)
    return repo_root


def _assert_single_batch_materialization(repo_root: Path, *, packet_id: str) -> None:
    calls = (repo_root / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1
    record = json.loads(calls[0])
    assert record["argv"][0] == "dev-bridge-materialize-batch"
    assert len(record["argv"]) == 2
    assert record["ai_name"] == "assistant.dev.source"
    assert record["auto_worker_markers"] == {}
    assert record["packet_id"] == packet_id
    assert len(record["tasks"]) == 1
    assert record["tasks"][0]["task_id"] == "INBOX-CLI-TASK-001"


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


def _copy_cli_to_isolated_worktree(tmp_path: Path, script: Path) -> tuple[Path, Path]:
    worktree_root = tmp_path / "isolated-worktree"
    worktree_script = worktree_root / "scripts" / script.name
    worktree_script.parent.mkdir(parents=True)
    shutil.copy2(script, worktree_script)
    return worktree_root, worktree_script


def _isolated_worktree_env(repo_root: Path) -> dict[str, str]:
    bff_dir = REPO_ROOT / "services" / "control-plane" / "bff"
    inherited_pythonpath = os.environ.get("PYTHONPATH")
    env = {
        **os.environ,
        "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": PUBLIC_KEYS_JSON,
        "PANTHEON_STATUS_ROOT": str(repo_root),
        "PYTHONPATH": os.pathsep.join(
            part for part in (str(bff_dir), inherited_pythonpath) if part
        ),
    }
    for name in (
        "PANTHEON_COMMAND_ROOT",
        "PANTHEON_COMMAND_RUNTIME_SHA",
        "PANTHEON_COMMAND_REMOTE",
        "PANTHEON_COMMAND_BASE_REF",
        "PANTHEON_STATUS_COMMAND_ROOT",
        "PANTHEON_STATUS_COMMAND_SHA",
        "PANTHEON_STATUS_COMMAND_REMOTE",
        "PANTHEON_STATUS_COMMAND_BASE_REF",
        "PANTHEON_TASK_STATE_STORE_MODE",
        "PANTHEON_TASK_STATE_EVENT_LOG",
    ):
        env.pop(name, None)
    return env


def test_queue_cli_accepts_dev_docs_generate_envelope(tmp_path: Path) -> None:
    repo_root = _write_fake_repo(tmp_path)
    signed = sign_packet(_make_packet("pkt_inbox_cli"), key_store={"assistant-bridge-dev": TEST_KEY})
    packet_path = tmp_path / "dev-docs-response.json"
    packet_path.write_text(
        json.dumps({"meta": {"taskPacket": signed.model_dump(mode="json", by_alias=True)}}),
        encoding="utf-8",
    )

    env = {**os.environ, "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": PUBLIC_KEYS_JSON}
    result = subprocess.run(
        [
            sys.executable,
            str(QUEUE_SCRIPT),
            "--packet-file",
            str(packet_path),
            "--repo-root",
            str(repo_root),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["status"] == "queued"
    assert body["packetId"] == "pkt_inbox_cli"
    assert (repo_root / ".orchestrator" / "assistant-dev-packets" / "pending" / "pkt_inbox_cli.json").exists()


def test_drain_cli_materializes_queued_packet(tmp_path: Path) -> None:
    repo_root = _write_fake_repo(tmp_path)
    signed = sign_packet(_make_packet("pkt_inbox_cli_drain"), key_store={"assistant-bridge-dev": TEST_KEY})
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps({"taskPacket": signed.model_dump(mode="json", by_alias=True)}), encoding="utf-8")
    env = {**os.environ, "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": PUBLIC_KEYS_JSON}

    queue_result = subprocess.run(
        [
            sys.executable,
            str(QUEUE_SCRIPT),
            "--packet-file",
            str(packet_path),
            "--repo-root",
            str(repo_root),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert queue_result.returncode == 0, queue_result.stderr

    drain_result = subprocess.run(
        [sys.executable, str(DRAIN_SCRIPT), "--repo-root", str(repo_root), "--limit", "1"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )

    assert drain_result.returncode == 0, drain_result.stderr
    body = json.loads(drain_result.stdout)
    assert body["processedCount"] == 1
    assert body["packets"][0]["packetId"] == "pkt_inbox_cli_drain"
    _assert_single_batch_materialization(
        repo_root,
        packet_id="pkt_inbox_cli_drain",
    )


def test_queue_cli_uses_status_root_when_invoked_from_isolated_worktree(tmp_path: Path) -> None:
    repo_root = _write_fake_repo(tmp_path)
    worktree_root, worktree_script = _copy_cli_to_isolated_worktree(tmp_path, QUEUE_SCRIPT)
    signed = sign_packet(_make_packet("pkt_inbox_cli_status_root_queue"), key_store={"assistant-bridge-dev": TEST_KEY})
    packet_path = tmp_path / "queue-packet.json"
    packet_path.write_text(
        json.dumps({"taskPacket": signed.model_dump(mode="json", by_alias=True)}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(worktree_script), "--packet-file", str(packet_path)],
        cwd=str(worktree_root),
        env=_isolated_worktree_env(repo_root),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["inbox"] == str(repo_root / ".orchestrator" / "assistant-dev-packets")
    assert (repo_root / ".orchestrator" / "assistant-dev-packets" / "pending" / "pkt_inbox_cli_status_root_queue.json").exists()
    assert not (worktree_root / ".orchestrator").exists()


def test_drain_cli_uses_status_root_when_invoked_from_isolated_worktree(tmp_path: Path) -> None:
    repo_root = _write_fake_repo(tmp_path)
    worktree_root, worktree_script = _copy_cli_to_isolated_worktree(tmp_path, DRAIN_SCRIPT)
    signed = sign_packet(_make_packet("pkt_inbox_cli_status_root_drain"), key_store={"assistant-bridge-dev": TEST_KEY})
    packet_path = tmp_path / "drain-packet.json"
    packet_path.write_text(
        json.dumps({"taskPacket": signed.model_dump(mode="json", by_alias=True)}),
        encoding="utf-8",
    )
    env = _isolated_worktree_env(repo_root)
    queue_result = subprocess.run(
        [
            sys.executable,
            str(QUEUE_SCRIPT),
            "--packet-file",
            str(packet_path),
            "--repo-root",
            str(repo_root),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert queue_result.returncode == 0, queue_result.stderr

    result = subprocess.run(
        [sys.executable, str(worktree_script), "--limit", "1"],
        cwd=str(worktree_root),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["processedCount"] == 1
    _assert_single_batch_materialization(
        repo_root,
        packet_id="pkt_inbox_cli_status_root_drain",
    )
    assert not (worktree_root / ".orchestrator").exists()


def test_queue_cli_serializes_concurrent_writers(tmp_path: Path) -> None:
    repo_root = _write_fake_repo(tmp_path)
    signed = sign_packet(_make_packet("pkt_inbox_cli_concurrent"), key_store={"assistant-bridge-dev": TEST_KEY})
    packet_path = tmp_path / "concurrent-packet.json"
    packet_path.write_text(
        json.dumps({"taskPacket": signed.model_dump(mode="json", by_alias=True)}),
        encoding="utf-8",
    )
    env = {**os.environ, "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": PUBLIC_KEYS_JSON}
    cmd = [
        sys.executable,
        str(QUEUE_SCRIPT),
        "--packet-file",
        str(packet_path),
        "--repo-root",
        str(repo_root),
    ]
    processes = [
        subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    outputs = [process.communicate(timeout=30) for process in processes]

    assert all(process.returncode == 0 for process in processes), outputs
    bodies = [json.loads(stdout) for stdout, _stderr in outputs]
    assert sum(body["status"] == "queued" for body in bodies) == 1
    assert sum(body["status"] == "duplicate" for body in bodies) == 5
    pending = repo_root / ".orchestrator" / "assistant-dev-packets" / "pending"
    assert [path.name for path in pending.glob("*.json")] == ["pkt_inbox_cli_concurrent.json"]


def test_drain_cli_serializes_concurrent_drainers(tmp_path: Path) -> None:
    repo_root = _write_fake_repo(tmp_path)
    signed = sign_packet(_make_packet("pkt_inbox_cli_drain_concurrent"), key_store={"assistant-bridge-dev": TEST_KEY})
    packet_path = tmp_path / "concurrent-drain-packet.json"
    packet_path.write_text(
        json.dumps({"taskPacket": signed.model_dump(mode="json", by_alias=True)}),
        encoding="utf-8",
    )
    env = {**os.environ, "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": PUBLIC_KEYS_JSON}
    queue_result = subprocess.run(
        [
            sys.executable,
            str(QUEUE_SCRIPT),
            "--packet-file",
            str(packet_path),
            "--repo-root",
            str(repo_root),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert queue_result.returncode == 0, queue_result.stderr

    cmd = [sys.executable, str(DRAIN_SCRIPT), "--repo-root", str(repo_root), "--limit", "1"]
    processes = [
        subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = [process.communicate(timeout=30) for process in processes]

    assert all(process.returncode == 0 for process in processes), outputs
    bodies = [json.loads(stdout) for stdout, _stderr in outputs]
    assert sorted(body["processedCount"] for body in bodies) == [0, 1]
    _assert_single_batch_materialization(
        repo_root,
        packet_id="pkt_inbox_cli_drain_concurrent",
    )
    processed = repo_root / ".orchestrator" / "assistant-dev-packets" / "processed"
    assert [path.name for path in processed.glob("*.json")] == [
        "pkt_inbox_cli_drain_concurrent.json"
    ]


def test_drain_cli_rearms_a_recovered_packet_after_a_new_failed_drain(
    tmp_path: Path,
) -> None:
    repo_root = _write_fake_repo(tmp_path)
    packet = sign_packet(
        _make_packet("pkt_inbox_cli_rearm"),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    queue_task_packet(
        packet,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    _drain_as_failed(
        repo_root,
        packet,
        error="injected initial CLI recovery failure",
        dispatched_at="2026-08-09T08:10:00Z",
    )
    recover_failed_task_packet(
        packet.packet_id,
        repo_root=str(repo_root),
        key_store={"assistant-bridge-dev": TEST_KEY},
    )
    failed = _drain_as_failed(
        repo_root,
        packet,
        error="injected recovered-packet CLI drain failure",
        dispatched_at="2026-08-09T08:11:00Z",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(DRAIN_SCRIPT),
            "--repo-root",
            str(repo_root),
            "--recover-failed-packet-id",
            packet.packet_id,
        ],
        cwd=str(REPO_ROOT),
        env=_isolated_worktree_env(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    evidence_path = (
        failed.parent.parent
        / "recovery-rearms"
        / failed.stem
        / "000001.json"
    )
    assert body["status"] == "rearmed"
    assert body["rearmAttempt"] == 1
    assert body["rearmEvidencePath"] == str(evidence_path)
    assert evidence_path.is_file()
    assert not failed.exists()
    assert (failed.parent.parent / "pending" / failed.name).is_file()
