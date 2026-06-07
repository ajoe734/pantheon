from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / ".orchestrator" / "supervisor.py"
RUNTIME_STATE = ROOT / ".orchestrator" / "runtime_state.py"


def test_supervisor_drains_assistant_dev_inbox_before_watch_scan() -> None:
    source = SUPERVISOR.read_text(encoding="utf-8")

    assert "def drain_assistant_dev_packet_inbox" in source
    assert "from assistant.dev_bridge_inbox import drain_task_packet_inbox" in source
    assert "assistant_dev_packet_inbox_drained" in source

    drain_pos = source.index("changed = drain_assistant_dev_packet_inbox(config, state) or changed")
    scan_pos = source.index("changed = run_scan(config, state, replay=replay, provider_capabilities=provider_report)")
    assert drain_pos < scan_pos


def test_runtime_state_preserves_assistant_dev_bridge_receipts() -> None:
    source = RUNTIME_STATE.read_text(encoding="utf-8")

    assert '"assistant_dev_bridge": {' in source
    assert '"last_drain_at": None' in source
    assert '"last_result": None' in source
    assert "state.setdefault(\"assistant_dev_bridge\", {})" in source
