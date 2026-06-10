from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ..dev_bridge_models import BridgeActor, BridgeTask, DevTaskPacket
from ..dev_bridge_signer import sign_packet


REPO_ROOT = Path(__file__).resolve().parents[5]
SCRIPT = REPO_ROOT / "scripts" / "dispatch_assistant_dev_task_packet.py"
TEST_KEY = b"test-key-for-dev-bridge-cli"


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
    (repo_root / "ai-status.json").write_text("{}", encoding="utf-8")
    scripts_dir = repo_root / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "ai_status.py").write_text(
        "\n".join(
            [
                "import json",
                "import os",
                "import sys",
                "from pathlib import Path",
                "record = {",
                "    'argv': sys.argv[1:],",
                "    'ai_name': os.environ.get('AI_NAME'),",
                "    'phase': os.environ.get('TASK_PHASE'),",
                "    'artifacts': os.environ.get('TASK_ARTIFACTS'),",
                "    'acceptance': os.environ.get('TASK_ACCEPTANCE'),",
                "}",
                "with Path('calls.jsonl').open('a', encoding='utf-8') as fh:",
                "    fh.write(json.dumps(record, sort_keys=True) + '\\n')",
                "sys.exit(0)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return repo_root


def _run_cli(packet_path: Path, repo_root: Path, *, dry_run: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "BRIDGE_SIGNING_KEY": TEST_KEY.hex()}
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
    assert (repo_root / ".orchestrator" / "dev-bridge-seen-packets.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["pkt_cli_live"]

    calls = (repo_root / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(calls[0])
    assert record["argv"] == [
        "assign",
        "CLI-TASK-001",
        "Codex",
        "Claude",
        "Materialize assistant generated task",
    ]
    assert record["ai_name"] == "management-ai"
    assert record["phase"] == "Sprint CLI / Dev bridge"


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
