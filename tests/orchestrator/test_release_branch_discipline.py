from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "release_branch_discipline.py"

spec = importlib.util.spec_from_file_location("release_branch_discipline", MODULE_PATH)
release_branch_discipline = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(release_branch_discipline)


def _event(ts: str, event: str, wave_id: str) -> dict:
    payload = {"ts": ts, "event": event, "wave_id": wave_id, "actor": "Claude"}
    if event == "open":
        payload["branch"] = f"wave/{wave_id}"
    return payload


def _done_archive(path: Path, task_id: str, archived_at: str, delivery: dict | None = None) -> None:
    task = {
        "id": task_id,
        "status": "done",
        "delivery": delivery
        if delivery is not None
        else {
            "commit": "a" * 40,
            "commit_metadata": {
                "LLM-Agent": "Codex",
                "Task-ID": task_id,
                "Reviewer": "Claude",
            },
        },
    }
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "task_id": task_id,
                "archived_at": archived_at,
                "terminal_status": "done",
                "terminal_outcome": "completed",
                "task": task,
                "handoffs": [],
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )


def _state_with_wave_history(history: list[dict], current_wave_id: str = "2026-W25") -> dict:
    return {
        "wave_state": {
            "current_wave_id": current_wave_id,
            "status": "closed",
            "history": history,
        },
        "tasks": [],
    }


