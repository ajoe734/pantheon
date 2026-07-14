#!/usr/bin/env python3
"""Completion guardrail checker for loop-autopilot tasks.

Reads ai-status.json and reports which loop-autopilot tasks have closure
evidence gaps that would cause the 'done' transition to be rejected.

Exit codes:
  0 — all scanned tasks have sufficient evidence (or there are none)
  1 — one or more tasks have evidence gaps

Usage:
    python3 scripts/loop_done_guardrail.py [--task-id TASK_ID] [--status-file PATH]

Examples:
    # Check every loop-autopilot task in ai-status.json
    python3 scripts/loop_done_guardrail.py

    # Check a specific task
    python3 scripts/loop_done_guardrail.py --task-id LOOP-AUTO-002

    # Check against a non-default status file
    python3 scripts/loop_done_guardrail.py --status-file /path/to/ai-status.json
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS_FILE = ROOT / "ai-status.json"

# Canonical non-goals that trigger loop guardrail checks.
LOOP_AUTOPILOT_NON_GOALS = {
    "No panel-only closure",
    "No seed fixture as live proof",
    "No approval gate bypass",
}

# Case-insensitive substrings that indicate a panel/fixture/route-only claim.
_FIXTURE_ONLY_SIGNALS = (
    "fixture only",
    "fixture-only",
    "fixture_only",
    "seed only",
    "seed-only",
    "seed_only",
    "seed fixture as proof",
    "seed fixture as live",
    "fixture as live proof",
    "panel only",
    "panel-only",
    "panel_only",
    "panel copy",
    "route only",
    "route-only",
    "route_only",
)


def is_loop_autopilot_task(task: dict[str, Any]) -> bool:
    """Return True when the task carries loop-autopilot guardrail requirements."""
    if task.get("loop_ids"):
        return True
    non_goals: list[str] = task.get("non_goals") or []
    return bool(set(non_goals) & LOOP_AUTOPILOT_NON_GOALS)


def check_task(task: dict[str, Any]) -> list[str]:
    """Return a list of evidence gap descriptions for the task.

    An empty list means the task passes all guardrail checks.
    """
    if not is_loop_autopilot_task(task):
        return []

    gaps: list[str] = []
    non_goals: set[str] = set(task.get("non_goals") or [])
    proof_required: list[str] = task.get("proof_required") or []
    review_file_path = str(task.get("review_file") or "").strip()

    # Gap 1: panel-only closure prohibited but no review_file.
    if "No panel-only closure" in non_goals and not review_file_path:
        gaps.append(
            "non_goal 'No panel-only closure' requires a review_file with controller "
            "liveness evidence (set REVIEW_FILE=<evidence-path> during approve)"
        )

    # Gap 2: fixture/seed signals in review notes.
    if "No seed fixture as live proof" in non_goals:
        review_notes: list[str] = task.get("review_notes_zh") or []
        if isinstance(review_notes, str):
            review_notes = [review_notes]
        combined = " ".join(str(n) for n in review_notes).lower()
        flagged = next((sig for sig in _FIXTURE_ONLY_SIGNALS if sig in combined), None)
        if flagged:
            gaps.append(
                f"review notes contain '{flagged}' which violates "
                "'No seed fixture as live proof'"
            )

    # Gap 3: proof_required listed but no review_file to link evidence.
    if proof_required and not review_file_path:
        sample = ", ".join(f'"{p}"' for p in proof_required[:2])
        suffix = " ..." if len(proof_required) > 2 else ""
        gaps.append(
            f"proof_required ({sample}{suffix}) but no review_file was recorded — "
            "reviewer must set REVIEW_FILE=<evidence-path> during approve"
        )

    # Deep product evidence manifest checks if review_file is provided and is evidence.json.
    if review_file_path and review_file_path.endswith("evidence.json"):
        evidence_file = ROOT / review_file_path
        if not evidence_file.exists():
            gaps.append(f"review_file does not exist: {review_file_path}")
            return gaps

        try:
            with open(evidence_file, encoding="utf-8") as fh:
                evidence_data = json.load(fh)
        except Exception as e:
            gaps.append(f"failed to parse review_file JSON: {e}")
            return gaps

        # 1. JSON Schema validation
        schema_path = ROOT / "schemas/product-evidence.schema.json"
        if schema_path.exists():
            try:
                import jsonschema
            except ImportError:
                gaps.append("jsonschema library is not installed on this host (ImportError)")
                return gaps

            try:
                with open(schema_path, encoding="utf-8") as sfh:
                    schema_data = json.load(sfh)
                jsonschema.validate(instance=evidence_data, schema=schema_data)
            except Exception as e:
                gaps.append(f"product evidence schema validation failed: {e}")
        else:
            gaps.append("missing product evidence schema file at schemas/product-evidence.schema.json")

        # 2. Task consistency checks
        ev_task = evidence_data.get("task", {})
        if ev_task.get("id") != task.get("id"):
            gaps.append(f"evidence manifest task ID mismatch: expected {task.get('id')}, got {ev_task.get('id')}")
        if task.get("owner") and ev_task.get("owner") != task.get("owner"):
            gaps.append(f"evidence manifest owner mismatch: expected {task.get('owner')}, got {ev_task.get('owner')}")
        if task.get("reviewer") and ev_task.get("reviewer") != task.get("reviewer"):
            gaps.append(f"evidence manifest reviewer mismatch: expected {task.get('reviewer')}, got {ev_task.get('reviewer')}")
        if ev_task.get("review_file") != review_file_path:
            gaps.append(f"evidence manifest review_file path mismatch: expected {review_file_path}, got {ev_task.get('review_file')}")

        # 3. Overall admission and acceptance checks
        overall_adm = str(ev_task.get("overall_admission") or "").lower()
        if "reject" in overall_adm or "fail" in overall_adm:
            gaps.append(f"evidence overall admission rejected or failed: {ev_task.get('overall_admission')}")

        target_maturity = str(ev_task.get("target_maturity") or "").lower()
        if target_maturity == "live" and "evidence_only" in overall_adm:
            gaps.append("unsupported maturity: 'live' target maturity cannot be accepted with 'evidence_only' admission constraint")

        acceptance_items = evidence_data.get("acceptance") or []
        for ac in acceptance_items:
            ac_id = ac.get("id", "?")
            ac_status = str(ac.get("status") or "").lower()
            if ac_status not in ("pass", "not_applicable"):
                gaps.append(f"blocking acceptance requirement ID '{ac_id}': status is '{ac.get('status')}'")

        # 4. Mock-only live claim check
        if target_maturity == "live":
            bp = evidence_data.get("behavioral_proof") or {}
            bp_texts = []
            for proof_item in bp.values():
                if isinstance(proof_item, dict) and "proof" in proof_item:
                    bp_texts.extend(proof_item["proof"])
            
            combined_bp = " ".join(str(t) for t in bp_texts).lower()
            combined_notes = " ".join(str(n) for n in (task.get("review_notes_zh") or [])).lower()
            
            bp_flagged = next((sig for sig in _FIXTURE_ONLY_SIGNALS if sig in combined_bp), None)
            notes_flagged = next((sig for sig in _FIXTURE_ONLY_SIGNALS if sig in combined_notes), None)
            
            if bp_flagged or notes_flagged:
                gaps.append(
                    f"mock-only live claim detected (flagged: '{bp_flagged or notes_flagged}') violating live maturity target"
                )

        # 5. Missing core properties checks
        # 5.1 Terminal readback check
        hosted_rb = evidence_data.get("hosted_readback") or {}
        ct_rb = hosted_rb.get("capture_time_hosted_readback")
        pre_dep = hosted_rb.get("pre_deploy")
        
        if not ct_rb and not pre_dep:
            gaps.append("missing terminal readback evidence in hosted_readback")
        else:
            for rb_sec in (ct_rb, pre_dep):
                if rb_sec and isinstance(rb_sec, dict):
                    for k, v in rb_sec.items():
                        if k.endswith("_http") and isinstance(v, int) and v >= 500:
                            gaps.append(f"terminal readback failure observed: {k} returned status {v}")

        # 5.2 Restart check
        bp = evidence_data.get("behavioral_proof") or {}
        restart_rec = bp.get("restart_and_recovery") or {}
        restart_proof = restart_rec.get("proof") or []
        if not restart_proof:
            gaps.append("missing restart and recovery proof in behavioral_proof")
        else:
            combined_restart = " ".join(str(p) for p in restart_proof).lower()
            if "mock" in combined_restart or "stub" in combined_restart:
                gaps.append("restart proof contains mock or stub claims violating restart guardrail")

        # 5.3 Hosted check for frontend tasks
        is_frontend = (
            task.get("repository") == "execute-plans" or
            any("execute-plans/" in str(f) or str(f).startswith("src/") for f in (task.get("artifacts") or [])) or
            any("execute-plans/" in str(f) or str(f).startswith("src/") for f in (evidence_data.get("scope", {}).get("implementation_changed_files") or []))
        )
        if is_frontend:
            sec_safety = evidence_data.get("security_and_safety") or {}
            hosted_fe = sec_safety.get("hosted_frontend") or {}
            fe_status = str(hosted_fe.get("status") or "").lower()
            if fe_status == "not_applicable":
                gaps.append("missing hosted evidence: frontend tasks require explicit hosted desktop/mobile proof")

        # 5.4 Security checks
        sec_safety = evidence_data.get("security_and_safety") or {}
        for sec_req in ("rbac", "tenant_isolation", "mfa", "no_live_capital", "two_person_approval"):
            req_sec = sec_safety.get(sec_req) or {}
            req_status = str(req_sec.get("status") or "").lower()
            if req_status not in ("pass", "not_applicable"):
                gaps.append(f"missing security evidence: {sec_req} status is not pass/not_applicable")

        # 5.5 Reviewer checks
        if not task.get("reviewer"):
            gaps.append("missing reviewer: task must have a reviewer assigned")
        elif task.get("reviewer") == task.get("owner"):
            gaps.append("invalid reviewer: reviewer cannot be the owner")
        
        record_log = evidence_data.get("record_log") or []
        reviewer_approved = False
        for log_item in record_log:
            log_kind = str(log_item.get("kind") or "").lower()
            log_status = str(log_item.get("status") or "").lower()
            if ("review" in log_kind or "approved" in log_kind) and log_status in ("pass", "approved"):
                reviewer_approved = True
                break
        if not reviewer_approved and task.get("status") == "done":
            gaps.append("missing reviewer verdict: no approved review verdict recorded in record_log")

        # 6. Phantom cross-repo delivery & merge-target ancestry
        impl_delivery = evidence_data.get("implementation_delivery") or {}
        pr = impl_delivery.get("pull_request") or {}
        prs = impl_delivery.get("pull_requests") or []
        
        has_pr = bool(pr.get("number") or any(item.get("number") for item in prs))
        if task.get("repository") == "execute-plans" and not has_pr:
            gaps.append("phantom cross-repo delivery rejected: missing PR records in implementation_delivery")

        # Ancestry check
        merge_sha = pr.get("merge_sha") or (prs[0].get("merge_sha") if prs else None)
        if merge_sha:
            if task.get("repository") == "pantheon" or task.get("repository") == "ajoe734/pantheon":
                try:
                    import subprocess
                    res = subprocess.run(
                        ["git", "merge-base", "--is-ancestor", merge_sha, "HEAD"],
                        cwd=str(ROOT),
                        capture_output=True
                    )
                    if res.returncode != 0:
                        gaps.append(f"merge-target ancestry validation failed: {merge_sha} is not an ancestor of HEAD")
                except Exception:
                    pass

    return gaps


def load_status(status_file: Path) -> dict[str, Any]:
    try:
        with open(status_file, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        print(f"ERROR: status file not found: {status_file}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"ERROR: cannot parse status file: {exc}", file=sys.stderr)
        sys.exit(2)


def run(status_file: Path, task_id: str | None) -> int:
    state = load_status(status_file)
    tasks: list[dict[str, Any]] = state.get("tasks") or []

    if task_id:
        matched = [t for t in tasks if t.get("id") == task_id]
        if not matched:
            print(f"ERROR: task '{task_id}' not found in {status_file}", file=sys.stderr)
            return 2
        tasks = matched

    loop_tasks = [t for t in tasks if is_loop_autopilot_task(t)]

    if not loop_tasks:
        target = f"'{task_id}'" if task_id else "any task"
        print(f"No loop-autopilot tasks matched ({target}). Nothing to check.")
        return 0

    fail_count = 0
    for task in loop_tasks:
        tid = task.get("id", "?")
        status = task.get("status", "?")
        gaps = check_task(task)
        if gaps:
            fail_count += 1
            print(f"[FAIL] {tid} (status={status})")
            for gap in gaps:
                print(f"       ✗ {gap}")
        else:
            print(f"[OK]   {tid} (status={status})")

    total = len(loop_tasks)
    ok_count = total - fail_count
    print(f"\n{ok_count}/{total} loop task(s) passed guardrail checks.")
    return 1 if fail_count else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task-id", metavar="ID", help="Check only this task ID")
    parser.add_argument(
        "--status-file",
        metavar="PATH",
        default=str(DEFAULT_STATUS_FILE),
        help=f"Path to ai-status.json (default: {DEFAULT_STATUS_FILE})",
    )
    args = parser.parse_args()

    status_path = Path(args.status_file)
    rc = run(status_path, args.task_id)
    sys.exit(rc)


if __name__ == "__main__":
    main()
