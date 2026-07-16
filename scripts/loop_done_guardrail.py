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
from datetime import UTC, datetime
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

_BLOCKING_ADMISSION_SIGNALS = (
    "blocked",
    "pending",
    "review_required",
    "review required",
    "review-required",
    "change_requested",
    "changes_requested",
    "change-requested",
    "changes-requested",
)

_ACCEPTED_ADMISSION_PREFIXES = (
    "pass",
    "approved",
    "accepted",
    "review_approved",
    "complete",
    "completed",
    "done",
)

_APPROVED_REVIEW_STATUSES = {"approved", "pass", "review_approved"}

_NON_VERDICT_REVIEW_KIND_SIGNALS = (
    "ready_for_review",
    "ready_for_independent_review",
    "evidence_ready",
    "owner_evidence_ready",
    "owner_closeout_gate",
)


def _as_lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _is_blocking_residual(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1", "blocking"}
    return False


def _blocking_residual_risk_ids(residual_risks: Any) -> list[str]:
    if not isinstance(residual_risks, dict):
        return []

    blocking: list[str] = []
    for risk_id, risk in residual_risks.items():
        if isinstance(risk, dict) and _is_blocking_residual(
            risk.get("blocking_for_this_task")
        ):
            blocking.append(str(risk_id))
    return blocking


def _has_positive_overall_admission(overall_admission: str) -> bool:
    normalized = overall_admission.strip().lower()
    return any(normalized.startswith(prefix) for prefix in _ACCEPTED_ADMISSION_PREFIXES)


def _is_formal_reviewer_verdict(
    log_item: Any,
    *,
    expected_reviewer: str,
    owner: str,
) -> bool:
    if not isinstance(log_item, dict):
        return False

    kind = _as_lower(log_item.get("kind"))
    status = _as_lower(log_item.get("status"))
    actor = str(log_item.get("actor") or "").strip()

    if status not in _APPROVED_REVIEW_STATUSES:
        return False
    if any(signal in kind for signal in _NON_VERDICT_REVIEW_KIND_SIGNALS):
        return False

    verdict_like = (
        "verdict" in kind
        or "approval" in kind
        or "approved" in kind
        or kind == "review"
        or kind == "implementation_review"
        or kind.startswith("independent_review")
        or kind.startswith("review_")
        or kind.endswith("_review")
    )
    if not verdict_like:
        return False

    if expected_reviewer:
        return actor == expected_reviewer
    if owner and actor:
        return actor != owner
    return bool(actor)


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
                message = getattr(e, "message", str(e).splitlines()[0])
                gaps.append(f"product evidence schema validation failed: {message}")
        else:
            gaps.append("missing product evidence schema file at schemas/product-evidence.schema.json")

        # 2. Task consistency checks
        ev_task = evidence_data.get("task", {})
        if ev_task.get("id") != task.get("id"):
            gaps.append(
                f"evidence manifest task ID mismatch: expected {task.get('id')}, "
                f"got {ev_task.get('id')}"
            )
        if task.get("owner") and ev_task.get("owner") != task.get("owner"):
            gaps.append(
                f"evidence manifest owner mismatch: expected {task.get('owner')}, "
                f"got {ev_task.get('owner')}"
            )
        if task.get("reviewer") and ev_task.get("reviewer") != task.get("reviewer"):
            gaps.append(
                f"evidence manifest reviewer mismatch: expected {task.get('reviewer')}, "
                f"got {ev_task.get('reviewer')}"
            )
        if ev_task.get("review_file") != review_file_path:
            gaps.append(
                "evidence manifest review_file path mismatch: "
                f"expected {review_file_path}, got {ev_task.get('review_file')}"
            )

        # 3. Overall admission and acceptance checks
        overall_adm = str(ev_task.get("overall_admission") or "").lower()
        if not overall_adm:
            gaps.append("missing evidence overall admission")
        elif (
            any(signal in overall_adm for signal in _BLOCKING_ADMISSION_SIGNALS)
            or "reject" in overall_adm
            or "fail" in overall_adm
        ):
            gaps.append(
                "evidence overall admission is not done-eligible: "
                f"{ev_task.get('overall_admission')}"
            )
        elif not _has_positive_overall_admission(overall_adm):
            gaps.append(
                "evidence overall admission is not an accepted closeout state: "
                f"{ev_task.get('overall_admission')}"
            )

        target_maturity = str(ev_task.get("target_maturity") or "").lower()
        if target_maturity == "live" and "evidence_only" in overall_adm:
            gaps.append(
                "unsupported maturity: 'live' target maturity cannot be accepted "
                "with 'evidence_only' admission constraint"
            )

        acceptance_items = evidence_data.get("acceptance") or []
        for ac in acceptance_items:
            ac_id = ac.get("id", "?")
            ac_status = str(ac.get("status") or "").lower()
            if ac_status not in ("pass", "not_applicable"):
                gaps.append(
                    f"blocking acceptance requirement ID '{ac_id}': "
                    f"status is '{ac.get('status')}'"
                )

        blocking_risk_ids = _blocking_residual_risk_ids(evidence_data.get("residual_risks"))
        for risk_id in blocking_risk_ids:
            gaps.append(f"blocking residual risk '{risk_id}' remains open")

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
                    "mock-only live claim detected "
                    f"(flagged: '{bp_flagged or notes_flagged}') "
                    "violating live maturity target"
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
                gaps.append(
                    "restart proof contains mock or stub claims violating restart guardrail"
                )

        # 5.3 Hosted check for frontend tasks
        is_frontend = (
            task.get("repository") == "execute-plans" or
            any(
                "execute-plans/" in str(f) or str(f).startswith("src/")
                for f in (task.get("artifacts") or [])
            ) or
            any(
                "execute-plans/" in str(f) or str(f).startswith("src/")
                for f in (
                    evidence_data.get("scope", {}).get("implementation_changed_files")
                    or []
                )
            )
        )
        if is_frontend:
            sec_safety = evidence_data.get("security_and_safety") or {}
            hosted_fe = sec_safety.get("hosted_frontend") or {}
            fe_status = str(hosted_fe.get("status") or "").lower()
            if fe_status == "not_applicable":
                gaps.append(
                    "missing hosted evidence: frontend tasks require explicit "
                    "hosted desktop/mobile proof"
                )

        # 5.4 Security checks
        sec_safety = evidence_data.get("security_and_safety") or {}
        for sec_req in (
            "rbac",
            "tenant_isolation",
            "mfa",
            "no_live_capital",
            "two_person_approval",
        ):
            req_sec = sec_safety.get(sec_req) or {}
            req_status = str(req_sec.get("status") or "").lower()
            if req_status not in ("pass", "not_applicable"):
                gaps.append(
                    f"missing security evidence: {sec_req} "
                    "status is not pass/not_applicable"
                )

        # 5.5 Reviewer checks
        if not task.get("reviewer"):
            gaps.append("missing reviewer: task must have a reviewer assigned")
        elif task.get("reviewer") == task.get("owner"):
            gaps.append("invalid reviewer: reviewer cannot be the owner")

        record_log = evidence_data.get("record_log") or []
        reviewer_approved = any(
            _is_formal_reviewer_verdict(
                log_item,
                expected_reviewer=str(
                    ev_task.get("reviewer") or task.get("reviewer") or ""
                ).strip(),
                owner=str(ev_task.get("owner") or task.get("owner") or "").strip(),
            )
            for log_item in record_log
        )
        if not reviewer_approved:
            gaps.append(
                "missing reviewer verdict: no approved formal reviewer verdict "
                "recorded in record_log"
            )

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
                        gaps.append(
                            "merge-target ancestry validation failed: "
                            f"{merge_sha} is not an ancestor of HEAD"
                        )
                except Exception:
                    pass

    return gaps


def _task_from_evidence_manifest(
    manifest_path: Path,
    evidence_data: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    ev_task = evidence_data.get("task") if isinstance(evidence_data.get("task"), dict) else {}
    task_id = str(ev_task.get("id") or "").strip()
    if not task_id:
        return None, "manifest task.id is missing"

    review_file = _display_path(manifest_path)
    task = {
        "id": task_id,
        "status": "done",
        "loop_ids": ["loop_product_level_replay"],
        "owner": ev_task.get("owner"),
        "reviewer": ev_task.get("reviewer"),
        "repository": ev_task.get("repository"),
        "review_file": review_file,
        "artifacts": (
            evidence_data.get("scope", {}).get("implementation_changed_files")
            if isinstance(evidence_data.get("scope"), dict)
            else []
        ),
    }
    return task, None


def audit_evidence_root(evidence_root: Path) -> dict[str, Any]:
    manifest_paths = sorted(evidence_root.rglob("evidence.json"))
    results: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []

    for manifest_path in manifest_paths:
        display_path = _display_path(manifest_path)
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                evidence_data = json.load(fh)
        except Exception as exc:
            excluded.append({"manifest": display_path, "reason": f"failed to parse JSON: {exc}"})
            continue

        task, excluded_reason = _task_from_evidence_manifest(manifest_path, evidence_data)
        if task is None:
            excluded.append(
                {
                    "manifest": display_path,
                    "reason": excluded_reason or "not a task evidence manifest",
                }
            )
            continue

        gaps = check_task(task)
        ev_task = evidence_data.get("task") if isinstance(evidence_data.get("task"), dict) else {}
        results.append(
            {
                "task_id": task["id"],
                "manifest": display_path,
                "owner": ev_task.get("owner"),
                "reviewer": ev_task.get("reviewer"),
                "overall_admission": ev_task.get("overall_admission"),
                "result": "pass" if not gaps else "fail",
                "gap_count": len(gaps),
                "gaps": gaps,
            }
        )

    failed = [result for result in results if result["result"] == "fail"]
    return {
        "audit_id": "closeout-truth-audit-2026-07-16",
        "generated_at": (
            datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "mode": "read_only_evidence_replay",
        "source_root": _display_path(evidence_root),
        "selection": {
            "included_manifests": len(results),
            "excluded_manifests": excluded,
            "archive_mutation": "none",
        },
        "summary": {
            "passed": len(results) - len(failed),
            "failed": len(failed),
            "scanned": len(results),
        },
        "results": results,
    }


def write_audit_json(audit: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(audit, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def write_audit_markdown(audit: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Loop Product Closeout Truth Audit",
        "",
        f"- Audit ID: `{audit['audit_id']}`",
        f"- Generated at: `{audit['generated_at']}`",
        f"- Mode: `{audit['mode']}`",
        f"- Source root: `{audit['source_root']}`",
        f"- Archive mutation: `{audit['selection']['archive_mutation']}`",
        f"- Scanned: {audit['summary']['scanned']}",
        f"- Passed: {audit['summary']['passed']}",
        f"- Failed: {audit['summary']['failed']}",
        "",
        "## Results",
        "",
        "| Task | Admission | Verdict | Gaps |",
        "|---|---|---:|---|",
    ]

    for result in audit["results"]:
        gaps = "<br>".join(result["gaps"]) if result["gaps"] else "none"
        lines.append(
            "| `{task_id}` | `{admission}` | `{verdict}` | {gaps} |".format(
                task_id=result["task_id"],
                admission=result.get("overall_admission") or "",
                verdict=result["result"],
                gaps=gaps,
            )
        )

    if audit["selection"]["excluded_manifests"]:
        lines.extend(["", "## Excluded Manifests", ""])
        for excluded in audit["selection"]["excluded_manifests"]:
            lines.append(f"- `{excluded['manifest']}`: {excluded['reason']}")

    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


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
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--task-id", metavar="ID", help="Check only this task ID")
    parser.add_argument(
        "--evidence-root",
        metavar="PATH",
        help="Replay guardrail against evidence.json manifests under PATH",
    )
    parser.add_argument(
        "--audit-json",
        metavar="PATH",
        help="Write evidence replay audit JSON to PATH",
    )
    parser.add_argument(
        "--audit-md",
        metavar="PATH",
        help="Write evidence replay audit Markdown to PATH",
    )
    parser.add_argument(
        "--status-file",
        metavar="PATH",
        default=str(DEFAULT_STATUS_FILE),
        help=f"Path to ai-status.json (default: {DEFAULT_STATUS_FILE})",
    )
    args = parser.parse_args()

    if args.evidence_root:
        audit = audit_evidence_root(Path(args.evidence_root))
        if args.audit_json:
            write_audit_json(audit, Path(args.audit_json))
        if args.audit_md:
            write_audit_markdown(audit, Path(args.audit_md))
        for result in audit["results"]:
            label = "OK" if result["result"] == "pass" else "FAIL"
            print(f"[{label}] {result['task_id']} ({result['overall_admission']})")
            for gap in result["gaps"]:
                print(f"       ✗ {gap}")
        summary = audit["summary"]
        print(
            f"\n{summary['passed']}/{summary['scanned']} evidence manifest(s) "
            "passed closeout truth replay."
        )
        sys.exit(1 if summary["failed"] else 0)

    status_path = Path(args.status_file)
    rc = run(status_path, args.task_id)
    sys.exit(rc)


if __name__ == "__main__":
    main()
