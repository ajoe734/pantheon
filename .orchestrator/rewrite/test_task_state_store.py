from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import task_state_store as store


def state(status: str, *, note: str = "") -> dict:
    return {
        "sprint": "L12",
        "tasks": [
            {
                "id": "T1",
                "status": status,
                "owner": "Codex",
                "reviewer": "Claude",
                "note": note,
            }
        ],
    }


def board(*rows: tuple[str, str], **extra) -> dict:
    payload = {
        "tasks": [
            {"id": task_id, "status": status, "payload": "x" * 1024}
            for task_id, status in rows
        ]
    }
    payload.update(extra)
    return payload


def drain_marker(*task_ids: str) -> dict:
    return {
        "reason": "operator drained a stuck board",
        "actor": "Human/Ops",
        "approved_at": "2026-07-26T00:00:00Z",
        "task_ids": list(task_ids),
    }


def journal_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_append_writes_delta_journal_and_atomic_current_head(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = state("todo", note="first")
    second = state("in_progress", note="second")

    first_event = store.append_state_commit(path, first, source="test")
    second_event = store.append_state_commit(path, second, source="test")
    rows = journal_rows(path)
    head = json.loads((tmp_path / "events.jsonl.head.json").read_text())

    assert [row["sequence"] for row in rows] == [1, 2]
    assert all("state" not in row for row in rows)
    assert rows[1]["delta"]["tasks"]["upsert"] == [second["tasks"][0]]
    assert second_event["previous_event_sha256"] == first_event["event_sha256"]
    assert head["state"] == second
    assert head["state_sha256"] == store.sha256_json(second)
    assert store.load_snapshot(path)["state"] == second


def test_transition_event_is_materially_smaller_than_full_board(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = board(*[(f"TASK-{index:04d}", "todo") for index in range(200)])
    second = copy.deepcopy(first)
    second["tasks"][117]["status"] = "in_progress"

    store.append_state_commit(path, first, source="test")
    store.append_state_commit(path, second, source="test")
    transition = journal_rows(path)[1]

    assert len(store.canonical_json_bytes(transition)) < len(store.canonical_json_bytes(second)) // 20
    assert transition["delta"]["tasks"]["upsert"] == [second["tasks"][117]]


def test_hot_read_uses_head_without_reading_journal_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    expected = board(*[(f"TASK-{index:04d}", "todo") for index in range(300)])
    store.append_state_commit(path, expected, source="test")
    calls: list[tuple[int, int]] = []
    original = store._read_range

    def spy(event_path: Path, *, offset: int, length: int) -> bytes:
        calls.append((offset, length))
        return original(event_path, offset=offset, length=length)

    monkeypatch.setattr(store, "_read_range", spy)
    snapshot = store.load_snapshot(path)

    assert snapshot["state"] == expected
    assert calls == [(path.stat().st_size, 0)]
    assert snapshot["revalidated_events"] == 0


def test_crash_after_journal_append_replays_only_tail(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "events.jsonl"
    first = state("todo", note="first")
    second = state("in_progress", note="second")
    store.append_state_commit(path, first, source="test")
    old_head = store._head_path(path).read_bytes()
    old_size = path.stat().st_size
    store.append_state_commit(path, second, source="test")
    # Simulate a crash between journal fsync and atomic head replacement.
    store._head_path(path).write_bytes(old_head)
    calls: list[tuple[int, int]] = []
    original = store._read_range

    def spy(event_path: Path, *, offset: int, length: int) -> bytes:
        calls.append((offset, length))
        return original(event_path, offset=offset, length=length)

    monkeypatch.setattr(store, "_read_range", spy)
    recovered = store.load_snapshot(path)

    assert recovered["state"] == second
    assert recovered["revalidated_events"] == 1
    assert calls == [(old_size, path.stat().st_size - old_size)]
    with store.snapshot_transaction(path) as transaction:
        assert transaction.load_snapshot()["state"] == second
    repaired = json.loads(store._head_path(path).read_text())
    assert repaired["sequence"] == 2
    assert repaired["state"] == second


def test_partial_crash_tail_is_ignored_then_truncated_by_writer(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = state("todo")
    second = state("in_progress")
    store.append_state_commit(path, first, source="test")
    committed_size = path.stat().st_size
    with path.open("ab") as stream:
        stream.write(b'{"version":2,"type":"task_state_transition"')

    observed = store.load_snapshot(path)
    assert observed["state"] == first
    assert observed["ignored_partial_tail_bytes"] > 0

    store.append_state_commit(path, second, source="test")
    assert store.load_snapshot(path)["state"] == second
    assert path.read_bytes()[committed_size:].startswith(b'{"archive_anchor_sha256"')
    assert len(journal_rows(path)) == 2


def test_identical_state_commit_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    payload = state("todo")
    first = store.append_state_commit(path, payload, source="test")
    second = store.append_state_commit(path, payload, source="different-source")
    assert second == first
    assert len(journal_rows(path)) == 1


def test_offline_load_events_materializes_compatibility_state(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = state("todo")
    second = state("review")
    store.append_state_commit(path, first, source="test")
    store.append_state_commit(path, second, source="test")

    events = store.load_events(path)
    assert [event["state"] for event in events] == [first, second]
    assert store.project_latest_state(events) == second


def test_tampered_delta_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store.append_state_commit(path, state("todo"), source="test")
    rows = journal_rows(path)
    rows[0]["delta"]["ops"][0]["value"] = "tampered"
    path.write_text(json.dumps(rows[0]) + "\n")

    with pytest.raises(store.TaskStateStoreError, match="event digest mismatch"):
        store.load_events(path)


def test_broken_hash_chain_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store.append_state_commit(path, state("todo"), source="test")
    store.append_state_commit(path, state("review"), source="test")
    rows = journal_rows(path)
    rows[1]["previous_event_sha256"] = "0" * 64
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    with pytest.raises(store.TaskStateStoreError, match="previous hash mismatch"):
        store.load_events(path)


def test_tampered_head_is_rejected_without_journal_fallback(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store.append_state_commit(path, state("todo"), source="test")
    head_path = store._head_path(path)
    head = json.loads(head_path.read_text())
    head["state"]["tasks"][0]["status"] = "done"
    head_path.write_text(json.dumps(head))

    with pytest.raises(store.TaskStateStoreError, match="head digest mismatch"):
        store.load_snapshot(path)


def test_legacy_v1_journal_requires_explicit_migration(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    legacy = {
        "version": 1,
        "type": "task_state_committed",
        "state": state("todo"),
    }
    path.write_text(json.dumps(legacy) + "\n")

    with pytest.raises(store.TaskStateStoreError, match="explicit V2 migration"):
        store.load_snapshot(path)


def test_headless_multigigabyte_authority_fails_before_journal_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    with path.open("wb") as stream:
        stream.truncate(store.MAX_HEADLESS_RECOVERY_BYTES + 1)
    monkeypatch.setattr(
        store,
        "_read_range",
        lambda *args, **kwargs: pytest.fail("hot path read a headless legacy-sized journal"),
    )

    with pytest.raises(store.TaskStateStoreError, match="bounded genesis-recovery"):
        store.load_snapshot(path)


def test_symlink_event_log_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real.jsonl"
    real.write_text("")
    link = tmp_path / "events.jsonl"
    link.symlink_to(real)
    with pytest.raises(store.TaskStateStoreError, match="regular file"):
        store.append_state_commit(link, state("todo"), source="test")


def test_observational_read_never_provisions_files(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    with pytest.raises(store.TaskStateStoreError, match="must be an existing"):
        store.load_snapshot(path, refresh_checkpoint=False)
    assert list(tmp_path.iterdir()) == []


def test_reject_nonterminal_collapse_and_partial_disappearance(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    before = board(("KEEP", "in_progress"), ("DROP", "review"))
    store.append_state_commit(path, before, source="test")
    with pytest.raises(store.TaskStateStoreError, match="disappearance"):
        store.append_state_commit(
            path, board(("KEEP", "in_progress")), source="rogue"
        )
    assert store.load_snapshot(path)["state"] == before


def test_audited_drain_marker_authorizes_exact_removal(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    before = board(("KEEP", "in_progress"), ("DROP", "blocked"))
    after = board(
        ("KEEP", "in_progress"),
        **{store.DRAIN_MARKER_KEY: drain_marker("DROP")},
    )
    store.append_state_commit(path, before, source="test")
    store.append_state_commit(path, after, source="test")
    assert store.load_snapshot(path)["state"] == after


def test_terminal_task_can_be_archived(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store.append_state_commit(path, board(("T", "review")), source="test")
    store.append_state_commit(path, board(("T", "done")), source="test")
    store.append_state_commit(path, {"tasks": []}, source="test")
    assert store.load_snapshot(path)["state"] == {"tasks": []}


def test_snapshot_transaction_extends_one_locked_generation(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store.append_state_commit(path, state("todo"), source="test")
    with store.snapshot_transaction(path) as transaction:
        current = transaction.load_snapshot()["state"]
        current["tasks"][0]["status"] = "in_progress"
        transaction.append_state_commit(current, source="transaction")
        current["tasks"][0]["note"] = "checkpoint two"
        transaction.append_state_commit(current, source="transaction")
    assert store.load_snapshot(path)["state"] == current
    assert len(journal_rows(path)) == 3


def test_full_audit_validates_head_and_entire_delta_chain(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    expected = state("review")
    store.append_state_commit(path, state("todo"), source="test")
    store.append_state_commit(path, expected, source="test")

    report = store.audit_full_journal(path)
    assert report["ok"] is True
    assert report["event_count"] == 2
    assert report["state"] == expected
    assert report["journal_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_archive_anchor_is_bound_to_genesis_and_verifiable(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy-v1.jsonl"
    legacy.write_bytes(b"immutable legacy bytes\n")
    path = tmp_path / "events-v2.jsonl"
    anchor = {
        "version": store.ARCHIVE_ANCHOR_VERSION,
        "type": store.ARCHIVE_ANCHOR_TYPE,
        "archived_path": str(legacy),
        "byte_size": legacy.stat().st_size,
        "journal_sha256": hashlib.sha256(legacy.read_bytes()).hexdigest(),
        "event_count": 1,
        "last_event_id": "legacy-1",
        "last_event_sha256": "a" * 64,
        "state_sha256": "b" * 64,
        "created_at": "2026-08-11T00:00:00Z",
    }
    written = store.write_archive_anchor(path, anchor)
    event = store.append_state_commit(path, state("todo"), source="migration")

    assert event["archive_anchor_sha256"] == written["anchor_sha256"]
    assert store.verify_archive_anchor(path)["ok"] is True

    changed = dict(anchor)
    changed["created_at"] = "2026-08-11T00:00:01Z"
    with pytest.raises(store.TaskStateStoreError, match="immutable"):
        store.write_archive_anchor(path, changed)


def test_verify_projection_reports_current_head_parity(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    expected = state("in_progress")
    store.append_state_commit(path, expected, source="test")
    report = store.verify_projection(path, expected)
    assert report["ok"] is True
    assert report["event_count"] == 1
    assert report["replayed_tail_events"] == 0
    assert report["nonterminal_task_count"] == 1
