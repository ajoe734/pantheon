from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke_management_ai_control_mode_queue.sh"
REPAIR_SCRIPT = ROOT / "scripts" / "smoke_management_ai_openclaw_repair_e2e.sh"
RUNBOOK = ROOT / "docs" / "deployment" / "management-ai-dev-kernel-control-mode.md"


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
    assert "/bff/assistant/dev-docs/generate" in source
    assert "/bff/assistant/orchestrator/status" in source
    assert "kernel_repair" in source
    assert "queueTaskPacket: true" in source
    assert "emitTaskPacket: true" in source
    assert "taskPacketQueued" in source


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
    assert "workspace_class" in source
    assert "task_worktree" in source
    assert "receipt_status" in source
    assert "processed" in source


def test_runbook_documents_positive_openclaw_repair_smoke() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "Positive OpenClaw Repair E2E Smoke" in runbook
    assert "scripts/smoke_management_ai_openclaw_repair_e2e.sh" in runbook
    assert "`/bff/assistant/repair-worktrees/prepare` returns a clean task worktree" in runbook
    assert "`/bff/management/nl/ask` forwards `openclaw.repair` metadata" in runbook
    assert "`workspaceClass=task_worktree`" in runbook
    assert "supervisor drains the queued DevTaskPacket" in runbook
