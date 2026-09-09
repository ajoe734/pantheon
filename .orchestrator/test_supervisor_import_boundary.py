"""Status-root files never select executable supervisor bridge imports."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".orchestrator"))

import supervisor


CHILD_PROBE = r'''
import importlib
import json
import sys
from pathlib import Path

repo_root, command_root, status_root = map(Path, sys.argv[1:4])
mode = sys.argv[4]
sys.path.insert(0, str(repo_root / ".orchestrator"))
import supervisor

for name in list(sys.modules):
    if name == "development_bridge" or name.startswith("development_bridge."):
        del sys.modules[name]
command_tooling = command_root / ".orchestrator"
status_tooling = status_root / ".orchestrator"
supervisor.THIS_DIR = command_tooling
sys.path.insert(0, str(command_tooling))
if mode == "cached-foreign":
    sys.path.insert(0, str(status_tooling))
    importlib.import_module("development_bridge.dev_bridge_inbox")
    sys.path.remove(str(status_tooling))

runtime_env = {
    "PANTHEON_COMMAND_ROOT": str(command_root),
    "PANTHEON_COMMAND_RUNTIME_SHA": "isolated-fixture",
    "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
    "PANTHEON_TASK_STATE_EVENT_LOG": str(status_root / "fixture-events.jsonl"),
}
supervisor.status_command_runtime_env = lambda config: dict(runtime_env)
events = []
supervisor.write_activity_log = lambda config, event: events.append(event)
config = {
    "paths": {"status_file": str(status_root / "ai-status.json")},
    "coordination": {"repositories": {"pantheon": {"repo": "ajoe734/pantheon"}}},
    "assistant_dev_bridge": {"enabled": mode != "disabled"},
}
state = {}
changed = supervisor.drain_assistant_dev_packet_inbox(config, state)
module = sys.modules.get("development_bridge.dev_bridge_inbox")
print(json.dumps({
    "changed": changed,
    "state": state,
    "events": events,
    "origin": getattr(module, "__file__", None),
    "calls": getattr(module, "CALLS", []),
    "status_source_imported": (status_root / "source-imported").exists(),
}))
'''


def _package(root: Path, *, status_root: Path, mutable: bool) -> None:
    package = root / ".orchestrator" / "development_bridge"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    import_effect = (
        f"Path({str(status_root / 'source-imported')!r}).write_text('imported')\n"
        if mutable
        else ""
    )
    (package / "dev_bridge_inbox.py").write_text(
        "from pathlib import Path\n"
        + import_effect
        + "CALLS = []\n"
        + "def drain_task_packet_inbox(**kwargs):\n"
        + "    CALLS.append(kwargs)\n"
        + "    return {'processedCount': 0, 'errorCount': 0}\n",
        encoding="utf-8",
    )


def _probe(tmp_path: Path, mode: str = "normal") -> tuple[dict, Path, Path]:
    command_root = tmp_path / "command"
    status_root = tmp_path / "status"
    (command_root / ".orchestrator").mkdir(parents=True)
    status_root.mkdir()
    if mode != "missing-source":
        _package(command_root, status_root=status_root, mutable=False)
    _package(status_root, status_root=status_root, mutable=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PANTHEON_", "ORCH_"))
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "-c", CHILD_PROBE, str(REPO_ROOT), str(command_root), str(status_root), mode],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return json.loads(result.stdout), command_root, status_root


def test_bridge_tooling_dirs_excludes_status_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    command_tooling = tmp_path / "command" / ".orchestrator"
    monkeypatch.setattr(supervisor, "THIS_DIR", command_tooling)
    assert supervisor.assistant_dev_bridge_tooling_dirs(tmp_path / "status") == [command_tooling]


def test_real_bridge_import_uses_command_source_and_status_data(tmp_path: Path) -> None:
    result, command_root, status_root = _probe(tmp_path)
    assert result["status_source_imported"] is False
    assert result["origin"] == str(command_root / ".orchestrator/development_bridge/dev_bridge_inbox.py")
    assert result["changed"] is False
    assert len(result["calls"]) == 1
    call = result["calls"][0]
    assert call["repo_root"] == str(status_root)
    assert call["dispatch_env"]["PANTHEON_STATUS_ROOT"] == str(status_root)
    assert call["dispatch_env"]["PANTHEON_COMMAND_ROOT"] == str(command_root)
    assert call["dispatch_env"]["PANTHEON_ASSISTANT_DEV_BRIDGE_REQUIRE_TASK_STATE_READBACK"] == "1"


def test_missing_command_package_never_falls_back_to_status_source(tmp_path: Path) -> None:
    result, _, _ = _probe(tmp_path, "missing-source")
    assert result["status_source_imported"] is False
    assert result["calls"] == []
    assert result["changed"] is True
    assert result["state"]["assistant_dev_bridge"]["last_result"]["status"] == "unavailable"
    assert result["events"][0]["type"] == "assistant_dev_packet_drain_unavailable"


def test_cached_foreign_bridge_module_is_rejected_before_call(tmp_path: Path) -> None:
    result, _, _ = _probe(tmp_path, "cached-foreign")
    assert result["calls"] == []
    assert result["changed"] is True
    assert result["state"]["assistant_dev_bridge"]["last_result"]["status"] == "unavailable"
    assert result["events"][0]["type"] == "assistant_dev_packet_drain_unavailable"


def test_disabled_bridge_does_not_import_source_or_change_state(tmp_path: Path) -> None:
    result, _, _ = _probe(tmp_path, "disabled")
    assert result["status_source_imported"] is False
    assert result["origin"] is None
    assert result["changed"] is False
    assert result["state"] == {}
    assert result["events"] == []


def test_retired_dashboard_refresh_entrypoint_is_absent() -> None:
    assert not hasattr(supervisor, "refresh_dashboard_runtime_artifacts")
