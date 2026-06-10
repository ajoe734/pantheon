#!/usr/bin/env python3
"""Read-only auditor for the 2026-05-19 blueprint completion surface."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


BLUEPRINT_SOURCE = (
    "docs/04/pantheon_design_blueprint_supplement_2026-05-19/"
    "pantheon_blueprint_supplement.md#12-final-acceptance-matrix"
)
TASK_ID = "BPC-001-V2"
DONE_STATUSES = {"done"}
PENDING_STATUSES = {"todo", "in_progress", "review", "review_approved", "blocked"}
GATE_PENDING_STATUSES = {"todo", "in_progress", "review", "review_approved", "blocked"}


class AuditError(RuntimeError):
    """Raised when the audit cannot produce a closed, evidence-backed report."""


@dataclass(frozen=True)
class ConditionSpec:
    id: str
    title: str
    blueprint_ref: str
    required_tasks: tuple[str, ...]
    evidence_ref: str
    gate_tasks: tuple[str, ...] = ()
    evidence_paths: tuple[str, ...] = ()


CONDITIONS: tuple[ConditionSpec, ...] = (
    ConditionSpec(
        id="ep5_canary_proof",
        title="EP5 canary proof closeout exists",
        blueprint_ref="2026-05-19 supplement §12: EP5 canary proof",
        required_tasks=("EP5-010-V2",),
        evidence_ref="ai-task-archive/tasks/EP5-010-V2.json",
    ),
    ConditionSpec(
        id="ooda_canary_loop",
        title="OODA canary loop closes with rollback linkage",
        blueprint_ref="2026-05-19 supplement §12: OODA canary loop",
        required_tasks=("OODA-CANARY-005-V2",),
        evidence_ref="ai-task-archive/tasks/OODA-CANARY-005-V2.json",
        evidence_paths=("support/evidence/OODA-CANARY-005-V2/closeout.md",),
    ),
    ConditionSpec(
        id="broker_live_criteria",
        title="Broker live criteria and go/no-go surface are complete",
        blueprint_ref="2026-05-19 supplement §12: Broker live criteria",
        required_tasks=("BLA-010-V2",),
        evidence_ref="ai-task-archive/tasks/BLA-010-V2.json",
    ),
    ConditionSpec(
        id="capital_binding_readiness",
        title="Capital binding live readiness and dashboard are complete",
        blueprint_ref="2026-05-19 supplement §12: Capital binding live readiness",
        required_tasks=("CBL-007-V2",),
        evidence_ref="ai-task-archive/tasks/CBL-007-V2.json",
        evidence_paths=("support/evidence/CBL-007-V2/owner-closeout.md",),
    ),
    ConditionSpec(
        id="bff_ha_topology",
        title="BFF HA production topology PoC and failover proof are complete",
        blueprint_ref="2026-05-19 supplement §12: BFF HA production topology",
        required_tasks=("HA-010-V2",),
        evidence_ref="ai-task-archive/tasks/HA-010-V2.json",
    ),
    ConditionSpec(
        id="strict_publish_final_audit",
        title="Strict publish final audit passes without silent fallback",
        blueprint_ref="2026-05-19 supplement §12: Strict publish final audit",
        required_tasks=("LSP-006-V2",),
        evidence_ref="support/evidence/LSP-006-V2/publish-gate.json",
        evidence_paths=("support/evidence/LSP-006-V2/publish-gate.json",),
    ),
    ConditionSpec(
        id="research_production_activation",
        title="Research production activation handoff is governed",
        blueprint_ref="2026-05-19 supplement §12: Research production activation",
        required_tasks=("RES-ACT-006-V2",),
        evidence_ref="ai-task-archive/tasks/RES-ACT-006-V2.json",
        evidence_paths=("support/evidence/RES-ACT-006-V2/review.md",),
    ),
    ConditionSpec(
        id="human_gate_model_and_audit",
        title="Human gate model, audit projection, and read model are complete",
        blueprint_ref="2026-05-19 supplement §2/A2 and §8: dual gate evidence",
        required_tasks=("HG-005-V2", "HG-006-V2"),
        evidence_ref="ai-task-archive/tasks/HG-005-V2.json; ai-task-archive/tasks/HG-006-V2.json",
    ),
    ConditionSpec(
        id="multi_persona_sponsor_lineage",
        title="Multi-persona sponsor lineage is bridged into EP5 proof",
        blueprint_ref="phase8 V3 blueprint residual: §17 acceptance #9",
        required_tasks=("MPO-004-V2",),
        evidence_ref="support/evidence/MPO-003-V2/full_packet.json",
        evidence_paths=("support/evidence/MPO-003-V2/full_packet.json",),
    ),
    ConditionSpec(
        id="telemetry_audit_incident_hardening",
        title="Telemetry, audit, incident, postmortem, and evolution hardening are complete",
        blueprint_ref="2026-05-19 supplement §12: Telemetry / audit / incident",
        required_tasks=("TEL-HARD-001-V2", "TEL-HARD-002-V2", "TEL-HARD-003-V2"),
        evidence_ref=(
            "support/evidence/TEL-HARD-001-V2/load_report.json; "
            "ai-task-archive/tasks/TEL-HARD-002-V2.json; "
            "ai-task-archive/tasks/TEL-HARD-003-V2.json"
        ),
        evidence_paths=("support/evidence/TEL-HARD-001-V2/load_report.json",),
    ),
    ConditionSpec(
        id="rollback_and_kill_switch",
        title="Rollback drill and kill switch demo evidence exist",
        blueprint_ref="2026-05-19 supplement §12: Rollback drill / Kill switch demo",
        required_tasks=("EP5-007-V2", "EP5-008-V2"),
        evidence_ref="support/evidence/EP5-007-V2/rollback-drill.json; support/evidence/EP5-008-V2/kill-switch-demo.json",
        evidence_paths=(
            "support/evidence/EP5-007-V2/rollback-drill.json",
            "support/evidence/EP5-008-V2/kill-switch-demo.json",
        ),
    ),
    ConditionSpec(
        id="production_activation_signoff_gates",
        title="Production real-writes and live-scale human signoff gates are resolved",
        blueprint_ref="phase8 V3 blueprint residual: §17 condition #12 human gate status",
        required_tasks=(),
        gate_tasks=("PROD-WRITES-001-V2", "LIVE-SCALE-001-V2"),
        evidence_ref="ai-status.json#tasks.PROD-WRITES-001-V2; ai-status.json#tasks.LIVE-SCALE-001-V2",
    ),
)


@dataclass(frozen=True)
class TaskLookup:
    task_id: str
    status: str
    source: str
    task: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_active_tasks(status_file: Path) -> dict[str, dict[str, Any]]:
    if not status_file.exists():
        return {}
    state = load_json(status_file)
    tasks = state.get("tasks")
    if not isinstance(tasks, list):
        raise AuditError(f"status file has no tasks list: {status_file}")
    return {str(task.get("id")): task for task in tasks if isinstance(task, dict) and task.get("id")}


def archive_candidates(root: Path, task_id: str) -> list[Path]:
    return [root / "ai-task-archive" / "tasks" / f"{task_id}.json"]


def lookup_task(task_id: str, *, repo_root: Path, status_root: Path, active_tasks: dict[str, dict[str, Any]]) -> TaskLookup | None:
    active = active_tasks.get(task_id)
    if active is not None:
        return TaskLookup(
            task_id=task_id,
            status=str(active.get("status") or "unknown"),
            source="ai-status.json",
            task=active,
        )

    seen: set[Path] = set()
    for root in (status_root, repo_root):
        for path in archive_candidates(root, task_id):
            resolved = path.resolve()
            if resolved in seen or not path.exists():
                continue
            seen.add(resolved)
            payload = load_json(path)
            task = payload.get("task") if isinstance(payload, dict) else None
            if not isinstance(task, dict):
                raise AuditError(f"archive has no task object: {path}")
            source_root = status_root if resolved.is_relative_to(status_root.resolve()) else repo_root
            return TaskLookup(
                task_id=task_id,
                status=str(task.get("status") or payload.get("terminal_status") or "unknown"),
                source=relative_or_absolute(path, source_root),
                task=task,
            )
    return None


def relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def status_root_label(status_root: Path, repo_root: Path) -> str:
    if status_root.resolve() == repo_root.resolve():
        return "."
    if os.environ.get("PANTHEON_STATUS_ROOT") and status_root.resolve() == Path(os.environ["PANTHEON_STATUS_ROOT"]).resolve():
        return "PANTHEON_STATUS_ROOT"
    return relative_or_absolute(status_root, repo_root)


def validate_condition_specs(conditions: Sequence[ConditionSpec]) -> None:
    if len(conditions) != 12:
        raise AuditError(f"expected 12 blueprint acceptance conditions, found {len(conditions)}")
    seen: set[str] = set()
    for condition in conditions:
        if not condition.id:
            raise AuditError("condition id is required")
        if condition.id in seen:
            raise AuditError(f"duplicate condition id: {condition.id}")
        seen.add(condition.id)
        if not condition.evidence_ref.strip():
            raise AuditError(f"condition {condition.id} lacks evidence_ref")
        if not condition.required_tasks and not condition.gate_tasks:
            raise AuditError(f"condition {condition.id} has no task-backed check")


def evaluate_condition(
    condition: ConditionSpec,
    *,
    repo_root: Path,
    status_root: Path,
    active_tasks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    validate_evidence_ref(condition)
    required_results = [
        task_result(task_id, repo_root=repo_root, status_root=status_root, active_tasks=active_tasks)
        for task_id in condition.required_tasks
    ]
    gate_results = [
        task_result(task_id, repo_root=repo_root, status_root=status_root, active_tasks=active_tasks)
        for task_id in condition.gate_tasks
    ]
    evidence_path_results = [
        {
            "path": path,
            "exists": (repo_root / path).exists(),
        }
        for path in condition.evidence_paths
    ]

    missing_required = [result["task_id"] for result in required_results if result["status"] != "done"]
    missing_evidence_paths = [result["path"] for result in evidence_path_results if not result["exists"]]
    pending_gates = [
        result["task_id"]
        for result in gate_results
        if result["status"] in GATE_PENDING_STATUSES or result["status"] in {"missing", "unknown"}
    ]
    failed_gates = [
        result["task_id"]
        for result in gate_results
        if result["status"] not in DONE_STATUSES | GATE_PENDING_STATUSES | {"missing", "unknown"}
    ]

    if pending_gates:
        status = "pending_human_signoff"
    elif missing_required or missing_evidence_paths or failed_gates:
        status = "failed"
    else:
        status = "passed"

    return {
        "id": condition.id,
        "title": condition.title,
        "blueprint_ref": condition.blueprint_ref,
        "passed": status == "passed",
        "status": status,
        "evidence_ref": condition.evidence_ref,
        "required_tasks": required_results,
        "gate_tasks": gate_results,
        "evidence_paths": evidence_path_results,
        "missing_required_tasks": missing_required,
        "missing_evidence_paths": missing_evidence_paths,
        "pending_human_signoff": pending_gates,
        "failed_gate_tasks": failed_gates,
    }


def validate_evidence_ref(condition: ConditionSpec) -> None:
    if not condition.evidence_ref.strip():
        raise AuditError(f"condition {condition.id} lacks evidence_ref")


def task_result(
    task_id: str,
    *,
    repo_root: Path,
    status_root: Path,
    active_tasks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    lookup = lookup_task(task_id, repo_root=repo_root, status_root=status_root, active_tasks=active_tasks)
    if lookup is None:
        return {
            "task_id": task_id,
            "status": "missing",
            "source": "missing",
            "delivery_commit": None,
            "head_merged_to_target": False,
        }
    delivery = lookup.task.get("delivery") if isinstance(lookup.task.get("delivery"), dict) else {}
    return {
        "task_id": task_id,
        "status": lookup.status,
        "source": lookup.source,
        "delivery_commit": delivery.get("commit"),
        "head_merged_to_target": bool(delivery.get("head_merged_to_target")) if delivery else lookup.status == "done",
    }


def build_report(
    *,
    repo_root: Path,
    status_root: Path,
    conditions: Sequence[ConditionSpec] = CONDITIONS,
    generated_at: str | None = None,
) -> dict[str, Any]:
    validate_condition_specs(conditions)
    status_file = status_root / "ai-status.json"
    active_tasks = load_active_tasks(status_file)
    condition_results = [
        evaluate_condition(condition, repo_root=repo_root, status_root=status_root, active_tasks=active_tasks)
        for condition in conditions
    ]
    overall_passed = all(condition["passed"] for condition in condition_results)
    return {
        "task_id": TASK_ID,
        "generated_at": generated_at or utc_now(),
        "blueprint_source": BLUEPRINT_SOURCE,
        "condition_count": len(condition_results),
        "overall_passed": overall_passed,
        "status": "passed" if overall_passed else "incomplete",
        "status_root": status_root_label(status_root, repo_root),
        "conditions": condition_results,
        "summary": {
            "passed": sum(1 for condition in condition_results if condition["passed"]),
            "failed": sum(1 for condition in condition_results if condition["status"] == "failed"),
            "pending_human_signoff": sum(
                1 for condition in condition_results if condition["status"] == "pending_human_signoff"
            ),
        },
    }


def write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Pantheon repo root to check evidence files against.")
    parser.add_argument(
        "--status-root",
        default=os.environ.get("PANTHEON_STATUS_ROOT") or ".",
        help="Root containing ai-status.json and ai-task-archive/.",
    )
    parser.add_argument(
        "--output",
        default="support/evidence/BPC-001-V2/blueprint_completion_report.json",
        help="Report JSON path to write.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any condition is failed or pending.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print the generated report to stdout.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    status_root = Path(args.status_root).resolve()
    report = build_report(repo_root=repo_root, status_root=status_root)
    write_report(report, Path(args.output))
    if args.print:
        print(json.dumps(report, indent=2, sort_keys=True))
    if args.strict and not report["overall_passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
