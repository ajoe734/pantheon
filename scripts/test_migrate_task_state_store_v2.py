from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import migrate_task_state_store_v2 as migration

ORCHESTRATOR = Path(__file__).resolve().parents[1] / ".orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

from rewrite import task_state_store as store


def legacy_event(
    state: dict,
    *,
    sequence: int,
    previous_sha256: str | None,
) -> dict:
    event = {
        "version": 1,
        "type": "task_state_committed",
        "sequence": sequence,
        "committed_at": f"2026-08-11T00:00:0{sequence}Z",
        "source": "legacy-test",
        "previous_event_sha256": previous_sha256,
        "state_sha256": store.sha256_json(state),
        "state": state,
    }
    digest = store.sha256_json(event)
    event["event_sha256"] = digest
    event["event_id"] = f"task-state-{digest}"
    return event


def write_legacy(path: Path) -> tuple[list[dict], bytes]:
    first_state = {"tasks": [{"id": "T1", "status": "todo"}]}
    second_state = {"tasks": [{"id": "T1", "status": "in_progress"}]}
    first = legacy_event(first_state, sequence=1, previous_sha256=None)
    second = legacy_event(
        second_state,
        sequence=2,
        previous_sha256=first["event_sha256"],
    )
    payload = b"".join(store.canonical_json_bytes(event) + b"\n" for event in (first, second))
    path.write_bytes(payload)
    return [first, second], payload


def test_migration_anchors_v1_and_bootstraps_v2(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.jsonl"
    events, legacy_bytes = write_legacy(legacy_path)
    v2_path = tmp_path / "v2.jsonl"

    report = migration.migrate(
        legacy_event_log=legacy_path,
        event_log=v2_path,
        expected_state=events[-1]["state"],
        created_at="2026-08-11T01:00:00Z",
    )
    expected_v2_state = {
        "tasks": [{"id": "T1", "status": "in_progress", "generation": 1}]
    }

    assert report["ok"] is True
    assert report["legacy_event_count"] == 2
    assert report["legacy_journal_sha256"] == hashlib.sha256(legacy_bytes).hexdigest()
    assert store.load_snapshot(v2_path)["state"] == expected_v2_state
    v2_event = json.loads(v2_path.read_text())
    assert "state" not in v2_event
    assert v2_event["archive_anchor_sha256"] == report["archive_anchor_sha256"]
    assert store.verify_archive_anchor(v2_path)["ok"] is True

    # A crash after the genesis append but before head replacement still binds
    # the replayed event to the same immutable legacy anchor.
    store._head_path(v2_path).unlink()
    recovered = store.load_snapshot(v2_path)
    assert recovered["state"] == expected_v2_state
    assert recovered["archive_anchor_sha256"] == report["archive_anchor_sha256"]
    resumed = migration.migrate(
        legacy_event_log=legacy_path,
        event_log=v2_path,
        expected_state=events[-1]["state"],
        created_at="2026-08-11T01:00:00Z",
    )
    assert resumed["already_migrated"] is True
    assert store._head_path(v2_path).exists()


def test_migration_rejects_projection_mismatch_without_creating_v2_journal(
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "legacy.jsonl"
    write_legacy(legacy_path)
    v2_path = tmp_path / "v2.jsonl"

    with pytest.raises(store.TaskStateStoreError, match="does not match"):
        migration.migrate(
            legacy_event_log=legacy_path,
            event_log=v2_path,
            expected_state={"tasks": []},
        )

    assert not v2_path.exists()
    assert not store.archive_anchor_path(v2_path).exists()


def test_migration_converts_legacy_task_quarantine_to_structured_block(
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "legacy.jsonl"
    legacy_state = {"tasks": [{"id": "Q1", "status": "quarantined"}]}
    event = legacy_event(legacy_state, sequence=1, previous_sha256=None)
    legacy_path.write_bytes(store.canonical_json_bytes(event) + b"\n")
    v2_path = tmp_path / "v2.jsonl"

    report = migration.migrate(
        legacy_event_log=legacy_path,
        event_log=v2_path,
        expected_state=legacy_state,
        created_at="2026-08-11T01:00:00Z",
    )

    task = store.load_snapshot(v2_path)["state"]["tasks"][0]
    assert report["converted_quarantined_tasks"] == 1
    assert task["status"] == "blocked"
    assert task["resume_status"] == "in_progress"
    assert task["block_reason"]["kind"] == "legacy_task_quarantine"
    assert report["legacy_state_sha256"] != report["v2_state_sha256"]


def test_migration_rejects_tampered_legacy_chain(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.jsonl"
    write_legacy(legacy_path)
    rows = [json.loads(line) for line in legacy_path.read_text().splitlines()]
    rows[1]["previous_event_sha256"] = "0" * 64
    legacy_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    with pytest.raises(store.TaskStateStoreError, match="previous hash mismatch"):
        migration.audit_legacy_journal(legacy_path)


def test_migration_refuses_to_overwrite_existing_v2_authority(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.jsonl"
    write_legacy(legacy_path)
    v2_path = tmp_path / "v2.jsonl"
    store.append_state_commit(
        v2_path,
        {"tasks": [{"id": "EXISTING", "status": "todo"}]},
        source="test",
    )

    with pytest.raises(store.TaskStateStoreError, match="must be empty"):
        migration.migrate(legacy_event_log=legacy_path, event_log=v2_path)


def test_cli_reports_success_as_json(tmp_path: Path, capsys) -> None:
    legacy_path = tmp_path / "legacy.jsonl"
    events, _ = write_legacy(legacy_path)
    status_path = tmp_path / "ai-status.json"
    status_path.write_text(json.dumps(events[-1]["state"]))
    v2_path = tmp_path / "v2.jsonl"

    result = migration.main(
        [
            "--legacy-event-log", str(legacy_path),
            "--event-log", str(v2_path),
            "--status-file", str(status_path),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["ok"] is True
