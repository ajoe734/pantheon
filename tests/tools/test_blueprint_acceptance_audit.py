from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "blueprint_acceptance_audit.py"
SPEC = importlib.util.spec_from_file_location("blueprint_acceptance_audit", MODULE_PATH)
assert SPEC is not None
audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def test_happy_path_reports_12_passed_conditions(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    repo_root.mkdir()
    status_root.mkdir()
    _write_status(status_root, active_tasks=[])
    _write_archives(repo_root, _all_required_non_gate_tasks())
    _write_archives(status_root, ())
    _write_evidence_paths(repo_root)
    _write_archives(
        status_root,
        ("PROD-WRITES-001-V2", "LIVE-SCALE-001-V2"),
    )

    report = audit.build_report(repo_root=repo_root, status_root=status_root, generated_at="2026-05-21T00:00:00Z")

    assert report["condition_count"] == 12
    assert report["overall_passed"] is True
    assert report["summary"] == {"failed": 0, "passed": 12, "pending_human_signoff": 0}
    assert all(condition["evidence_ref"] for condition in report["conditions"])


def test_pending_human_gates_do_not_pass_or_lose_evidence_ref(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    repo_root.mkdir()
    status_root.mkdir()
    _write_status(
        status_root,
        active_tasks=[
            {"id": "PROD-WRITES-001-V2", "status": "blocked"},
            {"id": "LIVE-SCALE-001-V2", "status": "blocked"},
        ],
    )
    _write_archives(repo_root, _all_required_non_gate_tasks())
    _write_evidence_paths(repo_root)

    report = audit.build_report(repo_root=repo_root, status_root=status_root, generated_at="2026-05-21T00:00:00Z")
    gate_condition = _condition(report, "production_activation_signoff_gates")

    assert report["overall_passed"] is False
    assert gate_condition["passed"] is False
    assert gate_condition["status"] == "pending_human_signoff"
    assert gate_condition["evidence_ref"] == (
        "ai-status.json#tasks.PROD-WRITES-001-V2; ai-status.json#tasks.LIVE-SCALE-001-V2"
    )
    assert gate_condition["pending_human_signoff"] == ["PROD-WRITES-001-V2", "LIVE-SCALE-001-V2"]


def test_missing_required_task_fails_closed_with_evidence_ref(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    repo_root.mkdir()
    status_root.mkdir()
    _write_status(status_root, active_tasks=[])
    required = tuple(task for task in _all_required_non_gate_tasks() if task != "EP5-010-V2")
    _write_archives(repo_root, required)
    _write_archives(status_root, ("PROD-WRITES-001-V2", "LIVE-SCALE-001-V2"))
    _write_evidence_paths(repo_root)

    report = audit.build_report(repo_root=repo_root, status_root=status_root, generated_at="2026-05-21T00:00:00Z")
    condition = _condition(report, "ep5_canary_proof")

    assert report["overall_passed"] is False
    assert condition["status"] == "failed"
    assert condition["evidence_ref"] == "ai-task-archive/tasks/EP5-010-V2.json"
    assert condition["missing_required_tasks"] == ["EP5-010-V2"]


def test_condition_without_evidence_ref_is_rejected(tmp_path: Path) -> None:
    broken = replace(audit.CONDITIONS[0], evidence_ref="")
    conditions = (broken, *audit.CONDITIONS[1:])

    with pytest.raises(audit.AuditError, match="lacks evidence_ref"):
        audit.build_report(repo_root=tmp_path, status_root=tmp_path, conditions=conditions)


def test_main_writes_report_without_mutating_status_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    output = tmp_path / "report.json"
    repo_root.mkdir()
    status_root.mkdir()
    _write_status(status_root, active_tasks=[])
    _write_archives(repo_root, _all_required_non_gate_tasks())
    _write_archives(status_root, ("PROD-WRITES-001-V2", "LIVE-SCALE-001-V2"))
    _write_evidence_paths(repo_root)
    before = (status_root / "ai-status.json").read_text(encoding="utf-8")

    exit_code = audit.main(
        [
            "--repo-root",
            str(repo_root),
            "--status-root",
            str(status_root),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert output.exists()
    assert (status_root / "ai-status.json").read_text(encoding="utf-8") == before


def _condition(report: dict, condition_id: str) -> dict:
    return next(condition for condition in report["conditions"] if condition["id"] == condition_id)


def _all_required_non_gate_tasks() -> tuple[str, ...]:
    tasks: list[str] = []
    for condition in audit.CONDITIONS:
        tasks.extend(condition.required_tasks)
    return tuple(dict.fromkeys(tasks))


def _write_status(status_root: Path, *, active_tasks: list[dict]) -> None:
    (status_root / "ai-status.json").write_text(
        json.dumps({"tasks": active_tasks}, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_archives(root: Path, task_ids: tuple[str, ...]) -> None:
    archive_dir = root / "ai-task-archive" / "tasks"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for task_id in task_ids:
        payload = {
            "version": 1,
            "task_id": task_id,
            "terminal_status": "done",
            "terminal_outcome": "completed",
            "task": {
                "id": task_id,
                "status": "done",
                "delivery": {
                    "commit": f"{task_id.lower()}-commit",
                    "head_merged_to_target": True,
                },
            },
        }
        (archive_dir / f"{task_id}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_evidence_paths(repo_root: Path) -> None:
    for condition in audit.CONDITIONS:
        for path in condition.evidence_paths:
            evidence_path = repo_root / path
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text("{}\n" if path.endswith(".json") else "evidence\n", encoding="utf-8")
