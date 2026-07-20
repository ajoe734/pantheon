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
