from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest

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


def test_cli_resume_after_archive_rename_restores_readonly_permissions(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.jsonl"
    write_legacy(path)
    archive = path.with_name(f"{path.name}.v1.archive.jsonl")
    real_chmod = store.os.chmod

    def crash_before_chmod(candidate: str | Path, mode: int) -> None:
        if Path(candidate) == archive and mode == stat.S_IRUSR:
            raise OSError("simulated crash after archive rename")
        real_chmod(candidate, mode)

    monkeypatch.setattr(store.os, "chmod", crash_before_chmod)
    assert migration.main(["--event-log", str(path), "--json"]) == 3
    assert "simulated crash" in json.loads(capsys.readouterr().out)["error"]
    assert archive.exists()
    assert stat.S_IMODE(archive.stat().st_mode) != stat.S_IRUSR

    monkeypatch.setattr(store.os, "chmod", real_chmod)
    assert migration.main(["--event-log", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "migrated"
    assert stat.S_IMODE(archive.stat().st_mode) == stat.S_IRUSR


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("byte_size", lambda value: int(value) + 1),
        ("final_sequence", lambda value: int(value) + 1),
        ("journal_sha256", lambda value: "0" * 64 if value != "0" * 64 else "f" * 64),
        (
            "projected_state_sha256",
            lambda value: "0" * 64 if value != "0" * 64 else "f" * 64,
        ),
    ],
)
def test_cli_rejects_resumed_archive_manifest_identity_mismatch(
    tmp_path: Path,
    capsys,
    field: str,
    replacement,
) -> None:
    path = tmp_path / "events.jsonl"
    expected = write_legacy(path)
    archive = path.with_name(f"{path.name}.v1.archive.jsonl")
    path.replace(archive)
    archive.chmod(stat.S_IRUSR)
    state, final_sequence, byte_size, journal_sha256 = store._read_legacy_journal(archive)
    assert state == expected
    manifest = store._make_archive_manifest(
        path,
        state=state,
        final_sequence=final_sequence,
        byte_size=byte_size,
        journal_sha256=journal_sha256,
    )
    manifest[field] = replacement(manifest[field])
    manifest["manifest_sha256"] = store.sha256_json(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    path.with_name(f"{path.name}.archive.json").write_bytes(
        store.canonical_json_bytes(manifest) + b"\n"
    )

    assert migration.main(["--event-log", str(path), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert field in payload["error"]
    assert "manifest does not match frozen archive" in payload["error"]
    assert not path.exists()
    assert not path.with_name(f"{path.name}.head.json").exists()
