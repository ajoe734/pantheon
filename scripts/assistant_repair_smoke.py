#!/usr/bin/env python3
"""Smoke-check assistant kernel repair worktree workflow metadata."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ADAPTER_DIR = ROOT / "services" / "openclaw-gateway-adapter"
if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))

from assistant_repair_workflow import AssistantRepairWorkflow, AssistantRepairWorkflowError  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--worktree", required=True, help="Task worktree/repository root.")
    parser.add_argument(
        "--worktree-root",
        default=None,
        help="Allowed parent for repair worktrees. Defaults to PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT.",
    )
    parser.add_argument(
        "--scope",
        action="append",
        default=[],
        help="Repo-relative declared scope. Repeat for multiple files/directories.",
    )
    parser.add_argument("--expected-branch", default=None)
    parser.add_argument("--remote", default=None)
    parser.add_argument("--merge-target", default=None)
    parser.add_argument("--require-pr", action="store_true")
    parser.add_argument(
        "--allow-dirty-task-scope",
        action="store_true",
        help="Allow dirty files inside declared scope; files outside scope still fail.",
    )
    parser.add_argument("--pr-number", type=int, default=None)
    parser.add_argument("--pr-url", default=None)
    parser.add_argument("--pr-state", default=None)
    parser.add_argument("--pr-base", default=None)
    parser.add_argument("--pr-head", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env: dict[str, str] = {}
    if args.worktree_root:
        env["PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT"] = args.worktree_root
    if args.remote:
        env["PANTHEON_ASSISTANT_REPAIR_REMOTE"] = args.remote
    if args.merge_target:
        env["PANTHEON_ASSISTANT_REPAIR_MERGE_TARGET"] = args.merge_target

    metadata: dict[str, Any] = {
        "task_id": args.task_id,
        "task_worktree": args.worktree,
        "declared_scope": args.scope,
        "require_pr": args.require_pr,
        "require_clean": not args.allow_dirty_task_scope,
    }
    if args.expected_branch:
        metadata["expected_branch"] = args.expected_branch
    if args.merge_target:
        metadata["merge_target"] = args.merge_target
    if args.remote:
        metadata["remote"] = args.remote
    if any(value is not None for value in (args.pr_number, args.pr_url, args.pr_state, args.pr_base, args.pr_head)):
        metadata["pull_request"] = {
            "number": args.pr_number,
            "url": args.pr_url,
            "state": args.pr_state,
            "baseRefName": args.pr_base,
            "headRefName": args.pr_head,
        }

    try:
        snapshot = AssistantRepairWorkflow(env if env else None).validate(metadata)
    except AssistantRepairWorkflowError as exc:
        print(json.dumps(exc.to_payload(), indent=2, sort_keys=True), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "status": "ok",
                "repair_workflow": snapshot.to_dict(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
