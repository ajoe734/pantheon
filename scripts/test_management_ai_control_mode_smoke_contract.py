from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke_management_ai_control_mode_queue.sh"
REPAIR_SCRIPT = ROOT / "scripts" / "smoke_management_ai_openclaw_repair_e2e.sh"
RUNBOOK = ROOT / "docs" / "deployment" / "management-ai-dev-kernel-control-mode.md"


def _install_fake_control_smoke_curl(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "curl-capture"
    fake = bin_dir / "curl"
    fake.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'argv=%s\\n' "$*" >> "${FAKE_CURL_CAPTURE}"
env | grep -E '^(BFF_AUTH_TOKEN|PANTHEON_ASSISTANT_CONTROL_PASSPHRASE|CONTROL_MODE_PASSPHRASE)=' >> "${FAKE_CURL_CAPTURE}" || true
out=""
previous=""
for argument in "$@"; do
  if [[ "$previous" == "-o" ]]; then out="$argument"; fi
  previous="$argument"
done
url="${!#}"
code=200
payload='{}'
case "$url" in
  */health) payload='{"status":"ok"}' ;;
  */bff/assistant/mode)
    payload='{"data":{"kernel_enabled":true,"control_mode":{"configured":true,"active":false,"state":"inactive"}}}'
    ;;
  */bff/assistant/control-mode/activate)
    code=202
    payload='{"data":{"mode":"kernel_repair","active":true}}'
    ;;
  */bff/assistant/control-mode/deactivate)
    code="${FAKE_DEACTIVATE_CODE:-202}"
    payload='{"data":{"active":false,"state":"inactive"}}'
    ;;
  */bff/assistant/dev-docs/generate)
    code=201
    payload='{"data":{"packetId":"packet-test"},"meta":{"taskPacketQueued":true,"taskPacketQueueReceipt":{"path":"pending/test.json"}}}'
    ;;
  */bff/assistant/orchestrator/status)
    payload='{"data":{"supervisor":{"lifecycle":"running"},"providerReadiness":{"status":"ready"},"assistantDevBridge":{"inbox":{"pendingCount":0}}}}'
    ;;
esac
if [[ -n "$out" ]]; then printf '%s\\n' "$payload" > "$out"; else printf '%s\\n' "$payload"; fi
printf '%s' "$code"
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return bin_dir, capture


def test_control_mode_queue_smoke_requires_operator_passphrase_without_literal_secret() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "PANTHEON_ASSISTANT_CONTROL_PASSPHRASE" in source
    assert "CONTROL_MODE_PASSPHRASE" in source
    assert "set PANTHEON_ASSISTANT_CONTROL_PASSPHRASE" in source
    assert "passphrase=configured" in source
    assert "九條好漢" not in source
    assert "control phrase ok" not in source


def test_control_mode_queue_smoke_hits_closed_loop_endpoints() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "/bff/assistant/mode" in source
    assert "/bff/assistant/control-mode/activate" in source
    assert "/bff/assistant/control-mode/deactivate" in source
    assert 'activated="true"' in source
    assert "trap cleanup EXIT" in source
    assert "/bff/assistant/dev-docs/generate" in source
    assert "/bff/assistant/orchestrator/status" in source
    assert "kernel_repair" in source
    assert "queueTaskPacket: true" in source
    assert "emitTaskPacket: true" in source
    assert 'TASK_OWNER="${TASK_OWNER:-Codex}"' in source
    assert 'TASK_REVIEWER="${TASK_REVIEWER:-Claude}"' in source
    assert "proposedReviewer: $reviewer" in source
    assert "taskPacketQueued" in source
    assert 'chmod 0600 "${request_tmp}" "${response_tmp}" "${auth_header}"' in source
    assert 'export -n BFF_AUTH_TOKEN' in source
    assert '-H "@${auth_header}"' in source
    assert 'deactivate_code="$(curl_json POST' in source
    assert '[ "${deactivate_code}" != "202" ]' in source
    assert '.data.control_mode.active == false' in source


