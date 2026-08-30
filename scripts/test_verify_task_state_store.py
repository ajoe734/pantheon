from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_task_state_store as verifier

ORCHESTRATOR = Path(__file__).resolve().parents[1] / ".orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

from rewrite.task_state_store import (
    ARCHIVE_ANCHOR_TYPE,
    ARCHIVE_ANCHOR_VERSION,
    TaskStateStoreError,
    append_state_commit,
    sha256_json,
    write_archive_anchor,
)


def test_verifier_reports_projection_parity(tmp_path: Path, capsys) -> None:
    status = {"tasks": [{"id": "STATE-001", "status": "todo"}]}
    status_file = tmp_path / "ai-status.json"
    event_log = tmp_path / "events.jsonl"
    status_file.write_text(json.dumps(status), encoding="utf-8")
    append_state_commit(event_log, status, source="test")

    result = verifier.main(
        ["--event-log", str(event_log), "--status-file", str(status_file), "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["ok"] is True
    assert payload["event_count"] == 1


def test_verifier_returns_integrity_exit_for_projection_mismatch(
    tmp_path: Path,
    capsys,
) -> None:
    status_file = tmp_path / "ai-status.json"
    event_log = tmp_path / "events.jsonl"
    status_file.write_text('{"tasks": []}\n', encoding="utf-8")
    append_state_commit(
        event_log,
        {"tasks": [{"id": "STATE-002", "status": "todo"}]},
        source="test",
    )

    result = verifier.main(
        ["--event-log", str(event_log), "--status-file", str(status_file), "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 2
    assert payload["ok"] is False


def test_verifier_requires_configured_event_log(capsys) -> None:
    result = verifier.main(["--event-log", "", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert result == 3
    assert "not configured" in payload["error"]


def test_verifier_reports_parity_after_rejected_nonterminal_drop(
    tmp_path: Path,
    capsys,
) -> None:
    status = {
        "tasks": [
            {"id": "STATE-003", "status": "in_progress"},
            {"id": "STATE-004", "status": "todo"},
        ]
    }
    status_file = tmp_path / "ai-status.json"
    event_log = tmp_path / "events.jsonl"
    status_file.write_text(json.dumps(status), encoding="utf-8")
    append_state_commit(event_log, status, source="test")
    journal_bytes = event_log.read_bytes()

    with pytest.raises(TaskStateStoreError, match="nonterminal drop rejected"):
        append_state_commit(event_log, {"tasks": []}, source="test")

    assert event_log.read_bytes() == journal_bytes

    result = verifier.main(
        ["--event-log", str(event_log), "--status-file", str(status_file), "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["ok"] is True
    assert payload["event_count"] == 1
    assert payload["nonterminal_task_count"] == 2


def test_verifier_reports_a_drained_board_as_zero_nonterminal_tasks(
    tmp_path: Path,
    capsys,
) -> None:
    status_file = tmp_path / "ai-status.json"
    event_log = tmp_path / "events.jsonl"
    completed = {"tasks": [{"id": "STATE-005", "status": "done"}]}
    status_file.write_text(json.dumps(completed), encoding="utf-8")
    append_state_commit(event_log, {"tasks": [{"id": "STATE-005", "status": "review"}]}, source="test")
    append_state_commit(event_log, completed, source="test")

    result = verifier.main(
        ["--event-log", str(event_log), "--status-file", str(status_file), "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["ok"] is True
    assert payload["nonterminal_task_count"] == 0


def test_verifier_text_output_surfaces_the_live_task_count(
    tmp_path: Path,
    capsys,
) -> None:
    status = {"tasks": [{"id": "STATE-006", "status": "in_progress"}]}
    status_file = tmp_path / "ai-status.json"
    event_log = tmp_path / "events.jsonl"
    status_file.write_text(json.dumps(status), encoding="utf-8")
    append_state_commit(event_log, status, source="test")

    assert verifier.main(["--event-log", str(event_log), "--status-file", str(status_file)]) == 0
    assert "nonterminal_tasks=1" in capsys.readouterr().out


def test_verifier_full_replay_is_explicit_and_reports_chain_digest(
    tmp_path: Path,
    capsys,
) -> None:
    status = {"tasks": [{"id": "STATE-007", "status": "review"}]}
    status_file = tmp_path / "ai-status.json"
    event_log = tmp_path / "events.jsonl"
    status_file.write_text(json.dumps(status), encoding="utf-8")
    append_state_commit(event_log, status, source="test")

    result = verifier.main(
        [
            "--event-log", str(event_log),
            "--status-file", str(status_file),
            "--full-replay",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["full_audit"]["ok"] is True
    assert len(payload["full_audit"]["journal_sha256"]) == 64


def test_verifier_archive_audit_is_separate_from_hot_parity(
    tmp_path: Path,
    capsys,
) -> None:
    legacy = tmp_path / "legacy.jsonl"
    legacy.write_bytes(b"legacy archive bytes\n")
    status = {"tasks": [{"id": "STATE-008", "status": "todo"}]}
    status_file = tmp_path / "ai-status.json"
    event_log = tmp_path / "events.jsonl"
    status_file.write_text(json.dumps(status), encoding="utf-8")
    write_archive_anchor(
        event_log,
        {
            "version": ARCHIVE_ANCHOR_VERSION,
            "type": ARCHIVE_ANCHOR_TYPE,
            "archived_path": str(legacy),
            "byte_size": legacy.stat().st_size,
            "journal_sha256": hashlib.sha256(legacy.read_bytes()).hexdigest(),
            "event_count": 1,
            "last_event_id": "legacy-id",
            "last_event_sha256": "a" * 64,
            "state_sha256": sha256_json(status),
            "created_at": "2026-08-11T00:00:00Z",
        },
    )
    append_state_commit(event_log, status, source="migration")

    result = verifier.main(
        [
            "--event-log", str(event_log),
            "--status-file", str(status_file),
            "--verify-archive",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["archive_audit"]["ok"] is True


def test_verifier_accepts_relocated_archive_only_when_hash_matches(
    tmp_path: Path,
    capsys,
) -> None:
    original = tmp_path / "missing-original.jsonl"
    relocated = tmp_path / "relocated.jsonl"
    relocated.write_bytes(b"exact immutable archive bytes\n")
    status = {"tasks": [{"id": "STATE-009", "status": "todo"}]}
    status_file = tmp_path / "ai-status.json"
    event_log = tmp_path / "events.jsonl"
    status_file.write_text(json.dumps(status), encoding="utf-8")
    write_archive_anchor(
        event_log,
        {
            "version": ARCHIVE_ANCHOR_VERSION,
            "type": ARCHIVE_ANCHOR_TYPE,
            "archived_path": str(original),
            "byte_size": relocated.stat().st_size,
            "journal_sha256": hashlib.sha256(relocated.read_bytes()).hexdigest(),
            "event_count": 1,
            "last_event_id": "legacy-id",
            "last_event_sha256": "a" * 64,
            "state_sha256": sha256_json(status),
            "created_at": "2026-08-11T00:00:00Z",
        },
    )
    append_state_commit(event_log, status, source="migration")

    result = verifier.main(
        [
            "--event-log", str(event_log),
            "--status-file", str(status_file),
            "--verify-archive",
            "--archive-path", str(relocated),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["archive_audit"]["archived_path"] == str(original)
    assert payload["archive_audit"]["audited_path"] == str(relocated)


def test_verifier_classifies_missing_historical_archive(
    tmp_path: Path,
    capsys,
) -> None:
    missing = tmp_path / "missing-archive.jsonl"
    status = {"tasks": [{"id": "STATE-010", "status": "todo"}]}
    status_file = tmp_path / "ai-status.json"
    event_log = tmp_path / "events.jsonl"
    status_file.write_text(json.dumps(status), encoding="utf-8")
    write_archive_anchor(
        event_log,
        {
            "version": ARCHIVE_ANCHOR_VERSION,
            "type": ARCHIVE_ANCHOR_TYPE,
            "archived_path": str(missing),
            "byte_size": 1,
            "journal_sha256": hashlib.sha256(b"x").hexdigest(),
            "event_count": 1,
            "last_event_id": "legacy-id",
            "last_event_sha256": "a" * 64,
            "state_sha256": sha256_json(status),
            "created_at": "2026-08-11T00:00:00Z",
        },
    )
    append_state_commit(event_log, status, source="migration")

    result = verifier.main(
        [
            "--event-log", str(event_log),
            "--status-file", str(status_file),
            "--verify-archive",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 3
    assert payload["error_kind"] == "historical_archive_unavailable"
