from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common


def test_shadow_store_env_is_added_to_issued_runtime_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    event_log = tmp_path / "runtime" / "task-state-events.jsonl"
    config = {
        "task_state_store": {
            "mode": "shadow",
            "event_log": str(event_log),
        }
    }
    monkeypatch.setattr(
        common,
        "_status_command_runtime_env_from_record",
        lambda record: {common.STATUS_COMMAND_ROOT_ENV: "/issued/root"},
    )

    env = common.status_command_runtime_env(
        config,
        {"status_command_runtime": {"source_sha": "issued"}},
    )

    assert env[common.STATUS_COMMAND_ROOT_ENV] == "/issued/root"
    assert env[common.TASK_STATE_STORE_MODE_ENV] == "shadow"
    assert env[common.TASK_STATE_EVENT_LOG_ENV] == str(event_log)


def test_relative_repo_template_is_not_exposed_to_status_commands() -> None:
    assert common.task_state_store_runtime_env(
        {
            "task_state_store": {
                "mode": "shadow",
                "event_log": ".orchestrator/task-state-events.jsonl",
            }
        }
    ) == {}