def test_control_mode_queue_smoke_cleanup_is_authoritative_and_credentials_are_not_exported(
    tmp_path: Path,
) -> None:
    bin_dir, capture = _install_fake_control_smoke_curl(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_CURL_CAPTURE": str(capture),
        "BFF_BASE_URL": "https://bff.invalid",
        "BFF_AUTH_TOKEN": "header-only-jwt-secret",
        "PANTHEON_ASSISTANT_CONTROL_PASSPHRASE": "control-passphrase-secret",
    }

    success = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert success.returncode == 0, success.stderr
    captured = capture.read_text(encoding="utf-8")
    assert "header-only-jwt-secret" not in captured
    assert "control-passphrase-secret" not in captured
    assert "/bff/assistant/control-mode/deactivate" in captured

    capture.unlink()
    cleanup_failure = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env={**env, "FAKE_DEACTIVATE_CODE": "500"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert cleanup_failure.returncode == 1
    assert "cleanup returned HTTP 500" in cleanup_failure.stderr


def test_runbook_documents_positive_queue_smoke() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "Positive SA/SD Queue Smoke" in runbook
    assert "scripts/smoke_management_ai_control_mode_queue.sh" in runbook
    assert "generated SA/SD artifacts are archived" in runbook
    assert "signed DevTaskPacket is queued into the supervisor inbox" in runbook


def test_openclaw_repair_smoke_requires_passphrase_without_literal_secret() -> None:
    source = REPAIR_SCRIPT.read_text(encoding="utf-8")

    assert "PANTHEON_ASSISTANT_CONTROL_PASSPHRASE" in source
    assert "CONTROL_MODE_PASSPHRASE" in source
    assert "passphrase=configured" in source
    assert "九條好漢" not in source
    assert "control phrase ok" not in source


def test_openclaw_repair_smoke_hits_write_and_bridge_endpoints() -> None:
    source = REPAIR_SCRIPT.read_text(encoding="utf-8")

    assert "/bff/assistant/control-mode/activate" in source
    assert "/bff/assistant/repair-worktrees/prepare" in source
    assert "/bff/management/nl/ask" in source
    assert "/bff/assistant/dev-docs/generate" in source
    assert "/bff/assistant/orchestrator/status" in source
    assert "openclaw: {repair: $repair}" in source
    assert "sessionId: $session" in source
    assert "workspace_class" in source
    assert "task_worktree" in source
    assert 'TASK_OWNER="${TASK_OWNER:-Codex}"' in source
    assert 'TASK_REVIEWER="${TASK_REVIEWER:-Claude}"' in source
    assert 'POLL_SECONDS="${POLL_SECONDS:-360}"' in source
    assert 'proposedReviewer: $reviewer' in source
    assert "pantheon-openclaw-gateway-adapter-1" in source
    assert "receipt_status" in source
    assert "processed" in source
    assert 'chmod 0600 "${request_tmp}" "${response_tmp}" "${status_tmp}" "${auth_header}"' in source
    assert 'export -n BFF_AUTH_TOKEN' in source
    assert 'local headers=(-H "@${auth_header}")' in source
    assert 'deactivate_code="$(curl_json POST' in source
    assert '[ "${deactivate_code}" != "202" ]' in source
    assert '.data.control_mode.active == false' in source


def test_runbook_documents_positive_openclaw_repair_smoke() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "Positive OpenClaw Repair E2E Smoke" in runbook
    assert "scripts/smoke_management_ai_openclaw_repair_e2e.sh" in runbook
    assert "`/bff/assistant/repair-worktrees/prepare` returns a clean task worktree" in runbook
    assert "`/bff/management/nl/ask` forwards `openclaw.repair` metadata" in runbook
    assert "`workspaceClass=task_worktree`" in runbook
    assert "supervisor drains the queued DevTaskPacket" in runbook
