from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / ".orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

from rewrite import task_state_store as store


SPEC = importlib.util.spec_from_file_location("migrate_task_state_store_v2", ROOT / "scripts/migrate_task_state_store_v2.py")
assert SPEC and SPEC.loader
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


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


def write_legacy(path: Path) -> dict:
    first = {"tasks": [{"id": "MIGRATE-001", "status": "todo"}]}
    second = {"tasks": [{"id": "MIGRATE-001", "status": "review"}]}
    one = legacy_event(first, sequence=1, previous=None)
    two = legacy_event(second, sequence=2, previous=one["event_sha256"])
    path.write_bytes(b"\n".join(store.canonical_json_bytes(item) for item in (one, two)) + b"\n")
    return second


def test_cli_dry_run_does_not_mutate_the_legacy_journal(tmp_path: Path, capsys) -> None:
    path = tmp_path / "events.jsonl"
    write_legacy(path)
    before = path.read_bytes()

    assert migration.main(["--event-log", str(path), "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "planned"
    assert path.read_bytes() == before
    assert not path.with_name(f"{path.name}.head.json").exists()


def test_cli_migrates_in_place_and_is_idempotent(tmp_path: Path, capsys) -> None:
    path = tmp_path / "events.jsonl"
    expected = write_legacy(path)

    assert migration.main(["--event-log", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "migrated"
    assert store.load_snapshot(path)["state"] == expected
    assert path.with_name(f"{path.name}.v1.archive.jsonl").exists()

    assert migration.main(["--event-log", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "already_v2"
