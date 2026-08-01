#!/usr/bin/env python3
"""Run the real reconcile_merged_done evidence gate against a candidate file.

This script deliberately does not reimplement any gate. It imports
``scripts/ai_status.py`` from a chosen Pantheon root and calls the governed
``validate_merged_done_evidence`` function, so a pass here is a pass of the
exact code ``reconcile_merged_done`` runs. A reimplementation would be a
self-attesting proof: it could drift from the governed validator and still
print success.

It is read-only. ``validate_merged_done_evidence`` performs no writes, and this
script never calls ``command_reconcile_merged_done``; only ``Human/Ops`` may do
that, through ``$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh``.

Two exit codes carry meaning:

  0  every gate passed for the supplied inputs
  1  a gate failed; the exact ``SystemExit`` message from the governed
     validator is printed verbatim

Pre-merge use
-------------
Until the evidence file is merged, the evidence commit cannot be an ancestor of
``origin/dev``. Passing ``--evidence-target-ref HEAD`` with ``--command-root``
set to the task worktree exercises every other gate against the real validator
and is reported as ``preflight`` rather than ``production`` in the output, so
the substitution is never silent.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


def _load_ai_status(command_root: Path):
    module_path = command_root / "scripts" / "ai_status.py"
    if not module_path.is_file():
        raise SystemExit(f"no scripts/ai_status.py under {command_root}")
    # ai_status.py imports sibling helpers from scripts/ and .orchestrator/.
    for extra in (command_root / "scripts", command_root / ".orchestrator"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    spec = importlib.util.spec_from_file_location("_governed_ai_status", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="canonical task id to reconcile")
    parser.add_argument(
        "--evidence-file",
        required=True,
        help="repository-relative path of the merged evidence file",
    )
    parser.add_argument("--evidence-commit", required=True)
    parser.add_argument("--delivery-commit", required=True)
    parser.add_argument("--delivery-repository", default="ajoe734/pantheon")
    parser.add_argument(
        "--command-root",
        default=os.environ.get("PANTHEON_COMMAND_ROOT", ""),
        help="root whose scripts/ai_status.py is imported and used as evidence ROOT",
    )
    parser.add_argument(
        "--delivery-root",
        default="",
        help="git root checked for the delivery commit; defaults to --command-root",
    )
    parser.add_argument("--evidence-target-ref", default="origin/dev")
    parser.add_argument("--delivery-target-ref", default="origin/dev")
    args = parser.parse_args(argv)

    if not args.command_root:
        raise SystemExit("--command-root or PANTHEON_COMMAND_ROOT is required")
    command_root = Path(args.command_root).resolve()
    delivery_root = Path(args.delivery_root or args.command_root).resolve()

    mode = "production" if args.evidence_target_ref == "origin/dev" else "preflight"

    module = _load_ai_status(command_root)
    governed = Path(os.environ.get("PANTHEON_COMMAND_ROOT", "")) / "scripts" / "ai_status.py"
    identity = {
        "imported_module": str(command_root / "scripts" / "ai_status.py"),
        "imported_sha256": _sha256(command_root / "scripts" / "ai_status.py"),
        "evidence_ROOT": str(module.ROOT),
        "status_file": str(module.STATUS_FILE),
    }
    if governed.is_file():
        identity["governed_command_root_module"] = str(governed)
        identity["governed_command_root_sha256"] = _sha256(governed)
        identity["module_matches_governed_command_root"] = (
            identity["imported_sha256"] == identity["governed_command_root_sha256"]
        )

    state = module.load_state()
    task = module.get_task(state, args.task)
    if task is None:
        print(json.dumps({"mode": mode, "task": args.task, "error": "unknown task"}, indent=2))
        return 1

    os.environ["RECONCILE_EVIDENCE_FILE"] = args.evidence_file
    os.environ["RECONCILE_EVIDENCE_COMMIT"] = args.evidence_commit
    os.environ["RECONCILE_EVIDENCE_TARGET_REF"] = args.evidence_target_ref
    os.environ["RECONCILE_DELIVERY_REPOSITORY"] = args.delivery_repository
    os.environ["RECONCILE_DELIVERY_ROOT"] = str(delivery_root)
    os.environ["RECONCILE_DELIVERY_COMMIT"] = args.delivery_commit
    os.environ["RECONCILE_DELIVERY_TARGET_REF"] = args.delivery_target_ref

    report: dict[str, object] = {
        "mode": mode,
        "task": args.task,
        "canonical_row": {
            "status": task.get("status"),
            "owner": task.get("owner"),
            "reviewer": task.get("reviewer"),
        },
        "module_identity": identity,
        "inputs": {
            "evidence_file": args.evidence_file,
            "evidence_commit": args.evidence_commit,
            "evidence_target_ref": args.evidence_target_ref,
            "delivery_repository": args.delivery_repository,
            "delivery_root": str(delivery_root),
            "delivery_commit": args.delivery_commit,
            "delivery_target_ref": args.delivery_target_ref,
        },
    }
    if mode == "preflight":
        report["preflight_substitution"] = (
            f"evidence ancestry checked against {args.evidence_target_ref} instead of "
            "origin/dev; the evidence file is not merged yet"
        )

    # Status precondition enforced by command_reconcile_merged_done itself.
    accepted = {"todo", "in_progress", "blocked", "review", "review_approved"}
    status = str(task.get("status") or "")
    report["status_precondition_ok"] = status in accepted

    try:
        delivery = module.validate_merged_done_evidence(task)
    except SystemExit as exc:
        report["result"] = "FAIL"
        report["gate_error"] = str(exc)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    except Exception as exc:  # noqa: BLE001 - surface the real failure
        report["result"] = "ERROR"
        report["gate_error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    delivery.pop("recorded_at", None)
    report["result"] = "PASS" if report["status_precondition_ok"] else "FAIL"
    report["validated_delivery"] = delivery
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