def test_wave_maps_to_publish_version_branch_and_tag(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    _done_archive(archive_dir / "OPS-WAVE-001-V2.json", "OPS-WAVE-001-V2", "2026-05-22T16:30:00Z")
    state = _state_with_wave_history(
        [
            _event("2026-05-15T09:00:00Z", "open", "2026-W24"),
            _event("2026-05-15T16:00:00Z", "freeze", "2026-W24"),
            _event("2026-05-15T17:00:00Z", "close", "2026-W24"),
            _event("2026-05-22T09:00:00Z", "open", "2026-W25"),
            _event("2026-05-22T16:00:00Z", "freeze", "2026-W25"),
            _event("2026-05-22T17:00:00Z", "close", "2026-W25"),
        ]
    )

    report = release_branch_discipline.build_release_discipline_report(
        state,
        config={"branch_workflow": {"publish_branch_prefix": "publish/", "release_tag_prefix": "release/"}},
        archive_dir=archive_dir,
    )

    assert report["ok"] is True
    assert report["version"] == "v2026.25.0"
    assert report["publish_branch"] == "publish/v2026.25.0"
    assert report["release_tag"] == "release/v2026.25.0"
    assert report["release_candidate_branch"] == "release-candidate/v2026.25.0"


def test_missing_wave_before_target_publish_is_rejected() -> None:
    state = _state_with_wave_history(
        [
            _event("2026-05-17T05:23:05Z", "open", "2026-W21"),
            _event("2026-05-17T05:30:00Z", "freeze", "2026-W21"),
            _event("2026-05-17T05:42:47Z", "close", "2026-W21"),
            _event("2026-05-17T05:46:59Z", "open", "2026-W22"),
            _event("2026-05-17T05:47:20Z", "freeze", "2026-W22"),
            _event("2026-05-17T05:47:39Z", "close", "2026-W22"),
            _event("2026-05-17T07:02:39Z", "open", "2026-W25"),
            _event("2026-05-17T07:32:39Z", "freeze", "2026-W25"),
            _event("2026-05-17T08:02:39Z", "close", "2026-W25"),
        ]
    )

    report = release_branch_discipline.build_release_discipline_report(state, archive_dir=None)

    assert report["ok"] is False
    assert any("2026-W23, 2026-W24" in error for error in report["errors"])


def test_publish_requires_freeze_before_close() -> None:
    state = _state_with_wave_history(
        [
            _event("2026-05-15T09:00:00Z", "open", "2026-W24"),
            _event("2026-05-15T16:00:00Z", "freeze", "2026-W24"),
            _event("2026-05-15T17:00:00Z", "close", "2026-W24"),
            _event("2026-05-22T09:00:00Z", "open", "2026-W25"),
            _event("2026-05-22T17:00:00Z", "close", "2026-W25"),
        ]
    )

    report = release_branch_discipline.build_release_discipline_report(state, archive_dir=None)

    assert report["ok"] is False
    assert any("must be frozen before publish" in error for error in report["errors"])


def test_unfinished_task_blocks_publish() -> None:
    state = _state_with_wave_history(
        [
            _event("2026-05-22T09:00:00Z", "open", "2026-W25"),
            _event("2026-05-22T16:00:00Z", "freeze", "2026-W25"),
            _event("2026-05-22T17:00:00Z", "close", "2026-W25"),
        ]
    )
    state["tasks"] = [{"id": "OPS-WAVE-004-V2", "status": "review_approved"}]

    report = release_branch_discipline.build_release_discipline_report(state, archive_dir=None)

    assert report["ok"] is False
    assert "task OPS-WAVE-004-V2 remains review_approved before publish" in report["errors"]


def test_non_dispatchable_human_gate_placeholder_is_reconciled() -> None:
    state = _state_with_wave_history(
        [
            _event("2026-05-22T09:00:00Z", "open", "2026-W25"),
            _event("2026-05-22T16:00:00Z", "freeze", "2026-W25"),
            _event("2026-05-22T17:00:00Z", "close", "2026-W25"),
        ]
    )
    state["tasks"] = [
        {
            "id": "BLA-LIVE-001-V2",
            "status": "blocked",
            "non_dispatchable": True,
            "gate_status": "pending_human_go_no_go",
            "allowed_workers": [],
        }
    ]

    report = release_branch_discipline.build_release_discipline_report(state, archive_dir=None)

    assert report["ok"] is True


def test_archived_done_task_requires_closeout_delivery_metadata(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    _done_archive(
        archive_dir / "OPS-WAVE-004-V2.json",
        "OPS-WAVE-004-V2",
        "2026-05-22T16:30:00Z",
        delivery={},
    )
    state = _state_with_wave_history(
        [
            _event("2026-05-22T09:00:00Z", "open", "2026-W25"),
            _event("2026-05-22T16:00:00Z", "freeze", "2026-W25"),
            _event("2026-05-22T17:00:00Z", "close", "2026-W25"),
        ]
    )

    report = release_branch_discipline.build_release_discipline_report(state, archive_dir=archive_dir)

    assert report["ok"] is False
    assert "task OPS-WAVE-004-V2 delivery metadata is missing commit" in report["errors"]
    assert "task OPS-WAVE-004-V2 delivery metadata is missing commit trailers" in report["errors"]


def test_per_task_release_state_supersedes_legacy_wave_state() -> None:
    state = _state_with_wave_history(
        [
            _event("2026-05-17T05:23:05Z", "open", "2026-W21"),
            _event("2026-05-17T05:42:47Z", "close", "2026-W21"),
            _event("2026-05-17T05:46:59Z", "open", "2026-W22"),
            _event("2026-05-17T05:47:39Z", "close", "2026-W22"),
            _event("2026-05-17T07:02:39Z", "open", "2026-W25"),
        ]
    )
    state["tasks"] = [
        {"id": "ASST-CTRL-001", "status": "todo"},
        {"id": "ASST-RUNTIME-001", "status": "todo"},
    ]

    report = release_branch_discipline.build_release_discipline_report(
        state,
        config={
            "branch_workflow": {
                "publish_branch_prefix": "publish/",
                "release_tag_prefix": "release/",
                "nightly_publish": {"version_format": "vYYYY.WW.0"},
            }
        },
        release_state={"mode": "per_task", "version_format": "vYYYY.MM.DD.N"},
        now=datetime(2026, 6, 9, 4, 0, tzinfo=timezone.utc),
        existing_versions=["v2026.06.09.0", "v2026.06.08.0", "v2026.25.0"],
    )

    assert report["ok"] is True
    assert report["release_state_mode"] == "per_task"
    assert report["version"] == "v2026.06.09.1"
    assert report["publish_branch"] == "publish/v2026.06.09.1"
    assert report["release_tag"] == "release/v2026.06.09.1"
    assert report["checks"]["legacy_wave_state"]["skipped"] is True
    assert report["checks"]["task_board_closeout"]["skipped"] is True
    assert not any("2026-W23" in error for error in report["errors"])
    assert not any("ASST-CTRL-001" in error for error in report["errors"])


def test_next_daily_publish_version_starts_at_zero() -> None:
    version = release_branch_discipline.next_daily_publish_version(
        now=datetime(2026, 6, 9, 4, 0, tzinfo=timezone.utc),
        existing_versions=["v2026.06.08.0", "v2026.25.0"],
    )

    assert version == "v2026.06.09.0"


def test_publish_version_round_trip() -> None:
    assert release_branch_discipline.wave_to_publish_version("2026-W5") == "v2026.05.0"
    assert release_branch_discipline.publish_version_to_wave_id("v2026.05.0") == "2026-W05"
