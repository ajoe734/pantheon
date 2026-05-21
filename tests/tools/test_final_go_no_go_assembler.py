from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "final_go_no_go_assembler.py"
SPEC = importlib.util.spec_from_file_location("final_go_no_go_assembler", MODULE_PATH)
assert SPEC is not None
assembler = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = assembler
SPEC.loader.exec_module(assembler)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_bpc001_report(repo_root: Path, *, overall_passed: bool, conditions: list[dict]) -> None:
    path = repo_root / "support" / "evidence" / "BPC-001-V2" / "blueprint_completion_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "task_id": "BPC-001-V2",
            "overall_passed": overall_passed,
            "conditions": conditions,
        }, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_condition(condition_id: str, passed: bool, status: str = "passed") -> dict:
    return {
        "id": condition_id,
        "passed": passed,
        "status": status,
        "evidence_ref": f"ref-for-{condition_id}",
    }


def _all_bpc001_condition_ids() -> list[str]:
    ids = []
    for row in assembler.MATRIX_ROWS:
        if row.bpc001_condition_id and row.bpc001_condition_id not in ids:
            ids.append(row.bpc001_condition_id)
    return ids


def _write_task_archive(root: Path, task_id: str, status: str = "done") -> None:
    archive_dir = root / "ai-task-archive" / "tasks"
    archive_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "task_id": task_id,
        "terminal_status": status,
        "task": {
            "id": task_id,
            "status": status,
            "delivery": {
                "commit": f"{task_id.lower()}-commit",
                "head_merged_to_target": status == "done",
            },
        },
    }
    (archive_dir / f"{task_id}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_evidence(repo_root: Path, path: str) -> None:
    evidence_path = repo_root / path
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text("{}\n" if path.endswith(".json") else "evidence\n", encoding="utf-8")


def _all_task_refs() -> list[str]:
    tasks: list[str] = []
    for row in assembler.MATRIX_ROWS:
        for t in row.task_refs:
            if t not in tasks:
                tasks.append(t)
    return tasks


def _all_evidence_paths() -> list[str]:
    paths: list[str] = []
    for row in assembler.MATRIX_ROWS:
        for p in row.evidence_paths:
            if p not in paths:
                paths.append(p)
    return paths


def _setup_full_go_state(repo_root: Path, status_root: Path, *, pending_human: bool = False) -> None:
    conditions = [
        _make_condition(
            cid,
            passed=not pending_human or cid != "production_activation_signoff_gates",
            status="pending_human_signoff" if pending_human and cid == "production_activation_signoff_gates" else "passed",
        )
        for cid in _all_bpc001_condition_ids()
    ]
    _write_bpc001_report(repo_root, overall_passed=not pending_human, conditions=conditions)
    for task_id in _all_task_refs():
        _write_task_archive(status_root, task_id)
    for path in _all_evidence_paths():
        _write_evidence(repo_root, path)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_happy_path_verdict_is_go(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    repo_root.mkdir()
    status_root.mkdir()
    _setup_full_go_state(repo_root, status_root)

    packet = assembler.build_packet(
        repo_root=repo_root, status_root=status_root, generated_at="2026-05-21T00:00:00Z"
    )

    assert packet["go"] is True
    assert packet["overall_verdict"] == "go"
    assert packet["summary"]["failed"] == 0
    assert packet["summary"]["pending_human_signoff"] == 0
    assert packet["bpc001_report_ref"] == "support/evidence/BPC-001-V2/blueprint_completion_report.json"


def test_matrix_has_expected_row_count(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    repo_root.mkdir()
    status_root.mkdir()
    _setup_full_go_state(repo_root, status_root)

    packet = assembler.build_packet(repo_root=repo_root, status_root=status_root)

    assert packet["summary"]["total"] == len(assembler.MATRIX_ROWS)
    assert packet["summary"]["total"] >= 14


def test_pending_human_signoff_blocks_go(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    repo_root.mkdir()
    status_root.mkdir()
    _setup_full_go_state(repo_root, status_root, pending_human=True)

    packet = assembler.build_packet(
        repo_root=repo_root, status_root=status_root, generated_at="2026-05-21T00:00:00Z"
    )

    assert packet["go"] is False
    assert packet["overall_verdict"] == "pending_human_signoff"
    assert len(packet["pending_human_signoff_rows"]) >= 1


def test_failed_bpc001_condition_propagates_to_no_go(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    repo_root.mkdir()
    status_root.mkdir()
    # All conditions passed except ep5_canary_proof
    conditions = [
        _make_condition(cid, passed=cid != "ep5_canary_proof", status="failed" if cid == "ep5_canary_proof" else "passed")
        for cid in _all_bpc001_condition_ids()
    ]
    _write_bpc001_report(repo_root, overall_passed=False, conditions=conditions)
    for task_id in _all_task_refs():
        _write_task_archive(status_root, task_id)
    for path in _all_evidence_paths():
        _write_evidence(repo_root, path)

    packet = assembler.build_packet(repo_root=repo_root, status_root=status_root)

    assert packet["go"] is False
    assert packet["overall_verdict"] == "no_go"
    assert "ep5_canary_proof" in packet["failed_rows"]


def test_human_gate_records_are_discovered(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    repo_root.mkdir()
    status_root.mkdir()
    _setup_full_go_state(repo_root, status_root)
    # Plant a fake human-gate record
    gate_dir = repo_root / "support" / "evidence" / "FAKE-GATE" / "human-gate"
    gate_dir.mkdir(parents=True, exist_ok=True)
    (gate_dir / "gate.json").write_text(
        json.dumps({"decision_id": "gate-001", "decision_type": "canary_activation", "result": {"status": "pending"}}),
        encoding="utf-8",
    )

    packet = assembler.build_packet(repo_root=repo_root, status_root=status_root)

    assert packet["human_gate_record_count"] >= 1
    assert any("gate.json" in ref for ref in packet["human_gate_records"])


def test_missing_bpc001_report_does_not_raise(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    repo_root.mkdir()
    status_root.mkdir()

    packet = assembler.build_packet(repo_root=repo_root, status_root=status_root)

    assert isinstance(packet, dict)
    assert packet["bpc001_overall_passed"] is None
    assert packet["overall_verdict"] in {"go", "no_go", "pending_human_signoff", "incomplete"}


def test_main_writes_packet_and_preserves_status_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    output = tmp_path / "packet.json"
    repo_root.mkdir()
    status_root.mkdir()
    status_path = status_root / "ai-status.json"
    status_path.write_text('{"tasks": []}\n', encoding="utf-8")
    _setup_full_go_state(repo_root, status_root)
    before = status_path.read_text(encoding="utf-8")

    exit_code = assembler.main([
        "--repo-root", str(repo_root),
        "--status-root", str(status_root),
        "--output", str(output),
    ])

    assert exit_code == 0
    assert output.exists()
    assert status_path.read_text(encoding="utf-8") == before
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["task_id"] == "BPC-002-V2"


def test_strict_flag_exits_nonzero_when_not_go(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    output = tmp_path / "packet.json"
    repo_root.mkdir()
    status_root.mkdir()
    # No BPC-001 report; no task archives → rows will fail/incomplete
    exit_code = assembler.main([
        "--repo-root", str(repo_root),
        "--status-root", str(status_root),
        "--output", str(output),
        "--strict",
    ])

    assert exit_code == 1
