from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ..dev_bridge_models import BridgeActor, BridgeTask, DevTaskPacket
from ..dev_bridge_signer import sign_packet


REPO_ROOT = Path(__file__).resolve().parents[5]
QUEUE_SCRIPT = REPO_ROOT / "scripts" / "queue_assistant_dev_task_packet.py"
DRAIN_SCRIPT = REPO_ROOT / "scripts" / "drain_assistant_dev_task_packet_inbox.py"
TEST_KEY = b"test-key-for-dev-bridge-inbox-cli"


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
    (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
    scripts_dir = repo_root / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "ai_status.py").write_text(
        "import sys\nfrom pathlib import Path\nPath('assigned.txt').write_text(' '.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    return repo_root


def test_queue_cli_accepts_dev_docs_generate_envelope(tmp_path: Path) -> None:
    repo_root = _write_fake_repo(tmp_path)
    signed = sign_packet(_make_packet("pkt_inbox_cli"), key_store={"assistant-bridge-dev": TEST_KEY})
    packet_path = tmp_path / "dev-docs-response.json"
    packet_path.write_text(
        json.dumps({"meta": {"taskPacket": signed.model_dump(mode="json", by_alias=True)}}),
        encoding="utf-8",
    )

    env = {**os.environ, "BRIDGE_SIGNING_KEY": TEST_KEY.hex()}
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
    env = {**os.environ, "BRIDGE_SIGNING_KEY": TEST_KEY.hex()}

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
    assert "INBOX-CLI-TASK-001" in (repo_root / "assigned.txt").read_text(encoding="utf-8")
