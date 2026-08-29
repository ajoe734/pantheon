from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from common import (
    CANONICAL_TASK_STATE_IDENTITY_ENV,
    canonical_task_state_identity_for_paths,
)
from .. import dev_bridge_admission, dev_bridge_dispatcher, dev_bridge_inbox
from ..dev_bridge_dispatcher import _task_metadata, dispatch_task_packet
from ..dev_bridge_inbox import drain_task_packet_inbox, queue_task_packet
from ..dev_bridge_models import (
    BridgeActor,
    BridgeDispatchRequest,
    BridgeOperatorAuthorization,
    BridgeTask,
    DevTaskPacket,
    MAX_TASKS_PER_PACKET,
    TaskDispatchRecord,
)
from ..dev_bridge_signer import (
    has_seen_packet,
    mark_packet_seen,
    packet_digest,
    public_key_environment,
    sign_packet,
)
from .dev_bridge_test_support import (
    bind_isolated_ai_status_module,
    write_materializing_ai_status,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_KEY = b"test-key-for-dev-bridge-reliability"
KEY_STORE = {"assistant-bridge-dev": TEST_KEY}


@pytest.fixture(autouse=True)
def _trusted_bridge_public_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BRIDGE_SIGNING_PUBLIC_KEYS_JSON",
        public_key_environment(KEY_STORE),
    )

ACTIVITY_ONLY_AI_STATUS_SCRIPT = """import json
import os
import sys
from pathlib import Path

root = Path(os.environ["PANTHEON_STATUS_ROOT"])
command = sys.argv[1]
if command == "dev-bridge-materialize-batch":
    status_path = root / "ai-status.json"
    state = json.loads(status_path.read_text(encoding="utf-8"))
    payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    materialized = []
    for row in payload["tasks"]:
        metadata = row["task_metadata"]
        spec = metadata["dev_bridge"]["task_spec"]
        materialized.append({
            "id": spec["id"],
            "title": spec["title"],
            "owner": spec["owner"],
            "reviewer": spec["reviewer"],
            "phase": spec["phase"],
            "depends_on": spec["depends_on"],
            "dependency_tracks": spec.get("dependency_tracks", {}),
            "artifacts": spec["artifacts"],
            "acceptance": spec["acceptance"],
            "summary_zh": spec["summary"],
            "dev_bridge": metadata["dev_bridge"],
        })
    task_ids = {task["id"] for task in materialized}
    state["tasks"] = [
        item for item in state.get("tasks", []) if item.get("id") not in task_ids
    ]
    state["tasks"].extend(materialized)
    status_path.write_text(json.dumps(state), encoding="utf-8")
    with (root / "ai-activity-log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "batch", "task_ids": sorted(task_ids)}) + "\\n")
    raise SystemExit(0)
if command == "dev-bridge-materialize-readback":
    print("injected projection-only runtime has no authoritative readback", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(f"unsupported command: {command}")
"""

