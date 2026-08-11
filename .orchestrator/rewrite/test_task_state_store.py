from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import task_state_store as store


def board(*rows: tuple[str, str], **extra: object) -> dict:
    value = {"tasks": [{"id": task_id, "status": status} for task_id, status in rows]}
    value.update(extra)
    return value


def drain(*task_ids: str) -> dict:
    return {
        "reason": "operator-approved drain",
        "actor": "Human/Ops",
        "approved_at": "2026-08-11T00:00:00Z",
        "task_ids": list(task_ids),
    }


def legacy_event(state: dict, *, sequence: int, previous: str | None) -> dict:
    event = {
        "version": 1,
        "type": "task_state_committed",
        "sequence": sequence,
        "committed_at": "2026-08-11T00:00:00Z",
        "source": "legacy-test",
        "previous_event_sha256": previous,
        "state_sha256": store.sha256_json(state),
        "state": state,
    }
    event["event_sha256"] = store.sha256_json(event)
    event["event_id"] = f"task-state-{event['event_sha256']}"
    return event


def write_legacy(path: Path) -> tuple[dict, bytes]:
    first = board(("LEGACY-001", "todo"))
    second = board(("LEGACY-001", "review"))
    one = legacy_event(first, sequence=1, previous=None)
    two = legacy_event(second, sequence=2, previous=one["event_sha256"])
    payload = b"\n".join(store.canonical_json_bytes(item) for item in (one, two)) + b"\n"
    path.write_bytes(payload)
    return second, payload


