from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ..dev_bridge_models import BridgeActor, BridgeTask, DevTaskPacket
from ..dev_bridge_signer import public_key_environment, sign_packet
from .dev_bridge_test_support import write_materializing_ai_status


REPO_ROOT = Path(__file__).resolve().parents[5]
SCRIPT = REPO_ROOT / "scripts" / "dispatch_assistant_dev_task_packet.py"
TEST_KEY = b"test-key-for-dev-bridge-cli"
PUBLIC_KEYS_JSON = public_key_environment({"assistant-bridge-dev": TEST_KEY})


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
        sourceConversationId="mgmt-nl-cli",
        sourceTurnIds=["turn-user", "turn-assistant"],
        tasks=[
            BridgeTask(
                id="CLI-TASK-001",
                title="Materialize assistant generated task",
                owner="Codex",
                reviewer="Claude",
                phase="Sprint CLI / Dev bridge",
                artifacts=["services/control-plane/bff/assistant/dev_bridge_dispatcher.py"],
                acceptance=["Task is assigned through ai_status.py"],
                summary="Verify CLI dispatch path.",
            )
        ],
        auditConversationHref="/bff/assistant/sessions/mgmt-nl-cli/transcript",
    )


def _write_fake_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_materializing_ai_status(repo_root)
    return repo_root


def _run_cli(packet_path: Path, repo_root: Path, *, dry_run: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": PUBLIC_KEYS_JSON}
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--packet-file",
        str(packet_path),
        "--repo-root",
        str(repo_root),
    ]
    if dry_run:
        cmd.append("--dry-run")
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))


def test_cli_accepts_dev_docs_generate_envelope_in_dry_run(tmp_path: Path) -> None:
    repo_root = _write_fake_repo(tmp_path)
    signed = sign_packet(_make_packet("pkt_cli_dry"), key_store={"assistant-bridge-dev": TEST_KEY})
    packet_path = tmp_path / "dev-docs-response.json"
    packet_path.write_text(
        json.dumps(
            {
                "data": {"packetId": "pkt_doc_archive"},
                "meta": {"taskPacket": signed.model_dump(mode="json", by_alias=True)},
            }
        ),
        encoding="utf-8",
    )

    result = _run_cli(packet_path, repo_root, dry_run=True)

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["packetId"] == "pkt_cli_dry"
    assert body["dryRun"] is True
    assert body["taskRecords"][0]["status"] == "dry_run"
    assert not (repo_root / "calls.jsonl").exists()


def test_cli_materializes_raw_signed_packet_through_ai_status(tmp_path: Path) -> None:
    repo_root = _write_fake_repo(tmp_path)
    signed = sign_packet(_make_packet("pkt_cli_live"), key_store={"assistant-bridge-dev": TEST_KEY})
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(signed.model_dump(mode="json", by_alias=True)), encoding="utf-8")

    result = _run_cli(packet_path, repo_root)

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["packetId"] == "pkt_cli_live"
    assert body["taskRecords"][0]["status"] == "dispatched"
    replay_rows = (repo_root / ".orchestrator" / "dev-bridge-seen-packets.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(replay_rows) == 1
    replay = json.loads(replay_rows[0])
    assert replay["packet_id"] == "pkt_cli_live"
    assert len(replay["digest"]) == 64

    calls = (repo_root / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(calls[0])
    assert record["argv"][0] == "dev-bridge-materialize-batch"
    assert len(record["argv"]) == 2
    assert record["ai_name"] == "assistant.dev.source"
    assert record["auto_worker_markers"] == {}
    assert record["packet_id"] == "pkt_cli_live"
    assert len(record["tasks"]) == 1
    assert record["tasks"][0]["task_id"] == "CLI-TASK-001"
    bridge = record["tasks"][0]["task_metadata"]["dev_bridge"]
    assert bridge["packet_id"] == "pkt_cli_live"
    assert bridge["conversation_id"] == "mgmt-nl-cli"
    assert bridge["source_turn_ids"] == ["turn-user", "turn-assistant"]
    assert bridge["task_spec"]["phase"] == "Sprint CLI / Dev bridge"
    assert bridge["task_spec"]["artifacts"] == [
        "services/control-plane/bff/assistant/dev_bridge_dispatcher.py"
    ]
    assert bridge["task_spec"]["acceptance"] == ["Task is assigned through ai_status.py"]


def test_cli_rejects_unsigned_packet(tmp_path: Path) -> None:
    repo_root = _write_fake_repo(tmp_path)
    packet_path = tmp_path / "unsigned.json"
    packet_path.write_text(
        json.dumps(_make_packet("pkt_cli_unsigned").model_dump(mode="json", by_alias=True)),
        encoding="utf-8",
    )

    result = _run_cli(packet_path, repo_root, dry_run=True)

    assert result.returncode == 2
    body = json.loads(result.stderr)
    assert body["status"] == "error"
    assert "Packet has no signature" in body["error"]


def test_concurrent_cli_dispatches_materialize_once(tmp_path: Path) -> None:
    repo_root = _write_fake_repo(tmp_path)
    signed = sign_packet(_make_packet("pkt_cli_concurrent"), key_store={"assistant-bridge-dev": TEST_KEY})
    packet_path = tmp_path / "concurrent-packet.json"
    packet_path.write_text(
        json.dumps(signed.model_dump(mode="json", by_alias=True)),
        encoding="utf-8",
    )
    env = {**os.environ, "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": PUBLIC_KEYS_JSON}
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--packet-file",
        str(packet_path),
        "--repo-root",
        str(repo_root),
    ]
    processes = [
        subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )
        for _ in range(4)
    ]
    outputs = [process.communicate(timeout=30) for process in processes]

    assert sorted(process.returncode for process in processes) == [0, 1, 1, 1], outputs
    bodies = [json.loads(stdout) for stdout, _stderr in outputs]
    assert sum(not body["replayRejected"] for body in bodies) == 1
    assert sum(body["replayRejected"] for body in bodies) == 3
    assert sum(
        record["status"] == "dispatched"
        for body in bodies
        for record in body["taskRecords"]
    ) == 1
    assert sum(
        record["status"] == "already_dispatched"
        for body in bodies
        for record in body["taskRecords"]
    ) == 3
    assert len((repo_root / "calls.jsonl").read_text(encoding="utf-8").splitlines()) == 1
