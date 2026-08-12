from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / ".orchestrator" / "supervisor.py"
RUNTIME_STATE = ROOT / ".orchestrator" / "runtime_state.py"


def test_supervisor_drains_assistant_dev_inbox_after_hot_dispatch() -> None:
    source = SUPERVISOR.read_text(encoding="utf-8")

    assert "def drain_assistant_dev_packet_inbox" in source
    assert "def assistant_dev_bridge_bff_dirs" in source
    assert "from assistant.dev_bridge_inbox import drain_task_packet_inbox" in source
    assert "assistant_dev_packet_inbox_drained" in source
    assert '"PANTHEON_ASSISTANT_DEV_BRIDGE_REQUIRE_TASK_STATE_READBACK": "1"' in source
    assert "**status_command_runtime_env(config)" in source
    assert "dispatch_env=bridge_runtime_env" in source
    assert '"canonical_readbacks": canonical_readbacks' in source

    dispatch_pos = source.index('"process_queue_reserved"')
    drain_pos = source.index('"drain_assistant_dev_packet_inbox"')
    assert dispatch_pos < drain_pos
    assert "_run_scan_locked" not in source


def test_supervisor_bridge_import_searches_code_root_before_status_root() -> None:
    source = SUPERVISOR.read_text(encoding="utf-8")

    assert 'code_bff_dir = THIS_DIR.parent / "services" / "control-plane" / "bff"' in source
    assert 'repo_bff_dir = repo_root / "services" / "control-plane" / "bff"' in source
    assert "bff_dirs = assistant_dev_bridge_bff_dirs(repo_root)" in source
    assert "for bff_dir in reversed(bff_dirs):" in source
    assert '"searched_bff_dirs": [str(path) for path in bff_dirs]' in source


def test_runtime_state_preserves_assistant_dev_bridge_receipts() -> None:
    source = RUNTIME_STATE.read_text(encoding="utf-8")

    assert '"assistant_dev_bridge": {' in source
    assert '"last_drain_at": None' in source
    assert '"last_result": None' in source
    assert "state.setdefault(\"assistant_dev_bridge\", {})" in source
