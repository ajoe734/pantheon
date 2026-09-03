from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seed_task_state_v2 as genesis

ORCHESTRATOR = Path(__file__).resolve().parents[1] / ".orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

from rewrite.task_state_store import append_state_commit, load_snapshot  # noqa: E402


def test_empty_journal_accepts_one_genesis_event(tmp_path: Path, capsys) -> None:
    event_log = tmp_path / "events.jsonl"

    result = genesis.main(
        ["--event-log", str(event_log), "--source", "operator-rebuild-test", "--json"]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "seeded"
    assert payload["state"] == {}
    assert payload["sequence"] == 1

    snapshot = load_snapshot(event_log)
    assert snapshot["event_count"] == 1
    assert snapshot["state"] == {}


def test_missing_journal_file_counts_as_empty(tmp_path: Path) -> None:
    event_log = tmp_path / "does-not-exist" / "events.jsonl"

    result = genesis.main(["--event-log", str(event_log), "--source", "operator-rebuild"])

    assert result == 0
    assert load_snapshot(event_log)["event_count"] == 1


def test_non_empty_journal_refuses_genesis(tmp_path: Path) -> None:
    event_log = tmp_path / "events.jsonl"
    append_state_commit(event_log, {"tasks": [{"id": "T-1", "status": "todo"}]}, source="prior")

    with pytest.raises(SystemExit, match="already holds 1 event"):
        genesis.main(["--event-log", str(event_log), "--source", "operator-rebuild"])

    # Refusal must not mutate the journal.
    assert load_snapshot(event_log)["event_count"] == 1


def test_partial_content_refuses_genesis(tmp_path: Path) -> None:
    event_log = tmp_path / "events.jsonl"
    event_log.parent.mkdir(parents=True, exist_ok=True)
    event_log.write_bytes(b"not a valid ndjson event line\n")

    with pytest.raises(SystemExit, match="refusing genesis on ambiguous content"):
        genesis.main(["--event-log", str(event_log), "--source", "operator-rebuild"])


def test_invalid_journal_refuses_genesis(tmp_path: Path) -> None:
    # No trailing newline: an unterminated/torn write, not a complete event.
    event_log = tmp_path / "events.jsonl"
    event_log.write_bytes(b"\x00\x01garbage-binary-not-json\xff")

    with pytest.raises(SystemExit, match="unparsed trailing byte"):
        genesis.main(["--event-log", str(event_log), "--source", "operator-rebuild"])

    assert not any(event_log.parent.glob("*.head"))


def test_dry_run_has_no_writes(tmp_path: Path) -> None:
    event_log = tmp_path / "events.jsonl"

    result = genesis.main(
        ["--event-log", str(event_log), "--source", "operator-rebuild", "--dry-run"]
    )

    assert result == 0
    assert not event_log.exists()


def test_source_is_required_and_non_empty(tmp_path: Path) -> None:
    event_log = tmp_path / "events.jsonl"

    with pytest.raises(SystemExit):
        genesis.main(["--event-log", str(event_log), "--source", "   "])

    assert not event_log.exists()


def test_no_projection_import_flag_exists() -> None:
    """The genesis tool must have no flag that can import ai-status.json or any
    other derived projection into the seeded state (SD.md 6.3 / SA.md invariant 9)."""
    parser_help = genesis.parse_args.__doc__ or ""
    args = genesis.parse_args(["--event-log", "/tmp/x", "--source", "s"])
    assert not hasattr(args, "status_file")
    assert not hasattr(args, "live_config") or args.live_config is None
    assert genesis.GENESIS_STATE == {}


def test_resolve_event_log_from_live_config_does_not_read_status_file(
    tmp_path: Path,
) -> None:
    event_log = tmp_path / "events.jsonl"
    status_file = tmp_path / "ai-status.json"
    status_file.write_text(
        json.dumps({"tasks": [{"id": "STALE-1", "status": "done"}]}), encoding="utf-8"
    )
    live_config = tmp_path / "live-config.json"
    live_config.write_text(
        json.dumps(
            {
                "task_state_store": {"event_log": str(event_log)},
                "paths": {"status_file": str(status_file)},
            }
        ),
        encoding="utf-8",
    )

    result = genesis.main(
        ["--live-config", str(live_config), "--source", "operator-rebuild", "--json"]
    )

    assert result == 0
    snapshot = load_snapshot(event_log)
    assert snapshot["state"] == {}
