from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_task_state_store as verifier

ORCHESTRATOR = Path(__file__).resolve().parents[1] / ".orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

from rewrite import task_state_store as store
from rewrite.task_state_store import TaskStateStoreError, append_state_commit


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


def test_verifier_accepts_a_valid_durable_post_head_tail(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_file = tmp_path / "ai-status.json"
    event_log = tmp_path / "events.jsonl"
    first = {"tasks": [{"id": "STATE-007", "status": "todo"}]}
    second = {"tasks": [{"id": "STATE-007", "status": "review"}]}
    append_state_commit(event_log, first, source="test")
    real_write_head = store._write_head_cas
    monkeypatch.setattr(
        store,
        "_write_head_cas",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("simulated head replace crash")
        ),
    )
    with pytest.raises(OSError, match="simulated head replace crash"):
        append_state_commit(event_log, second, source="test")
    monkeypatch.setattr(store, "_write_head_cas", real_write_head)
    status_file.write_text(json.dumps(second), encoding="utf-8")

    assert verifier.main(
        ["--event-log", str(event_log), "--status-file", str(status_file), "--json"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["full_chain"]["event_count"] == 2
    assert payload["full_chain"]["head_sequence"] == 1
    assert payload["full_chain"]["tail_event_count"] == 1
