from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import task_state_store as store


def state(status: str, *, next_value: str) -> dict:
    return {
        "tasks": [
            {
                "id": "T1",
                "status": status,
                "owner": "Codex",
                "reviewer": "Claude",
                "next": next_value,
            }
        ]
    }


def test_append_replays_latest_state_and_retains_history(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    first = state("todo", next_value="first")
    second = state("in_progress", next_value="second")

    first_event = store.append_state_commit(path, first, source="test", committed_at="2026-07-20T07:00:00Z")
    second_event = store.append_state_commit(path, second, source="test", committed_at="2026-07-20T07:01:00Z")
    events = store.load_events(path)

    assert [event["sequence"] for event in events] == [1, 2]
    assert events[0]["state"] == first
    assert events[1]["state"] == second
    assert second_event["previous_event_sha256"] == first_event["event_sha256"]
    assert store.project_latest_state(events) == second
    assert store.verify_projection(path, second)["ok"] is True


def test_identical_state_commit_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    payload = state("todo", next_value="same")

    first = store.append_state_commit(path, payload, source="test")
    second = store.append_state_commit(path, payload, source="test")

    assert second == first
    assert len(store.load_events(path)) == 1


def test_projection_of_prefix_is_point_in_time_state(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    first = state("todo", next_value="first")
    second = state("review", next_value="review it")
    store.append_state_commit(path, first, source="test")
    store.append_state_commit(path, second, source="test")

    events = store.load_events(path)

    assert store.project_latest_state(events[:1]) == first
    assert store.project_latest_state(events) == second


def test_tampered_state_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    store.append_state_commit(path, state("todo", next_value="first"), source="test")
    event = json.loads(path.read_text(encoding="utf-8"))
    event["state"]["tasks"][0]["status"] = "done"
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(store.TaskStateStoreError, match="state digest mismatch"):
        store.load_events(path)


def test_broken_hash_chain_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    store.append_state_commit(path, state("todo", next_value="first"), source="test")
    store.append_state_commit(path, state("review", next_value="second"), source="test")
    lines = path.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    second["previous_event_sha256"] = "0" * 64
    path.write_text(lines[0] + "\n" + json.dumps(second) + "\n", encoding="utf-8")

    with pytest.raises(store.TaskStateStoreError, match="previous hash mismatch"):
        store.load_events(path)


def test_symlink_event_log_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real.jsonl"
    real.write_text("", encoding="utf-8")
    link = tmp_path / "events.jsonl"
    link.symlink_to(real)

    with pytest.raises(store.TaskStateStoreError, match="regular file"):
        store.append_state_commit(link, state("todo", next_value="first"), source="test")


def test_empty_journal_does_not_claim_projection_parity(tmp_path: Path) -> None:
    report = store.verify_projection(
        tmp_path / "missing.jsonl",
        state("todo", next_value="first"),
    )

    assert report["ok"] is False
    assert report["event_count"] == 0


def test_reject_nonterminal_drop_to_empty_state(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    first = state("todo", next_value="first")
    empty_state = {"tasks": []}

    store.append_state_commit(path, first, source="test")
    with pytest.raises(store.TaskStateStoreError, match="nonterminal drop rejected"):
        store.append_state_commit(path, empty_state, source="test")

    events = store.load_events(path)
    assert len(events) == 1
    assert events[0]["state"] == first


def test_allow_first_bootstrap_with_empty_or_no_tasks(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    empty_state = {"tasks": []}
    store.append_state_commit(path, empty_state, source="test")
    events = store.load_events(path)
    assert len(events) == 1
    assert events[0]["state"] == empty_state


def test_allow_drain_when_all_previous_tasks_were_terminal(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    all_done = state("done", next_value="finished")
    empty_state = {"tasks": []}

    store.append_state_commit(path, all_done, source="test")
    store.append_state_commit(path, empty_state, source="test")

    events = store.load_events(path)
    assert len(events) == 2
    assert events[1]["state"] == empty_state


def test_relative_event_log_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(store.TaskStateStoreError, match="must be an absolute path"):
        store.append_state_commit("relative_events.jsonl", {"tasks": []}, source="test")


def test_unrelated_workers_not_superseded_and_parity_preserved_after_rejected_drop(
    tmp_path: Path,
) -> None:
    path = tmp_path / "task-state-events.jsonl"
    active_state = {
        "tasks": [
            {"id": "TASK-001", "status": "in_progress", "owner": "Codex", "reviewer": "Claude"},
            {"id": "TASK-002", "status": "todo", "owner": "Antigravity", "reviewer": "Claude"},
        ],
        "workers": {
            "worker-1": {"status": "running", "current_task_id": "TASK-001"},
            "worker-2": {"status": "idle"},
        },
    }
    empty_state = {"tasks": []}

    store.append_state_commit(path, active_state, source="test")
    with pytest.raises(store.TaskStateStoreError, match="nonterminal drop rejected"):
        store.append_state_commit(path, empty_state, source="test")

    events = store.load_events(path)
    latest = store.project_latest_state(events)
    assert len(events) == 1
    assert latest == active_state
    assert len(latest["tasks"]) == 2
    assert latest["tasks"][0]["status"] == "in_progress"
    assert latest["tasks"][1]["status"] == "todo"

    report = store.verify_projection(path, active_state)
    assert report["ok"] is True
    assert report["event_count"] == 1