def test_v2_transition_records_are_deltas_not_full_boards(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = {"tasks": [{"id": f"TASK-{number:04d}", "status": "todo"} for number in range(1200)]}
    second = json.loads(json.dumps(first))
    second["tasks"][731]["status"] = "review"

    store.append_state_commit(path, first, source="test")
    store.append_state_commit(path, second, source="test")

    raw_events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert "state" not in raw_events[1]
    assert raw_events[1]["delta"] == [{"op": "set", "path": ["tasks", 731, "status"], "value": "review"}]
    assert len(store.canonical_json_bytes(raw_events[1])) < len(store.canonical_json_bytes(second)) // 20
    assert store.load_snapshot(path)["state"] == second


def test_head_read_never_opens_or_hashes_frozen_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "events.jsonl"
    expected, _legacy_bytes = write_legacy(path)
    store.migrate_legacy_journal(path)
    archive = path.with_name(f"{path.name}.v1.archive.jsonl")
    before = archive.stat().st_atime_ns

    def forbidden_archive_read(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("hot read touched the frozen archive")

    monkeypatch.setattr(store, "_read_legacy_journal", forbidden_archive_read)
    snapshots = [store.load_snapshot(path) for _ in range(3)]

    assert [item["state"] for item in snapshots] == [expected, expected, expected]
    assert archive.stat().st_atime_ns == before


def test_crash_after_fsynced_delta_recovers_only_the_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "events.jsonl"
    first = board(("CRASH-001", "todo"))
    second = board(("CRASH-001", "review"))
    third = board(("CRASH-001", "review"), revision=3)
    store.append_state_commit(path, first, source="test")
    real_write_head = store._write_head_cas

    monkeypatch.setattr(store, "_write_head_cas", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("simulated head replace crash")))
    with pytest.raises(OSError, match="simulated head replace crash"):
        store.append_state_commit(path, second, source="test")
    monkeypatch.setattr(store, "_write_head_cas", real_write_head)

    recovered = store.load_snapshot(path)
    assert recovered["state"] == second
    assert recovered["tail_event_count"] == 1
    assert recovered["recovered_tail"] is True

    store.append_state_commit(path, third, source="test")
    settled = store.load_snapshot(path)
    assert settled["state"] == third
    assert settled["tail_event_count"] == 0
    assert store.verify_full_chain(path)["event_count"] == 3


def test_corrupted_or_incomplete_tail_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store.append_state_commit(path, board(("TAIL-001", "todo")), source="test")
    with path.open("ab") as stream:
        stream.write(b'{"broken":')

    with pytest.raises(store.TaskStateStoreError, match="corrupted task-state transition tail"):
        store.load_snapshot(path)


def test_stale_sequence_cas_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store.append_state_commit(path, board(("CAS-001", "todo")), source="test")
    store.append_state_commit(path, board(("CAS-001", "review")), source="test", expected_sequence=1)

    with pytest.raises(store.TaskStateStoreError, match="stale task-state head CAS"):
        store.append_state_commit(path, board(("CAS-001", "done")), source="test", expected_sequence=1)


def test_nonterminal_loss_still_fails_closed_before_append(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    before = board(("KEEP-001", "in_progress"), ("DROP-001", "review"))
    store.append_state_commit(path, before, source="test")
    bytes_before = path.read_bytes()

    with pytest.raises(store.TaskStateStoreError, match="nonterminal drop rejected"):
        store.append_state_commit(path, board(("KEEP-001", "in_progress")), source="test")

    assert path.read_bytes() == bytes_before
    legal = board(("KEEP-001", "in_progress"), task_state_drain=drain("DROP-001"))
    store.append_state_commit(path, legal, source="test")
    assert store.load_snapshot(path)["state"] == legal


def test_v1_input_never_becomes_an_automatic_runtime_fallback(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    write_legacy(path)
    path.with_name(f"{path.name}.lock").write_text("", encoding="utf-8")

    with pytest.raises(store.TaskStateStoreError, match="V2 head is missing"):
        store.load_snapshot(path)


def test_migration_freezes_exact_legacy_identity_and_binds_genesis(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    expected, legacy_bytes = write_legacy(path)

    result = store.migrate_legacy_journal(path)
    archive = path.with_name(f"{path.name}.v1.archive.jsonl")
    manifest_path = path.with_name(f"{path.name}.archive.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot = store.load_snapshot(path)
    genesis = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    assert result["status"] == "migrated"
    assert archive.read_bytes() == legacy_bytes
    assert stat.S_IMODE(archive.stat().st_mode) == stat.S_IRUSR
    assert manifest["byte_size"] == len(legacy_bytes)
    assert manifest["final_sequence"] == 2
    assert manifest["journal_sha256"] == hashlib.sha256(legacy_bytes).hexdigest()
    assert manifest["projected_state_sha256"] == store.sha256_json(expected)
    assert snapshot["state"] == expected
    assert genesis["archive_identity"] == snapshot["archive_identity"]
    assert store.verify_full_chain(path)["archive_verified"] is True


def test_full_verification_detects_a_tampered_archive(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    write_legacy(path)
    store.migrate_legacy_journal(path)
    archive = path.with_name(f"{path.name}.v1.archive.jsonl")
    archive.chmod(stat.S_IRUSR | stat.S_IWUSR)
    archive.write_bytes(archive.read_bytes().replace(b"review", b"rogue!", 1))
    archive.chmod(stat.S_IRUSR)

    # Normal reads remain constant-cost and do not inspect historical bytes.
    assert store.load_snapshot(path)["event_count"] == 1
    with pytest.raises(store.TaskStateStoreError, match="archive"):
        store.verify_full_chain(path)


def test_offline_event_reader_materializes_compatibility_state_without_persisting_it(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = board(("AUDIT-001", "todo"))
    second = board(("AUDIT-001", "review"))
    store.append_state_commit(path, first, source="test")
    store.append_state_commit(path, second, source="test")

    events = store.load_events(path)
    raw = path.read_text(encoding="utf-8")
    assert [event["state"] for event in events] == [first, second]
    assert store.project_latest_state(events) == second
    assert '"state"' not in raw


def test_transaction_advances_one_locked_head_without_replay(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store.append_state_commit(path, board(("TX-001", "todo")), source="test")

    with store.snapshot_transaction(path) as transaction:
        state = transaction.load_snapshot()["state"]
        state["tasks"][0]["status"] = "in_progress"
        first = transaction.append_state_commit(state, source="test")
        state = transaction.load_snapshot()["state"]
        state["tasks"][0]["status"] = "review"
        second = transaction.append_state_commit(state, source="test")

    assert (first["sequence"], second["sequence"]) == (2, 3)
    assert store.load_snapshot(path)["state"]["tasks"][0]["status"] == "review"


def test_relative_path_and_symlink_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(store.TaskStateStoreError, match="absolute"):
        store.append_state_commit("relative-events.jsonl", {"tasks": []}, source="test")

    real = tmp_path / "real.jsonl"
    real.write_text("", encoding="utf-8")
    linked = tmp_path / "linked.jsonl"
    linked.symlink_to(real)
    with pytest.raises(store.TaskStateStoreError, match="regular file"):
        store.append_state_commit(linked, {"tasks": []}, source="test")


def test_invalid_delta_is_caught_by_offline_full_chain_verification(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store.append_state_commit(path, board(("BAD-001", "todo")), source="test")
    store.append_state_commit(path, board(("BAD-001", "review")), source="test")
    lines = path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[1])
    event["delta"] = [{"op": "delete", "path": ["missing"]}]
    lines[1] = json.dumps(event, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(store.TaskStateStoreError, match="event digest|missing target"):
        store.verify_full_chain(path)
