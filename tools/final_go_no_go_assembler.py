#!/usr/bin/env python3
"""Assemble final Go/No-Go packet per blueprint §16 (supplement §12 acceptance matrix).

Aggregates readiness refs (EP5, BLA, CBL, HA, LSP, RES-ACT, MPO, TEL-HARD),
HumanGateDecision records, and BPC-001 completion report into a single
final_go_no_go_packet.json. Read-only; never mutates any state file.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


BLUEPRINT_SOURCE = (
    "docs/04/pantheon_design_blueprint_supplement_2026-05-19/"
    "pantheon_blueprint_supplement.md#12-final-acceptance-matrix"
)
TASK_ID = "BPC-002-V2"
BPC001_REPORT_PATH = "support/evidence/BPC-001-V2/blueprint_completion_report.json"


class AssemblerError(RuntimeError):
    """Raised when the assembler cannot produce a closed, evidence-backed packet."""


@dataclass(frozen=True)
class MatrixRow:
    id: str
    capability: str
    required_before_live: bool
    status_target: str
    # task archive refs and evidence path refs used to evaluate this row
    task_refs: tuple[str, ...] = ()
    evidence_paths: tuple[str, ...] = ()
    # optional: human gate decision type from H3 schema
    human_gate_type: str | None = None
    # optional: bpc001_condition_id to inherit from BPC-001 report
    bpc001_condition_id: str | None = None


# §12 final acceptance matrix rows, enriched with task/evidence refs
MATRIX_ROWS: tuple[MatrixRow, ...] = (
    MatrixRow(
        id="ooda_paper_loop",
        capability="OODA paper loop",
        required_before_live=True,
        status_target="closed",
        bpc001_condition_id=None,
        task_refs=(),
        evidence_paths=(),
    ),
    MatrixRow(
        id="ooda_canary_loop",
        capability="OODA canary loop",
        required_before_live=True,
        status_target="must pass",
        bpc001_condition_id="ooda_canary_loop",
        task_refs=("OODA-CANARY-005-V2",),
        evidence_paths=("support/evidence/OODA-CANARY-005-V2/closeout.md",),
    ),
    MatrixRow(
        id="ep4_governed_paper",
        capability="EP4 governed paper",
        required_before_live=True,
        status_target="stable",
        bpc001_condition_id=None,
        task_refs=(),
        evidence_paths=(),
    ),
    MatrixRow(
        id="ep5_canary_proof",
        capability="EP5 canary proof",
        required_before_live=True,
        status_target="must pass",
        bpc001_condition_id="ep5_canary_proof",
        task_refs=("EP5-010-V2",),
        evidence_paths=(),
    ),
    MatrixRow(
        id="broker_live_criteria",
        capability="Broker live criteria",
        required_before_live=True,
        status_target="approved",
        bpc001_condition_id="broker_live_criteria",
        task_refs=("BLA-010-V2",),
        evidence_paths=(),
    ),
    MatrixRow(
        id="risk_owner_signoff",
        capability="Risk-owner signoff",
        required_before_live=True,
        status_target="approved, unexpired",
        human_gate_type="broker_live_activation",
        bpc001_condition_id="human_gate_model_and_audit",
        task_refs=("HG-005-V2", "HG-006-V2"),
        evidence_paths=(),
    ),
    MatrixRow(
        id="operator_signoff",
        capability="Operator signoff",
        required_before_live=True,
        status_target="approved, unexpired",
        human_gate_type="broker_live_activation",
        bpc001_condition_id="human_gate_model_and_audit",
        task_refs=("HG-005-V2", "HG-006-V2"),
        evidence_paths=(),
    ),
    MatrixRow(
        id="capital_binding_live_readiness",
        capability="Capital binding live readiness",
        required_before_live=True,
        status_target="approved",
        bpc001_condition_id="capital_binding_readiness",
        task_refs=("CBL-007-V2",),
        evidence_paths=("support/evidence/CBL-007-V2/owner-closeout.md",),
    ),
    MatrixRow(
        id="bff_ha_production_topology",
        capability="BFF HA production topology",
        required_before_live=True,
        status_target="PoC passed + approved",
        bpc001_condition_id="bff_ha_topology",
        task_refs=("HA-010-V2",),
        evidence_paths=(),
    ),
    MatrixRow(
        id="strict_publish_final_audit",
        capability="Strict publish final audit",
        required_before_live=True,
        status_target="passed",
        bpc001_condition_id="strict_publish_final_audit",
        task_refs=("LSP-006-V2",),
        evidence_paths=("support/evidence/LSP-006-V2/publish-gate.json",),
    ),
    MatrixRow(
        id="telemetry_audit_incident",
        capability="Telemetry / audit / incident",
        required_before_live=True,
        status_target="available",
        bpc001_condition_id="telemetry_audit_incident_hardening",
        task_refs=("TEL-HARD-001-V2", "TEL-HARD-002-V2", "TEL-HARD-003-V2"),
        evidence_paths=("support/evidence/TEL-HARD-001-V2/load_report.json",),
    ),
    MatrixRow(
        id="rollback_drill",
        capability="Rollback drill",
        required_before_live=True,
        status_target="passed",
        bpc001_condition_id="rollback_and_kill_switch",
        task_refs=("EP5-007-V2",),
        evidence_paths=("support/evidence/EP5-007-V2/rollback-drill.json",),
    ),
    MatrixRow(
        id="kill_switch_demo",
        capability="Kill switch demo",
        required_before_live=True,
        status_target="passed",
        bpc001_condition_id="rollback_and_kill_switch",
        task_refs=("EP5-008-V2",),
        evidence_paths=("support/evidence/EP5-008-V2/kill-switch-demo.json",),
    ),
    MatrixRow(
        id="multi_persona_sponsor_lineage",
        capability="Multi-persona sponsor lineage",
        required_before_live=True,
        status_target="bridged",
        bpc001_condition_id="multi_persona_sponsor_lineage",
        task_refs=("MPO-004-V2",),
        evidence_paths=("support/evidence/MPO-003-V2/full_packet.json",),
    ),
    MatrixRow(
        id="research_production_activation",
        capability="Research production activation",
        required_before_live=False,
        status_target="governed",
        bpc001_condition_id="research_production_activation",
        task_refs=("RES-ACT-006-V2",),
        evidence_paths=("support/evidence/RES-ACT-006-V2/review.md",),
    ),
    MatrixRow(
        id="production_activation_gates",
        capability="Production real-writes and live-scale human signoff",
        required_before_live=True,
        status_target="approved",
        human_gate_type="production_real_writes_enable",
        bpc001_condition_id="production_activation_signoff_gates",
        task_refs=(),
        evidence_paths=(),
    ),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_bpc001_report(repo_root: Path, status_root: Path) -> dict[str, Any] | None:
    for root in (repo_root, status_root):
        path = root / BPC001_REPORT_PATH
        if path.exists():
            return load_json(path)
    return None


def find_task_archive(task_id: str, repo_root: Path, status_root: Path) -> dict[str, Any] | None:
    archive_rel = Path("ai-task-archive") / "tasks" / f"{task_id}.json"
    seen: set[Path] = set()
    for root in (repo_root, status_root):
        path = root / archive_rel
        resolved = path.resolve()
        if resolved in seen or not path.exists():
            continue
        seen.add(resolved)
        payload = load_json(path)
        task = payload.get("task") if isinstance(payload, dict) else None
        if not isinstance(task, dict):
            return None
        return task
    return None


def load_human_gate_records(repo_root: Path, status_root: Path) -> list[dict[str, Any]]:
    """Discover all human-gate JSON packets under support/evidence/."""
    records: list[dict[str, Any]] = []
    seen_resolved: set[Path] = set()
    seen_rel: set[str] = set()  # deduplicate by relative path across roots
    for root in (repo_root, status_root):
        evidence_root = root / "support" / "evidence"
        if not evidence_root.is_dir():
            continue
        for path in sorted(evidence_root.rglob("*.json")):
            resolved = path.resolve()
            if resolved in seen_resolved:
                continue
            seen_resolved.add(resolved)
            rel = _relative_or_absolute(path, root)
            if rel in seen_rel:
                continue
            seen_rel.add(rel)
            try:
                payload = load_json(path)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            # Match any JSON that looks like a HumanGateDecision by schema presence
            if payload.get("decision_type") or payload.get("decision_id") or payload.get("mode") == "human_gate_packet":
                records.append({"ref": rel, "payload": payload})
    return records


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def evaluate_matrix_row(
    row: MatrixRow,
    *,
    repo_root: Path,
    status_root: Path,
    bpc001_conditions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    # Inherit from BPC-001 if available
    bpc001_result: dict[str, Any] | None = None
    if row.bpc001_condition_id and row.bpc001_condition_id in bpc001_conditions:
        bpc001_result = bpc001_conditions[row.bpc001_condition_id]

    # Evaluate evidence paths
    evidence_path_results = [
        {"path": p, "exists": (repo_root / p).exists()}
        for p in row.evidence_paths
    ]
    missing_evidence = [r["path"] for r in evidence_path_results if not r["exists"]]

    # Evaluate task archives
    task_results = []
    for task_id in row.task_refs:
        task = find_task_archive(task_id, repo_root, status_root)
        if task is None:
            task_results.append({"task_id": task_id, "status": "missing", "source": "missing"})
        else:
            delivery = task.get("delivery") if isinstance(task.get("delivery"), dict) else {}
            task_results.append({
                "task_id": task_id,
                "status": str(task.get("status") or "unknown"),
                "source": f"ai-task-archive/tasks/{task_id}.json",
                "delivery_commit": delivery.get("commit"),
                "head_merged_to_target": bool(delivery.get("head_merged_to_target")) if delivery else task.get("status") == "done",
            })

    tasks_not_done = [r["task_id"] for r in task_results if r["status"] != "done"]

    # Determine row verdict
    if bpc001_result is not None:
        verdict = bpc001_result.get("status", "unknown")
        passed = bpc001_result.get("passed", False)
    elif row.task_refs and tasks_not_done:
        verdict = "failed"
        passed = False
    elif missing_evidence:
        verdict = "failed"
        passed = False
    else:
        verdict = "passed"
        passed = True

    return {
        "id": row.id,
        "capability": row.capability,
        "required_before_live": row.required_before_live,
        "status_target": row.status_target,
        "verdict": verdict,
        "passed": passed,
        "bpc001_condition_id": row.bpc001_condition_id,
        "bpc001_status": bpc001_result.get("status") if bpc001_result else None,
        "task_refs": task_results,
        "evidence_paths": evidence_path_results,
        "missing_evidence_paths": missing_evidence,
        "tasks_not_done": tasks_not_done,
        "human_gate_type": row.human_gate_type,
    }


def build_packet(
    *,
    repo_root: Path,
    status_root: Path,
    rows: Sequence[MatrixRow] = MATRIX_ROWS,
    generated_at: str | None = None,
) -> dict[str, Any]:
    bpc001_report = load_bpc001_report(repo_root, status_root)
    bpc001_conditions: dict[str, dict[str, Any]] = {}
    if bpc001_report and isinstance(bpc001_report.get("conditions"), list):
        bpc001_conditions = {
            c["id"]: c
            for c in bpc001_report["conditions"]
            if isinstance(c, dict) and c.get("id")
        }

    matrix = [
        evaluate_matrix_row(
            row,
            repo_root=repo_root,
            status_root=status_root,
            bpc001_conditions=bpc001_conditions,
        )
        for row in rows
    ]

    human_gate_records = load_human_gate_records(repo_root, status_root)

    required_rows = [r for r in matrix if r["required_before_live"]]
    all_required_passed = all(r["passed"] for r in required_rows)
    pending_human_signoff = [r["id"] for r in matrix if r["verdict"] == "pending_human_signoff"]
    failed_rows = [r["id"] for r in matrix if r["verdict"] == "failed"]

    if pending_human_signoff:
        overall_verdict = "pending_human_signoff"
    elif failed_rows:
        overall_verdict = "no_go"
    elif all_required_passed:
        overall_verdict = "go"
    else:
        overall_verdict = "incomplete"

    return {
        "task_id": TASK_ID,
        "generated_at": generated_at or utc_now(),
        "blueprint_source": BLUEPRINT_SOURCE,
        "bpc001_report_ref": BPC001_REPORT_PATH,
        "bpc001_overall_passed": bpc001_report.get("overall_passed") if bpc001_report else None,
        "overall_verdict": overall_verdict,
        "go": overall_verdict == "go",
        "matrix": matrix,
        "human_gate_records": [r["ref"] for r in human_gate_records],
        "human_gate_record_count": len(human_gate_records),
        "summary": {
            "total": len(matrix),
            "passed": sum(1 for r in matrix if r["passed"]),
            "failed": len(failed_rows),
            "pending_human_signoff": len(pending_human_signoff),
        },
        "pending_human_signoff_rows": pending_human_signoff,
        "failed_rows": failed_rows,
    }


def write_packet(packet: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Pantheon repo root.")
    parser.add_argument(
        "--status-root",
        default=os.environ.get("PANTHEON_STATUS_ROOT") or ".",
        help="Root containing ai-status.json and ai-task-archive/.",
    )
    parser.add_argument(
        "--output",
        default="support/evidence/BPC-002-V2/final_go_no_go_packet.json",
        help="Output JSON path.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when verdict is not go.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print the generated packet to stdout.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    status_root = Path(args.status_root).resolve()
    packet = build_packet(repo_root=repo_root, status_root=status_root)
    write_packet(packet, Path(args.output))
    if args.print:
        print(json.dumps(packet, indent=2, sort_keys=True))
    if args.strict and not packet["go"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
