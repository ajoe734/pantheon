from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common
from rewrite import task_state_store


def test_authoritative_store_env_is_added_to_issued_runtime_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    event_log = tmp_path / "runtime" / "task-state-events.jsonl"
    config = {
        "task_state_store": {
            "mode": "authoritative",
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
    assert env[common.TASK_STATE_STORE_MODE_ENV] == "authoritative"
    assert env[common.TASK_STATE_EVENT_LOG_ENV] == str(event_log)


def test_relative_repo_template_is_rejected_for_status_commands() -> None:
    with pytest.raises(RuntimeError, match="provisioned absolute event_log"):
        common.task_state_store_runtime_env(
        {
            "task_state_store": {
                "mode": "authoritative",
                "event_log": ".orchestrator/task-state-events.jsonl",
            }
        }
        )


def test_authoritative_store_env_is_added_to_status_commands(tmp_path: Path) -> None:
    event_log = tmp_path / "runtime" / "task-state-events.jsonl"

    env = common.task_state_store_runtime_env(
        {
            "task_state_store": {
                "mode": "authoritative",
                "event_log": str(event_log),
            }
        }
    )

    assert env[common.TASK_STATE_STORE_MODE_ENV] == "authoritative"
    assert env[common.TASK_STATE_EVENT_LOG_ENV] == str(event_log)


def test_common_status_io_uses_authoritative_journal(tmp_path: Path) -> None:
    status_file = tmp_path / "status" / "ai-status.json"
    status_file.parent.mkdir()
    event_log = tmp_path / "runtime" / "task-state-events.jsonl"
    first = {"tasks": [{"id": "STATE-IO-001", "status": "todo"}]}
    second = {"tasks": [{"id": "STATE-IO-001", "status": "in_progress"}]}
    task_state_store.append_state_commit(event_log, first, source="migration")
    status_file.write_text('{"tasks":[{"id":"ROGUE"}]}\n', encoding="utf-8")
    config = {
        "paths": {"status_file": str(status_file)},
        "task_state_store": {
            "mode": "authoritative",
            "event_log": str(event_log),
        },
    }

    assert common.load_status(config) == first
    common.write_status(config, second, source="supervisor-test")

    assert common.load_status(config) == second
    assert task_state_store.load_events(event_log)[-1]["source"] == "supervisor-test"
    assert common.load_json(status_file) == second


def test_common_status_io_rejects_unprovisioned_authoritative_template(tmp_path: Path) -> None:
    status_file = tmp_path / "ai-status.json"
    status_file.write_text('{"tasks":[{"id":"ROGUE"}]}\n', encoding="utf-8")
    config = {
        "paths": {"status_file": str(status_file)},
        "task_state_store": {
            "mode": "authoritative",
            "event_log": ".orchestrator/task-state-events.jsonl",
        },
    }

    with pytest.raises(RuntimeError, match="provisioned absolute event_log"):
        common.load_status(config)
    with pytest.raises(RuntimeError, match="provisioned absolute event_log"):
        common.write_status(config, {"tasks": []}, source="should-not-write")

    assert common.load_json(status_file) == {"tasks": [{"id": "ROGUE"}]}