NONZERO_MATERIALIZING_AI_STATUS_SCRIPT = """import json
import os
import sys
from pathlib import Path

root = Path(os.environ["PANTHEON_STATUS_ROOT"])
status_path = root / "ai-status.json"
state = json.loads(status_path.read_text(encoding="utf-8"))
command = sys.argv[1]
payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
if command == "dev-bridge-materialize-batch":
    materialized = []
    for row in payload["tasks"]:
        metadata = row["task_metadata"]
        spec = metadata["dev_bridge"]["task_spec"]
        materialized.append({
            "id": spec["id"],
            "title": spec["title"],
            "owner": spec["owner"],
            "reviewer": spec["reviewer"],
            "phase": spec["phase"],
            "depends_on": spec["depends_on"],
            "dependency_tracks": spec.get("dependency_tracks", {}),
            "artifacts": spec["artifacts"],
            "acceptance": spec["acceptance"],
            "summary_zh": spec["summary"],
            "dev_bridge": metadata["dev_bridge"],
        })
    task_ids = {task["id"] for task in materialized}
    existing = {
        item.get("id"): item
        for item in state.get("tasks", [])
        if item.get("id") in task_ids
    }
    if existing and len(existing) != len(materialized):
        print("partial pre-existing packet", file=sys.stderr)
        raise SystemExit(2)
    for task in materialized:
        prior = existing.get(task["id"])
        if prior is not None and prior.get("dev_bridge") != task["dev_bridge"]:
            print(f"bridge provenance conflict: {task['id']}", file=sys.stderr)
            raise SystemExit(2)
    state["tasks"].extend(
        task for task in materialized if task["id"] not in existing
    )
    status_path.write_text(json.dumps(state), encoding="utf-8")
    with (root / "ai-activity-log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "batch", "task_ids": sorted(task_ids)}) + "\\n")
    with (root / "calls.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"command": command, "task_ids": sorted(task_ids)}) + "\\n")
    print("injected nonzero after canonical commit", file=sys.stderr)
    raise SystemExit(9)
if command == "dev-bridge-materialize-readback":
    mode = os.environ.get("BRIDGE_TEST_READBACK_MODE", "exact")
    if mode == "missing":
        print(f"Dev bridge materialize readback task is missing: {payload['tasks'][0]['task_id']}", file=sys.stderr)
        raise SystemExit(1)
    if mode == "unavailable":
        print("injected governed readback unavailable", file=sys.stderr)
        raise SystemExit(75)
    if mode == "malformed":
        print("not-json")
        raise SystemExit(0)
    task_rows = []
    for row in payload["tasks"]:
        task = next((item for item in state.get("tasks", []) if item.get("id") == row["task_id"]), None)
        if task is None:
            print(f"Dev bridge materialize readback task is missing: {row['task_id']}", file=sys.stderr)
            raise SystemExit(1)
        if task.get("dev_bridge") != row["task_metadata"]["dev_bridge"]:
            print(f"Dev bridge materialize readback provenance mismatch: {row['task_id']}", file=sys.stderr)
            raise SystemExit(2)
        task_rows.append({
            "taskId": row["task_id"],
            "source": "active",
            "taskSpecHash": row["task_metadata"]["dev_bridge"]["task_spec_hash"],
        })
    print(json.dumps({
        "status": "verified",
        "packetId": payload["packet_id"],
        "packetDigest": payload["packet_digest"],
        "taskIds": [row["task_id"] for row in payload["tasks"]],
        "tasks": task_rows,
        "pendingAuditProjections": [],
        "checkpoint": {"eventCount": 1, "lastEventId": "bridge-test-event", "stateSha256": "a" * 64},
    }))
    raise SystemExit(0)
raise SystemExit(f"unsupported command: {command}")
"""

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
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _load_task_state_latency_benchmark():
    path = (
        REPO_ROOT
        / "docs"
        / "deployment"
        / "evidence"
        / "supervisor"
        / "SUP-TASK-STATE-LOCK-LATENCY-001"
        / "task_state_lock_latency_bench.py"
    )
    spec = importlib.util.spec_from_file_location(
        "dev_bridge_task_state_latency_benchmark",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


AI_STATUS = _load_ai_status_module()


@pytest.fixture(autouse=True)
def _isolated_bridge_status_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make every reliability test inherit an explicit scratch status/audit."""

    status_root = bind_isolated_ai_status_module(
        AI_STATUS,
        tmp_path / "ambient-status-root",
    )
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


def test_focused_harness_never_binds_live_status_or_audit_paths() -> None:
    live_root = Path("/home/lupin/pantheon").resolve()
    bound_paths = (
        AI_STATUS.STATUS_ROOT,
        AI_STATUS.STATUS_FILE,
        AI_STATUS.LOG_FILE,
        Path(os.environ["PANTHEON_STATUS_ROOT"]),
    )
    assert all(
        path.resolve() != live_root
        and live_root not in path.resolve().parents
        for path in bound_paths
    )
    assert AI_STATUS.LOG_FILE.name == "ai-activity-log.jsonl"
    assert AI_STATUS.LOG_FILE.read_text(encoding="utf-8") == ""


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
        operatorAuthorization=BridgeOperatorAuthorization(
            operatorId="human-ops-reliability",
            controlActivationId=f"control-{packet_id}",
            capability="assistant.canonical.mutate",
            mfaVerified=True,
            issuedAt="2026-07-15T00:00:00Z",
            expiresAt="2026-07-15T00:05:00Z",
            nonce=f"reliability-{packet_id}",
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


def _signed_without_dependencies(packet_id: str) -> DevTaskPacket:
    packet = _packet(packet_id)
    return sign_packet(
        packet.model_copy(
            update={
                "tasks": [
                    task.model_copy(update={"depends_on": []})
                    for task in packet.tasks
                ]
            }
        ),
        key_store=KEY_STORE,
    )


def test_packet_task_count_is_bounded() -> None:
    with pytest.raises(ValueError, match="at most 16 items"):
        _packet("pkt_too_many_tasks", task_count=MAX_TASKS_PER_PACKET + 1)


def _fake_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    write_materializing_ai_status(root)
    event_log = tmp_path / "runtime" / "task-state-events.jsonl"
    event_log.parent.mkdir()
    event_log.touch()
    os.environ.update(
        {
            "PANTHEON_STATUS_ROOT": str(root),
            "PANTHEON_COMMAND_ROOT": str(root),
            "PANTHEON_COMMAND_RUNTIME_SHA": "test-command-runtime",
            "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
            "PANTHEON_COMMAND_BASE_REF": "origin/dev",
            "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
            "PANTHEON_TASK_STATE_EVENT_LOG": str(event_log),
        }
    )
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


def _canonical_identity_json(status_root: Path, event_log: Path) -> str:
    return json.dumps(
        canonical_task_state_identity_for_paths(
            status_root=status_root,
            event_log=event_log,
        ),
        sort_keys=True,
        separators=(",", ":"),
    )


def _nonzero_command_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    status_root = tmp_path / "nonzero-status-root"
    status_root.mkdir()
    state = AI_STATUS.default_state()
    state["tasks"] = []
    state["handoffs"] = []
    state["blockers"] = []
    state["wave_state"] = {"status": "open"}
    (status_root / "ai-status.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    (status_root / "ai-activity-log.jsonl").write_text("", encoding="utf-8")
    command_root = tmp_path / "nonzero-command-root"
    scripts = command_root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "ai_status.py").write_text(
        NONZERO_MATERIALIZING_AI_STATUS_SCRIPT,
        encoding="utf-8",
    )
    event_log = tmp_path / "nonzero-runtime" / "task-state-events.jsonl"
    event_log.parent.mkdir()
    event_log.touch()
    return status_root, {
        "PANTHEON_STATUS_ROOT": str(status_root),
        "PANTHEON_COMMAND_ROOT": str(command_root),
        "PANTHEON_COMMAND_RUNTIME_SHA": "isolated-nonzero-fixture",
        "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
        "PANTHEON_COMMAND_BASE_REF": "origin/dev",
        "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
        "PANTHEON_TASK_STATE_EVENT_LOG": str(event_log),
        "PANTHEON_ASSISTANT_DEV_BRIDGE_REQUIRE_TASK_STATE_READBACK": "1",
    }


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
    assert call["ai_name"] == "assistant.dev.source"
    assert call["auto_worker_markers"] == {}
    assert len(call["tasks"]) == 1
    assert (
        call["tasks"][0]["task_metadata"]["dev_bridge"]["actor"]["id"]
        == "management-ai"
    )


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

    with pytest.raises(RuntimeError, match="exact active worker lease"):
        AI_STATUS.validate_active_status_command_lease(
            "assign",
            ["UNTRUSTED-TASK", "Codex", "Claude", "Untrusted mutation"],
        )


def test_runtime_binding_requires_explicit_authoritative_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_root = tmp_path / "status-root"
    status_root.mkdir()
    event_log = tmp_path / "runtime" / "task-state-events.jsonl"
    event_log.parent.mkdir()
    event_log.touch()
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(status_root))
    monkeypatch.setenv("PANTHEON_TASK_STATE_STORE_MODE", "authoritative")
    monkeypatch.setenv("PANTHEON_TASK_STATE_EVENT_LOG", str(event_log))

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


def test_runtime_binding_rejects_symlinked_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_root = tmp_path / "status-root"
    status_root.mkdir()
    real_event_log = tmp_path / "runtime" / "task-state-events.jsonl"
    real_event_log.parent.mkdir()
    real_event_log.touch()
    linked_event_log = tmp_path / "linked-task-state-events.jsonl"
    linked_event_log.symlink_to(real_event_log)
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(status_root))
    monkeypatch.setenv("PANTHEON_TASK_STATE_STORE_MODE", "authoritative")
    monkeypatch.setenv("PANTHEON_TASK_STATE_EVENT_LOG", str(linked_event_log))

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
    monkeypatch.setenv(
        CANONICAL_TASK_STATE_IDENTITY_ENV,
        _canonical_identity_json(status_root, event_log),
    )
    for marker in (
        "ORCH_RUN_ID",
        "ORCH_TASK_ID",
        "PANTHEON_WORKTREE_ROOT",
        "ORCH_WORKSPACE_PATH",
        "ORCH_RUNNER_STATUS_PATH",
        "ORCH_HEARTBEAT_PATH",
    ):
        monkeypatch.delenv(marker, raising=False)
    packet = _signed_without_dependencies("pkt_authoritative_projection")
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
    assert receipt["status"] == "processed", json.dumps(receipt, indent=2)
    assert receipt["result"]["admissionStatus"] == "admitted"
    readback = receipt["result"]["auditRefs"]["materializationReadback"]
    assert readback["status"] == "verified"
    assert readback["storeMode"] == "authoritative"
    assert readback["taskIds"] == [packet.tasks[0].id]
    assert readback["tasks"] == [
        {
            "taskId": packet.tasks[0].id,
            "source": "active",
            "taskSpecHash": dev_bridge_dispatcher._task_spec_hash(packet.tasks[0]),
        }
    ]
    assert readback["checkpoint"]["eventCount"] == initial_event_count + 1
    assert readback["checkpoint"]["lastEventId"].startswith("task-state-")
    assert len(readback["checkpoint"]["stateSha256"]) == 64
    assert readback["pendingAuditProjections"] == ["status_activity_outbox"]
    admission_path = status_root / receipt["result"]["admissionRecord"][
        "admission_record_path"
    ]
    assert admission_path.is_file()
    snapshot = AI_STATUS.load_snapshot(event_log)
    assert snapshot["event_count"] == initial_event_count + 1
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


def test_full_supervisor_cycle_drains_signed_packet_with_authoritative_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real prelock drain and locked cycle against one journal."""

    benchmark = _load_task_state_latency_benchmark()
    status_root = tmp_path / "status-root"
    status_root.mkdir()
    event_log = tmp_path / "runtime" / "task-state-events.jsonl"
    specs = benchmark.governed_command_specs(workers=1, commands_per_worker=1)
    benchmark.build_fixture(
        event_log,
        events=8,
        task_rows=4,
        command_specs=specs,
    )
    snapshot = AI_STATUS.load_snapshot(event_log)
    status_path = status_root / "ai-status.json"
    status_path.write_text(
        json.dumps(snapshot["state"], indent=2) + "\n",
        encoding="utf-8",
    )
    command_binding_fixture = {
        "command_root": str(REPO_ROOT),
        "source_sha": _git_stdout(REPO_ROOT, "rev-parse", "HEAD"),
        "remote": "ajoe734/pantheon",
        "base_ref": "HEAD",
    }
    with patch.object(
        benchmark,
        "command_runtime_binding",
        return_value=command_binding_fixture,
    ):
        config, command_binding = benchmark.prepare_full_supervisor_fixture(
            status_root,
            event_log,
            status_path,
            specs,
        )
    config["assistant_dev_bridge"] = {
        "enabled": True,
        "max_packets_per_tick": 1,
    }
    benchmark.supervisor_module.PLANNING_STATE_FILE = (
        status_root / ".orchestrator" / "planning-state.json"
    )
    runtime_env = {
        "PANTHEON_STATUS_ROOT": str(status_root),
        "PANTHEON_COMMAND_ROOT": command_binding["command_root"],
        "PANTHEON_COMMAND_RUNTIME_SHA": command_binding["source_sha"],
        "PANTHEON_COMMAND_REMOTE": command_binding["remote"],
        "PANTHEON_COMMAND_BASE_REF": command_binding["base_ref"],
        "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
        "PANTHEON_TASK_STATE_EVENT_LOG": str(event_log),
        CANONICAL_TASK_STATE_IDENTITY_ENV: _canonical_identity_json(
            status_root, event_log
        ),
    }
    for name, value in runtime_env.items():
        monkeypatch.setenv(name, value)
    for marker in dev_bridge_dispatcher.AUTO_WORKER_ENV_NAMES:
        monkeypatch.delenv(marker, raising=False)

    packet = _signed_without_dependencies("pkt_full_supervisor_authoritative")
    queued = queue_task_packet(
        packet,
        repo_root=str(status_root),
        key_store=KEY_STORE,
    )
    assert queued["status"] == "queued"

    with patch.object(
        benchmark.supervisor_module,
        "status_command_runtime_env",
        return_value=runtime_env,
    ):
        changed = benchmark.supervisor_module.run_once(
            config,
            quiet=True,
            once=True,
        )

    assert changed is True
    receipt_path = (
        status_root
        / ".orchestrator"
        / "assistant-dev-packets"
        / "receipts"
        / f"{packet.packet_id}.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "processed", json.dumps(receipt, indent=2)
    assert receipt["result"]["admissionStatus"] == "admitted"
    readback = receipt["result"]["auditRefs"]["materializationReadback"]
    assert readback["status"] == "verified"
    assert readback["taskIds"] == [packet.tasks[0].id]
    final_snapshot = AI_STATUS.load_snapshot(event_log)
    materialized = next(
        task
        for task in final_snapshot["state"]["tasks"]
        if task.get("id") == packet.tasks[0].id
    )
    assert materialized["owner"] == packet.tasks[0].owner
    assert materialized["reviewer"] == packet.tasks[0].reviewer
    assert materialized["status"] == "todo"
    assert benchmark.store.sha256_json(
        json.loads(status_path.read_text(encoding="utf-8"))
    ) == final_snapshot["state_sha256"]


def test_full_supervisor_cycle_never_queues_partially_materialized_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A canonical prefix is inert until the whole signed packet is admitted."""

    benchmark = _load_task_state_latency_benchmark()
    status_root = tmp_path / "status-root"
    status_root.mkdir()
    event_log = tmp_path / "runtime" / "task-state-events.jsonl"
    event_log.parent.mkdir()
    initial_state = AI_STATUS.default_state()
    initial_state["tasks"] = []
    initial_state["handoffs"] = []
    initial_state["blockers"] = []
    initial_state["wave_state"] = {"status": "open"}
    status_path = status_root / "ai-status.json"
    status_path.write_text(
        json.dumps(initial_state, indent=2) + "\n",
        encoding="utf-8",
    )
    AI_STATUS.append_state_commit(
        event_log,
        initial_state,
        source="bridge-partial-gate-fixture",
    )
    command_binding_fixture = {
        "command_root": str(REPO_ROOT),
        "source_sha": _git_stdout(REPO_ROOT, "rev-parse", "HEAD"),
        "remote": "ajoe734/pantheon",
        "base_ref": "HEAD",
    }
    with patch.object(
        benchmark,
        "command_runtime_binding",
        return_value=command_binding_fixture,
    ):
        config, command_binding = benchmark.prepare_full_supervisor_fixture(
            status_root,
            event_log,
            status_path,
            [],
        )
    repository_config = json.loads(
        (REPO_ROOT / ".orchestrator" / "config.json").read_text(encoding="utf-8")
    )
    config["ready_dispatcher"] = copy.deepcopy(repository_config["ready_dispatcher"])
    config["assistant_dev_bridge"] = {
        "enabled": True,
        "max_packets_per_tick": 1,
    }
    benchmark.supervisor_module.PLANNING_STATE_FILE = (
        status_root / ".orchestrator" / "planning-state.json"
    )
    runtime_env = {
        "PANTHEON_STATUS_ROOT": str(status_root),
        "PANTHEON_COMMAND_ROOT": command_binding["command_root"],
        "PANTHEON_COMMAND_RUNTIME_SHA": command_binding["source_sha"],
        "PANTHEON_COMMAND_REMOTE": command_binding["remote"],
        "PANTHEON_COMMAND_BASE_REF": command_binding["base_ref"],
        "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
        "PANTHEON_TASK_STATE_EVENT_LOG": str(event_log),
        CANONICAL_TASK_STATE_IDENTITY_ENV: _canonical_identity_json(
            status_root, event_log
        ),
    }
    for name, value in runtime_env.items():
        monkeypatch.setenv(name, value)
    for marker in dev_bridge_dispatcher.AUTO_WORKER_ENV_NAMES:
        monkeypatch.delenv(marker, raising=False)

    unsigned_packet = _packet("pkt_full_supervisor_partial_gate", task_count=2)
    unsigned_packet = unsigned_packet.model_copy(
        update={
            "tasks": [
                unsigned_packet.tasks[0].model_copy(update={"depends_on": []}),
                unsigned_packet.tasks[1].model_copy(
                    update={
                        "depends_on": [],
                        # The canonical batch validates every row before its
                        # single save. This validly signed but inadmissible
                        # second row must leave the first row uncommitted.
                        "reviewer": unsigned_packet.tasks[1].owner,
                    }
                ),
            ]
        }
    )
    packet = sign_packet(unsigned_packet, key_store=KEY_STORE)
    queue_task_packet(packet, repo_root=str(status_root), key_store=KEY_STORE)

    with (
        patch.object(
            benchmark.supervisor_module,
            "status_command_runtime_env",
            return_value=runtime_env,
        ),
        patch.object(
            benchmark.supervisor_module,
            "dispatch_loop_agent_ids",
            return_value=["codex"],
        ),
        patch.object(
            benchmark.supervisor_module,
            "scan_live_worker_pids_by_agent",
            return_value={},
        ),
        patch.object(
            benchmark.supervisor_module,
            "start_worker_for_request",
            side_effect=AssertionError("unadmitted bridge task reached worker launch"),
        ),
    ):
        changed = benchmark.supervisor_module.run_once(
            config,
            quiet=True,
            once=True,
        )

    assert changed is True
    snapshot = AI_STATUS.load_snapshot(event_log)
    materialized_ids = {
        task.get("id") for task in snapshot["state"].get("tasks", [])
    }
    assert packet.tasks[0].id not in materialized_ids
    assert packet.tasks[1].id not in materialized_ids
    assert dev_bridge_admission.load_admission_record(
        repo_root=str(status_root),
        packet_id=packet.packet_id,
        packet_digest=packet_digest(packet),
    ) is None
    runtime_state = benchmark.supervisor_module.load_runtime_state(config)
    assert benchmark.supervisor_module.queue_events(runtime_state) == []
    assert runtime_state.get("workers", {}) == {}


def test_activity_log_and_projection_only_dispatch_cannot_create_admission(
    tmp_path: Path,
) -> None:
    status_root, event_log, _initial_state = _authoritative_status_root(tmp_path)
    command_root = tmp_path / "activity-only-command"
    scripts_dir = command_root / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "ai_status.py").write_text(
        ACTIVITY_ONLY_AI_STATUS_SCRIPT,
        encoding="utf-8",
    )
    packet = _signed("pkt_activity_only_false_positive")
    runtime_env = {
        "PANTHEON_STATUS_ROOT": str(status_root),
        "PANTHEON_COMMAND_ROOT": str(command_root),
        "PANTHEON_COMMAND_RUNTIME_SHA": "activity-only-fixture",
        "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
        "PANTHEON_COMMAND_BASE_REF": "origin/dev",
        "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
        "PANTHEON_TASK_STATE_EVENT_LOG": str(event_log),
        CANONICAL_TASK_STATE_IDENTITY_ENV: _canonical_identity_json(
            status_root, event_log
        ),
        "PANTHEON_ASSISTANT_DEV_BRIDGE_REQUIRE_TASK_STATE_READBACK": "1",
    }

    result = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(status_root)),
        key_store=KEY_STORE,
        runtime_env=runtime_env,
    )

    task_id = packet.tasks[0].id
    projected = json.loads(
        (status_root / "ai-status.json").read_text(encoding="utf-8")
    )
    assert any(task.get("id") == task_id for task in projected["tasks"])
    assert json.loads(
        (status_root / "ai-activity-log.jsonl").read_text(encoding="utf-8")
    ) == {"type": "batch", "task_ids": [task_id]}
    snapshot = AI_STATUS.load_snapshot(event_log)
    assert not any(
        task.get("id") == task_id for task in snapshot["state"].get("tasks", [])
    )
    assert result.admission_record is None
    assert result.admission_status == "invalid_materialization"
    assert result.retryable is False
    assert "authoritative post-batch readback" in result.errors[0]
    assert "projection-only runtime has no authoritative readback" in result.errors[0]
    assert not has_seen_packet(packet.packet_id, repo_root=str(status_root))


def test_supervisor_required_readback_rejects_missing_journal_binding(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_required_readback_without_binding")

    result = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
        key_store=KEY_STORE,
        runtime_env={
            "PANTHEON_STATUS_ROOT": str(repo_root),
            "PANTHEON_ASSISTANT_DEV_BRIDGE_REQUIRE_TASK_STATE_READBACK": "1",
            "PANTHEON_COMMAND_ROOT": "",
            "PANTHEON_COMMAND_RUNTIME_SHA": "",
            "PANTHEON_COMMAND_REMOTE": "",
            "PANTHEON_COMMAND_BASE_REF": "",
            "PANTHEON_TASK_STATE_STORE_MODE": "",
            "PANTHEON_TASK_STATE_EVENT_LOG": "",
        },
    )

    assert result.admission_record is None
    assert result.admission_status == "invalid_materialization"
    assert result.retryable is False
    assert "PANTHEON_TASK_STATE_STORE_MODE=authoritative is required" in result.errors[0]
    assert not has_seen_packet(packet.packet_id, repo_root=str(repo_root))


def test_nonzero_governed_batch_requires_and_accepts_exact_readback(
    tmp_path: Path,
) -> None:
    status_root, runtime_env = _nonzero_command_fixture(tmp_path)
    packet = _signed("pkt_nonzero_exact_readback")

    result = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(status_root)),
        key_store=KEY_STORE,
        runtime_env=runtime_env,
    )

    assert result.errors == []
    assert result.task_records[0].status == "dispatched"
    assert result.admission_status == "admitted"
    assert result.admission_record is not None
    assert result.admission_record["packet_digest"] == packet_digest(packet)
    assert result.admission_record["actor"] == packet.actor.model_dump(
        mode="json",
        by_alias=True,
    )
    isolated_audit = status_root / "ai-activity-log.jsonl"
    assert json.loads(isolated_audit.read_text(encoding="utf-8")) == {
        "type": "batch",
        "task_ids": [packet.tasks[0].id],
    }


@pytest.mark.parametrize(
    ("readback_mode", "expected_status", "expected_retryable", "error_fragment"),
    [
        ("missing", "invalid_materialization", False, "nonzero after canonical commit"),
        ("unavailable", "task_state_mutation_retryable", True, "unavailable"),
        ("malformed", "invalid_materialization", False, "invalid JSON"),
    ],
)
def test_nonzero_governed_batch_rejects_invalid_authoritative_readback(
    tmp_path: Path,
    readback_mode: str,
    expected_status: str,
    expected_retryable: bool,
    error_fragment: str,
) -> None:
    status_root, runtime_env = _nonzero_command_fixture(tmp_path)
    runtime_env["BRIDGE_TEST_READBACK_MODE"] = readback_mode
    packet = _signed(f"pkt_nonzero_{readback_mode}_readback")

    result = dispatch_task_packet(
        BridgeDispatchRequest(packet=packet, repoRoot=str(status_root)),
        key_store=KEY_STORE,
        runtime_env=runtime_env,
    )

    assert result.admission_record is None
    assert result.admission_status == expected_status
    assert result.retryable is expected_retryable
    assert error_fragment in result.errors[0]
    assert not has_seen_packet(packet.packet_id, repo_root=str(status_root))


def test_governed_readback_never_falls_back_to_local_task_projection(
    tmp_path: Path,
) -> None:
    status_root, runtime_env = _nonzero_command_fixture(tmp_path)
    packet = _signed("pkt_no_local_projection_fallback")

    with (
        patch.object(
            dev_bridge_dispatcher,
            "_canonical_task_state_readback",
            side_effect=ValueError("injected governed readback malformed"),
        ),
        pytest.raises(ValueError, match="governed readback malformed"),
    ):
        dev_bridge_dispatcher._validate_materialized_tasks(
            packet,
            repo_root=str(status_root),
            environment=runtime_env,
        )


def test_replacement_packet_id_for_materialized_task_fails_before_assign(
    tmp_path: Path,
) -> None:
    status_root, runtime_env = _nonzero_command_fixture(tmp_path)
    original = _signed("pkt_original_materialized_id")
    first = dispatch_task_packet(
        BridgeDispatchRequest(packet=original, repoRoot=str(status_root)),
        key_store=KEY_STORE,
        runtime_env=runtime_env,
    )
    assert first.admission_status == "admitted"
    replacement = _packet("pkt_replacement_materialized_id").model_copy(
        update={"tasks": original.tasks}
    )
    replacement = sign_packet(replacement, key_store=KEY_STORE)

    rejected = dispatch_task_packet(
        BridgeDispatchRequest(packet=replacement, repoRoot=str(status_root)),
        key_store=KEY_STORE,
        runtime_env=runtime_env,
    )

    assert rejected.admission_record is None
    assert rejected.admission_status == "invalid_materialization"
    assert "bridge provenance conflict" in rejected.errors[0]
    assert "readback provenance mismatch" in rejected.errors[0]
    calls = [
        json.loads(line)
        for line in (status_root / "calls.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert calls == [
        {
            "command": "dev-bridge-materialize-batch",
            "task_ids": [original.tasks[0].id],
        }
    ]


def test_atomic_batch_failure_is_retryable_and_only_full_success_marks_seen(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_partial_retry", task_count=2)
    request = BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root))
    failed_records = [
        TaskDispatchRecord(
            taskId=task.id,
            owner="Codex",
            reviewer="Claude",
            status="retryable",
        )
        for task in packet.tasks
    ]
    with patch.object(
        dev_bridge_dispatcher,
        "_dispatch_task_batch",
        return_value=(failed_records, None, "injected atomic batch failure", True),
    ):
        first = dispatch_task_packet(request, key_store=KEY_STORE)

    assert first.errors == ["packet batch: injected atomic batch failure"]
    assert not has_seen_packet(packet.packet_id, repo_root=str(repo_root))
    state = json.loads((repo_root / "ai-status.json").read_text(encoding="utf-8"))
    assert state["tasks"] == []

    retry = dispatch_task_packet(request, key_store=KEY_STORE)

    assert retry.errors == []
    assert has_seen_packet(packet.packet_id, repo_root=str(repo_root))


def test_assign_timeout_before_task_is_retryable_and_unadmitted(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_timeout_before_task")

    with patch.object(
        dev_bridge_dispatcher.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(["ai_status.py", "assign"], 2.0),
    ):
        result = dispatch_task_packet(
            BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
            key_store=KEY_STORE,
            runtime_env={
                dev_bridge_dispatcher.ASSIGN_TIMEOUT_ENV: "2",
            },
        )

    assert result.retryable is True
    assert result.admission_status == "task_state_mutation_retryable"
    assert result.task_records[0].status == "retryable"
    assert "dev-bridge-materialize-batch timed out after 2s" in result.errors[0]
    assert "authoritative post-batch readback" in result.errors[0]
    assert not has_seen_packet(packet.packet_id, repo_root=str(repo_root))
    state = json.loads((repo_root / "ai-status.json").read_text(encoding="utf-8"))
    assert state["tasks"] == []


def test_assign_timeout_defaults_and_caps_at_ten_seconds() -> None:
    assert dev_bridge_dispatcher._assign_timeout_seconds({}) == 10.0
    assert dev_bridge_dispatcher._assign_timeout_seconds(
        {dev_bridge_dispatcher.ASSIGN_TIMEOUT_ENV: "30"}
    ) == 10.0
    assert dev_bridge_dispatcher._assign_timeout_seconds(
        {dev_bridge_dispatcher.ASSIGN_TIMEOUT_ENV: "3"}
    ) == 3.0


def test_timeout_after_atomic_commit_is_accepted_only_after_exact_readback(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_timeout_after_atomic_commit", task_count=2)
    request = BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root))
    real_run = subprocess.run
    command_count = 0

    def timeout_after_child_commit(*args, **kwargs):
        nonlocal command_count
        completed = real_run(*args, **kwargs)
        assert completed.returncode == 0
        command_count += 1
        if command_count == 1:
            raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))
        return completed

    with patch.object(
        dev_bridge_dispatcher.subprocess,
        "run",
        side_effect=timeout_after_child_commit,
    ):
        recovered = dispatch_task_packet(request, key_store=KEY_STORE)

    assert recovered.errors == []
    assert recovered.admission_status == "admitted"
    assert recovered.audit_refs["materializationReadback"]["taskIds"] == [
        task.id for task in packet.tasks
    ]
    state = json.loads((repo_root / "ai-status.json").read_text(encoding="utf-8"))
    materialized_ids = [task["id"] for task in state["tasks"]]
    assert materialized_ids.count(packet.tasks[0].id) == 1
    assert materialized_ids.count(packet.tasks[1].id) == 1


def test_historical_partial_prefix_is_not_completed_by_a_batch_retry(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_historical_partial_prefix", task_count=2)
    request = BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root))
    state_path = repo_root / "ai-status.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    first = packet.tasks[0]
    metadata = _task_metadata(packet, first)
    spec = metadata["dev_bridge"]["task_spec"]
    state["tasks"].append(
        {
            "id": spec["id"],
            "title": spec["title"],
            "owner": spec["owner"],
            "reviewer": spec["reviewer"],
            "phase": spec["phase"],
            "depends_on": spec["depends_on"],
            "dependency_tracks": spec.get("dependency_tracks", {}),
            "artifacts": spec["artifacts"],
            "acceptance": spec["acceptance"],
            "summary_zh": spec["summary"],
            "dev_bridge": metadata["dev_bridge"],
        }
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")

    rejected = dispatch_task_packet(request, key_store=KEY_STORE)

    assert rejected.admission_record is None
    assert rejected.admission_status == "invalid_materialization"
    assert "partial pre-existing packet" in rejected.errors[0]
    reread = json.loads(state_path.read_text(encoding="utf-8"))
    ids = [item["id"] for item in reread["tasks"]]
    assert ids.count(packet.tasks[0].id) == 1
    assert packet.tasks[1].id not in ids
    assert not has_seen_packet(packet.packet_id, repo_root=str(repo_root))


def test_live_dispatch_fence_survives_expired_json_claim(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_live_fence_past_ttl")
    request = BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root))
    entered = threading.Event()
    release = threading.Event()
    real_dispatch = dev_bridge_dispatcher._dispatch_task_batch
    dispatch_calls = 0
    call_lock = threading.Lock()

    def delayed_dispatch(*args, **kwargs):
        nonlocal dispatch_calls
        with call_lock:
            dispatch_calls += 1
        entered.set()
        assert release.wait(5)
        return real_dispatch(*args, **kwargs)

    with (
        patch.object(
            dev_bridge_dispatcher,
            "_dispatch_task_batch",
            side_effect=delayed_dispatch,
        ),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        original = executor.submit(dispatch_task_packet, request, key_store=KEY_STORE)
        assert entered.wait(5)
        claim_path = dev_bridge_dispatcher._dispatch_claim_path(
            str(repo_root),
            packet.packet_id,
        )
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        claim["expires_at"] = "2026-08-01T00:00:00Z"
        claim_path.write_text(json.dumps(claim), encoding="utf-8")

        replacement = executor.submit(
            dispatch_task_packet,
            request,
            key_store=KEY_STORE,
        ).result(timeout=5)
        assert replacement.retryable is True
        assert replacement.admission_status == "dispatch_fence_retryable"
        release.set()
        admitted = original.result(timeout=5)

    assert dispatch_calls == 1
    assert admitted.admission_status == "admitted"


def test_dispatch_fence_parent_symlink_fails_without_outside_write(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    outside = tmp_path / "outside-dispatch-fence"
    outside.mkdir()
    claims = repo_root / ".orchestrator" / "assistant-dev-packet-claims"
    claims.parent.mkdir()
    claims.symlink_to(outside, target_is_directory=True)
    packet = _signed("pkt_dispatch_fence_parent_symlink")

    with pytest.raises(ValueError, match="Bridge dispatch fence is unsafe"):
        dispatch_task_packet(
            BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root)),
            key_store=KEY_STORE,
        )

    assert list(outside.iterdir()) == []


def test_stale_dispatch_claim_recovers_after_crashed_claimant(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_stale_dispatch_claim")
    request = BridgeDispatchRequest(packet=packet, repoRoot=str(repo_root))

    with (
        patch.object(
            dev_bridge_dispatcher,
            "_dispatch_task_batch",
            side_effect=SystemExit("injected claimant crash"),
        ),
        pytest.raises(SystemExit, match="claimant crash"),
    ):
        dispatch_task_packet(request, key_store=KEY_STORE)

    claim_path = dev_bridge_dispatcher._dispatch_claim_path(
        str(repo_root),
        packet.packet_id,
    )
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["expires_at"] = "2026-08-01T00:00:00Z"
    claim_path.write_text(json.dumps(claim), encoding="utf-8")

    recovered = dispatch_task_packet(request, key_store=KEY_STORE)

    assert recovered.errors == []
    assert recovered.admission_status == "admitted"
    assert not claim_path.exists()


def test_mismatched_payload_cannot_take_live_dispatch_claim(tmp_path: Path) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_claim_identity")
    digest = packet_digest(packet)
    with dev_bridge_dispatcher.packet_replay_lock(repo_root=str(repo_root)):
        state, _claim = dev_bridge_dispatcher._claim_packet_dispatch_locked(
            packet,
            repo_root=str(repo_root),
            digest=digest,
            environment={},
        )
    assert state == "claimed"

    changed = _packet(packet.packet_id).model_copy(update={"intent": "forged"})
    changed = sign_packet(changed, key_store=KEY_STORE)
    with pytest.raises(ValueError, match="mismatched dispatch claim"):
        dispatch_task_packet(
            BridgeDispatchRequest(packet=changed, repoRoot=str(repo_root)),
            key_store=KEY_STORE,
        )


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


def test_authoritative_admission_projection_retry_adds_no_task_state_event(
    tmp_path: Path,
) -> None:
    """Admission is recoverable audit output, never a second task authority."""

    status_root, event_log, _initial_state = _authoritative_status_root(tmp_path)
    runtime_env = {
        "PANTHEON_STATUS_ROOT": str(status_root),
        "PANTHEON_COMMAND_ROOT": str(REPO_ROOT),
        "PANTHEON_COMMAND_RUNTIME_SHA": _git_stdout(REPO_ROOT, "rev-parse", "HEAD"),
        "PANTHEON_COMMAND_REMOTE": "ajoe734/pantheon",
        "PANTHEON_COMMAND_BASE_REF": "HEAD",
        "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
        "PANTHEON_TASK_STATE_EVENT_LOG": str(event_log),
        "PANTHEON_ASSISTANT_DEV_BRIDGE_REQUIRE_TASK_STATE_READBACK": "1",
        CANONICAL_TASK_STATE_IDENTITY_ENV: _canonical_identity_json(
            status_root, event_log
        ),
    }
    unsigned = _packet("pkt_authoritative_admission_projection_retry", task_count=2)
    unsigned = unsigned.model_copy(
        update={
            "tasks": [
                task.model_copy(
                    update={
                        "depends_on": [],
                        "artifacts": [f"docs/deployment/evidence/{task.id}/"],
                    }
                )
                for task in unsigned.tasks
            ]
        }
    )
    packet = sign_packet(unsigned, key_store=KEY_STORE)
    request = BridgeDispatchRequest(packet=packet, repoRoot=str(status_root))
    baseline = AI_STATUS.load_snapshot(event_log)["event_count"]

    with patch.object(
        dev_bridge_dispatcher,
        "persist_admission_record",
        side_effect=OSError("injected admission projection failure"),
    ):
        first = dispatch_task_packet(
            request,
            key_store=KEY_STORE,
            runtime_env=runtime_env,
        )

    assert first.retryable is True
    assert first.admission_status == "admission_persistence_retryable"
    committed = AI_STATUS.load_snapshot(event_log)
    assert committed["event_count"] == baseline + 1
    assert {task.id for task in packet.tasks}.issubset(
        {task["id"] for task in committed["state"]["tasks"]}
    )

    retry = dispatch_task_packet(
        request,
        key_store=KEY_STORE,
        runtime_env=runtime_env,
    )

    assert retry.errors == []
    assert retry.admission_status == "admitted"
    assert retry.admission_record is not None
    assert AI_STATUS.load_snapshot(event_log)["event_count"] == baseline + 1


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
    assert "authoritative post-batch readback invalid" in result.errors[0]
    assert "canonical packet materialization readback returned invalid JSON" in result.errors[0]
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
    assert "canonical packet materialization readback failed" in replay.errors[0]


def test_replay_rejects_terminal_projection_after_active_prune(
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
    assert replay.admission_status == "invalid_replay_materialization"
    assert "canonical packet materialization readback failed" in replay.errors[0]


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
    monkeypatch: pytest.MonkeyPatch,
    patched_name: str,
    expected_status: str,
) -> None:
    monkeypatch.setattr(dev_bridge_inbox, "RETRY_BASE_SECONDS", 0.0)
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
    monkeypatch.setenv("AI_NAME", "Codex")
    row = dev_bridge_dispatcher._materialization_batch_payload(packet)["tasks"][0]
    state = AI_STATUS.default_state()
    state["tasks"] = []
    state["handoffs"] = []
    state["blockers"] = []
    state["wave_state"] = {"status": "open"}

    with (
        patch.object(AI_STATUS, "load_archived_snapshot", return_value=None),
        patch.object(AI_STATUS, "append_log") as append_log,
    ):
        with AI_STATUS.dev_bridge_materialize_mutation_environment(
            row, AI_STATUS.DEV_BRIDGE_BATCH_ACTOR
        ):
            first = AI_STATUS.command_assign(
                state,
                [task.id, task.owner, task.reviewer, task.title],
            )
        snapshot = copy.deepcopy(state)
        with AI_STATUS.dev_bridge_materialize_mutation_environment(
            row, AI_STATUS.DEV_BRIDGE_BATCH_ACTOR
        ):
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
    row = dev_bridge_dispatcher._materialization_batch_payload(packet)["tasks"][0]
    state = AI_STATUS.default_state()
    state["tasks"] = []
    state["wave_state"] = {"status": "open"}

    with (
        patch.object(AI_STATUS, "load_archived_snapshot", return_value=None),
        patch.object(AI_STATUS, "append_log"),
    ):
        with AI_STATUS.dev_bridge_materialize_mutation_environment(
            row, AI_STATUS.DEV_BRIDGE_BATCH_ACTOR
        ):
            AI_STATUS.command_assign(
                state, [task.id, task.owner, task.reviewer, task.title]
            )
        conflicting = copy.deepcopy(metadata)
        conflicting["dev_bridge"]["packet_id"] = "pkt_other_packet"
        conflicting_row = {**row, "task_metadata": conflicting}
        with (
            AI_STATUS.dev_bridge_materialize_mutation_environment(
                conflicting_row, AI_STATUS.DEV_BRIDGE_BATCH_ACTOR
            ),
            pytest.raises(SystemExit, match="Bridge assignment conflict"),
        ):
            AI_STATUS.command_assign(
                state, [task.id, task.owner, task.reviewer, task.title]
            )
        provenance_conflict = copy.deepcopy(metadata)
        provenance_conflict["dev_bridge"]["conversation_id"] = "different-conversation"
        provenance_row = {**row, "task_metadata": provenance_conflict}
        with (
            AI_STATUS.dev_bridge_materialize_mutation_environment(
                provenance_row, AI_STATUS.DEV_BRIDGE_BATCH_ACTOR
            ),
            pytest.raises(SystemExit, match="Bridge assignment conflict"),
        ):
            AI_STATUS.command_assign(
                state, [task.id, task.owner, task.reviewer, task.title]
            )


def test_ai_status_bridge_assignment_rejects_existing_unprovenanced_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _signed("pkt_ai_status_unprovenanced")
    task = packet.tasks[0]
    row = dev_bridge_dispatcher._materialization_batch_payload(packet)["tasks"][0]
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

    with (
        AI_STATUS.dev_bridge_materialize_mutation_environment(
            row, AI_STATUS.DEV_BRIDGE_BATCH_ACTOR
        ),
        pytest.raises(SystemExit, match="without bridge provenance"),
    ):
        AI_STATUS.command_assign(
            state, [task.id, task.owner, task.reviewer, task.title]
        )


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


def test_forged_retry_metadata_cannot_delay_signed_packet(tmp_path: Path) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_forged_retry")
    queued = queue_task_packet(packet, repo_root=str(repo_root), key_store=KEY_STORE)
    pending = Path(queued["path"])
    processing = pending.parent.parent / "processing" / pending.name
    processing.parent.mkdir(parents=True)
    os.replace(pending, processing)
    retry_path = processing.parent.parent / "retries" / processing.name
    retry_path.parent.mkdir(parents=True)
    retry_path.write_text(
        json.dumps(
            {
                "schema": dev_bridge_inbox.PROCESSING_RETRY_SCHEMA,
                "packet_id": packet.packet_id,
                "packet_digest": "0" * 64,
                "attempt": 99,
                "next_attempt_epoch": 9_999_999_999,
            }
        ),
        encoding="utf-8",
    )

    result = drain_task_packet_inbox(repo_root=str(repo_root))

    assert result["processedCount"] == 1
    assert result["packets"][0]["status"] == "processed"
    assert not retry_path.exists()


def test_concurrent_drainers_apply_one_processing_claim(tmp_path: Path) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_concurrent_drainers")
    queue_task_packet(packet, repo_root=str(repo_root), key_store=KEY_STORE)
    entered_dispatch = threading.Event()
    release_dispatch = threading.Event()
    real_dispatch = dev_bridge_inbox.dispatch_task_packet
    dispatch_calls = 0
    call_lock = threading.Lock()

    def delayed_dispatch(*args, **kwargs):
        nonlocal dispatch_calls
        with call_lock:
            dispatch_calls += 1
        entered_dispatch.set()
        assert release_dispatch.wait(5)
        return real_dispatch(*args, **kwargs)

    with (
        patch.object(
            dev_bridge_inbox,
            "dispatch_task_packet",
            side_effect=delayed_dispatch,
        ),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        first = executor.submit(
            drain_task_packet_inbox,
            repo_root=str(repo_root),
        )
        assert entered_dispatch.wait(5)
        second = executor.submit(
            drain_task_packet_inbox,
            repo_root=str(repo_root),
        )
        second_result = second.result(timeout=5)
        release_dispatch.set()
        first_result = first.result(timeout=5)

    assert dispatch_calls == 1
    assert first_result["processedCount"] == 1
    assert second_result["processedCount"] == 0
    assert second_result["errorCount"] == 0
    inbox = repo_root / ".orchestrator" / "assistant-dev-packets"
    assert (inbox / "processed" / f"{packet.packet_id}.json").is_file()
    assert not (inbox / "processing" / f"{packet.packet_id}.json").exists()


def test_live_processing_fence_survives_expired_json_claim(tmp_path: Path) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_live_processing_fence")
    queue_task_packet(packet, repo_root=str(repo_root), key_store=KEY_STORE)
    entered_dispatch = threading.Event()
    release_dispatch = threading.Event()
    real_dispatch = dev_bridge_inbox.dispatch_task_packet

    def delayed_dispatch(*args, **kwargs):
        entered_dispatch.set()
        assert release_dispatch.wait(5)
        return real_dispatch(*args, **kwargs)

    with (
        patch.object(
            dev_bridge_inbox,
            "dispatch_task_packet",
            side_effect=delayed_dispatch,
        ),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        original = executor.submit(
            drain_task_packet_inbox,
            repo_root=str(repo_root),
        )
        assert entered_dispatch.wait(5)
        inbox = repo_root / ".orchestrator" / "assistant-dev-packets"
        claim_path = inbox / "claims" / f"{packet.packet_id}.json"
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        claim["expires_at_epoch"] = 0
        claim_path.write_text(json.dumps(claim), encoding="utf-8")

        replacement = executor.submit(
            drain_task_packet_inbox,
            repo_root=str(repo_root),
        ).result(timeout=5)
        assert replacement["processedCount"] == 0
        assert replacement["errorCount"] == 0
        release_dispatch.set()
        completed = original.result(timeout=5)

    assert completed["processedCount"] == 1


def test_inbox_processing_fence_parent_symlink_fails_without_outside_write(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_inbox_fence_parent_symlink")
    queued = queue_task_packet(packet, repo_root=str(repo_root), key_store=KEY_STORE)
    inbox = Path(queued["path"]).parent.parent
    outside = tmp_path / "outside-inbox-fence"
    outside.mkdir()
    (inbox / "claims").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="Bridge inbox processing fence is unsafe"):
        drain_task_packet_inbox(repo_root=str(repo_root))

    assert list(outside.iterdir()) == []


def test_processing_fence_stays_held_through_receipt_commit(
    tmp_path: Path,
) -> None:
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_processing_fence_receipt_commit")
    queue_task_packet(packet, repo_root=str(repo_root), key_store=KEY_STORE)
    entered_receipt = threading.Event()
    release_receipt = threading.Event()
    real_dispatch = dev_bridge_inbox.dispatch_task_packet
    real_write_json_atomic = dev_bridge_inbox._write_json_atomic
    dispatch_calls = 0
    call_lock = threading.Lock()

    def counted_dispatch(*args, **kwargs):
        nonlocal dispatch_calls
        with call_lock:
            dispatch_calls += 1
        return real_dispatch(*args, **kwargs)

    def pause_first_receipt(path: Path, payload: dict) -> None:
        if path.parent.name == "receipts" and not entered_receipt.is_set():
            entered_receipt.set()
            assert release_receipt.wait(5)
        real_write_json_atomic(path, payload)

    with (
        patch.object(
            dev_bridge_inbox,
            "dispatch_task_packet",
            side_effect=counted_dispatch,
        ),
        patch.object(
            dev_bridge_inbox,
            "_write_json_atomic",
            side_effect=pause_first_receipt,
        ),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        original = executor.submit(
            drain_task_packet_inbox,
            repo_root=str(repo_root),
        )
        assert entered_receipt.wait(5)
        inbox = repo_root / ".orchestrator" / "assistant-dev-packets"
        claim_path = inbox / "claims" / f"{packet.packet_id}.json"
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        claim["expires_at_epoch"] = 0
        claim_path.write_text(json.dumps(claim), encoding="utf-8")

        replacement = executor.submit(
            drain_task_packet_inbox,
            repo_root=str(repo_root),
        ).result(timeout=5)
        assert replacement["processedCount"] == 0
        assert replacement["errorCount"] == 0
        release_receipt.set()
        completed = original.result(timeout=5)

    assert dispatch_calls == 1
    assert completed["processedCount"] == 1
    assert completed["errorCount"] == 0


def test_existing_receipt_does_not_suppress_exact_dispatch(tmp_path: Path) -> None:
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

    result = drain_task_packet_inbox(repo_root=str(repo_root))

    assert result["processedCount"] == 1
    assert result["packets"][0]["result"]["admissionStatus"] == "admitted"
    assert result["packets"][0].get("recoveredFromReceipt") is None
    assert has_seen_packet(packet.packet_id, repo_root=str(repo_root))
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

    recovered = drain_task_packet_inbox(repo_root=str(repo_root))

    assert recovered["processedCount"] == 1
    assert recovered["packets"][0]["recoveredFromReceipt"] is True
    assert recovered["packets"][0]["result"]["admissionStatus"] == "admitted_replay"
    assert not processing.exists()


def test_receipt_persistence_failure_leaves_processing_for_safe_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dev_bridge_inbox, "RETRY_BASE_SECONDS", 0.0)
    repo_root = _fake_repo(tmp_path)
    packet = _signed("pkt_receipt_write_failure")
    queued = queue_task_packet(packet, repo_root=str(repo_root), key_store=KEY_STORE)
    pending = Path(queued["path"])
    processing = pending.parent.parent / "processing" / pending.name
    receipt = pending.parent.parent / "receipts" / pending.name
    real_write_json_atomic = dev_bridge_inbox._write_json_atomic

    def fail_receipt_only(path: Path, payload: dict) -> None:
        if path.parent.name == "receipts":
            raise OSError("injected receipt fsync failure")
        real_write_json_atomic(path, payload)

    with patch.object(
        dev_bridge_inbox,
        "_write_json_atomic",
        side_effect=fail_receipt_only,
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
